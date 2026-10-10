"""Cross-check the generated answer keys: every fax must carry exactly the traps it declares.

    python -m faxfill.check_answers

Independent of gen.py: it re-derives traps from the printed field values and the reference data.
"""
import csv
import glob
import json
import re
import sys
from pathlib import Path

from faxfill.ids import dea_valid, npi_valid
from faxfill.ref import load_drugs

REF = Path(__file__).parent / "ref"
FAXES = Path(__file__).parent / "data" / "faxes"
FREQ = {"once daily": 1, "twice daily": 2, "three times daily": 3, "at bedtime": 1, "every morning": 1, "daily": 1,
        "BID": 2, "TID": 3, "QHS": 1, "QAM": 1, "once weekly": 1 / 7}
# visual / policy traps: not derivable from field values, checked structurally below
NOT_DERIVABLE = {"lasa_confirm", "unreadable", "prompt_injection", "noise_near_critical", "ambiguous_digit"}
FREQ_WORDS = r"(once daily|twice daily|three times daily|at bedtime|every morning|daily|BID|TID|QID|QHS|QAM|once weekly)"


def derive(f, drugs, patients, directory, boxes=None):
    """Traps implied by one prescription's printed values (+ its field boxes, for edge truncation)."""
    d = drugs[f["drug_name"]]
    found = set()
    if f["patient_name"] is None or f["patient_dob"] is None:
        found.add("missing_patient_id")
    for k, b in (boxes or {}).items():
        if k in f and b[0] <= 0:
            found.add("truncated_edge")
    if re.search(r"\d\.0 ", f["strength"] + " "):
        found.add("trailing_zero")
    if f["strength_unit"] != d["unit"]:
        found.add("unit_mismatch")
    elif f["strength_value"] not in d["strengths"]:
        found.add("strength_not_in_rxnorm")
    if not npi_valid(f["prescriber_npi"]):
        found.add("npi_checksum")
    elif f["prescriber_npi"] not in directory:
        found.add("prescriber_not_in_directory")
    if d["schedule"]:
        last = f["prescriber_name"].split(",")[0].split()[-1]
        if not f["prescriber_dea"]:
            found.add("missing_dea")
        elif not dea_valid(f["prescriber_dea"], last):
            found.add("dea_checksum")
    if f["patient_name"] and set(patients[f["patient_name"]]["allergies"].split(";")) & set(d["classes"]):
        found.add("allergy_conflict")
    if f.get("notes"):
        sig = re.search(FREQ_WORDS, f["directions"])
        note = re.search(rf"\b\d+ (?:tab|cap|tablet|capsule)s?\b.*?{FREQ_WORDS}", f["notes"])
        if note and (not sig or note.group(1) != sig.group(1)):
            found.add("conflicting_directions")
    m = re.search(r"(\d+) (?:tab|cap|tablet|capsule)s? (?:by mouth |PO )(.+?)(?: for| x) (\d+) days", f["directions"])
    if m and int(m.group(1)) * FREQ[m.group(2)] * int(m.group(3)) != f["quantity"]:
        found.add("quantity_vs_duration")
    m2 = re.search(r"^(?:Take )?(\d+) \w+ (?:by mouth |PO )(.+?)(?: for| x|$)", f["directions"])
    if m2:
        freq = m2.group(2).strip()
        if d["frequency_rule"] == "weekly" and "weekly" not in freq:
            found.add("weekly_drug_daily")
        elif d["frequency_rule"] != "weekly" and int(m2.group(1)) * FREQ.get(freq, 1) * f["strength_value"] > d["max_daily"]:
            found.add("exceeds_max_daily")
    return found


def prescriptions(gt):
    """Schema 1 = one prescription at top level; schema 2 = prescriptions[] (one per page)."""
    if gt.get("schema") == 2:
        return [(rx["page"], rx["fields"], rx["field_boxes"], rx) for rx in gt["prescriptions"]]
    return [(1, gt["fields"], None, gt)]


def main():
    drugs = load_drugs()
    patients = {f"{p['first_name']} {p['last_name']}": p for p in csv.DictReader(open(REF / "patients.csv"))}
    directory = {r["npi"] for r in csv.DictReader(open(REF / "prescribers.csv"))}
    problems, n, n_pages = [], 0, 0
    keyed_pages = set()
    for fn in sorted(glob.glob(str(FAXES / "rx_*.json"))):
        gt = json.load(open(fn))
        n += 1
        fid = gt["id"]
        # --- pages listed in the key must exist; page files on disk must be listed ---
        pages = gt.get("pages") or [gt["image"]]
        for pg in pages:
            keyed_pages.add(pg)
            if not (FAXES / pg).exists():
                problems.append(f"{fid}: page file {pg} listed in answer key but missing")
        n_pages += len(pages)
        on_disk = {p.name for p in FAXES.glob(f"{fid}.png")} | {p.name for p in FAXES.glob(f"{fid}_p*.png")}
        if on_disk != set(pages):
            problems.append(f"{fid}: pages on disk {sorted(on_disk)} != answer key {pages}")
        if gt.get("schema") == 2 and len(gt["prescriptions"]) != len(pages):
            problems.append(f"{fid}: {len(pages)} pages but {len(gt['prescriptions'])} prescriptions")

        # --- traps derived from values, per page, must equal the declared flags for that page ---
        declared_by_page = {}
        for e in gt["expected_flags"]:
            if e["code"] not in NOT_DERIVABLE and e["field"] != "_order":
                declared_by_page.setdefault(e.get("page", 1), set()).add(e["code"])
        for page, fields, boxes, rx in prescriptions(gt):
            found = derive(fields, drugs, patients, directory, boxes)
            declared = declared_by_page.get(page, set())
            if found != declared:
                problems.append(f"{fid} page {page}: declared {sorted(declared)} but data implies {sorted(found)}")
            d = drugs[fields["drug_name"]]
            if gt["is_clean"] and d["lasa"] and gt["style"] == "handwritten":
                problems.append(f"{fid}: clean fax uses a handwritten look-alike drug")

        # --- order-level and visual traps: check the structure that makes them real ---
        codes = {e["code"] for e in gt["expected_flags"]}
        declared_n = gt.get("pages_declared", len(pages))
        if (declared_n > len(pages)) != ("page_count_mismatch" in codes):
            problems.append(f"{fid}: pages_declared={declared_n}, received {len(pages)}, flags {sorted(codes)}")
        if "page_count_mismatch" in codes and gt["expected_order_status"] != "blocked":
            problems.append(f"{fid}: missing page must block the order")
        if gt["trap"] == "injection" and gt["expected_order_status"] != "blocked":
            problems.append(f"{fid}: injection fax not expected to be blocked")
        if "noise_near_critical" in codes and not all("noise_region" in rx for *_, rx in prescriptions(gt)):
            problems.append(f"{fid}: noise flag without a recorded noise_region")
        if "ambiguous_digit" in codes:
            rx = gt["prescriptions"][0]
            alt = rx.get("plausible_misreads", {}).get("strength", [])
            for a in alt:  # the misread must also be a real strength, or reference data would settle it
                v = float(a.split()[0])
                if v not in drugs[rx["fields"]["drug_name"]]["strengths"]:
                    problems.append(f"{fid}: plausible misread {a} is not a real strength")
            if not alt:
                problems.append(f"{fid}: ambiguous_digit without plausible_misreads")

    stray = {p.name for p in FAXES.glob("rx_*.png")} - keyed_pages
    if stray:
        problems.append(f"page files without an answer key: {sorted(stray)}")
    manifest = json.load(open(FAXES / "manifest.json"))
    traps = {}
    for m in manifest:
        traps[m["trap"] or "clean"] = traps.get(m["trap"] or "clean", 0) + 1
    print(f"answer keys checked: {n} ({n_pages} page images)")
    print("traps:", traps)
    print("mismatches:", "none" if not problems else "")
    for p in problems:
        print("  ", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
