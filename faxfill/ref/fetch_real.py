"""Build drugs.json from real US drug data plus our hand-entered fields.

    python -m faxfill.ref.fetch_real            # download into raw/, then build drugs.json
    python -m faxfill.ref.fetch_real --offline  # rebuild drugs.json from raw/ only

Sources, recorded on every value:
  rxnorm             RxNav /REST/drugs.json, SCD (semantic clinical drug) concepts: RxCUI, strength, dose form
  openfda            openFDA /drug/ndc.json, field dea_schedule (first result for generic_name)
  manual, unverified manual.json (look-alike partners, max daily dose, allergy classes, frequency rules)
"""
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
RAW = HERE / "raw"
MANUAL = "manual, unverified"

FORM_KEYS = [  # (RxNorm dose form, our key) — oral tablets / capsules / solutions only
    ("Extended Release Oral Tablet", "tablet_er"),
    ("Delayed Release Oral Tablet", "tablet_dr"),
    ("Disintegrating Oral Tablet", "tablet_odt"),
    ("Chewable Tablet", "tablet_chewable"),
    ("Oral Tablet", "tablet"),
    ("Extended Release Oral Capsule", "capsule_er"),
    ("Delayed Release Oral Capsule", "capsule_dr"),
    ("Oral Capsule", "capsule"),
    ("Oral Solution", "solution"),
]
SCD_RE = re.compile(r"^(?P<ing>[a-z][a-z ,\-]*?) (?P<val>\d+(?:\.\d+)?) (?P<unit>MG|MCG|MG/ML|MCG/ML) (?P<form>.+)$")
SCHEDULE = {"CI": "C-I", "CII": "C-II", "CIII": "C-III", "CIV": "C-IV", "CV": "C-V"}


def slug(name):
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def fetch(url, path):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "sigcheck-hackathon"}),
                                    timeout=20) as r:
            body, status = r.read().decode(), r.status
    except urllib.error.HTTPError as e:   # openFDA returns 404 + JSON body for "no matches"
        body, status = e.read().decode(), e.code
    path.write_text(json.dumps({"url": url, "status": status, "fetched": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                "body": json.loads(body) if body.strip().startswith("{") else body}, indent=1))


def download(names):
    RAW.mkdir(exist_ok=True)
    for name in names:
        q = urllib.parse.quote(name)
        fetch(f"https://rxnav.nlm.nih.gov/REST/drugs.json?name={q}", RAW / f"rxnav_{slug(name)}.json")
        fetch(f"https://api.fda.gov/drug/ndc.json?search=generic_name:%22{q}%22&limit=1",
              RAW / f"openfda_{slug(name)}.json")
        time.sleep(0.3)


def parse_rxnorm(name, raw, display_unit):
    products, skipped = [], []
    groups = (raw["body"] or {}).get("drugGroup", {}).get("conceptGroup", []) if isinstance(raw["body"], dict) else []
    for g in groups:
        if g.get("tty") != "SCD":
            continue
        for c in g.get("conceptProperties", []):
            n = c["name"]
            m = SCD_RE.match(n)
            if " / " in n or not m or not m.group("ing").startswith(name):
                skipped.append(n)
                continue
            key = next((k for f, k in FORM_KEYS if m.group("form") == f), None)
            if key is None:
                skipped.append(n)
                continue
            val, unit = float(m.group("val")), m.group("unit").lower().replace("/ml", "/mL")
            if display_unit == "mcg" and unit == "mg":       # RxNorm normalises e.g. levothyroxine to MG
                val, unit = round(val * 1000, 3), "mcg"
            products.append({"rxcui": c["rxcui"], "name": n, "ingredient": m.group("ing"), "strength": val,
                             "unit": unit, "dose_form": m.group("form"), "form_key": key})
    products.sort(key=lambda p: (p["form_key"], p["strength"]))
    return products, skipped


def parse_openfda(raw):
    body = raw["body"] if isinstance(raw["body"], dict) else {}
    res = body.get("results") or []
    if not res:
        return {"value": None, "source": "openfda", "note": "no openFDA NDC result"}
    r = res[0]
    sched = r.get("dea_schedule")
    out = {"value": SCHEDULE.get(sched, sched) if sched else None, "source": "openfda",
           "openfda_generic_name": r.get("generic_name"), "openfda_product_ndc": r.get("product_ndc")}
    if not sched:
        out["note"] = "first openFDA result has no dea_schedule"
    return out


def num(v):
    return int(v) if float(v).is_integer() else v


def build():
    manual = json.load(open(HERE / "manual.json"))["drugs"]
    drugs, report = [], []
    for m in manual:
        name = m["name"]
        rx = json.load(open(RAW / f"rxnav_{slug(name)}.json"))
        fda = json.load(open(RAW / f"openfda_{slug(name)}.json"))
        products, skipped = parse_rxnorm(name, rx, m["unit"])
        by_form = {}
        for p in products:
            v = num(p["strength"]) if p["form_key"] != "solution" else f"{num(p['strength'])} {p['unit']}"
            by_form.setdefault(p["form_key"], [])
            if v not in by_form[p["form_key"]]:
                by_form[p["form_key"]].append(v)
        # primary form = our printed form (tablet/capsule), or its delayed/extended-release variant if that's all there is
        primary = next((k for k in (m["form"], m["form"] + "_dr", m["form"] + "_er") if k in by_form), None)
        d = {"name": name}
        if primary:
            dose_form = next(p["dose_form"] for p in products if p["form_key"] == primary)
            d["form"] = {"value": m["form"], "rxnorm_dose_form": dose_form, "source": "rxnorm"}
            d["unit"] = {"value": m["unit"], "source": "rxnorm" + (" (converted from MG)" if m["unit"] == "mcg" else "")}
            d["strengths"] = {"value": by_form[primary], "dose_form": dose_form, "source": "rxnorm"}
            d["rxcui"] = {"value": {str(num(p["strength"])): p["rxcui"] for p in products if p["form_key"] == primary},
                          "source": "rxnorm"}
        else:
            d["form"] = {"value": m["form"], "source": MANUAL}
            d["unit"] = {"value": m["unit"], "source": MANUAL}
            d["strengths"] = {"value": m["strengths"], "source": MANUAL, "note": "RxNorm returned no usable SCD"}
            d["rxcui"] = {"value": {}, "source": "rxnorm", "note": "none found"}
        d["strengths_by_form"] = {"value": by_form, "source": "rxnorm"}
        d["schedule"] = parse_openfda(fda)
        d["max_daily"] = {"value": m["max_daily"], "unit": m["unit"], "source": MANUAL}
        d["lasa"] = {"value": m["lasa"], "source": MANUAL}
        if m.get("lasa_note"):
            d["lasa"]["note"] = m["lasa_note"]
        d["classes"] = {"value": m["classes"], "source": MANUAL}
        if m.get("frequency_rule"):
            d["frequency_rule"] = {"value": m["frequency_rule"], "note": m.get("frequency_note"), "source": MANUAL}
        drugs.append(d)
        report.append({"name": name, "old": m["strengths"], "new": d["strengths"]["value"],
                       "source": d["strengths"]["source"], "primary": primary, "forms": sorted(by_form),
                       "n_products": len(products), "n_skipped": len(skipped), "schedule": d["schedule"]})
    out = {"_meta": {
        "version": "2026-10-10-rxnorm",
        "warning": "Hackathon demo reference data for testing a review workflow; NOT for clinical use.",
        "sources": {
            "rxnorm": "NLM RxNav https://rxnav.nlm.nih.gov/REST/drugs.json?name=<drug> (SCD concepts, oral tablet/capsule/solution only)",
            "openfda": "https://api.fda.gov/drug/ndc.json?search=generic_name:\"<drug>\"&limit=1 (dea_schedule)",
            "manual, unverified": "hand-entered (faxfill/ref/manual.json)",
        },
        "raw_responses": "faxfill/ref/raw/",
        "built_with": "python -m faxfill.ref.fetch_real",
    }, "drugs": drugs}
    json.dump(out, open(HERE / "drugs.json", "w"), indent=1)
    return report


if __name__ == "__main__":
    names = [m["name"] for m in json.load(open(HERE / "manual.json"))["drugs"]]
    if "--offline" not in sys.argv:
        download(names)
    for r in build():
        changed = sorted(map(float, r["old"])) != sorted(map(float, r["new"])) if r["source"] == "rxnorm" else True
        flag = "SAME " if not changed else ("NONE " if r["source"] != "rxnorm" else "DIFF ")
        print(f"{flag}{r['name']:<20} old={r['old']} rxnorm={r['new']} [{r['primary']}] "
              f"sched={r['schedule']['value']} ({r['schedule'].get('openfda_generic_name') or r['schedule'].get('note')})")
