# Reference data (demo only)

Everything here is fictional and exists to test a review workflow. It is **not for clinical use**.

- `drugs.json`: 41 common drugs with strengths, form, approximate adult max daily dose, DEA schedule and look-alike/sound-alike partners (modelled on ISMP's List of Confused Drug Names). The dose values are approximate.
- `prescribers.csv`: 15 fictional prescribers, the "directory". NPIs pass the Luhn check (80840 prefix); DEA numbers pass the DEA check digit.
- `unknown_prescribers.csv`: 2 fictional prescribers deliberately left out of the directory, for the "unknown prescriber" trap.
- `patients.csv`: 15 fictional patients with DOB and allergies.

All 17 NPIs were checked against the public CMS NPI Registry (npiregistry.cms.hhs.gov) on 2026-10-10. Every one returned 0 results, so none belongs to a real provider. There is no public DEA lookup, so the DEA numbers are only checksum-valid.

Regenerate with `python -m faxfill.ref.make_people` (deterministic).
