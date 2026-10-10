"""Print the three independent reads of a fax side by side.

    python -m faxfill.read rx_001 [rx_018 ...] [--refresh]

A / B = vision models via Nicrron (cached in data/reads/), C = regex over Tesseract text (no model).
If the fax has an answer key, it is shown in the last column for checking; readers never see it.
`*` marks fields where the readers do not all agree (after trimming case and spaces); `-` = null.
"""
import json
import re
import shutil
import sys

from faxfill.readers import FIELDS, MODELS, read_all
from faxfill.ocr import FAXES

KEY_MAP = {"patient_dob": None, "date_written": None, "quantity": "quantity_text"}


def norm(v):
    return re.sub(r"\s+", " ", str(v)).strip().lower() if v is not None else None


def cell(v, w):
    s = "-" if v is None else str(v).replace("\n", " ")
    return (s[: w - 1] + "…") if len(s) > w else s.ljust(w)


def answer(gt, f):
    if gt is None:
        return None
    t = gt["fields"]
    if f == "patient_dob":
        y, m, d = t["patient_dob"].split("-")
        return f"{m}/{d}/{y}"
    if f == "date_written":
        y, m, d = t["date_written"].split("-")
        return f"{m}/{d}/{y}"
    if f == "quantity":
        return t["quantity_text"]
    if f in ("refills",):
        return str(t[f])
    return t.get(f)


def show(fax_id, refresh=False):
    r = read_all(fax_id, refresh)
    key_path = FAXES / f"{fax_id}.json"
    gt = json.load(open(key_path)) if key_path.exists() else None
    width = shutil.get_terminal_size((170, 20)).columns
    w = max(22, min(34, (width - 20) // 4))
    hdr = ["A " + MODELS["A"], "B " + MODELS["B"], "C regex+tesseract", "answer key" if gt else ""]
    print(f"\n== {fax_id}" + (f"   (trap: {gt['trap'] or 'none'}; level {gt['degradation']['level']}, {gt['style']})" if gt else ""))
    for k in "AB":
        rec = r[k]
        note = f"{rec['status']}, {rec.get('latency_s')}s"
        if rec.get("schema_errors"):
            note += f", schema issues: {rec['schema_errors']}"
        print(f"   reader {k}: {note}")
    print(" " + "field".ljust(16) + "".join(cell(h, w) + " " for h in hdr))
    for f in FIELDS:
        vals = [(r[k]["fields"] or {}).get(f) if r[k].get("fields") else None for k in "ABC"]
        agree = len({norm(v) for v in vals}) == 1
        print(("*" if not agree else " ") + f.ljust(16) + "".join(cell(v, w) + " " for v in vals)
              + (cell(answer(gt, f), w) if gt else ""))


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    for fid in args:
        show(fid, refresh="--refresh" in sys.argv)
