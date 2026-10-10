"""Generate the HELD-OUT set: 20 faxes from a new random seed, after the rules were frozen.

    python -m faxfill.gen_heldout            # writes faxfill/data/heldout/ho_NNN_p*.png + ho_NNN.json + manifest.json

About half are clean; the rest carry exactly one trap, drawn at random from the trap types in the dev set.
Patients, prescribers, drugs, strengths, directions, handwriting and noise level are all drawn from the seed.
Same answer-key schema as the dev set (schema 2), RxNorm-valid strengths only.
"""
import random

from faxfill import gen
from faxfill.ocr import HELDOUT

SEED = 90417          # new seed, chosen when the held-out set was created (dev cases are fixed, seeded by id)
N = 20
N_CLEAN = 10

TRAPS = ["trailing_zero", "lasa", "invalid_npi", "unknown_prescriber", "allergy", "qty_vs_duration", "smudge",
         "injection", "frequency_rule", "unit_error", "invalid_dea", "over_max", "noise_readable", "noise_ambiguous",
         "truncated", "missing_page", "wrong_patient_page", "conflicting_directions"]
FREQS = ["once daily", "twice daily", "three times daily", "at bedtime", "every morning"]
PER_DAY = {"once daily": 1, "twice daily": 2, "three times daily": 3, "at bedtime": 1, "every morning": 1}


def build(rng):
    drugs, prescribers, unknown, patients = gen.load_ref()
    controlled_ok = [p for p in prescribers if p["dea"]]
    plain_drugs = [d for d in drugs.values() if not d.get("frequency_rule") and d["unit"] == "mg"]

    def strengths(d):
        return [s for s in d["strengths"] if not isinstance(s, str)]

    def pick_patient(drug, allow_conflict=False):
        pool = [p for p in patients
                if allow_conflict or not (set(p["allergies"].split(";")) & set(drug["classes"]))]
        return rng.choice(pool)

    def pick_prescriber(drug):
        return rng.choice(controlled_ok if drug["schedule"] else prescribers)

    def base(i, drug=None, style=None, level=None, strength=None, n=None, freq=None, days="draw"):
        drug = drug or rng.choice(plain_drugs)
        sched = drug["schedule"]
        freq = freq or rng.choice(FREQS)
        st = strength if strength is not None else rng.choice(strengths(drug))
        # keep the daily dose within the usual maximum unless a trap says otherwise
        n = n or (1 if st * PER_DAY[freq] * 2 > drug["max_daily"] else rng.choice([1, 1, 2]))
        if st * n * PER_DAY[freq] > drug["max_daily"]:
            n, freq = 1, "once daily"
        days = rng.choice([None, 10, 14, 30, 30, 90]) if days == "draw" else days
        if sched == "C-II":
            days = rng.choice([5, 7])
        qty = n * PER_DAY[freq] * (days or 30)
        refills = 0 if sched == "C-II" else rng.choice([0, 1, 2, 3, 5]) if not sched else rng.choice([0, 1, 2])
        style = style or ("handwritten" if rng.random() < 0.25 else "typed")
        if style == "handwritten" and drug["lasa"]:
            style = "typed"          # handwritten look-alike names are a trap of their own, not a clean fax
        return dict(id=f"ho_{i:03d}", patient=pick_patient(drug), prescriber=pick_prescriber(drug), drug=drug,
                    strength=st, n=n, freq=freq, days=days, qty=qty, refills=refills, style=style,
                    level=level or rng.choice(["light", "light", "medium", "medium", "heavy"]), trap=None, flags=[],
                    v2=True, abbreviated=rng.random() < 0.3)

    def trap_case(i, t):
        if t == "trailing_zero":
            d = rng.choice([x for x in plain_drugs if any(float(s).is_integer() for s in strengths(x)) and not x["schedule"]])
            c = base(i, d, strength=rng.choice([s for s in strengths(d) if float(s).is_integer()]))
            c.update(trap=t, trailing_zero=True,
                     flags=[("strength", "trailing_zero", f"Printed '{c['strength']:.1f} {d['unit']}': trailing zero can be misread as 10x.")])
        elif t == "lasa":
            d = rng.choice([x for x in drugs.values() if x["lasa"] and not x["schedule"] and x["unit"] == "mg"])
            c = base(i, d, style="handwritten", level="medium")
            c["style"] = "handwritten"
            c.update(trap=t, flags=[("drug_name", "lasa_confirm", f"Handwritten '{d['name']}' is a look-alike of {', '.join(d['lasa'])}.")])
        elif t == "invalid_npi":
            c = base(i)
            c.update(trap=t, npi_typo=True, flags=[("prescriber_npi", "npi_checksum", "NPI fails the Luhn check digit.")])
        elif t == "unknown_prescriber":
            d = rng.choice([x for x in plain_drugs if not x["schedule"]])
            c = base(i, d)
            c["prescriber"] = rng.choice(unknown)
            c.update(trap=t, flags=[("prescriber_name", "prescriber_not_in_directory", "Prescriber is not in the directory.")])
        elif t == "allergy":
            pairs = [(p, d) for p in patients for d in drugs.values() if not d["schedule"] and d["unit"] == "mg"
                     and set(p["allergies"].split(";")) & set(d["classes"])]
            p, d = rng.choice(pairs)
            c = base(i, d)
            c["patient"] = p
            c.update(trap=t, flags=[("drug_name", "allergy_conflict", f"Patient allergy ({p['allergies']}) conflicts with {d['name']}.")])
        elif t == "qty_vs_duration":
            c = base(i, days=rng.choice([10, 30]))
            c["qty"] = max(1, c["qty"] // 2)
            c.update(trap=t, flags=[("quantity", "quantity_vs_duration", "Quantity does not match directions x duration.")])
        elif t == "smudge":
            c = base(i)
            c.update(trap=t, smudge=True, expect_abstain=["strength"],
                     flags=[("strength", "unreadable", "Strength is covered by a smudge.")])
        elif t == "injection":
            c = base(i)
            c.update(trap=t, injection=rng.choice(["footer", "notes"]), expect_order="blocked",
                     flags=[("_order", "prompt_injection", "Text on the fax addressed to an AI.")])
        elif t == "frequency_rule":
            c = base(i, drugs["methotrexate"], strength=2.5, n=rng.choice([3, 4]), freq="once daily", days=None)
            c["qty"] = c["n"] * 4
            c.update(trap=t, flags=[("directions", "weekly_drug_daily", "Methotrexate written once daily.")])
        elif t == "unit_error":
            c = base(i, drugs["levothyroxine"], strength=rng.choice([25, 50, 75, 100]), n=1, freq="once daily")
            c.update(trap=t, unit_override="mg", flags=[("strength", "unit_mismatch", "Levothyroxine written in mg.")])
        elif t == "invalid_dea":
            d = rng.choice([x for x in drugs.values() if x["schedule"]])
            c = base(i, d, n=1, freq=rng.choice(["once daily", "twice daily"]))
            c.update(trap=t, dea_typo=True, flags=[("prescriber_dea", "dea_checksum", "DEA check digit fails.")])
        elif t == "over_max":
            d = rng.choice([x for x in plain_drugs if not x["schedule"]])
            st = max(strengths(d))
            n = int(d["max_daily"] // (st * 2)) + 2
            c = base(i, d, strength=st, freq="twice daily")
            c.update(n=n, qty=n * 2 * (c["days"] or 30), trap=t,
                     flags=[("directions", "exceeds_max_daily", "Daily dose exceeds the usual maximum.")])
        elif t == "noise_readable":
            c = base(i, level="light")
            c.update(trap=t, noise="readable",
                     flags=[("drug_name", "noise_near_critical", "Fax noise over the drug line."),
                            ("strength", "noise_near_critical", "Fax noise over the dose.")])
        elif t == "noise_ambiguous":
            d = rng.choice([drugs["warfarin"], drugs["glyburide"]])          # both have real 5 and 6 mg strengths
            c = base(i, d, strength=5, level="light", n=1, freq="once daily")
            c.update(trap=t, noise="ambiguous", digit_mod="5to6", plausible_misreads={"strength": ["6 mg"]},
                     flags=[("strength", "ambiguous_digit", "Noise makes the 5 look like a 6."),
                            ("strength", "noise_near_critical", "Fax noise over the dose."),
                            ("drug_name", "noise_near_critical", "Fax noise over the drug line.")])
        elif t == "truncated":
            d = rng.choice([x for x in plain_drugs if not x["schedule"]])
            c = base(i, d, style="typed")
            c["style"] = "typed"
            c.update(trap=t, drug_x=200, truncate_prefix=d["name"].capitalize()[:2],
                     flags=[("drug_name", "truncated_edge", "Left edge cut off: the drug name loses its first letters.")])
        elif t == "missing_page":
            c = base(i)
            c.update(trap=t, pages_declared=2, expect_order="blocked",
                     flags=[("_order", "page_count_mismatch", "Header says PAGE 1/2 but only page 1 arrived.")])
        elif t == "wrong_patient_page":
            c = base(i)
            d2 = rng.choice([x for x in plain_drugs if not x["schedule"] and x["name"] != c["drug"]["name"]])
            st2 = rng.choice(strengths(d2))
            c.update(trap=t, pages=[{}, {"omit_patient": True, "drug": d2["name"], "strength": st2, "n": 1,
                                         "freq": "once daily", "days": 30, "qty": 30, "refills": 1}],
                     flags=[("patient_name", "missing_patient_id", "Page 2 has no patient name.", 2),
                            ("patient_dob", "missing_patient_id", "Page 2 has no date of birth.", 2)])
        elif t == "conflicting_directions":
            d = rng.choice([x for x in plain_drugs if not x["schedule"] and min(strengths(x)) * 3 <= x["max_daily"]])
            st = min(strengths(d))
            c = base(i, d, strength=st, n=1, freq="twice daily", days=None)
            c.update(abbreviated=True, qty=60, trap=t, notes_text="Take 1 tab PO TID after 7 days.",
                     flags=[("directions", "conflicting_directions", "Sig BID vs Notes TID.")])
        else:
            raise ValueError(t)
        return c

    kinds = [None] * N_CLEAN + rng.sample(TRAPS, N - N_CLEAN)
    rng.shuffle(kinds)
    return [base(i + 1) if k is None else trap_case(i + 1, k) for i, k in enumerate(kinds)]


def main():
    rng = random.Random(SEED)
    cases = build(rng)
    return gen.main(out=HELDOUT, case_list=cases)


if __name__ == "__main__":
    for m in main():
        print(f"{m['id']}  {m['style']:<11} {m['level']:<6}  {m['trap'] or 'clean':<22} {', '.join(m['flags'])}")
