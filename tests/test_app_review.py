from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from app import main


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA", tmp_path)
    rec = {"id": "ui-test", "status": "needs_review", "revision": 1,
           "fields": {"drug_name": {"value": "Metformin", "required": True,
                                    "status": "checks_passed", "source_text": "Metformin", "reasons": []}},
           "findings": [], "review": {}, "events": [], "source": "synthetic test", "sha256": "demo"}
    main.save(rec)
    return TestClient(main.app), rec


def test_review_approval_export_flow(workspace):
    client, rec = workspace
    assert client.post('/api/documents/ui-test/approve', json={"revision": 1}).status_code == 409
    reviewed = client.post('/api/documents/ui-test/fields/drug_name', json={"action": "confirm", "revision": 1}).json()
    assert reviewed['revision'] == 2
    assert client.post('/api/documents/ui-test/approve', json={"revision": 1}).status_code == 409
    assert client.post('/api/documents/ui-test/approve', json={"revision": 2}).status_code == 200
    assert client.get('/api/documents/ui-test/export').status_code == 200
    clarified = client.post('/api/documents/ui-test/clarify', json={"kind": "rescan", "fields": ["drug_name"]}).json()
    assert clarified['revision'] == 3 and not clarified['approved']
    assert client.get('/api/documents/ui-test/export').status_code == 409


@pytest.mark.parametrize("status", ["blocked", "processing", "processing_failed"])
def test_clarification_cannot_bypass_document_block(workspace, status):
    client, rec = workspace
    rec['status'] = status
    rec['fields'] = {}
    main.save(rec)
    assert client.post('/api/documents/ui-test/clarify', json={"kind": "rescan"}).status_code == 409
    assert client.post('/api/documents/ui-test/approve', json={"revision": 1}).status_code == 409


def test_empty_value_and_document_findings_block_approval(workspace):
    client, rec = workspace
    rec['review'] = {"drug_name": {"action": "edit"}}
    blank = deepcopy(rec)
    blank['fields']['drug_name']['value'] = '   '
    main.save(blank)
    assert client.post('/api/documents/ui-test/approve', json={"revision": 1}).status_code == 409
    rec['findings'] = [{"field": None, "reason": "Two prescriptions"}]
    main.save(rec)
    assert client.post('/api/documents/ui-test/approve', json={"revision": 1}).status_code == 409


def test_all_ui_assets_are_served(workspace):
    client, _ = workspace
    for path in ('/', '/scan', '/scorecard', '/static/ui.css', '/static/ui.js', '/static/review.js', '/static/scan.js', '/static/scorecard.js'):
        assert client.get(path).status_code == 200
