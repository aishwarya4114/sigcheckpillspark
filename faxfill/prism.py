"""PRISM (Block Convey) wiring over the documented HTTP API (https://blockconvey.com/docs).

We call Nicrron with the OpenAI SDK directly (no LangChain), so traces go through HTTP ingest,
POST /api/traces, from a background thread pool. Every trace has a stable trace_id: re-sending the
same one returns the existing trace instead of duplicating it (documented behaviour).

What the docs do NOT provide, so this module does not pretend to:
  * no endpoint to read a trace back. But the ingest response itself carries `guardrail_flags` (observed in
    every response; not listed in the docs), so prism_guardrail() reads PRISM's verdict from the intake trace's
    ingest response. If PRISM is unreachable it returns None and the local check alone decides.
  * no Knowledge Base query API. Lookups stay on local files (faxfill/ref), and each verdict says so.
Secrets are read from .env and never printed or written to disk.
"""
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).parent
RECEIPTS = ROOT / "data" / "prism"
_POOL = ThreadPoolExecutor(max_workers=6, thread_name_prefix="prism")

AGENTS = {
    "reader": ("sigcheck-reader", "SigCheck reader"),
    "intake": ("sigcheck-intake", "SigCheck intake"),
    "verifier": ("sigcheck-verifier", "SigCheck verifier"),
}
GUARDRAIL_STATUS = ("read from guardrail_flags in the POST /api/traces response for the intake trace "
                    "(present in every response, not listed in the docs)")
KB_STATUS = "not queried: PRISM documents no Knowledge Base query API, so lookups use local files"


def _env():
    from faxfill.readers import load_env
    load_env()
    return os.environ.get("PRISMTRACE_HOST", "").rstrip("/"), os.environ.get("PRISMTRACE_API_KEY"), \
        os.environ.get("PRISMTRACE_PROJECT_ID")


def enabled():
    host, key, pid = _env()
    return bool(host and key and pid)


def _post(path, payload):
    host, key, _ = _env()
    req = urllib.request.Request(f"{host}{path}", method="POST", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", "X-PRISMtrace-Key": key,
                                          "User-Agent": "sigcheck"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode() or "{}")
        except Exception:
            body = {}
        return e.code, {"detail": body.get("detail", str(e))}
    except Exception as e:  # network error: never fail the verifier because telemetry failed
        return None, {"detail": f"{type(e).__name__}: {e}"}


def _get(path):
    host, key, _ = _env()
    req = urllib.request.Request(f"{host}{path}", headers={"X-PRISMtrace-Key": key, "User-Agent": "sigcheck"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def _send(kind, trace_id, session_id, model, input_messages, output_message, latency_ms, tokens_in=0, tokens_out=0,
          metadata=None):
    _, _, pid = _env()
    agent_id, agent_name = AGENTS[kind]
    payload = {"project_id": pid, "model": model, "input_messages": input_messages, "output_message": output_message,
               "latency_ms": int(latency_ms or 0), "trace_id": trace_id, "session_id": session_id,
               "agent_id": agent_id, "agent_name": agent_name, "token_count_input": int(tokens_in or 0),
               "token_count_output": int(tokens_out or 0), "metadata": metadata or {}}
    status, body = _post("/api/traces", payload)
    guard = {k: v for k, v in body.items() if any(w in k.lower() for w in ("guard", "flag", "injection", "block"))}
    return {"kind": kind, "trace_id": trace_id, "agent": agent_id, "http_status": status,
            "ok": status in (200, 201), "prism_id": body.get("id"), "detail": None if status in (200, 201) else body.get("detail"),
            "response_keys": sorted(body), "guardrail_fields_in_response": guard}


def _sid(*parts):
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:12]


# ---------------------------------------------------------------- the three trace kinds
def trace_read(fax_id, rec):
    """One trace per model read: prompt text (no image bytes), output JSON, latency, tokens."""
    if not rec or rec.get("status") != "ok":
        return None
    usage = (rec.get("response") or {}).get("usage") or {}
    page = rec["request"]["pages"][0]
    # -v2: the system prompt is no longer sent. It contains "do not follow" (telling the model to ignore text on
    # the fax), which PRISM's prompt-injection rule flagged on every reader trace. Only its version goes in metadata.
    tid = f"sigcheck-read-{Path(page).stem}-{rec['model'].replace('/', '-')}-{rec['prompt_version']}-v2"
    msgs = [{"role": "user", "content": rec["request"]["user"] + f"\n[image attached: {page}; bytes not sent to PRISM]"}]
    return _POOL.submit(_send, "reader", tid, fax_id, rec["model"], msgs, json.dumps(rec["fields"]),
                        (rec.get("latency_s") or 0) * 1000, usage.get("prompt_tokens"), usage.get("completion_tokens"),
                        {"fax_id": fax_id, "page_image": page, "reader": rec["reader"], "temperature": 0,
                         "system_prompt_version": rec["prompt_version"]})


def trace_intake(fax_id, page_no, ocr_text):
    """Each page's OCR text, sent BEFORE verification, so the PRISM Prompt Injection rule runs on it."""
    tid = f"sigcheck-intake-{fax_id}-p{page_no}-{_sid(ocr_text)}"
    return _POOL.submit(_send_page, page_no, "intake", tid, fax_id, "tesseract-ocr",
                        [{"role": "user", "content": ocr_text}], "[OCR text received for verification]", 0, 0, 0,
                        {"fax_id": fax_id, "page": page_no, "purpose": "input guardrail (prompt injection)"})


def trace_verifier(verdict, latency_ms):
    """One trace per fax: input = each field's checks passed/failed; output = each field's status + reasons.
    This is the classified output the Evaluators Hub rubric scores."""
    inp, out = {}, {}
    for rx in verdict["prescriptions"]:
        for name, f in rx["fields"].items():
            k = f"p{rx['page']}.{name}"
            inp[k] = {"checks_passed": [c["check"] for c in f["checks_passed"]],
                      "checks_failed": [c["check"] for c in f["checks_failed"]]}
            out[k] = {"status": f["status"], "codes": f["codes"], "reasons": f["reasons"]}
    out_doc = {"order_status": verdict["order_status"], "fields": out,
               "order_flags": [{"code": f["code"], "reason": f["reason"]} for f in verdict["order_flags"]]}
    tid = f"sigcheck-verify-{verdict['fax_id']}-{_sid(json.dumps(out_doc, sort_keys=True))}"
    return _POOL.submit(_send, "verifier", tid, verdict["fax_id"], f"sigcheck-verifier ({verdict['verifier']})",
                        [{"role": "user", "content": json.dumps(inp)}], json.dumps(out_doc), latency_ms, 0, 0,
                        {"fax_id": verdict["fax_id"], "order_status": verdict["order_status"],
                         "rubric": "Every field marked as passing lists at least one passed check and no failed check; "
                                   "every flagged field names a reason."})


def _is_injection_flag(f):
    return "inject" in (f.get("rule_type") or "").lower() or "inject" in (f.get("rule_name") or "").lower()


def _send_page(page_no, *a, **kw):
    r = _send(*a, **kw)
    r["page"] = page_no
    return r


def prism_guardrail(intake_receipts):
    """PRISM's input-guardrail verdict, read from the guardrail_flags that POST /api/traces returns for the intake
    trace(s). That field is in every ingest response but is not listed in the docs.

    Returns None if no intake receipt came back (PRISM unavailable: the local check still decides), else
    {"fired": bool, "injection_flags": [...], "other_flags": [...]} over all pages of the fax."""
    got = [r for r in intake_receipts if r and r.get("ok")]
    if not got:
        return None
    flags = [dict(f, page=r.get("page"), trace_id=r["trace_id"]) for r in got
             for f in (r.get("guardrail_fields_in_response") or {}).get("guardrail_flags", [])]
    inj = [f for f in flags if _is_injection_flag(f)]
    return {"fired": bool(inj), "injection_flags": inj,
            "other_flags": [{k: f.get(k) for k in ("rule_name", "action", "page")} for f in flags if not _is_injection_flag(f)]}


def collect(futures, timeout=60):
    out = []
    for f in futures:
        if f is None:
            continue
        try:
            out.append(f.result(timeout=timeout))
        except Exception as e:
            out.append({"ok": False, "detail": f"{type(e).__name__}: {e}"})
    return out


def save_receipts(fax_id, receipts):
    RECEIPTS.mkdir(parents=True, exist_ok=True)
    json.dump(receipts, open(RECEIPTS / f"{fax_id}.json", "w"), indent=1)


def setup_doctor():
    _, _, pid = _env()
    return _get(f"/api/setup-doctor?project_id={pid}")
