"""Compare verdicts with answer keys. The ONLY module that reads answer keys.

    python -m faxfill.eval            # dev set (data/faxes); writes data/eval/dev_report.txt + .json
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from faxfill.verify import VERDICTS, norm

ROOT = Path(__file__).parent
FAXES = ROOT / "data" / "faxes"
OUT = ROOT / "data" / "eval"

# answer-key reason code -> verifier codes that catch it "for the right reason"
RIGHT_REASON = {
    "trailing_zero": {"TRAILING_ZERO"},
    "lasa_confirm": {"LOOKALIKE_RISK"},
    "npi_checksum": {"NPI_INVALID"},
    "prescriber_not_in_directory": {"PRESCRIBER_UNKNOWN"},
    "allergy_conflict": {"ALLERGY_CONFLICT"},
    "quantity_vs_duration": {"QTY_DURATION_MISMATCH"},
    "unreadable": {"READER_ABSTAINED", "READER_DISAGREEMENT", "UNREADABLE"},
    "prompt_injection": {"INJECTION"},
    "weekly_drug_daily": {"METHOTREXATE_DAILY"},
    "unit_mismatch": {"UNIT_MISMATCH"},
    "dea_checksum": {"DEA_INVALID"},
    "exceeds_max_daily": {"DOSE_ABOVE_MAX"},
    "noise_near_critical": {"NOISE_NEAR_CRITICAL"},
    "ambiguous_digit": {"AMBIGUOUS_DIGIT"},
    "truncated_edge": {"TRUNCATED_EDGE"},
    "page_count_mismatch": {"PAGE_COUNT_MISMATCH"},
    "missing_patient_id": {"MISSING_PATIENT_ID"},
    "conflicting_directions": {"CONFLICTING_DIRECTIONS"},
}
STATUS = {"blocked": "BLOCKED", "needs_review": "NEEDS_PHARMACIST", "ready_for_review": "READY"}
SCORED = ["patient_name", "patient_dob", "drug_name", "strength", "dosage_form", "directions", "quantity", "refills",
          "prescriber_name", "prescriber_npi", "prescriber_dea", "date_written", "substitution"]


def truth_value(fields, name):
    if name == "quantity":
        return fields["quantity_text"]
    if name == "refills":
        return str(fields["refills"])
    return fields.get(name)


def prescriptions(gt):
    if gt.get("schema") == 2:
        return [(rx["page"], rx["fields"]) for rx in gt["prescriptions"]]
    return [(1, gt["fields"])]


def main():
    keys = {json.load(open(p))["id"]: json.load(open(p)) for p in sorted(FAXES.glob("rx_*.json"))}
    verdicts = {fid: json.load(open(VERDICTS / f"{fid}.json")) for fid in keys if (VERDICTS / f"{fid}.json").exists()}
    missing = sorted(set(keys) - set(verdicts))
    acc = Counter()
    per_field = defaultdict(Counter)
    verified_wrong, should_abstain = [], []
    traps = []
    clean_flags, unexplained = [], []
    c_only = {"clean": [], "trap": []}
    status_pairs = []

    for fid, gt in keys.items():
        v = verdicts.get(fid)
        if not v:
            continue
        status_pairs.append((fid, STATUS[gt["expected_order_status"]], v["order_status"]))
        vrx = {rx["page"]: rx for rx in v["prescriptions"]}
        expected = {(e.get("page", 1), e["field"]) for e in gt["expected_flags"]}
        for page, tfields in prescriptions(gt):
            rx = vrx.get(page)
            if rx is None:
                continue
            for name in SCORED:
                f = rx["fields"][name]
                t = truth_value(tfields, name)
                nt, nv = norm(name, t), norm(name, f["value"])
                if nt is None and nv is None:
                    kind = "both_empty"
                elif nv is None:
                    kind = "abstained"
                elif nv == nt:
                    kind = "correct"
                else:
                    kind = "wrong"
                acc[kind] += 1
                per_field[name][kind] += 1
                if f["status"] == "verified":
                    acc["verified"] += 1
                    if kind == "correct" or kind == "both_empty":
                        acc["verified_right"] += 1
                    else:
                        verified_wrong.append(f"{fid} p{page} {name}: verified '{f['value']}' but truth is '{t}'")
                if name in gt.get("expect_abstain", []) and nv is not None:
                    should_abstain.append(f"{fid} {name}: should have been left empty, verifier value '{f['value']}' "
                                          f"(status {f['status']})")
                if f["codes"]:
                    if f["codes"] == ["OCR_DISAGREEMENT"]:   # (no longer produced since the Reader C decision)
                        c_right = norm(name, f["reads"].get("C")) == nt
                        c_only["clean" if gt["is_clean"] else "trap"].append(
                            f"{fid} p{page} {name}: models '{f['value']}', regex '{f['reads'].get('C')}', truth '{t}' "
                            f"-> {'regex was RIGHT (models wrong)' if c_right else 'regex was wrong'}")
                    if gt["is_clean"]:
                        clean_flags.append(f"{fid} p{page} {name}: {f['codes']} (value '{f['value']}', truth '{t}', "
                                           f"{'value wrong' if kind == 'wrong' else 'value right' if kind == 'correct' else kind})")
                    elif (page, name) not in expected:
                        unexplained.append(f"{fid} p{page} {name}: {f['codes']} ({'value wrong -> justified' if kind in ('wrong', 'abstained') else 'value right'})")
        if gt["is_clean"]:
            for fl in v["order_flags"]:
                clean_flags.append(f"{fid} order: {fl['code']}")

        for e in gt["expected_flags"]:
            page = e.get("page", 1)
            want = RIGHT_REASON.get(e["code"], set())
            if e["field"] == "_order":
                got = {fl["code"] for fl in v["order_flags"]}
            else:
                rx = vrx.get(page)
                got = set(rx["fields"][e["field"]]["codes"]) if rx else set()
            right = sorted(got & want)
            outcome = "caught (right reason)" if right else ("caught (other reason)" if got else "MISSED")
            traps.append({"fax": fid, "page": page, "field": e["field"], "code": e["code"], "outcome": outcome,
                          "caught_by": sorted(got) if got else [], "expected_check": sorted(want)})

    n_val = acc["correct"] + acc["wrong"]
    lines = []
    P = lines.append
    P(f"DEV-SET EVALUATION  ({len(verdicts)} faxes, {sum(len(prescriptions(g)) for g in keys.values())} prescriptions; "
      f"synthetic; thresholds tuned on this set, so these are NOT the final scorecard numbers)")
    if missing:
        P(f"!! no verdict for: {missing}")
    P("")
    P("1. FIELD ACCURACY (value the verifier produced vs answer key, over 13 scored fields)")
    P(f"   values given: {n_val}  correct: {acc['correct']} ({acc['correct'] / max(1, n_val):.1%})  wrong: {acc['wrong']}  "
      f"left empty (abstained): {acc['abstained']}  both empty: {acc['both_empty']}")
    for name in SCORED:
        c = per_field[name]
        P(f"     {name:<16} correct {c['correct']:>2}  wrong {c['wrong']:>2}  abstained {c['abstained']:>2}")
    P("")
    P("2. VERIFIED PRECISION (when we said Verified, how often the value was right)")
    P(f"   {acc['verified_right']}/{acc['verified']} = {acc['verified_right'] / max(1, acc['verified']):.1%}")
    P("   Verified but wrong: " + ("none" if not verified_wrong else ""))
    for x in verified_wrong:
        P(f"     - {x}")
    P("   Should have abstained (value under a smudge): " + ("none" if not should_abstain else ""))
    for x in should_abstain:
        P(f"     - {x}")
    P("")
    caught_right = sum(t["outcome"] == "caught (right reason)" for t in traps)
    caught_other = sum(t["outcome"] == "caught (other reason)" for t in traps)
    P(f"3. TRAP CATCH RATE  {caught_right + caught_other}/{len(traps)} expected flags caught "
      f"({caught_right} for the right reason, {caught_other} by a different check, "
      f"{len(traps) - caught_right - caught_other} missed)")
    for t in traps:
        P(f"   {t['outcome']:<22} {t['fax']} p{t['page']} {t['field']:<16} {t['code']:<28} caught by {t['caught_by'] or '-'}"
          + ("" if t["outcome"].startswith("caught (right") else f"   (expected {t['expected_check']})"))
    P("")
    P("4. ORDER STATUS (expected -> verifier)")
    right_status = sum(e == g for _, e, g in status_pairs)
    P(f"   {right_status}/{len(status_pairs)} match")
    for fid, e, g in status_pairs:
        if e != g:
            P(f"     {fid}: expected {e}, got {g}")
    P("")
    P(f"5. FALSE ALARMS ON CLEAN FAXES ({sum(1 for g in keys.values() if g['is_clean'])} clean faxes): "
      f"{len(clean_flags)} flag(s)")
    for x in clean_flags:
        P(f"     - {x}")
    P(f"   Flags on trap faxes outside the planted trap: {len(unexplained)}")
    for x in unexplained:
        P(f"     - {x}")
    P("")
    n_c = len(c_only["clean"]) + len(c_only["trap"])
    c_right = sum("RIGHT" in x for x in c_only["clean"] + c_only["trap"])
    P(f"6. NEEDS-PHARMACIST FLAGS FROM READER C (REGEX) ALONE: {n_c} "
      f"({len(c_only['clean'])} on clean faxes, {len(c_only['trap'])} on trap faxes; regex was right in {c_right}, wrong in {n_c - c_right})")
    for x in c_only["clean"] + c_only["trap"]:
        P(f"     - {x}")
    P("")
    queue = sorted(verdicts.values(), key=lambda v: -v["danger_score"])
    P("7. QUEUE ORDER (danger score, highest first)")
    P("   " + ", ".join(f"{v['fax_id']}({v['danger_score']})" for v in queue[:12]) + ", ...")

    report = "\n".join(lines)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "dev_report.txt").write_text(report + "\n")
    json.dump({"accuracy": dict(acc), "per_field": {k: dict(v) for k, v in per_field.items()},
               "verified_wrong": verified_wrong, "traps": traps, "clean_flags": clean_flags,
               "unexplained_flags": unexplained, "c_only_flags": c_only, "status": status_pairs},
              open(OUT / "dev_report.json", "w"), indent=1)
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
