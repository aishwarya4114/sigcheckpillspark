# sponsors/: Nicrron + Block Convey PRISM

Self-contained. The pipeline imports it; nothing here depends on the pipeline, the UI or the iPhone app.

## Setup
```bash
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements-sponsors.txt
cp .env.example .env        # add NICRRON_API_KEY, PRISMTRACE_API_KEY, PRISMTRACE_PROJECT_ID
python scripts/smoke_sponsors.py --upload-kb   # live check of both sponsors + loads reference/*.md into the KB
python -m pytest -q tests                       # offline tests
```

## How the pipeline calls it (in this order)
```python
from sponsors import NicrronReader, Prism
prism, reader = Prism(), NicrronReader()

g = prism.guardrail(doc_id, ocr_text)              # 1. BEFORE any model sees the text
if g.blocked:
    raise ValueError("Document blocked; show findings to reviewer")  # stop this pipeline run
run = reader.extract(image_bytes, "image/png", ocr_text=g.safe_text)   # 2. Nicrron vision read
prism.trace_reader_run(doc_id, run)                # 3. trace the call (returns Delivery: accepted or not)
kb = prism.kb_verify_catalog(drug, strength, form) # 4. KB claim + citation for the review screen
pol = prism.kb_policy("blank refills")             #    policy passage for a flagged field
ev = prism.request_evaluation(doc_id, classification=..., facts={...})  # 5. evaluator on the final classification
prism.evaluation_result(ev.trajectory_id)          #    None = pending: show "evaluation pending", never a pass
```
Optional second reader: set `NICRRON_SECOND_READER_MODEL`, call `reader.extract(..., model=settings.second_reader_model)`, then `compare_readers(a, b)`. Agreement alone is not proof; it only counts with a passed reference check.

## What PRISM does and does not do (from prismtrace-sdk 0.5.1 source)
- **Guardrail / evaluator rules live in the PRISM dashboard**, scoped to the agent ids `sigcheck-input-guardrail`, `sigcheck-extractor`, `sigcheck-review-pipeline`. Create the rules there. They **flag** recorded traces; they don't stop our code. `sponsors/injection.py` is what blocks.
- **Evaluator:** we submit a classification trace for the dashboard output rule, plus a trajectory for asynchronous evaluation. Acceptance of either is not a passing evaluator verdict. Configure the rule and inspect its result in PRISM.
- **Knowledge base** is searchable from code. We retrieve, then verify the exact catalogue row ourselves; a fuzzy hit is not support.
- **The SDK fails open.** Every call here returns a `Delivery` saying whether PRISM accepted it. Never show a trace as live unless `accepted` is true.

## Result handling and verification limits
- `ReaderRun.ok` means the response passed schema validation, not that its values are correct. The pipeline must still block/review conflicts, multiple prescriptions, suspicious text and missing required fields.
- Known injection patterns in supplied OCR stop the reader before any request. `safe_text` on a blocked result is annotated evidence for display, not sanitised input. Regex checks cannot detect every instruction in an image.
- A catalogue claim requires an exact row from `catalog.md`. `KBResult.status` distinguishes `supported`, `no_match`, and `unavailable`. No match in top search results does not prove absence from the full catalogue.
- `kb_policy` returns `status="retrieved"` and `supported=False` for an intake-policy excerpt: retrieval alone cannot prove the answer to an arbitrary question. Show the excerpt for review; never use it as automatic approval.
- SDK 0.5.1 private synchronous transport is used for acknowledgements because its public search collapses outages and empty results. Keep the SDK pinned and rerun the transport contract tests before upgrading.
- The smoke script exits **1 on failures, 2 on skipped/incomplete checks, 0 only when all reported checks pass**. Dashboard rule verdicts require manual verification and are reported as incomplete. An upload/trace acknowledgement is not proof that a guardrail or evaluator executed.
- Only synthetic demo documents should be sent to sponsors. Model IDs in `.env.example` are examples; select a model actually available in your Nicrron account.

## Still to confirm with organizers
- Whether calling the Nicrron API satisfies the "NicrronNinoo" requirement (or Ninoo Builder must also be used).
- Which Nicrron models accept images (the smoke test checks the configured one).
