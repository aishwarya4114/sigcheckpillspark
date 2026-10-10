"""The workflow guarantee: a prescriber-clarification issue can't be cleared by confirming the fax."""
from tests.test_policy_rerun import client, load, confirm_all  # noqa: F401  (fixture + helpers)


def key_of(rec, code):
    return next(i["key"] for i in rec["workflow"]["issues"] if i["code"] == code)


def test_confirming_the_fax_does_not_clear_a_trailing_zero(client):
    rec = client.post(f"/api/documents/{load(client, 'fax_02_trailing_zero')['id']}/open").json()
    assert rec["workflow"]["status"] == "needs_clarification"
    rec = confirm_all(client, rec)
    assert rec["workflow"]["status"] == "needs_clarification"
    assert client.post(f"/api/documents/{rec['id']}/approve", json={"revision": rec["revision"]},
                       headers={"X-Role": "pharmacist"}).status_code == 409


def test_contact_wait_and_record_answer_then_approve(client):
    rec = confirm_all(client, client.post(f"/api/documents/{load(client, 'fax_02_trailing_zero')['id']}/open").json())
    k = key_of(rec, "AMBIGUOUS_TRAILING_ZERO")
    rec = client.post(f"/api/documents/{rec['id']}/issues/{k}/contacted", json={"revision": rec["revision"]}).json()
    assert rec["workflow"]["status"] in ("waiting_for_prescriber", "needs_verification")
    assert client.post(f"/api/documents/{rec['id']}/issues/{k}/resolve", json={"note": " ", "revision": rec["revision"]}).status_code == 400
    rec = client.post(f"/api/documents/{rec['id']}/issues/{k}/resolve",
                      json={"note": "Dr Patel by phone: 5 mg", "value": "5 mg", "revision": rec["revision"]}).json()
    assert rec["fields"]["strength"]["value"] == "5 mg"
    rec = confirm_all(client, rec)
    open_issues = [i for i in rec["workflow"]["issues"] if i["state"] != "resolved"]
    if not open_issues:
        assert rec["workflow"]["status"] == "ready_for_approval"
        ok = client.post(f"/api/documents/{rec['id']}/approve", json={"revision": rec["revision"]}, headers={"X-Role": "pharmacist"})
        assert ok.status_code == 200 and ok.json()["workflow"]["status"] == "approved" and ok.json()["approved"]["attestation"]


def test_transcription_issue_is_cleared_by_confirming(client):
    rec = client.post(f"/api/documents/{load(client, 'fax_05_bad_npi')['id']}/open").json()
    assert any(i["category"] == "verify_transcription" and i["state"] == "open" for i in rec["workflow"]["issues"])
    rec = confirm_all(client, rec)
    assert all(i["state"] == "resolved" for i in rec["workflow"]["issues"] if i["category"] == "verify_transcription")


def test_blocked_document_is_blocked_in_workflow(client):
    rec = load(client, "fax_04_injection")
    assert rec["workflow"]["status"] == "blocked" and rec["workflow"]["issues"][0]["code"] == "INSTRUCTIONS_TO_SOFTWARE"
    q = client.post(f"/api/documents/{rec['id']}/quarantine").json()
    assert q["quarantined"] and q["workflow"]["status"] == "blocked"
