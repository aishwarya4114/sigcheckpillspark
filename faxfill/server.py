"""Pharmacist review screen.

    python -m faxfill.server        # http://localhost:8000

Reads verdicts from data/verdicts/ (it never runs models or the verifier). Resolutions are appended to
data/corrections.jsonl; submissions to data/submissions.jsonl. The server, not just the page, refuses to
submit a blocked order or one with unresolved flags.
"""
import datetime as dt
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image

from faxfill import ocr

ROOT = Path(__file__).parent
DATA = ROOT / "data"
VERDICTS = DATA / "verdicts"
CORRECTIONS = DATA / "corrections.jsonl"
SUBMISSIONS = DATA / "submissions.jsonl"
WEB = ROOT / "web"
UI_CONFIG = json.load(open(ROOT / "ui_config.json"))

app = FastAPI(title="SigCheck review")
app.mount("/static", StaticFiles(directory=WEB), name="static")


def load_verdict(fax_id):
    p = VERDICTS / f"{fax_id}.json"
    if not p.exists() or "/" in fax_id:
        raise HTTPException(404, f"no verdict for {fax_id}")
    return json.load(open(p))


def flag_items(v):
    """Everything the pharmacist must resolve: flagged fields on every page + non-blocking order flags."""
    items = []
    for rx in v["prescriptions"]:
        for name, f in rx["fields"].items():
            if f["codes"]:
                items.append({"key": f"p{rx['page']}:{name}", "page": rx["page"], "field": name,
                              "system_value": f["value"]})
    for i, fl in enumerate(v["order_flags"]):
        if fl["status"] != "blocked":
            items.append({"key": f"order:{fl['code']}", "page": None, "field": "_order", "system_value": None})
    return items


def resolutions(fax_id):
    out = {}
    if CORRECTIONS.exists():
        for line in CORRECTIONS.read_text().splitlines():
            r = json.loads(line)
            if r["fax"] == fax_id:
                out[r["key"]] = r          # latest resolution per item wins
    return out


SEVERITY = ["INJECTION", "PAGE_COUNT_MISMATCH", "ALLERGY_CONFLICT", "DEA_INVALID", "DEA_MISSING", "C2_REFILLS",
            "C3_5_REFILLS_OVER_LIMIT", "METHOTREXATE_DAILY", "AMBIGUOUS_DIGIT", "NOISE_NEAR_CRITICAL", "TRUNCATED_EDGE",
            "LOOKALIKE_RISK", "DOSE_ABOVE_MAX", "UNIT_MISMATCH", "TRAILING_ZERO", "MISSING_PATIENT_ID"]


def summary(v):
    rx = v["prescriptions"][0]
    f = rx["fields"]
    # unresolved values are never shown as if they were true: no falling back to an alternative
    shown = lambda k, unclear: f[k]["value"] if f[k]["value"] else (unclear if f[k]["codes"] else None)
    reasons = [(fl["code"], fl["reason"]) for fl in v["order_flags"]]
    reasons += [(c, r) for x in v["prescriptions"] for fl in x["fields"].values() for c, r in zip(fl["codes"], fl["reasons"])]
    rank = lambda cr: SEVERITY.index(cr[0]) if cr[0] in SEVERITY else len(SEVERITY)
    top = min(reasons, key=rank)[1] if reasons else None
    n_flags = sum(1 for r in v["prescriptions"] for fl in r["fields"].values() if fl["codes"])
    drug = " ".join(x for x in (shown("drug_name", "drug unclear"), shown("strength", "strength unclear")) if x)
    return {"fax_id": v["fax_id"], "status": v["order_status"], "danger": v["danger_score"],
            "patient": shown("patient_name", "patient unclear"), "drug": drug,
            "top_reason": top, "flags": n_flags + len(v["order_flags"]), "pages": v["pages_received"],
            "resolved": len(resolutions(v["fax_id"]))}


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


@app.get("/api/config")
def config():
    return UI_CONFIG


@app.get("/api/queue")
def queue():
    rows = [summary(json.load(open(p))) for p in VERDICTS.glob("rx_*.json")]
    rows.sort(key=lambda r: (-r["danger"], r["fax_id"]))      # by danger, never by arrival
    return rows


@app.get("/api/fax/{fax_id}")
def fax(fax_id: str):
    v = load_verdict(fax_id)
    pages = []
    for n, p in enumerate(ocr.pages(fax_id), start=1):
        with Image.open(p) as im:
            pages.append({"page": n, "url": f"/api/page/{fax_id}/{n}", "width": im.width, "height": im.height})
    return {"verdict": v, "pages": pages, "items": flag_items(v), "resolutions": resolutions(fax_id)}


@app.get("/api/page/{fax_id}/{n}")
def page(fax_id: str, n: int):
    load_verdict(fax_id)
    paths = ocr.pages(fax_id)
    if not 1 <= n <= len(paths):
        raise HTTPException(404)
    return FileResponse(paths[n - 1], media_type="image/png")


@app.post("/api/resolve/{fax_id}")
def resolve(fax_id: str, body: dict):
    v = load_verdict(fax_id)
    if v["order_status"] == "BLOCKED":
        raise HTTPException(409, "order is blocked; flags cannot be resolved here")
    items = {i["key"]: i for i in flag_items(v)}
    key = body.get("key")
    if key not in items:
        raise HTTPException(400, "not a flagged item")
    action = body.get("action")
    chosen = (body.get("chosen_value") or "").strip()
    reason = (body.get("reason") or "").strip()
    if action not in ("alternative", "typed", "call_prescriber", "confirm_order_flag"):
        raise HTTPException(400, "unknown action")
    if action in ("alternative", "typed") and not chosen:
        raise HTTPException(400, "a value is required")
    if len(reason) < 3:
        raise HTTPException(400, "a short reason is required")
    rec = {"fax": fax_id, "key": key, "page": items[key]["page"], "field": items[key]["field"],
           "system_value": items[key]["system_value"], "chosen_value": chosen or None, "action": action,
           "reason": reason, "time": dt.datetime.now().astimezone().isoformat(timespec="seconds")}
    with open(CORRECTIONS, "a") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


@app.post("/api/submit/{fax_id}")
def submit(fax_id: str):
    v = load_verdict(fax_id)
    if v["order_status"] == "BLOCKED":
        raise HTTPException(409, "blocked orders cannot be submitted")
    res = resolutions(fax_id)
    open_items = [i["key"] for i in flag_items(v) if i["key"] not in res]
    if open_items:
        raise HTTPException(409, f"unresolved: {open_items}")
    calls = [r for r in res.values() if r["action"] == "call_prescriber"]
    rec = {"fax": fax_id, "time": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
           "outcome": "held_for_prescriber_call" if calls else "approved_draft",
           "resolutions": list(res.values())}
    with open(SUBMISSIONS, "a") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


@app.get("/api/scorecard")
def scorecard():
    ev = DATA / "eval"
    if not (ev / "dev_report.txt").exists():
        return JSONResponse({"dev": None})
    return {"dev": {"text": (ev / "dev_report.txt").read_text(), "data": json.load(open(ev / "dev_report.json"))},
            "heldout": None}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("faxfill.server:app", host="127.0.0.1", port=8000)
