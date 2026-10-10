# External test images (not committed)

Third-party images are downloaded locally, never committed, because of licence terms and because they may name real clinicians.

## Mobiusi Pharmacy Prescription Text Extraction Dataset
- Source: https://huggingface.co/datasets/Mobiusi/Pharmacy-Prescription-Text-Extraction-Dataset
- Licence: CC BY-NC-SA 4.0 (attribution, non-commercial, share-alike). Only one sample is public.
- Content: one real prescription photo (UAE format, 4 medicines on one page, patient redacted) with a JSON label.
- Use: a real-world stress test only (multi-prescription page, brand names, "every 8 hours" directions). Report separately from the synthetic set.

Download: `python scripts/fetch_external.py`
