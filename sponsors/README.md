# sponsors/: Nicrron + Block Convey PRISM

Self-contained. The pipeline imports it; nothing here depends on the pipeline, the UI or the iPhone app.

## Setup
```bash
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements-sponsors.txt
cp .env.example .env        # add NICRRON_API_KEY, PRISMTRACE_API_KEY, PRISMTRACE_PROJECT_ID
python scripts/smoke_sponsors.py --upload-kb   # live check of both sponsors + loads reference/*.md into the KB
pytest -q tests                                 # offline tests
```

## How the pipeline calls it (in this order)
```python
from sponsors import NicrronReader, Prism
prism, reader = Prism(), NicrronReader()

g = prism.guardrail(doc_id, ocr_text)              # 1. BEFORE any model sees the text
if g.blocked: ...                                  #    order BLOCKED, show g.findings (text + reason)
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
- **Evaluator** = trajectory submission; PRISM scores it asynchronously.
- **Knowledge base** is searchable from code. We retrieve, then verify the exact catalogue row ourselves; a fuzzy hit is not support.
- **The SDK fails open.** Every call here returns a `Delivery` saying whether PRISM accepted it. Never show a trace as live unless `accepted` is true.

## Still to confirm with organizers
- Whether calling the Nicrron API satisfies the "NicrronNinoo" requirement (or Ninoo Builder must also be used).
- Which Nicrron models accept images (the smoke test checks the configured one).
