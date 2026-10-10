"""The verification list from the final plan, run against the real pipeline on the synthetic faxes."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main, policy

SAMPLES = Path(__file__).resolve().parents[1] / "fixtures" / "synthetic"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA", tmp_path / "docs")
    monkeypatch.setattr(policy, "PATH", tmp_path / "policy.json")
    (tmp_path / "docs").mkdir()
    return TestClient(main.app)


def load(client, name):
    doc_id = client.post(f"/api/samples/{name}").json()["id"]
    return client.get(f"/api/documents/{doc_id}").json()


def confirm_all(client, rec):
    for k, f in rec["fields"].items():
        if f["required"] and f["value"]:
            rec = client.post(f"/api/documents/{rec['id']}/fields/{k}", json={"action": "confirm", "revision": rec["revision"]}).json()
    return rec


def test_clean_fax_is_reviewable_and_export_needs_current_approval(client):
    rec = load(client, "fax_01_clean")                                              # 1
    assert rec["status"] == "needs_review"
    assert client.get(f"/api/documents/{rec['id']}/export").status_code == 409      # 8: not before approval
    rec = confirm_all(client, rec)
    ok = client.post(f"/api/documents/{rec['id']}/approve", json={"revision": rec["revision"]}, headers={"X-Role": "pharmacist"})
    assert ok.status_code == 200
    assert client.get(f"/api/documents/{rec['id']}/export").status_code == 200


def test_rerun_increments_revision_clears_confirmations_and_invalidates_approval(client):
    rec = confirm_all(client, load(client, "fax_01_clean"))
    rec = client.post(f"/api/documents/{rec['id']}/approve", json={"revision": rec["revision"]}, headers={"X-Role": "pharmacist"}).json()
    values_before = {k: f["value"] for k, f in rec["fields"].items()}
    r = client.post(f"/api/documents/{rec['id']}/rerun-checks",
                    json={"max_rx_age_days": 7, "recognizer_threshold": 0.9, "revision": rec["revision"]}).json()
    d = r["document"]
    assert d["revision"] == rec["revision"] + 1                                     # 3
    assert d["review"] == {}                                                        # 4
    assert d["approved"] is None and d["status"] == "needs_review"                  # 5
    assert client.get(f"/api/documents/{rec['id']}/export").status_code == 409
    assert {k: f["value"] for k, f in d["fields"].items()} == values_before         # values unchanged
    assert r["after"] > r["before"]                                                 # 2: stricter policy adds flags
    assert d["fields"]["date_written"]["status"] == "needs_review"
    ev = d["events"][-1]                                                            # 9: audit trail
    assert ev["action"] == "policy rerun" and ev["after"]["max_rx_age_days"] == 7 and ev["approval_invalidated"]
    again = client.get(f"/api/documents/{rec['id']}").json()                       # 9: survives reload
    assert again["policy"]["max_rx_age_days"] == 7 and again["events"][-1]["action"] == "policy rerun"
    assert client.get("/api/policy").json()["policy"]["max_rx_age_days"] == 7


def test_trailing_zero_stays_unchanged_and_flagged_after_rerun(client):
    rec = load(client, "fax_02_trailing_zero")
    assert rec["fields"]["strength"]["value"] == "5.0 mg" and rec["fields"]["strength"]["status"] == "needs_review"
    d = client.post(f"/api/documents/{rec['id']}/rerun-checks", json={"revision": rec["revision"]}).json()["document"]
    assert d["fields"]["strength"]["value"] == "5.0 mg" and d["fields"]["strength"]["status"] == "needs_review"  # 6


def test_injection_is_blocked_before_extraction_and_cannot_be_rerun(client):
    rec = load(client, "fax_04_injection")
    assert rec["status"] == "blocked" and rec["fields"] == {}                       # 7: nothing extracted
    assert client.post(f"/api/documents/{rec['id']}/rerun-checks", json={"revision": rec["revision"]}).status_code == 409


def test_policy_limits_and_locked_rules(client):
    rec = load(client, "fax_01_clean")
    for bad in ({"recognizer_threshold": 0.3}, {"recognizer_threshold": 0.99}, {"max_rx_age_days": 2}, {"max_rx_age_days": 400}):
        assert client.post(f"/api/documents/{rec['id']}/rerun-checks", json={**bad, "revision": rec["revision"]}).status_code == 400
    locked = client.get("/api/policy").json()["locked_rules"]
    assert any("software block" in r for r in locked) and any("approval" in r.lower() for r in locked)
