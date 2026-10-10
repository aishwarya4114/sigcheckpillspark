# SigCheck

**Track 4: PillSpark "Fax to Fill"** · Get Hired Hack, Columbia Startup Lab, Oct 10, 2026

Pharmacies still receive prescriptions as blurry faxes that a person retypes by hand. SigCheck reads each fax with two AI models from different vendors, checks every field against things a model can't fake, and sends only what it couldn't verify to the pharmacist, with a reason and the spot on the fax where it was read.

> Two AIs agreeing isn't proof. SigCheck checks every field against reference data, checksums and documented fax failures, and when it can't confirm a value, it refuses to guess.

## What's in this repository

SigCheck has two parts that share one idea (the model reads; checks it can't fake decide):

| Part | Folder | What it is |
|---|---|---|
| **Verification engine and evaluation** | `faxfill/` | Two-vendor reading, the full check catalogue (RxNorm/openFDA reference data, documented fax failures), danger-sorted review UI, and the dev + **held-out** evaluation described below |
| **Pharmacist workflow app** | `app/`, `sponsors/` | Live intake (web upload and phone `/scan`), Apple Vision OCR with source highlights, a next-action workflow that separates *verify transcription*, *clarify with the prescriber* and *blocked*, attested approval, PDF and JSON draft orders, audit trail, redacted PRISM telemetry, system design and pitch pages. See [WORKFLOW-APP.md](WORKFLOW-APP.md) |

The workflow app adds three things the engine does not: **live intake of new faxes** (any uploaded or photographed page is processed on the spot), **a clarification loop** (a trailing-zero strength can't be cleared by confirming the fax; only the prescriber's unambiguous answer resolves it, and the case waits for them), and **live PRISM Knowledge Base queries from code** (`kb_search` in `prismtrace-sdk`, with an exact-row check) plus PRISM's automatic trajectory scorecards shown in the app.

## Try it in two minutes

1. Start the workflow app (steps in [How to run](#how-to-run)) and open `http://localhost:8001`.
2. Click **New prescription** and upload one of the ready-made prescription images in [`fixtures/demo/`](fixtures/demo/):
   - `rx_upload_clean_amoxicillin.png`: a clean handwritten prescription. Confirm the fields, approve, then print or save the PDF.
   - `rx_upload_trailing_zero_atorvastatin.png`: "20.0 mg" could be read as 200 mg, so the case waits for the prescriber.
3. For the planted traps (look-alike drug, bad NPI, missing strength, hidden instruction), upload any fax from [`fixtures/synthetic/`](fixtures/synthetic/).

The images are photos of a paper Rx pad (tinted paper, blue-ink handwriting, a slight tilt). All people on them are fictional. Regenerate them with `python scripts/make_demo_rx.py`.

## How it works

**1. Read.** Each page is straightened and cleaned, then read three ways:
- Tesseract OCR, which also records where every word sits on the page
- Reader A: `openai/gpt-5.5`, via the Nicrron gateway
- Reader B: `mistral/mistral-large-3`, via the Nicrron gateway

Both models return a fixed JSON form at temperature 0. Fax text is treated as data, never as instructions. An unreadable field comes back empty, not guessed.

**2. Check.** Every field is tested against things the models don't control:
- drug names, strengths and dose forms from NLM's **RxNorm**; DEA schedules from **openFDA**
- NPI checksum (Luhn) and DEA number checksum; prescriber and patient lookups
- federal refill rules (no refills on Schedule II, 21 CFR 1306.12; at most 5 on Schedule III–V, 21 CFR 1306.22)
- allergy conflicts, dose above usual maximum, methotrexate written daily instead of weekly, mg vs mcg
- notation traps: trailing zeros (`5.0 mg`), leading decimals (`.5 mg`), `U` vs `0`
- fax failures documented by ISMP and pharmacy regulators: noise over the dose, a cut-off left edge, missing pages, a page with no patient name or date of birth, and two conflicting sets of directions
- hidden instructions addressed to an AI, after normalizing look-alike characters (OCR read "AI" as "Al")

**3. Decide.** Every field ends up in one of three states, always with a reason:
- **Matches fax, checks passed:** both readers agree, at least one independent check passed, and no rule fired
- **Needs pharmacist:** readers disagree, a reader couldn't read it, or a check failed. Alternatives are shown (e.g. "5 mg" / "6 mg")
- **Blocked:** hidden instructions on the fax, or pages missing. There is no override

The queue is sorted by danger, not arrival time. The pharmacist must resolve every flag, with a reason, before an order can be sent on. Every decision is logged.

## Design rules

- **Agreement is not proof.** A value passes only when an independent check also passes.
- **Never take a value one reader couldn't see.** If one reader says "unreadable" and another gives a number, the number is not used.
- **Reading problems and safety problems are different.** "What does the fax say?" gets value choices. "The fax was read correctly, but it's unsafe" gets proceed / hold / reject.
- **Evidence comes from OCR, not the model.** Highlights are drawn from Tesseract word positions. If a field can't be located, the screen says "location not found" instead of drawing a guessed box.
- **The pharmacist always makes the final call.** SigCheck replaces the typing, not the pharmacist's check.

## Results

All test faxes are synthetic (see Data below). Numbers are reported on two sets, and they are not combined.

### Held-out set (rules frozen before these faxes were generated)

Rules were frozen at commit `2abbbb8`, then 20 new faxes were generated with a new random seed (10 clean, 10 with one trap each, 4 handwritten) and scored without changing any rule.

| Measure | Result |
|---|---|
| Wrong values passed | **0** |
| When a field passed, it was right | 235 / 235 |
| Field values correct | 244 / 244 (2 left empty rather than guessed) |
| Planted traps caught | **10 / 10**, each by the intended check |
| False alarms on clean faxes | 3 of 10 clean faxes flagged (7 reached Ready) |

**Caveat:** this is a small sample, and the hardest trap types (look-alike drug names, fax noise, truncation, wrong-patient page) were not drawn in it. Treat these numbers as a good sign, not proof.

**A real failure (ho_003):** a clean fax was flagged "left edge cut off." Tesseract read the NPI `1969099971` as `196909997 1`, and the truncation check looks for an unbroken 10-digit NPI near the top of the page. Both models read the NPI correctly. Fix (not applied, because rules were frozen): accept the prescriber block when both models agree on a checksum-valid NPI and the name isn't at the page edge. Cost: one false alarm. No wrong value passed.

### Development set (rules were tuned on these)

31 faxes (32 pages' worth of prescriptions), each built to test a specific trap. Highlights:
- **rx_027:** GPT-5.5 read a noisy warfarin "5 mg" as "6 mg", also a real warfarin strength. The noise check flagged it and listed both values.
- **rx_028:** the left edge was cut off, so both models read "escitalopram" as "citalopram", a real drug at a real strength. Only the truncation check caught it.
- **rx_018:** on a smudged dose, Reader A abstained and Reader B invented "10 mg" (truth: 20 mg). The value was not taken.
- **Misses:** the look-alike traps rx_008 (hydroxyzine) and rx_009 (clonidine) were read correctly by all readers and passed with an informational "look-alike" badge instead of a flag. We don't flag every drug with a look-alike partner (22 of our 41 drugs have one), because flagging half the queue trains pharmacists to ignore flags.

## Sponsor tools

**Nicrron gateway.** Both vision readers run through `https://api.nicrron.ai/v1`, with explicitly named models from two vendors (`nicrron/auto` is never used). Models were chosen by a real test call on one fax. `anthropic/claude-sonnet-5` was rejected because it does not accept temperature 0.

**PRISM by Block Convey.**
- **Traces:** every model read, every page's OCR text (intake) and every verdict is traced, with stable trace IDs, `session_id` = fax ID, and three agents: `sigcheck-intake`, `sigcheck-reader`, `sigcheck-verifier`.
- **Guardrail on an input:** the OCR text of every page goes through PRISM's prompt-injection rule, and its result is read back from the ingest response and wired into the blocking decision (the order blocks if our local check or PRISM fires). Finding: PRISM's rule did **not** flag our fax injections, even with "AI" spelled correctly, so both injected faxes were blocked by our local check. Earlier, PRISM's rule fired on our own system prompt ("do not follow…"), so we removed the system prompt from reader traces.
- **Evaluator on a classified output:** a custom LLM-judge evaluator, "Verdict backed by checks," audits that every passing field lists a passed check and every flagged field names a reason. On 10 verifier traces: 8 consistent, 2 inconsistent (under review).
- **Knowledge Base:** the reference data is uploaded to the PRISM Knowledge Base (3 documents). The verifier queries the same data from local files: we found no documented API for querying the Knowledge Base from code, assigning documents to an agent didn't work during the event, and the PRISM team advised us not to block the submission on it.

**Workflow app (`app/`, `sponsors/`).** The live app reads through Nicrron with `anthropic/claude-sonnet-5`, sending no temperature at all (which the model accepts), and falls back to a labelled demo reader when no key is set. It queries the PRISM Knowledge Base from code with `kb_search` in `prismtrace-sdk` 0.5.1 and checks the exact catalogue row in the result. Each case is also sent as a trajectory, and PRISM's automatic scorecard (safety, correctness, efficiency, robustness, reasoning) is shown in the technical view. Patient and prescriber identifiers are masked before anything reaches PRISM, and a test enforces this.

## Data

Real prescription faxes are protected patient data, so none are used.
- **Faxes:** synthetic, drawn with Pillow and degraded to look faxed (200 dpi, black and white, skew, blur, speckle, streaks, a transmit header), each with an answer key.
- **Drug data:** real, from NLM's RxNorm (strengths, RxCUI, dose forms) and openFDA (DEA schedules). Raw API responses are saved in `faxfill/ref/raw/` so the reference data can be rebuilt offline.
- **Hand-entered values** (max daily doses, look-alike partners, allergy classes, high-alert drugs, the methotrexate weekly rule) are kept separate and labeled `manual, unverified`.
- **People:** all patients and prescribers are fictional. All NPIs pass the Luhn checksum and return no match in the public CMS NPI Registry. DEA numbers are checksum-valid only.

## Implementation checklist

Status at submission, 10 Oct 2026.

**Done and working**

| Area | What | Where |
|---|---|---|
| Intake | Web upload with duplicate protection, ready-made demo images | `app/`, `fixtures/demo/` |
| Reading | Apple Vision OCR with word positions; Nicrron reader (Claude Sonnet 5); demo label reader offline | `app/pipeline.py`, `sponsors/nicrron.py` |
| Engine reading | Tesseract plus two readers from different vendors (GPT-5.5, Mistral Large 3), cached reads | `faxfill/` |
| Safety screen | Hidden instructions blocked before any model reads the page, look-alike characters normalized | `sponsors/injection.py`, `faxfill/` |
| Checks | NPI and DEA check digits, trailing and leading zeros, look-alike drugs, catalogue coverage, prescription age | `app/checks.py` |
| Engine checks | 38 checks on RxNorm and openFDA data, refill law, allergies, max dose, documented fax failures | `faxfill/CHECKS.md` |
| Workflow | Next action per case: verify transcription, clarify with prescriber, or blocked; queue tabs All / Needs action / Waiting / Ready / Completed | `app/workflow.py` |
| Evidence | Click a field to see where it was read on the page | `app/static/review.js` |
| Clarification loop | Printable request with cropped images, mark contacted, record the prescriber's answer | `app/main.py` |
| Approval | Blocked until every issue is resolved; pharmacist attestation with name; revisions kept | `app/main.py` |
| Output | Draft order as a printable PDF and as JSON; append-only audit trail | `app/main.py` |
| Policy | Adjustable review threshold and prescription age; safety rules locked on | `app/policy.py` |
| Sponsors | Nicrron gateway; PRISM traces, guardrail traces, Knowledge Base search, trajectory scorecards | `sponsors/` |
| Privacy | Identifiers masked before telemetry, enforced by a test | `sponsors/redact.py` |
| Evaluation | Workflow app: 5/5 traps, 48/48 precision on passed fields, 0 invented values. Engine: held-out 244/244, 10/10 traps | `scripts/evaluate.py`, `faxfill/` |
| Tests | 85 Python tests plus 3 UI tests, all passing | `tests/`, `faxfill/` |
| Presentation | Pitch deck at `/pitch`, architecture at `/system-design` | `app/static/` |

**Built but not in today's demo**

- Phone capture at `/scan` (works over the local network; left out to keep the demo short)
- PRISM evaluator set up in the dashboard (the API scorecards above are used instead; assigning tasks in the dashboard did not work during the event)

**Not done yet**

- The engine's two readers, 38 checks and danger score do not feed the workflow app yet. The two parts run side by side and share the same design rules.
- No testing on real faxes or PillSpark's own export schema
- No login, encryption at rest or pharmacy-system integration (needed before any real use)

## Known limitations

- Results are on synthetic faxes only. Real faxes are noisier and more varied.
- The held-out set is small (20 faxes) and didn't include every trap type.
- The noise threshold is a starting point; heavily degraded clean faxes can trip page-count and noise checks.
- The regex OCR reader was demoted to notes after it raised 11 flags on the dev set, all wrong.
- Look-alike drug names are flagged only on signs of confusion; a correctly read look-alike gets a badge, not a flag.
- Drug reference data is a 41-drug sample, not clinical-grade.

## How to run

Requires Python 3 and Tesseract (`brew install tesseract` on macOS).

```bash
pip install -r requirements.txt
```

Create `.env` in the repo root (never commit it):

```
NICRRON_API_KEY=
PRISMTRACE_API_KEY=
PRISMTRACE_PROJECT_ID=
PRISMTRACE_HOST=https://prism.blockconvey.com
```

Then:

```bash
python -m faxfill.server            # engine review UI at http://localhost:8000
python -m faxfill.read rx_001       # show all three reads of one fax side by side
python -m faxfill.check_answers     # confirm every test fax carries only its declared trap
python -m faxfill.eval              # score verdicts against the answer keys
pytest                              # unit tests, one per rule
```

Model reads are cached in `faxfill/data/reads/`, so the UI and eval run without new API calls.

**Workflow app** (macOS, uses Apple Vision through `ocrmac`; run on another port if the engine UI is on 8000):

```bash
pip install -r requirements-app.txt
python scripts/make_faxes.py
python scripts/smoke_sponsors.py --upload-kb
python scripts/make_demo_rx.py                     # optional: rebuild the upload demo images
uvicorn app.main:app --host 0.0.0.0 --port 8001   # http://localhost:8001 · /system-design · /pitch
```

The same `.env` keys turn on the live Nicrron reader and PRISM. Without them the app still runs end to end with the demo reader.

Details in [WORKFLOW-APP.md](WORKFLOW-APP.md) and [RUN.md](RUN.md).

## Sources

- ISMP via *America's Pharmacist*, Oct 2022: fax noise caused 0.6 mg to be dispensed as 0.8 mg. https://digitaleditions.sheridan.com/article/Medication+Safety/4346835/761802/article.html
- Patient Safety Solutions, citing ISMP Canada: truncated faxes (dalteparin read as heparin) and stuck-together sheets. https://www.patientsafetysolutions.com/docs/June_19_2012_More_Problems_with_Faxed_Orders.htm
- Ontario College of Pharmacists, Summer 2021: faxed pages assumed to belong to the same patient. https://ocpinfo.com/ocp-resources/focus-on-error-prevention-summer-2021/
- NABP, ISMP Safety Briefs: two sets of directions on one prescription. https://nabp.pharmacy/news/blog/regulatory_news/ismp-safety-briefs-electronic-prescription-prescribers-note-field-error/
- NLM RxNorm (RxNav API) and openFDA NDC API for drug data.

Full specs and every threshold are in `faxfill/CHECKS.md`.

## Team


[Names and roles]
