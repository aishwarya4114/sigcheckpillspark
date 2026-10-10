"""Read -> Guard -> Extract -> Check -> Decide. Produces one document record for the review screen."""
from __future__ import annotations

import hashlib
import io
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from PIL import Image

from sponsors import FIELDS, NicrronReader, Prism, settings

from . import checks, policy

OCR_LOW = 0.6  # recogniser's own score; low score only ADDS review, it never verifies anything


# ---------------------------------------------------------------- OCR (Apple Vision on the Mac)
def ocr_lines(image_bytes: bytes) -> list[dict]:
    """[{text, confidence, box:[x,y,w,h] normalised, origin top-left}] in reading order."""
    try:
        from ocrmac import ocrmac
    except ImportError:
        return []
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    out = []
    for text, conf, (x, y, w, h) in ocrmac.OCR(img, recognition_level="accurate").recognize():
        out.append({"text": text, "confidence": round(float(conf), 2), "box": [x, 1 - y - h, w, h]})
    out.sort(key=lambda l: (round(l["box"][1], 2), l["box"][0]))
    return out


# ---------------------------------------------------------------- demo reader (no Nicrron key)
LABELS = {
    "patient": "patient_name", "patient name": "patient_name", "dob": "patient_dob", "date of birth": "patient_dob",
    "medication": "drug_line", "rx": "drug_line", "drug": "drug_line", "sig": "directions", "directions": "directions",
    "qty": "quantity", "quantity": "quantity", "disp": "quantity", "refills": "refills", "refill": "refills",
    "prescriber": "prescriber_name", "physician": "prescriber_name", "npi": "prescriber_npi", "dea": "prescriber_dea",
    "date": "date_written", "date written": "date_written",
}


def demo_read(lines: list[dict]) -> dict[str, dict]:
    """Pairs 'Label:' lines with the value printed on the same row. Demo only: real faxes need the Nicrron reader."""
    fields = {f: {"value": None, "source_text": "", "conflict": False, "note": ""} for f in FIELDS}
    labels = [l for l in lines if l["text"].strip().rstrip(":").lower() in LABELS and l["text"].strip().endswith(":")]
    values = [l for l in lines if l not in labels]
    for lab in labels:
        key = LABELS[lab["text"].strip().rstrip(":").lower()]
        ly = lab["box"][1] + lab["box"][3] / 2
        same_row = [v for v in values if abs((v["box"][1] + v["box"][3] / 2) - ly) < 0.02 and v["box"][0] > lab["box"][0]]
        if not same_row:
            continue
        v = min(same_row, key=lambda x: x["box"][0])
        if key == "drug_line":
            m = re.match(r"\s*([A-Za-z][A-Za-z\- ]*?)\s+(\d+(?:\.\d+)?\s*(?:mg|mcg|g|units?|ml|u)\b)?\s*(\w+)?\s*$", v["text"], re.I)
            name = m.group(1) if m else v["text"].split()[0]
            fields["drug_name"] = {"value": name, "source_text": v["text"], "conflict": False, "note": ""}
            fields["strength"] = {"value": (m.group(2) if m and m.group(2) else None), "source_text": v["text"], "conflict": False, "note": ""}
            fields["dosage_form"] = {"value": (m.group(3) if m and m.group(3) else None), "source_text": v["text"], "conflict": False, "note": ""}
        else:
            fields[key] = {"value": v["text"].strip(), "source_text": v["text"], "conflict": False, "note": ""}
    return fields


def find_box(lines: list[dict], source: str, value: str | None) -> tuple[list[float] | None, float | None]:
    """Highlight comes from real OCR boxes matched to the text, never from model-reported coordinates."""
    for needle in (source, value):
        if not needle:
            continue
        n = needle.strip().lower()
        for l in lines:
            t = l["text"].strip().lower()
            if n and (n == t or n in t or (len(t) > 3 and t in n)):
                return l["box"], l["confidence"]
    return None, None


# ---------------------------------------------------------------- the whole run
def process(image_bytes: bytes, mime: str, *, doc_id: str | None = None, source: str = "web upload",
            prism: Prism | None = None, reader: NicrronReader | None = None) -> dict[str, Any]:
    doc_id = doc_id or uuid.uuid4().hex[:10]
    prism = prism or Prism()
    t = {}
    rec: dict[str, Any] = {
        "id": doc_id, "source": source, "received_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sha256": hashlib.sha256(image_bytes).hexdigest(), "revision": 1, "status": "processing",
        "sponsors": {}, "findings": [], "fields": {}, "review": {}, "events": [],
    }

    s = time.monotonic(); lines = ocr_lines(image_bytes); t["ocr"] = int((time.monotonic() - s) * 1000)
    ocr_text = "\n".join(l["text"] for l in lines)
    rec["ocr"] = {"engine": "Apple Vision (on-device)" if lines else "unavailable", "lines": lines}

    # 1. Guardrail BEFORE any model sees the text
    s = time.monotonic(); g = prism.guardrail(doc_id, ocr_text); t["guardrail"] = int((time.monotonic() - s) * 1000)
    rec["sponsors"]["guardrail"] = {"blocked": g.blocked, "prism": vars(g.prism), "note": g.note,
                                    "findings": [{"text": f.text, "reason": f.reason} for f in g.findings]}
    if g.blocked:
        rec["status"] = "blocked"
        for f in g.findings:
            box, _ = find_box(lines, f.text, None)
            rec["findings"].append({"field": None, "severity": "block", "box": box,
                                    "reason": f"Text on the fax gives instructions to the software: \"{f.text}\" ({f.reason}). Whole order blocked; nothing was sent to a model."})
        rec["classification"] = "blocked"
        _evaluate(rec, prism, t, ocr_text)
        return rec

    # 2. Extraction: Nicrron vision reader when a key is set, otherwise the labelled demo reader
    s = time.monotonic()
    if settings.nicrron_enabled:
        reader = reader or NicrronReader()
        run = reader.extract(image_bytes, mime, ocr_text=ocr_text)
        rec["sponsors"]["reader"] = {"engine": f"Nicrron · {run.model}", "ok": run.ok, "error": run.error,
                                     "latency_ms": run.latency_ms, "prompt_version": run.prompt_version,
                                     "trace": vars(prism.trace_reader_run(doc_id, run))}
        fields = run.fields if run.ok else {f: {"value": None, "source_text": "", "conflict": False, "note": "extraction failed"} for f in FIELDS}
        rec["prescription_count"] = run.prescription_count
        if run.suspicious_text:
            rec["findings"].append({"field": None, "severity": "review", "box": None,
                                    "reason": "The reader noticed something on the page worth a look: " + "; ".join(run.suspicious_text)})
        if not run.ok:
            rec["status"] = "processing_failed"
    else:
        fields = demo_read(lines)
        rec["sponsors"]["reader"] = {"engine": "DEMO label reader (no Nicrron key set): reads our synthetic layout only",
                                     "ok": True, "error": None, "trace": {"sent": False, "accepted": False, "detail": "no Nicrron run"}}
    t["extract"] = int((time.monotonic() - s) * 1000)

    # 3. Checks + decide per field (deterministic; re-runnable with a new policy)
    s = time.monotonic()
    located = {}
    for name in FIELDS:
        f = fields.get(name) or {}
        located[name] = find_box(lines, f.get("source_text", ""), f.get("value"))
    rec["policy"] = policy.current()
    rec["fields"] = assess_fields(fields, located, rec["policy"])
    t["checks"] = int((time.monotonic() - s) * 1000)

    # 4. Knowledge-base claim for the drug/strength/form, with citation
    s = time.monotonic()
    d, st, fm = (rec["fields"][k]["value"] for k in ("drug_name", "strength", "dosage_form"))
    if d and st:
        kb = prism.kb_verify_catalog(d, st, fm or "")
        rec["sponsors"]["kb"] = {"status": kb.status, "claim": kb.claim, "citation": kb.citation, "document": kb.document,
                                 "source": kb.source, "prism": vars(kb.prism)}
    else:
        rec["sponsors"]["kb"] = {"status": "unavailable", "claim": "No drug + strength to look up.", "prism": {"sent": False, "accepted": False, "detail": ""}}
    t["kb"] = int((time.monotonic() - s) * 1000)

    if rec["status"] == "processing":
        unresolved = [k for k, v in rec["fields"].items() if v["status"] in ("missing", "needs_review")]
        rec["status"] = "needs_review"
        rec["classification"] = ("needs_clarification" if any(rec["fields"][k]["status"] == "missing" and rec["fields"][k]["required"] for k in unresolved)
                                 else "complete_for_review")
    else:
        rec["classification"] = "extraction_failed"
    _evaluate(rec, prism, t, ocr_text)
    return rec


def _evaluate(rec: dict, prism: Prism, timings: dict, ocr_text: str) -> None:
    facts = {"ocr_lines": len(rec["ocr"]["lines"]), "guardrail": rec["sponsors"].get("guardrail"),
             "fields": rec["fields"],
             "checks": {k: v["reasons"] for k, v in rec["fields"].items() if v["reasons"]},
             "kb": rec["sponsors"].get("kb")}
    ev = prism.request_evaluation(rec["id"], classification=rec["classification"], facts=facts, stage_ms=timings)
    rec["sponsors"]["telemetry"] = "redacted: PRISM receives statuses and issue codes; names, dates, NPI/DEA, phones and emails are masked"
    rec["sponsors"]["evaluator"] = {"trajectory_id": ev.trajectory_id, "prism": vars(ev.prism),
                                    "verdict": None, "note": "PRISM scores asynchronously; until a verdict arrives this shows as pending, never as a pass."}
    rec["timings_ms"] = timings


def assess_fields(fields: dict, located: dict, pol: dict) -> dict:
    """Deterministic decision for every field. No OCR, no model: safe to re-run under a new policy."""
    threshold = float(pol.get("recognizer_threshold", OCR_LOW))
    max_age = int(pol.get("max_rx_age_days", 365))
    controlled = checks.is_controlled(fields)
    required = set(checks.REQUIRED) | ({"prescriber_dea"} if controlled else set())
    out = {}
    for name in FIELDS:
        f = fields.get(name) or {"value": None, "source_text": ""}
        box, conf = located.get(name, (None, None))
        log = checks.explain_field(name, f.get("value"), fields, max_rx_age_days=max_age)
        if f.get("conflict"):
            log.append(checks._c("field.single_reading", "One consistent reading on the page", False,
                                 "Two incompatible readings: " + (f.get("note") or "see fax"),
                                 "Ask the prescriber which value is intended."))
        if conf is not None and f.get("value"):
            log.append(checks._c("ocr.legible", "Clearly legible on the fax", conf >= threshold,
                                 "This part of the fax is hard to read.",
                                 "Compare this value with the fax image; request a clearer scan if unsure."))
        problems = [c["detail"] for c in log if not c["passed"]]
        if f.get("value") is None and name not in required and name != "refills":
            status = "not_applicable"
        elif f.get("value") is None:
            status = "missing"
        elif problems:
            status = "needs_review"
        else:
            status = "checks_passed"
        out[name] = {"value": f.get("value"), "source_text": f.get("source_text", ""), "box": box,
                     "ocr_score": conf, "status": status, "reasons": problems, "checks": log,
                     "required": name in required, "note": f.get("note", ""), "conflict": bool(f.get("conflict"))}
    return out


def attention_count(fields: dict) -> int:
    return sum(1 for f in fields.values() if f["status"] in ("missing", "needs_review"))
