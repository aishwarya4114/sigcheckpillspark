"""Reference data loaders. drugs.json stores every value with its source; load_drugs() flattens to plain values."""
import json
from pathlib import Path

HERE = Path(__file__).parent


def load_drugs(raw=False):
    """{name: {name, form, unit, strengths, max_daily, schedule, lasa, classes, frequency_rule, rxcui, sources}}"""
    data = json.load(open(HERE / "drugs.json"))
    if raw:
        return data
    out = {}
    for d in data["drugs"]:
        flat = {"name": d["name"], "sources": {}}
        for k, v in d.items():
            if isinstance(v, dict) and "value" in v:
                flat[k] = v["value"]
                flat["sources"][k] = v["source"]
        flat.setdefault("frequency_rule", None)
        out[d["name"]] = flat
    return out
