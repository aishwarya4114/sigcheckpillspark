"""SigCheck server: one shared backend for the web review workspace, the phone /scan page and the iPhone app.

Run:  .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Literal

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from html import escape
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from sponsors import Prism, settings

from . import pipeline, policy as policy_mod, workflow

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "docs"
STATIC = Path(__file__).parent / "static"
SAMPLES = ROOT / "fixtures" / "synthetic"
DATA.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="SigCheck")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
prism = Prism()
lock = threading.Lock()
idempotency: dict[str, str] = {}
rescan_idempotency: dict[tuple[str, str], str] = {}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def path(doc_id: str) -> Path:
    if not doc_id.replace("-", "").isalnum():
        raise HTTPException(400, "bad id")
    return DATA / doc_id


def load(doc_id: str) -> dict:
    p = path(doc_id) / "record.json"
    if not p.exists():
        raise HTTPException(404, "document not found")
    return json.loads(p.read_text())


def save(rec: dict) -> None:
    d = path(rec["id"]); d.mkdir(parents=True, exist_ok=True)
    tmp = d / "record.json.tmp"
    tmp.write_text(json.dumps(rec, indent=1, default=str))
    tmp.replace(d / "record.json")


def event(rec: dict, actor: str, action: str, **kw) -> None:
    rec.setdefault("events", []).append({"time": now(), "actor": actor, "action": action, **kw})


def out(rec: dict) -> dict:
    """Record plus its derived workflow (never stored, always recomputed)."""
    return {**rec, "workflow": workflow.compute(rec)}


def recheck(rec: dict) -> None:
    """Re-run the deterministic checks on the CURRENT values (after a correction or prescriber answer)."""
    if not rec.get("fields"):
        return
    raw = {k: {"value": f["value"], "source_text": f.get("source_text", ""), "conflict": f.get("conflict", False),
               "note": f.get("note", "")} for k, f in rec["fields"].items()}
    located = {k: (f.get("box"), f.get("ocr_score")) for k, f in rec["fields"].items()}
    rec["fields"] = pipeline.assess_fields(raw, located, rec.get("policy") or policy_mod.current())


def summary(rec: dict) -> dict:
    unresolved = [k for k, f in rec.get("fields", {}).items()
                  if f["status"] in ("missing", "needs_review") and not rec.get("review", {}).get(k)]
    return {"id": rec["id"], "source": rec["source"], "received_at": rec["received_at"], "status": rec["status"],
            "revision": rec["revision"], "unresolved": len(unresolved),
            "drug": (rec.get("fields", {}).get("drug_name") or {}).get("value"),
            "patient": (rec.get("fields", {}).get("patient_name") or {}).get("value"),
            "clarification": rec.get("clarification"),
            "workflow_status": (wf := workflow.compute(rec))["status"], "workflow_label": wf["label"],
            "actions": wf["actions"], "next_action": wf["next_action"], "source_kind": workflow.source_kind(rec["source"]),
            "headline": ("Blocked: text on the fax tries to instruct the software" if rec["status"] == "blocked"
                         else "Extraction failed: nothing was guessed" if rec["status"] == "processing_failed" else None)}


def ingest(data: bytes, mime: str, source: str, doc_id: Optional[str] = None) -> dict:
    rec = pipeline.process(data, mime, doc_id=doc_id, source=source, prism=prism)
    d = path(rec["id"]); d.mkdir(parents=True, exist_ok=True)
    ext = "png" if "png" in mime else "jpg"
    (d / f"original.{ext}").write_bytes(data)
    rec["image"] = f"original.{ext}"
    event(rec, source, "received", revision=rec["revision"])
    save(rec)
    return rec


# ------------------------------------------------------------------ pages
@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/scan")
def scan_page():
    return FileResponse(STATIC / "scan.html")


@app.get("/system-design")
def system_design_page():
    return FileResponse(STATIC / "system-design.html")


@app.get("/pitch")
def pitch_page():
    return FileResponse(STATIC / "pitch.html")


@app.get("/scorecard")
def scorecard_page():
    return FileResponse(STATIC / "scorecard.html")


# ------------------------------------------------------------------ API
@app.get("/api/health")
def health():
    return {"nicrron": "live" if settings.nicrron_enabled else "not configured (demo label reader in use)",
            "reader_model": settings.reader_model if settings.nicrron_enabled else None,
            "prism": "configured" if prism.enabled else f"not configured ({prism.init_error})",
            "ocr": "Apple Vision (on-device)"}


@app.get("/api/documents")
def list_docs():
    recs = [json.loads(p.read_text()) for p in DATA.glob("*/record.json")]
    return [summary(r) for r in sorted(recs, key=lambda r: r["received_at"], reverse=True)]


@app.post("/api/documents")
async def upload(file: UploadFile = File(...), source: str = Form("web upload"),
                 idempotency_key: Optional[str] = Header(None)):
    if idempotency_key and idempotency_key in idempotency:
        return summary(load(idempotency[idempotency_key]))          # retry after a dropped connection: no duplicate
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    mime = file.content_type or "image/png"
    if not mime.startswith("image/"):
        raise HTTPException(415, "Upload a PNG or JPEG image of the fax.")
    rec = ingest(data, mime, source)
    if idempotency_key:
        idempotency[idempotency_key] = rec["id"]
    return summary(rec)


@app.get("/api/samples")
def samples():
    return [{"name": p.stem, "trap": json.loads((SAMPLES / f"{p.stem}.truth.json").read_text())["trap"]}
            for p in sorted(SAMPLES.glob("*.png"))]


@app.post("/api/samples/{name}")
def load_sample(name: str):
    p = SAMPLES / f"{name}.png"
    if not p.exists():
        raise HTTPException(404, "no such sample")
    return summary(ingest(p.read_bytes(), "image/png", f"synthetic sample: {name}"))


@app.get("/api/documents/{doc_id}")
def get_doc(doc_id: str):
    return out(load(doc_id))


@app.get("/api/documents/{doc_id}/image")
def get_image(doc_id: str):
    rec = load(doc_id)
    return FileResponse(path(doc_id) / rec["image"])


class FieldReview(BaseModel):
    action: str                     # "confirm" | "edit"
    value: Optional[str] = None
    reason: str = ""
    reviewer: str = "pharmacist"
    revision: int


@app.post("/api/documents/{doc_id}/fields/{field}")
def review_field(doc_id: str, field: str, body: FieldReview):
    with lock:
        rec = load(doc_id)
        if rec["status"] == "blocked":
            raise HTTPException(409, "Order is blocked: text on the fax tried to instruct the software.")
        if body.revision != rec["revision"]:
            raise HTTPException(409, f"Document changed (now revision {rec['revision']}). Reload and review again.")
        f = rec["fields"].get(field)
        if f is None:
            raise HTTPException(404, "unknown field")
        if body.action == "edit":
            if not body.reason.strip():
                raise HTTPException(400, "A correction needs a reason (what on the fax or which clarification supports it).")
            event(rec, body.reviewer, "edited", field=field, before=f["value"], after=body.value, reason=body.reason)
            f["value"] = body.value
            recheck(rec)
        elif body.action == "confirm":
            if f["value"] is None:
                raise HTTPException(400, "Nothing to confirm: the field is empty. Edit it from evidence or request clarification.")
            event(rec, body.reviewer, "confirmed", field=field, value=f["value"], reason=body.reason)
        else:
            raise HTTPException(400, "action must be confirm or edit")
        rec.setdefault("review", {})[field] = {"action": body.action, "by": body.reviewer, "at": now(), "reason": body.reason}
        if rec.get("approved"):
            rec["approved"] = None                                    # any change after approval invalidates it
            rec["status"] = "needs_review"
            event(rec, "system", "approval invalidated", reason="field changed after approval")
        rec["revision"] += 1
        save(rec)
        return out(rec)


class Clarify(BaseModel):
    kind: Literal["rescan", "prescriber"]
    fields: list[str] = []
    message: str = ""
    reviewer: str = "pharmacist"


@app.post("/api/documents/{doc_id}/clarify")
def clarify(doc_id: str, body: Clarify):
    with lock:
        rec = load(doc_id)
        if rec["status"] in ("blocked", "processing_failed", "processing"):
            raise HTTPException(409, "Resolve the document issue with a new scan before requesting field clarification.")
        if any(k not in rec["fields"] for k in body.fields):
            raise HTTPException(400, "Unknown clarification field")
        names = ", ".join(body.fields) or "the flagged fields"
        draft = body.message or (
            f"Request clearer image of: {names}." if body.kind == "rescan" else
            f"Clarification needed from prescriber for: {names}. Please re-send or call the pharmacy. (Draft only: not sent automatically.)")
        rec["clarification"] = {"kind": body.kind, "fields": body.fields, "message": draft, "requested_at": now(), "open": True}
        rec["status"] = "needs_clarification"
        rec["approved"] = None
        rec["revision"] += 1
        event(rec, body.reviewer, f"clarification requested ({body.kind})", fields=body.fields, message=draft)
        save(rec)
        return out(rec)


@app.post("/api/documents/{doc_id}/rescan")
async def rescan(doc_id: str, file: UploadFile = File(...), source: str = Form("phone rescan"),
                 idempotency_key: Optional[str] = Header(None)):
    """A clearer scan is attached to the SAME document as a new revision. Earlier evidence and human
    decisions are kept; reviews on fields whose value changed are cleared and must be redone."""
    data = await file.read()
    if not data or not (file.content_type or "").startswith("image/"):
        raise HTTPException(400, "Select a nonempty prescription image.")
    with lock:
        if idempotency_key and (doc_id, idempotency_key) in rescan_idempotency:
            return summary(load(doc_id))
        old = load(doc_id)
        new = pipeline.process(data, file.content_type or "image/jpeg", doc_id=doc_id, source=source, prism=prism)
        d = path(doc_id)
        ext = "png" if "png" in (file.content_type or "") else "jpg"
        name = f"rescan_r{old['revision'] + 1}.{ext}"
        (d / name).write_bytes(data)
        kept = {k: v for k, v in old.get("review", {}).items()
                if new["fields"].get(k, {}).get("value") == old["fields"].get(k, {}).get("value")}
        changed = sorted(k for k in set(new.get("fields", {})) | set(old.get("fields", {}))
                         if new.get("fields", {}).get(k, {}).get("value") != old.get("fields", {}).get(k, {}).get("value"))
        new["changed_fields"] = changed
        new["resolutions"] = {k: v for k, v in old.get("resolutions", {}).items() if k.split(":")[0] not in changed}
        new["opened_at"] = old.get("opened_at")
        new.update({"image": name, "revision": old["revision"] + 1, "review": kept, "events": old.get("events", []),
                    "history": old.get("history", []) + [{"revision": old["revision"], "image": old["image"],
                                                          "fields": old["fields"], "replaced_at": now()}],
                    "received_at": old["received_at"]})
        if old.get("clarification"):
            new["clarification"] = {**old["clarification"], "open": False, "answered_at": now()}
        event(new, source, "updated document attached", revision=new["revision"], reviews_kept=list(kept), changed_fields=changed)
        save(new)
        if idempotency_key:
            rescan_idempotency[(doc_id, idempotency_key)] = new["id"]
        return summary(new)


class Approve(BaseModel):
    reviewer: str = "pharmacist"
    revision: int


@app.post("/api/documents/{doc_id}/approve")
def approve(doc_id: str, body: Approve, x_role: str = Header("pharmacist")):
    with lock:
        rec = load(doc_id)
        if x_role != "pharmacist":
            raise HTTPException(403, "Only a pharmacist can approve.")
        if rec["status"] in ("blocked", "processing_failed", "processing"):
            raise HTTPException(409, "Blocked, failed or processing orders cannot be approved.")
        if not rec.get("fields") or any(not f.get("field") for f in rec.get("findings", [])):
            raise HTTPException(409, "Resolve document-level findings before approval.")
        if body.revision != rec["revision"]:
            raise HTTPException(409, f"Stale approval: document is at revision {rec['revision']}. Review the current version.")
        missing = [k for k, f in rec["fields"].items() if f["required"] and (not str(f["value"] or "").strip() or not rec.get("review", {}).get(k))]
        if missing:
            raise HTTPException(409, {"error": "Every required field must be reviewed and have a value.", "fields": missing})
        open_issues = [i for i in workflow.compute(rec)["issues"] if i["state"] != "resolved"]
        if open_issues:
            raise HTTPException(409, {"error": "Resolve every open issue first (prescriber clarifications can't be cleared by confirming the fax).",
                                      "fields": sorted({i["field"] or "document" for i in open_issues})})
        rec["approved"] = {"by": body.reviewer, "at": now(), "revision": rec["revision"],
                           "attestation": "I reviewed every required field against the source and resolved all open issues."}
        rec["status"] = "approved"
        if rec.get("clarification"):
            rec["clarification"]["open"] = False
        event(rec, body.reviewer, "approved draft", revision=rec["revision"])
        save(rec)
        return out(rec)


@app.get("/api/documents/{doc_id}/export")
def export(doc_id: str):
    rec = load(doc_id)
    if rec["status"] != "approved" or not rec.get("approved") or rec["approved"]["revision"] != rec["revision"]:
        raise HTTPException(409, "Only an approved current revision can be exported.")
    out = {
        "schema": "sigcheck.draft_order.v1", "document_id": rec["id"], "revision": rec["revision"],
        "source_sha256": rec["sha256"], "source": rec["source"],
        "order": {k: f["value"] for k, f in rec["fields"].items()},
        "original_text": {k: f["source_text"] for k, f in rec["fields"].items()},
        "checks": {k: f["reasons"] for k, f in rec["fields"].items()},
        "review": rec["review"], "approved": rec["approved"], "history": rec["events"],
        "note": "Approved DRAFT for pharmacy entry. Not dispensed, not sent to a pharmacy system.",
    }
    return Response(json.dumps(out, indent=2), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="sigcheck-{rec["id"]}-r{rec["revision"]}.json"'})


@app.get("/api/documents/{doc_id}/evaluation")
def evaluation(doc_id: str):
    rec = load(doc_id)
    tid = (rec.get("sponsors", {}).get("evaluator") or {}).get("trajectory_id")
    verdict = prism.evaluation_result(tid) if tid else None
    return {"trajectory_id": tid, "verdict": verdict, "state": "received" if verdict else "pending or unavailable"}


@app.get("/api/scorecard")
def scorecard():
    p = ROOT / "evals" / "results.json"
    result = json.loads(p.read_text()) if p.exists() else {"error": "Run: python scripts/evaluate.py"}
    heldout = ROOT / "faxfill" / "data" / "eval" / "heldout.json"
    if heldout.exists():
        result["heldout"] = json.loads(heldout.read_text())
    return JSONResponse(result)


LABELS = {"patient_name": "Patient", "patient_dob": "Date of birth", "drug_name": "Drug", "strength": "Strength",
          "dosage_form": "Dosage form", "directions": "Directions (sig)", "quantity": "Quantity", "refills": "Refills",
          "prescriber_name": "Prescriber", "prescriber_npi": "Prescriber NPI", "prescriber_dea": "DEA number",
          "date_written": "Date written"}


@app.get("/api/documents/{doc_id}/clarification-sheet", response_class=HTMLResponse)
def clarification_sheet(doc_id: str):
    """A one-page, printable clarification request to fax back to the prescriber: exactly the unclear
    fields, what we read, why it's unclear, and a crop of that part of their fax. Nothing is sent automatically."""
    rec = load(doc_id)
    if rec["status"] == "blocked":
        raise HTTPException(409, "Blocked document: request a new fax instead of a field clarification.")
    open_fields = [k for k, f in rec.get("fields", {}).items()
                   if f["status"] in ("missing", "needs_review") and not rec.get("review", {}).get(k)]
    if rec.get("clarification", {}).get("fields"):
        open_fields = list(dict.fromkeys(rec["clarification"]["fields"] + open_fields))
    img = f"/api/documents/{doc_id}/image?r={rec['revision']}"
    f = rec["fields"]
    rows = []
    for k in open_fields:
        fld = f[k]
        crop = (f'<img class="crop" alt="Crop of the fax for {escape(LABELS.get(k, k))}" '
                f'src="/api/documents/{doc_id}/crop/{k}?r={rec["revision"]}">') if fld.get("box") else ""
        why = "; ".join(fld.get("reasons") or ["Please confirm."])
        nxt = next((c["next_step"] for c in fld.get("checks", []) if not c["passed"] and c["next_step"]), "")
        rows.append(f"""<tr><td><b>{escape(LABELS.get(k, k))}</b></td>
          <td>{escape(fld['value']) if fld['value'] else '<i>not readable / not stated</i>'}{crop}</td>
          <td>{escape(why)}{('<br><small>' + escape(nxt) + '</small>') if nxt else ''}</td>
          <td class="reply">&nbsp;</td></tr>""")
    body = "".join(rows) or '<tr><td colspan="4">No open questions on this prescription.</td></tr>'
    v = lambda k: escape((f.get(k) or {}).get("value") or "not readable")
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Clarification request {escape(doc_id)}</title>
<style>body{{font:14px/1.45 Arial,sans-serif;color:#111;max-width:900px;margin:24px auto;padding:0 24px}}
h1{{font-size:22px;margin:0}}.meta{{display:grid;grid-template-columns:1fr 1fr;gap:4px 24px;margin:14px 0;padding:10px;border:1px solid #999}}
table{{width:100%;border-collapse:collapse;margin-top:12px}}td,th{{border:1px solid #888;padding:8px;vertical-align:top;text-align:left}}
th{{background:#eee}}.reply{{width:26%}}.crop{{display:block;margin-top:6px;border:1px solid #bbb;max-width:100%;max-height:120px}}
.sig{{margin-top:28px;display:grid;grid-template-columns:2fr 1fr;gap:24px}}.sig div{{border-top:1px solid #111;padding-top:4px}}
.note{{font-size:12px;color:#444;margin-top:14px}}@media print{{button{{display:none}}}}</style></head><body>
<button onclick="print()" style="float:right;padding:8px 14px">Print / save as PDF</button>
<h1>Prescription clarification request</h1><div>Please write the correct value in the right-hand column, sign, and fax this page back.</div>
<div class="meta"><span><b>Patient:</b> {v('patient_name')}</span><span><b>Date of birth:</b> {v('patient_dob')}</span>
<span><b>Prescriber:</b> {v('prescriber_name')}</span><span><b>NPI:</b> {v('prescriber_npi')}</span>
<span><b>Rx date:</b> {v('date_written')}</span><span><b>Reference:</b> {escape(doc_id)} · revision {rec['revision']}</span></div>
<table><tr><th>Field</th><th>What we read on your fax</th><th>Why we're asking</th><th>Correct value (prescriber)</th></tr>{body}</table>
<div class="sig"><div>Prescriber signature</div><div>Date</div></div>
<p class="note">Generated by SigCheck for pharmacist review. Synthetic demo data. This request was not sent automatically; the pharmacy decides whether to send it.</p>
</body></html>"""


@app.get("/api/documents/{doc_id}/crop/{field}")
def crop(doc_id: str, field: str):
    """PNG crop of the region a field was read from (real OCR box, padded)."""
    import io
    from PIL import Image
    rec = load(doc_id)
    b = (rec.get("fields", {}).get(field) or {}).get("box")
    if not b:
        raise HTTPException(404, "no source region for this field")
    im = Image.open(path(doc_id) / rec["image"])
    W, H = im.size
    px, py = 0.04 * W, 0.012 * H
    box = (max(0, int(b[0] * W - px)), max(0, int(b[1] * H - py)),
           min(W, int((b[0] + b[2]) * W + px)), min(H, int((b[1] + b[3]) * H + py)))
    buf = io.BytesIO()
    im.crop(box).save(buf, format="PNG")
    return Response(buf.getvalue(), media_type="image/png")


@app.get("/api/policy")
def get_policy():
    return {"policy": policy_mod.current(), "limits": policy_mod.LIMITS,
            "locked_rules": policy_mod.LOCKED_RULES, "data_sources": policy_mod.DATA_SOURCES}


class Rerun(BaseModel):
    recognizer_threshold: Optional[float] = None
    max_rx_age_days: Optional[int] = None
    revision: int
    reviewer: str = "pharmacist"


@app.post("/api/documents/{doc_id}/rerun-checks")
def rerun_checks(doc_id: str, body: Rerun):
    """Re-apply the deterministic checks under a new policy snapshot. The image and extracted values are
    unchanged; no OCR or model call is made. Human confirmations are cleared and any approval is invalidated."""
    with lock:
        rec = load(doc_id)
        if rec["status"] in ("blocked", "processing_failed", "processing"):
            raise HTTPException(409, "Checks can't be re-run on a blocked or failed document. Safety blocks are not configurable.")
        if body.revision != rec["revision"]:
            raise HTTPException(409, f"Document changed (now revision {rec['revision']}). Reload first.")
        try:
            new_pol = policy_mod.validate({"recognizer_threshold": body.recognizer_threshold,
                                           "max_rx_age_days": body.max_rx_age_days})
        except ValueError as e:
            raise HTTPException(400, str(e))
        snap = policy_mod.save(new_pol)
        old_pol = rec.get("policy") or policy_mod.DEFAULTS
        before = pipeline.attention_count(rec["fields"])
        raw = {k: {"value": f["value"], "source_text": f.get("source_text", ""), "conflict": f.get("conflict", False),
                   "note": f.get("note", "")} for k, f in rec["fields"].items()}
        located = {k: (f.get("box"), f.get("ocr_score")) for k, f in rec["fields"].items()}
        rec["fields"] = pipeline.assess_fields(raw, located, new_pol)
        after = pipeline.attention_count(rec["fields"])
        cleared = list(rec.get("review", {}))
        rec["review"] = {}
        had_approval = bool(rec.get("approved"))
        rec["approved"] = None
        rec["policy"] = {k: new_pol[k] for k in ("version", "recognizer_threshold", "max_rx_age_days")}
        rec["revision"] += 1
        rec["status"] = "needs_clarification" if (rec.get("clarification") or {}).get("open") else "needs_review"
        missing_required = any(f["status"] == "missing" and f["required"] for f in rec["fields"].values())
        rec["classification"] = "needs_clarification" if missing_required else "complete_for_review"
        msg = f"Policy rerun complete · {before} → {after} fields require attention"
        event(rec, body.reviewer, "policy rerun", reason=msg,
              before={k: old_pol.get(k) for k in ("recognizer_threshold", "max_rx_age_days")},
              after={k: new_pol[k] for k in ("recognizer_threshold", "max_rx_age_days")},
              confirmations_cleared=cleared, approval_invalidated=had_approval, revision=rec["revision"],
              policy_saved_at=snap["saved_at"])
        save(rec)
        return {"message": msg, "before": before, "after": after, "document": out(rec)}


class Note(BaseModel):
    note: str = ""
    value: Optional[str] = None
    reviewer: str = "pharmacist"
    revision: int


@app.post("/api/documents/{doc_id}/open")
def open_case(doc_id: str):
    with lock:
        rec = load(doc_id)
        if not rec.get("opened_at"):
            rec["opened_at"] = now()
            event(rec, "pharmacist", "opened case")
            save(rec)
        return out(rec)


def _issue(rec: dict, key: str) -> dict:
    for i in workflow.issues(rec):
        if i["key"] == key:
            return i
    raise HTTPException(404, "No such open issue on the current revision.")


@app.post("/api/documents/{doc_id}/issues/{key}/contacted")
def mark_contacted(doc_id: str, key: str, body: Note):
    """Prescriber contacted: the case waits for their answer. Nothing is sent automatically."""
    with lock:
        rec = load(doc_id)
        if body.revision != rec["revision"]:
            raise HTTPException(409, f"Document changed (now revision {rec['revision']}). Reload first.")
        i = _issue(rec, key)
        if i["category"] != "prescriber_clarification":
            raise HTTPException(400, "Only prescriber clarifications are waited on.")
        rec.setdefault("resolutions", {})[key] = {"state": "waiting", "by": body.reviewer, "at": now(),
                                                   "note": body.note or "Prescriber contacted"}
        event(rec, body.reviewer, "prescriber contacted", field=i["field"], reason=f"{i['code']}: {body.note}".strip(": "))
        rec["revision"] += 1
        save(rec)
        return out(rec)


@app.post("/api/documents/{doc_id}/issues/{key}/resolve")
def resolve_clarification(doc_id: str, key: str, body: Note):
    """Record the prescriber's answer. A note is required; a corrected value is optional and re-checked."""
    with lock:
        rec = load(doc_id)
        if body.revision != rec["revision"]:
            raise HTTPException(409, f"Document changed (now revision {rec['revision']}). Reload first.")
        i = _issue(rec, key)
        if i["category"] != "prescriber_clarification":
            raise HTTPException(400, "Transcription issues are resolved by confirming or correcting the field.")
        if not body.note.strip():
            raise HTTPException(400, "Record what the prescriber said, who, and how (for example: 'Dr Patel by phone 2:31 PM: 5 mg').")
        field = i["field"]
        needs_value = i["code"] not in workflow.NOTE_RESOLVABLE
        if needs_value and field and not (body.value or "").strip():
            raise HTTPException(400, f"Record the unambiguous value the prescriber gave (for example \"5 mg\"). "
                                     f"A note alone can't resolve {i['code'].replace('_', ' ').lower()}.")
        if body.value is not None and field:
            before = rec["fields"][field]["value"]
            rec["fields"][field]["value"] = body.value.strip() or None
            recheck(rec)
            rec.setdefault("review", {})[field] = {"action": "edit", "by": body.reviewer, "at": now(), "reason": body.note}
            event(rec, body.reviewer, "edited", field=field, before=before, after=body.value, reason=f"Prescriber: {body.note}")
        if needs_value and field:
            still = [c for c in rec["fields"][field].get("checks", []) if not c["passed"] and workflow.RULES.get(c["rule"], (None, None))[1] == i["code"]]
            if still:
                raise HTTPException(400, f"\"{rec['fields'][field]['value']}\" is still ambiguous ({still[0]['detail']}) Record an unambiguous value.")
        rec.setdefault("resolutions", {})[key] = {"state": "resolved", "by": body.reviewer, "at": now(), "note": body.note}
        # an answer can resolve the same field's other clarification issues only if they no longer fail
        event(rec, body.reviewer, "prescriber clarification recorded", field=field, reason=f"{i['code']}: {body.note}")
        rec["approved"] = None
        if rec["status"] == "approved":
            rec["status"] = "needs_review"
        rec["revision"] += 1
        save(rec)
        return out(rec)


@app.post("/api/documents/{doc_id}/quarantine")
def quarantine_doc(doc_id: str):
    with lock:
        rec = load(doc_id)
        if rec["status"] != "blocked":
            raise HTTPException(409, "Only blocked documents are quarantined.")
        rec["quarantined"] = True
        event(rec, "pharmacist", "document quarantined", reason="Instructions aimed at the software; kept for manual review.")
        save(rec)
        return out(rec)


@app.get("/api/documents/{doc_id}/order-sheet", response_class=HTMLResponse)
def order_sheet(doc_id: str):
    """Printable approved draft order (save as PDF from the browser). Approved current revision only."""
    rec = load(doc_id)
    if rec["status"] != "approved" or not rec.get("approved") or rec["approved"]["revision"] != rec["revision"]:
        raise HTTPException(409, "Only an approved current revision can be printed as a draft order.")
    f, rv, ap = rec["fields"], rec.get("review", {}), rec["approved"]
    wf = workflow.compute(rec)
    prescriber = {i["field"]: i for i in wf["issues"] if i["category"] == "prescriber_clarification" and i["state"] == "resolved"}
    v = lambda k: escape(str((f.get(k) or {}).get("value") or "not stated"))
    rows = []
    for group, keys in (("Patient", ["patient_name", "patient_dob"]),
                        ("Prescription", ["drug_name", "strength", "dosage_form", "directions", "quantity", "refills", "date_written"]),
                        ("Prescriber", ["prescriber_name", "prescriber_npi", "prescriber_dea"])):
        rows.append(f'<tr class="grp"><th colspan="4">{group}</th></tr>')
        for k in keys:
            fld = f.get(k)
            if not fld or fld.get("status") == "not_applicable":
                continue
            r = rv.get(k) or {}
            if k in prescriber:
                how = f"Resolved with prescriber: {escape(prescriber[k].get('note') or '')}"
            elif r:
                how = ("Corrected from source" if r.get("action") == "edit" else "Verified against fax") + (f" · {escape(r.get('reason') or '')}" if r.get("action") == "edit" else "")
            else:
                how = "Not required"
            crop = (f'<img class="crop" alt="" src="/api/documents/{doc_id}/crop/{k}?r={rec["revision"]}">') if fld.get("box") else ""
            rows.append(f"<tr><td>{escape(LABELS.get(k, k))}</td><td><b>{v(k)}</b></td><td>{crop}</td><td class='how'>{how}<br><small>{escape(r.get('by') or '')} {escape((r.get('at') or '')[:16].replace('T', ' '))}</small></td></tr>")
    events = "".join(f"<li>{escape(e['time'][:16].replace('T', ' '))} · {escape(e['actor'])} · {escape(e['action'])}"
                     f"{(' · ' + escape(LABELS.get(e['field'], e['field']))) if e.get('field') else ''}</li>" for e in rec.get("events", []))
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Draft order {escape(doc_id)} r{rec['revision']}</title>
<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">
<style>body{{font:13px/1.45 Arial,sans-serif;color:#111;max-width:900px;margin:24px auto;padding:0 24px}}
.top{{display:flex;align-items:center;gap:12px;border-bottom:2px solid #216b55;padding-bottom:10px}}.top img{{width:36px;height:36px}}
h1{{font-size:20px;margin:0}}.sub{{color:#444}}.banner{{margin:12px 0;padding:8px 12px;border:1px solid #216b55;background:#f1f8f4;font-weight:bold}}
table{{width:100%;border-collapse:collapse;margin-top:8px}}td,th{{border-bottom:1px solid #ccc;padding:6px 8px;text-align:left;vertical-align:middle}}
tr.grp th{{background:#eef2ef;font-size:12px;text-transform:uppercase;letter-spacing:1px}}.crop{{max-height:34px;max-width:240px;border:1px solid #ddd}}
.how{{font-size:12px;color:#333}}.attest{{margin-top:16px;padding:10px 12px;border:1px solid #999}}ul{{margin:6px 0 0 18px;padding:0;font-size:12px;color:#333}}
.note{{font-size:11px;color:#555;margin-top:14px}}@media print{{button{{display:none}}body{{margin:0}}}}</style></head><body>
<button onclick="print()" style="float:right;padding:8px 14px">Print / save as PDF</button>
<div class="top"><img src="/static/logo.svg" alt=""><div><h1>Approved draft order</h1><div class="sub">SigCheck · reference {escape(doc_id)} · revision {rec['revision']}</div></div></div>
<div class="banner">Draft order for pharmacy entry. Not dispensed and not sent to a pharmacy system.</div>
<table><tr><th>Field</th><th>Value</th><th>On the original fax</th><th>How it was confirmed</th></tr>{''.join(rows)}</table>
<div class="attest"><b>Pharmacist attestation</b><br>{escape(ap.get('attestation') or '')}<br>Approved by <b>{escape(ap.get('by') or '')}</b> on {escape((ap.get('at') or '')[:16].replace('T', ' '))} UTC · revision {ap.get('revision')}</div>
<p><b>Audit trail</b></p><ul>{events}</ul>
<p class="note">Source fax SHA-256 {escape(rec.get('sha256', ''))}. Generated by SigCheck. Synthetic demo data.</p>
</body></html>"""
