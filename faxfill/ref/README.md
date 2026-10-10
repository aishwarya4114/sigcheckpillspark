# Reference data (demo only)

This data exists to test a review workflow. It is **not for clinical use**.

## Drugs: `drugs.json` (41 drugs, real US data plus hand-entered fields)

Every value is stored as `{"value": ..., "source": ...}` with one of three sources:

| Source | What | From |
|---|---|---|
| `rxnorm` | RxCUI, strengths, dose form (`strengths` = the primary oral form we print; `strengths_by_form` = all oral tablet / capsule / solution forms, including ER/DR/ODT/chewable variants) | NLM RxNav `https://rxnav.nlm.nih.gov/REST/drugs.json?name=<drug>`, SCD (semantic clinical drug) concepts only; combination products skipped |
| `openfda` | DEA schedule (`C-II`, `C-IV`, … or `null` if the result has no `dea_schedule`) | `https://api.fda.gov/drug/ndc.json?search=generic_name:"<drug>"&limit=1` |
| `manual, unverified` | max daily dose, look-alike/sound-alike partners (modelled on ISMP's List of Confused Drug Names), allergy classes, the methotrexate weekly rule | `manual.json` (hand-entered; not checked against a source) |

The raw API responses are saved in `raw/` (`rxnav_<drug>.json`, `openfda_<drug>.json`, fetched 2026-10-10), so everything rebuilds offline.

```bash
python -m faxfill.ref.fetch_real            # re-download into raw/ and rebuild drugs.json
python -m faxfill.ref.fetch_real --offline  # rebuild drugs.json from raw/ only
```

Code should read the data through `faxfill.ref.load_drugs()`, which flattens it to plain values and keeps a `sources` map.

**Known limitations**
- RxNorm lists every strength it has a concept for, including some that are rarely dispensed (for example prednisolone 20/25/50 mg tablets). "In RxNorm" means "a real product concept", not "commonly stocked".
- RxNorm states microgram strengths in MG. Levothyroxine `0.025 MG` is converted to `25 mcg` and marked `rxnorm (converted from MG)`.
- openFDA's first result is sometimes a combination product (for example `losartan` → losartan/hydrochlorothiazide). Its `dea_schedule` still reflects the controlled ingredient, but treat the schedule as a single-record lookup, not a full search.
- 24 of the 41 drugs have different strengths from our original hand-entered list. In every case RxNorm lists more strengths than we did; it never lists fewer.

## People (fictional)

- `prescribers.csv`: 15 fictional prescribers, the "directory". NPIs pass the Luhn check (80840 prefix); DEA numbers pass the DEA check digit.
- `unknown_prescribers.csv`: 2 fictional prescribers deliberately left out of the directory, for the "unknown prescriber" trap.
- `patients.csv`: 15 fictional patients with DOB and allergies.

All 17 NPIs were checked against the public CMS NPI Registry (npiregistry.cms.hhs.gov) on 2026-10-10. Every one returned 0 results, so none belongs to a real provider. There is no public DEA lookup, so the DEA numbers are only checksum-valid.

Regenerate with `python -m faxfill.ref.make_people` (deterministic).

## Checking the faxes against this data

```bash
python -m faxfill.gen             # regenerate faxes + answer keys
python -m faxfill.check_answers   # every fax must carry exactly its declared traps
```
