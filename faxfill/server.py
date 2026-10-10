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
from faxfill.lookup import LocalLookup

ROOT = Path(__file__).parent
DATA = ROOT / "data"
VERDICTS = DATA / "verdicts"
CORRECTIONS = DATA / "corrections.jsonl"
SUBMISSIONS = DATA / "submissions.jsonl"
WEB = ROOT / "web"
UI_CONFIG = json.load(open(ROOT / "ui_config.json"))

app = FastAPI(title="SigCheck review")
LOOKUP = LocalLookup()

# Two kinds of flag, two kinds of decision.
READING = {"READER_ABSTAINED", "READER_DISAGREEMENT", "UNREADABLE", "OCR_DISAGREEMENT", "NOISE_NEAR_CRITICAL",
           "AMBIGUOUS_DIGIT", "TRUNCATED_EDGE", "TRAILING_ZERO", "LEADING_DECIMAL", "U_VS_0", "LOOKALIKE_RISK",
           "NO_INDEPENDENT_CHECK", "QTY_NUMERAL_WORDS_MISMATCH", "REFILLS_UNPARSEABLE", "SUBSTITUTION_UNCLEAR",
           "DATE_INVALID", "DRUG_UNKNOWN", "PAGE_COUNT_UNKNOWN"}
# everything else is SAFETY: the fax was read correctly, but what it says is a problem
READING_ACTIONS = {"alternative", "typed", "call_prescriber", "confirm_order_flag"}
SAFETY_ACTIONS = {"confirmed_proceed", "hold_call", "reject"}
FIELD_NAMES = {"patient_name": "Patient", "patient_dob": "DOB", "drug_name": "Drug", "strength": "Strength",
               "dosage_form": "Form", "directions": "Directions", "quantity": "Quantity", "refills": "Refills",
               "prescriber_name": "Prescriber", "prescriber_npi": "NPI", "prescriber_dea": "DEA",
               "date_written": "Date written", "substitution": "Substitution", "notes": "Notes"}


def short_reason(code, field=None, f=None, flag=None):
    """At most ~8 words, for the queue and section headers. Full reasons stay on the detail page."""
    fn = FIELD_NAMES.get(field, "Field")
    val = (f or {}).get("value")
    alts = (f or {}).get("alternatives") or []
    reasons = " ".join((f or {}).get("reasons") or [])
    import re as _re
    if code == "INJECTION":
        return "Hidden AI instruction on fax"
    if code == "PAGE_COUNT_MISMATCH":
        m = _re.search(r"missing page ([\d, ]+)", (flag or {}).get("reason", ""))
        d = (flag or {}).get("declared")
        return f"Page {m.group(1).strip()} of {d} not received" if m and d else "Pages missing"
    if code == "PAGE_COUNT_UNKNOWN":
        return "Page count could not be confirmed"
    if code == "ALLERGY_CONFLICT":
        m = _re.search(r"lists an? ([a-z, ]+?) allergy", reasons)
        nice = {"nsaid": "NSAID", "nsaid, sulfonamide": "NSAID, sulfa", "sulfonamide": "sulfa"}
        return f"Allergy: {nice.get(m.group(1), m.group(1))}" if m else "Allergy conflict"
    if code == "AMBIGUOUS_DIGIT":
        return f"Strength unclear: {' or '.join(alts[:2])}" if alts else "Strength unclear"
    table = {
        "READER_ABSTAINED": f"{fn} unreadable", "UNREADABLE": f"{fn} unreadable",
        "READER_DISAGREEMENT": f"{fn}: readers disagree", "OCR_DISAGREEMENT": f"{fn}: OCR text differs",
        "NOISE_NEAR_CRITICAL": f"Fax noise over {fn.lower()}", "TRUNCATED_EDGE": "Left edge of fax cut off",
        "TRAILING_ZERO": f"Trailing zero: {val}", "LEADING_DECIMAL": f"No leading zero: {val}",
        "U_VS_0": "'U' written for units", "LOOKALIKE_RISK": f"Look-alike drug: confirm {val or 'name'}",
        "DOSE_ABOVE_MAX": "Dose above usual maximum", "UNIT_MISMATCH": f"Wrong unit: {val}",
        "METHOTREXATE_DAILY": "Methotrexate written daily, not weekly", "DEA_INVALID": "DEA number invalid",
        "DEA_MISSING": "DEA number missing", "C2_REFILLS": "Refills not allowed (Schedule II)",
        "C3_5_REFILLS_OVER_LIMIT": "Too many refills (controlled)", "NPI_INVALID": "NPI number invalid",
        "PRESCRIBER_UNKNOWN": "Prescriber not in directory", "PATIENT_NOT_FOUND": "Patient not in profiles",
        "DOB_MISMATCH": "DOB doesn't match profile", "MISSING_PATIENT_ID": f"No patient {'name' if field == 'patient_name' else 'DOB'} on page",
        "QTY_DURATION_MISMATCH": "Quantity doesn't match duration", "CONFLICTING_DIRECTIONS": "Two sets of directions",
        "DRUG_UNKNOWN": "Unknown drug name", "STRENGTH_NOT_IN_RXNORM": "Strength not available for drug",
        "QTY_NUMERAL_WORDS_MISMATCH": "Quantity number and words differ", "DATE_IN_FUTURE": "Date written is in future",
        "DATE_TOO_OLD": "Prescription over a year old", "DATE_INVALID": "Date not valid",
        "NO_INDEPENDENT_CHECK": f"{fn} could not be checked", "REFILLS_UNPARSEABLE": "Refills not a number",
        "SUBSTITUTION_UNCLEAR": "Substitution box unclear",
    }
    return table.get(code, code.replace("_", " ").capitalize())


def flag_kind(codes):
    return "reading" if any(c in READING for c in codes) else "safety"
app.mount("/static", StaticFiles(directory=WEB), name="static")


def load_verdict(fax_id):
    p = VERDICTS / f"{fax_id}.json"
    if not p.exists() or "/" in fax_id:
        raise HTTPException(404, f"no verdict for {fax_id}")
    return json.load(open(p))


def flag_items(v):
    """Everything the pharmacist must decide: flagged fields on every page + non-blocking order flags."""
    items = []
    for rx in v["prescriptions"]:
        for name, f in rx["fields"].items():
            if f["codes"]:
                items.append({"key": f"p{rx['page']}:{name}", "page": rx["page"], "field": name,
                              "system_value": f["value"], "kind": flag_kind(f["codes"]),
                              "short": short_reason(f["codes"][0], name, f)})
    for fl in v["order_flags"]:
        if fl["status"] != "blocked":
            items.append({"key": f"order:{fl['code']}", "page": None, "field": "_order", "system_value": None,
                          "kind": "reading", "short": short_reason(fl["code"], flag=fl)})
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
            "LOOKALIKE_RISK", "DOSE_ABOVE_MAX", "UNIT_MISMATCH", "TRAILING_ZERO", "MISSING_PATIENT_ID",
            "CONFLICTING_DIRECTIONS", "QTY_DURATION_MISMATCH", "NPI_INVALID", "PRESCRIBER_UNKNOWN", "READER_ABSTAINED",
            "UNREADABLE", "READER_DISAGREEMENT"]


def clinic_of(v):
    """From the prescriber directory (by NPI, then name). A cut-off clinic line from the fax is not shown."""
    f = v["prescriptions"][0]["fields"]
    for ans in (LOOKUP.prescriber_by_npi(f["prescriber_npi"]["value"]), LOOKUP.prescriber_by_name(f["prescriber_name"]["value"])):
        if ans.found:
            return ans.value["clinic"]
    return "clinic not identified"


def received_at(fax_id):
    """When the fax file arrived in our inbox (file time), not a time read off the fax."""
    t = dt.datetime.fromtimestamp(ocr.pages(fax_id)[0].stat().st_mtime)
    return t.strftime("%b %-d, %-I:%M %p")


def priority(v):
    p = UI_CONFIG["priority"]
    if v["order_status"] == "BLOCKED" or v["danger_score"] >= p["urgent_min_danger"]:
        return "Urgent"
    return "High" if v["danger_score"] >= p["high_min_danger"] else "Normal"


def summary(v):
    rx = v["prescriptions"][0]
    f = rx["fields"]
    # unresolved values are never shown as if they were true: no falling back to an alternative
    shown = lambda k, unclear: f[k]["value"] if f[k]["value"] else (unclear if f[k]["codes"] else None)
    cands = [(fl["code"], short_reason(fl["code"], flag=fl)) for fl in v["order_flags"]]
    cands += [(c, short_reason(c, name, fl)) for x in v["prescriptions"] for name, fl in x["fields"].items() for c in fl["codes"]]
    rank = lambda cr: SEVERITY.index(cr[0]) if cr[0] in SEVERITY else len(SEVERITY)
    top = min(cands, key=rank)[1] if cands else None
    n_flags = sum(1 for r in v["prescriptions"] for fl in r["fields"].values() if fl["codes"])
    drug = " ".join(x for x in (shown("drug_name", "drug unclear"), shown("strength", "strength unclear")) if x)
    return {"fax_id": v["fax_id"], "status": v["order_status"], "danger": v["danger_score"], "priority": priority(v),
            "clinic": clinic_of(v), "received": received_at(v["fax_id"]),
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
    return {"verdict": v, "pages": pages, "items": flag_items(v), "resolutions": resolutions(fax_id),
            "summary": summary(v)}


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
    allowed = SAFETY_ACTIONS if items[key]["kind"] == "safety" else READING_ACTIONS
    if action not in allowed:
        raise HTTPException(400, f"action '{action}' is not valid for a {items[key]['kind']} flag")
    if action in ("alternative", "typed") and not chosen:
        raise HTTPException(400, "a value is required")
    if len(reason) < 3:
        raise HTTPException(400, "a short reason is required")
    rec = {"fax": fax_id, "key": key, "kind": items[key]["kind"], "page": items[key]["page"], "field": items[key]["field"],
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
    acts = {r["action"] for r in res.values()}
    outcome = ("rejected" if "reject" in acts else
               "held_for_prescriber_call" if acts & {"call_prescriber", "hold_call"} else
               "sent_to_pharmacist_verification")
    rec = {"fax": fax_id, "time": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
           "outcome": outcome, "note": UI_CONFIG["demo_label"], "resolutions": list(res.values())}
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
