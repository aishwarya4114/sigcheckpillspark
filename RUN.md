# Run SigCheck (feat/app-spine)

```bash
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements-app.txt
python scripts/make_faxes.py          # 6 synthetic faxes with planted traps
python scripts/evaluate.py            # scorecard -> evals/results.json
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- Review workspace: http://localhost:8000
- Phone capture (same WiFi): http://<your-mac-ip>:8000/scan  (find the IP with `ipconfig getifaddr en0`)
- Scorecard: http://localhost:8000/scorecard

Without keys it runs in clearly labelled demo mode (Apple Vision OCR + a simple label reader that only understands our synthetic layout). Add `NICRRON_API_KEY` and the PRISM keys to `.env` and restart: extraction switches to the Nicrron vision reader and every stage is traced in PRISM.

## API (shared by web, /scan and the iPhone app)
| Method | Path | Purpose |
|---|---|---|
| GET | /api/health | which sponsors are live |
| GET | /api/documents | queue (id, status, revision, unresolved count, clarification) |
| POST | /api/documents | upload (multipart `file`, `source`; header `Idempotency-Key` prevents duplicates) |
| GET | /api/documents/{id} | full record: fields, reasons, boxes, sponsors, history |
| GET | /api/documents/{id}/image | current image |
| POST | /api/documents/{id}/fields/{field} | `{action: confirm|edit, value, reason, revision}` (stale revision -> 409) |
| POST | /api/documents/{id}/clarify | `{kind: rescan|prescriber, fields}` |
| POST | /api/documents/{id}/rescan | attach a clearer scan as a new revision (multipart `file`) |
| POST | /api/documents/{id}/approve | `{revision}` + header `X-Role: pharmacist`; blocked / stale / unreviewed -> 409, intake -> 403 |
| GET | /api/documents/{id}/export | approved current revision only |
| GET | /api/documents/{id}/evaluation | PRISM evaluator verdict, or "pending" |
