"""Every reference lookup the verifier makes goes through one interface.

The verifier only calls methods on a `Lookup`. `LocalLookup` answers from faxfill/ref/ files; a PRISM
Knowledge Base implementation can replace it later without touching the verifier. Every answer
carries the source it came from, so the verdict can show where a check was grounded.
"""
import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz import fuzz, process

from faxfill.ref import load_drugs

REF = Path(__file__).parent / "ref"


@dataclass
class Answer:
    found: bool
    value: object = None
    source: str = ""
    detail: dict = field(default_factory=dict)


class Lookup:
    """Interface. Implementations: LocalLookup (files), later PrismKBLookup (PRISM Knowledge Base)."""

    name = "abstract"

    def drug(self, name) -> Answer: ...
    def closest_drugs(self, text, limit=3) -> list: ...
    def strengths(self, drug, form=None) -> Answer: ...
    def lasa_partners(self, drug) -> Answer: ...
    def high_alert(self, drug) -> Answer: ...
    def prescriber_by_npi(self, npi) -> Answer: ...
    def prescriber_by_name(self, name) -> Answer: ...
    def patient(self, name) -> Answer: ...


def _norm_name(s):
    return " ".join(str(s or "").replace(",", " ").replace(".", " ").lower().split())


class LocalLookup(Lookup):
    name = "local files (faxfill/ref)"

    def __init__(self):
        self.drugs = load_drugs()
        self.prescribers = list(csv.DictReader(open(REF / "prescribers.csv")))
        self.patients = list(csv.DictReader(open(REF / "patients.csv")))
        self.high_alert_list = json.load(open(REF / "high_alert.json"))

    def drug(self, name):
        d = self.drugs.get(_norm_name(name))
        if d is None:
            # salt forms ("metformin hydrochloride") resolve to the base entry
            base = next((k for k in self.drugs if _norm_name(name).startswith(k + " ")), None)
            d = self.drugs.get(base) if base else None
        return Answer(d is not None, d, "rxnorm (drugs.json)" if d else "drugs.json: not found")

    def closest_drugs(self, text, limit=3):
        hits = process.extract(_norm_name(text), list(self.drugs), scorer=fuzz.ratio, limit=limit)
        return [(name, round(score, 1)) for name, score, _ in hits]

    def strengths(self, drug, form=None):
        d = self.drugs.get(drug)
        if not d:
            return Answer(False, None, "drugs.json: drug not found")
        if form is None or form == d["form"]:
            return Answer(True, d["strengths"], d["sources"]["strengths"], {"unit": d["unit"], "form": d["form"]})
        by_form = d.get("strengths_by_form") or {}
        if form in by_form:
            return Answer(True, by_form[form], "rxnorm", {"unit": d["unit"], "form": form})
        return Answer(False, None, "rxnorm: form not available for this drug", {"forms": sorted(by_form)})

    def lasa_partners(self, drug):
        d = self.drugs.get(drug)
        return Answer(bool(d and d["lasa"]), (d or {}).get("lasa", []), (d or {}).get("sources", {}).get("lasa", ""))

    def high_alert(self, drug):
        hit = next((e for e in self.high_alert_list["drugs"] if e["name"] == drug), None)
        return Answer(hit is not None, hit, self.high_alert_list["_meta"]["source"])

    def prescriber_by_npi(self, npi):
        p = next((r for r in self.prescribers if r["npi"] == str(npi or "")), None)
        return Answer(p is not None, p, "prescriber directory (prescribers.csv)")

    def prescriber_by_name(self, name):
        n = _norm_name(name)
        for r in self.prescribers:
            full = _norm_name(f"{r['first_name']} {r['last_name']} {r['credential']}")
            if n == full or n == _norm_name(f"{r['first_name']} {r['last_name']}"):
                return Answer(True, r, "prescriber directory (prescribers.csv)")
        return Answer(False, None, "prescriber directory (prescribers.csv)")

    def patient(self, name):
        n = _norm_name(name)
        p = next((r for r in self.patients if _norm_name(f"{r['first_name']} {r['last_name']}") == n), None)
        return Answer(p is not None, p, "patient profiles (patients.csv)")
