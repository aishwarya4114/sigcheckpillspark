"""Preload the review queue with cases in every workflow state, through the public API.

    python scripts/seed_demo.py [http://localhost:8000]

Leaves rx_upload_clean_amoxicillin.png for the live upload in the demo. All people are fictional.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")
WHO = "Seeded demo reviewer"


def call(path: str, body: dict | None = None, method: str = "POST") -> dict:
    data = json.dumps(body or {}).encode() if method == "POST" else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())


def upload(path: Path) -> dict:
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\n"
            f"Content-Type: image/png\r\n\r\n").encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(BASE + "/api/documents", data=body, method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "Idempotency-Key": uuid.uuid4().hex})
    with urllib.request.urlopen(req, timeout=180) as r:
        doc = json.loads(r.read())
    return call(f"/api/documents/{doc['id']}", method="GET")


def confirm_all(doc: dict) -> dict:
    for k, f in doc["fields"].items():
        if f.get("value") not in (None, "") and not (doc.get("review") or {}).get(k):
            doc = call(f"/api/documents/{doc['id']}/fields/{k}",
                       {"action": "confirm", "revision": doc["revision"], "reviewer": WHO, "reason": "Verified against the original"})
    return doc


def main() -> None:
    demo, syn = ROOT / "fixtures" / "demo", ROOT / "fixtures" / "synthetic"
    plan = [
        ("approved", demo / "seed_clean_amlodipine.png"),
        ("ready", demo / "seed_clean_sertraline.png"),
        ("waiting", syn / "fax_02_trailing_zero.png"),
        ("new", syn / "fax_03_lookalike.png"),
        ("new", syn / "fax_05_bad_npi.png"),
        ("new", syn / "fax_06_missing_strength.png"),
        ("new", syn / "fax_04_injection.png"),
    ]
    for goal, path in plan:
        doc = upload(path)
        if goal != "new":
            doc = call(f"/api/documents/{doc['id']}/open", {"revision": doc["revision"], "reviewer": WHO})
        if goal in ("approved", "ready"):
            doc = confirm_all(doc)
            if goal == "approved" and doc["workflow"]["status"] == "ready_for_approval":
                doc = call(f"/api/documents/{doc['id']}/approve", {"revision": doc["revision"], "reviewer": WHO})
        if goal == "waiting":
            for issue in doc["workflow"].get("issues", []):
                if issue["category"] == "prescriber_clarification":
                    doc = call(f"/api/documents/{doc['id']}/issues/{urllib.request.quote(issue['key'], safe='')}/contacted",
                               {"note": "Faxed clarification request to prescriber", "revision": doc["revision"], "reviewer": WHO})
        print(f"{path.name:40s} -> {doc['workflow']['status']}")
        time.sleep(0.2)


if __name__ == "__main__":
    main()
