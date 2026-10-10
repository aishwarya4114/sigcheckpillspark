"""Run the pipeline on every synthetic fax and measure it against ground truth.

    python scripts/evaluate.py        -> evals/results.json (shown at /scorecard)

Claims are scoped to "our synthetic test set". The real-photo set (fixtures/external) is reported separately.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import pipeline  # noqa: E402
from sponsors import settings  # noqa: E402

norm = lambda v: re.sub(r"\s+", " ", str(v).strip().lower()) if v not in (None, "") else None


def main() -> None:
    rows, readable, exact, said_ok, said_ok_right, wrong_nonnull_on_absent = [], 0, 0, 0, 0, 0
    traps_caught = traps_total = 0
    for truth_p in sorted((ROOT / "fixtures" / "synthetic").glob("*.truth.json")):
        truth = json.loads(truth_p.read_text())
        rec = pipeline.process((truth_p.parent / truth["fax"]).read_bytes(), "image/png", source=truth["fax"])
        per = {}
        for k, tv in truth["fields"].items():
            got = rec["fields"].get(k, {}) if rec["status"] != "blocked" else {}
            gv, st = got.get("value"), got.get("status", "blocked")
            match = norm(gv) == norm(tv)
            if tv is not None:
                readable += 1
                exact += match
            elif gv is not None:
                wrong_nonnull_on_absent += 1
            if st == "checks_passed":
                said_ok += 1
                said_ok_right += match
            per[k] = {"truth": tv, "read": gv, "status": st, "match": match}
        caught = (rec["status"] == "blocked") if truth["expected"]["blocked"] else rec["classification"] != "complete_for_review" or any(
            f["status"] in ("needs_review", "missing") for f in rec["fields"].values())
        if "clean" not in truth["fax"]:
            traps_total += 1
            traps_caught += bool(caught)
        rows.append({"fax": truth["fax"], "trap": truth["trap"], "status": rec["status"], "flagged": bool(caught), "fields": per})
    result = {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "reader": f"Nicrron {settings.reader_model}" if settings.nicrron_enabled else "DEMO label reader (no Nicrron key)",
        "scope": "synthetic test set only (fictional people); not a real-world accuracy claim",
        "faxes": len(rows),
        "field_accuracy": {"exact": exact, "of": readable},
        "verified_precision": {"right": said_ok_right, "of": said_ok,
                               "meaning": "when a field passed all checks, how often the value matched the truth"},
        "invented_values_on_absent_fields": wrong_nonnull_on_absent,
        "traps_caught": {"caught": traps_caught, "of": traps_total},
        "rows": rows,
    }
    out = ROOT / "evals"; out.mkdir(exist_ok=True)
    (out / "results.json").write_text(json.dumps(result, indent=2))
    print(f"field accuracy {exact}/{readable} · passed-checks precision {said_ok_right}/{said_ok} · "
          f"invented values {wrong_nonnull_on_absent} · traps caught {traps_caught}/{traps_total}")


if __name__ == "__main__":
    main()
