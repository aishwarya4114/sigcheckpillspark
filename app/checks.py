"""Deterministic checks the model cannot influence. Each returns (passed, reason-if-failed)."""
from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ["patient_name", "patient_dob", "drug_name", "strength", "directions", "quantity",
            "prescriber_name", "prescriber_npi", "date_written"]


def load_catalog() -> dict[str, dict]:
    """drug -> {strengths: {(strength, form)}, schedule, lookalikes}"""
    cat: dict[str, dict] = {}
    for line in (ROOT / "reference" / "catalog.md").read_text().splitlines():
        if not line.startswith("| "):
            continue
        cells = [c.strip().lower() for c in line.strip().strip("|").split("|")]
        if len(cells) < 5:
            continue
        drug, strength, form, sched, alikes = cells[:5]
        e = cat.setdefault(drug, {"rows": set(), "schedule": sched, "lookalikes": set()})
        e["rows"].add((re.sub(r"\s+", " ", strength), form))
        e["lookalikes"].update(a.strip() for a in alikes.split(",") if a.strip())
    return cat


CATALOG = load_catalog()


def npi_valid(npi: str) -> bool:
    d = [int(c) for c in npi if c.isdigit()]
    if len(d) != 10 or len(npi.strip()) != 10:
        return False
    payload = [8, 0, 8, 4, 0] + d[:9]
    total = 0
    for i, x in enumerate(reversed(payload)):
        if i % 2 == 0:
            x *= 2
            x = x - 9 if x > 9 else x
        total += x
    return (10 - total % 10) % 10 == d[9]


def dea_check(dea: str, last_name: str | None) -> list[str]:
    s = re.sub(r"\s", "", dea or "").upper()
    if not re.fullmatch(r"[A-Z]{2}\d{7}", s):
        return ["DEA number isn't 2 letters + 7 digits."]
    n = [int(c) for c in s[2:]]
    problems = []
    if ((n[0] + n[2] + n[4]) + 2 * (n[1] + n[3] + n[5])) % 10 != n[6]:
        problems.append("DEA number fails its check digit.")
    if last_name and s[1] != last_name[0].upper():
        problems.append("DEA number's second letter doesn't match the prescriber's last name.")
    return problems


def parse_date(s: str) -> date | None:
    for fmt in ("%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d", "%d-%m-%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except (ValueError, AttributeError):
            pass
    return None


def last_name(full: str | None) -> str | None:
    if not full:
        return None
    parts = [p for p in re.sub(r"Dr\.?|,", " ", full).split() if p.upper() not in {"MD", "DO", "NP", "PA"}]
    return parts[-1] if parts else None


def check_field(name: str, value: str | None, fields: dict, today: date | None = None) -> list[str]:
    """Return a list of plain-language problems (empty = checks passed)."""
    today = today or date.today()
    if value is None or not str(value).strip():
        if name == "refills":
            return ["Refills not stated: recorded as 'not stated', never as zero. Pharmacist decides."]
        if name in REQUIRED:
            return ["Missing or unreadable. Left empty, never guessed. Ask the prescriber."]
        return []
    v = str(value).strip()
    drug = (fields.get("drug_name") or {}).get("value")
    drug_l = (drug or "").strip().lower()
    entry = CATALOG.get(drug_l)
    if name == "drug_name":
        if not entry:
            return [f"\"{v}\" isn't in the reference catalogue. Coverage result, not a safety judgement: confirm with the prescriber."]
        if entry["lookalikes"]:
            return [f"{v} is often confused with {', '.join(sorted(entry['lookalikes']))}. Confirm against the diagnosis or with the prescriber."]
        return []
    if name == "strength":
        probs = []
        if re.search(r"\d\.0\b", v):
            probs.append(f"Trailing zero in \"{v}\": could be misread as {v.replace('.0', '0', 1)}. Always confirmed by a pharmacist.")
        if re.search(r"(^|\s)\.\d", v):
            probs.append(f"Missing leading zero in \"{v}\". Always confirmed by a pharmacist.")
        if re.search(r"\d\s*u\b", v, re.I):
            probs.append(f"\"U\" in \"{v}\" can be misread as 0. Always confirmed by a pharmacist.")
        if entry:
            form = ((fields.get("dosage_form") or {}).get("value") or "").strip().lower()
            norm = re.sub(r"\s+", " ", v.lower()).replace(".0 ", " ")
            if not any(s == norm and (not form or f == form) for s, f in entry["rows"]):
                avail = ", ".join(sorted({s for s, _ in entry["rows"]}, key=lambda x: float(re.findall(r'[\d.]+', x)[0])))
                probs.append(f"{v} isn't a catalogued strength of {drug} (catalogued: {avail}).")
        return probs
    if name == "prescriber_npi":
        return [] if npi_valid(v) else [f"NPI {v} fails its check digit: probably a misread or mistyped digit."]
    if name == "prescriber_dea":
        return dea_check(v, last_name((fields.get("prescriber_name") or {}).get("value")))
    if name in ("patient_dob", "date_written"):
        d = parse_date(v)
        if not d:
            return [f"\"{v}\" isn't a valid date."]
        if d > today:
            return ["Date is in the future."]
        if name == "patient_dob" and (today.year - d.year) > 120:
            return ["Implausible date of birth."]
        if name == "date_written" and (today - d).days > 365:
            return ["Written more than a year ago: may have expired."]
        return []
    if name in ("quantity", "refills"):
        if not re.fullmatch(r"\d{1,4}", v):
            return [f"\"{v}\" isn't a whole number."]
        if name == "refills" and int(v) > 11:
            return ["More than 11 refills."]
        return []
    if name == "directions":
        s = f" {v.lower()} "
        if "as directed" in s:
            return ["\"As directed\" has no dose or frequency. Ask the prescriber."]
        if re.search(r"(^|\s)i\s+tab", s):
            return ["Dose reads as \"I tab\": probably \"1 tab\", but a pharmacist confirms."]
        has_freq = re.search(r"\b(qd|daily|bid|tid|qid|qhs|q\d+h|prn|once|twice|every|at bedtime|times)\b", s)
        has_dose = re.search(r"\b(\d+(\.\d+)?|one|two|half)\b", s)
        if not (has_freq and has_dose):
            return ["No clear dose and frequency. Ask the prescriber."]
        return []
    if name in ("patient_name", "prescriber_name"):
        return [] if re.fullmatch(r"[A-Za-z][A-Za-z .,'\-]+", v) else ["Doesn't look like a name."]
    return []


def is_controlled(fields: dict) -> bool:
    e = CATALOG.get(((fields.get("drug_name") or {}).get("value") or "").strip().lower())
    return bool(e and e["schedule"] not in ("none", ""))
