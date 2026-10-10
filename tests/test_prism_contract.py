"""Exercise real pinned SDK serialization with a mocked HTTP transport; no credentials."""
import httpx
import pytest
from prismtrace import PRISMtrace
from sponsors.config import Settings
from sponsors.prism import Prism


@pytest.fixture
def configured():
    clients = []
    def build(handler):
        p = Prism(Settings(prism_api_key="", prism_project_id="demo"))
        pt = PRISMtrace(api_key="test", host="https://prism.invalid", project_id="demo")
        pt._client.close()
        pt._client = httpx.Client(base_url="https://prism.invalid", transport=httpx.MockTransport(handler))
        p.pt = pt
        clients.append(pt)
        return p
    yield build
    for client in clients:
        client.close()


def search_response(chunks):
    return lambda request: httpx.Response(200, json={"results": chunks})


def test_catalog_requires_exact_row_and_named_source(configured):
    chunks = [{"content": "| metformin | 500 mg | tablet | none | |", "document_name": "catalog.md"}]
    p = configured(search_response(chunks))
    hit = p.kb_verify_catalog("Metformin", "500 mg", "tablet")
    assert hit.supported and hit.status == "supported" and hit.document == "catalog.md"
    assert not p.kb_verify_catalog("metformin", "5000 mg", "tablet").supported
    chunks[0]["document_name"] = "uploaded_fax.md"
    assert not p.kb_verify_catalog("metformin", "500 mg", "tablet").supported


def test_empty_search_and_outage_are_different(configured):
    empty = configured(search_response([])).kb_verify_catalog("x", "y", "z")
    assert empty.status == "no_match" and empty.prism.accepted and not empty.supported
    down = configured(lambda request: httpx.Response(503)).kb_verify_catalog("x", "y", "z")
    assert down.status == "unavailable" and not down.prism.accepted
    assert "unavailable" in down.claim.lower()


@pytest.mark.parametrize("body", [{}, {"results": {}}, {"results": ["bad"]}, {"results": [{"content": []}]}])
def test_malformed_search_response_is_unavailable(configured, body):
    p = configured(lambda request: httpx.Response(200, json=body))
    assert p.kb_verify_catalog("x", "y", "z").status == "unavailable"


def test_policy_never_falls_back_to_unrelated_document(configured):
    chunks = [{"document_name": "catalog.md", "content": "metformin"}]
    p = configured(search_response(chunks))
    assert p.kb_policy("blank refills").status == "no_match"
    chunks[0] = {"document_name": "intake_policy.md", "content": "Blank refills stay null."}
    result = p.kb_policy("blank refills")
    assert result.status == "retrieved" and result.citation and not result.supported


def test_evaluator_sends_classification_trace_and_trajectory(configured):
    import json
    calls = []
    def handler(request):
        calls.append((request.url.path, json.loads(request.content)))
        return httpx.Response(201, json={"id": "result-id"})
    p = configured(handler)
    result = p.request_evaluation("doc-1", classification="blocked", facts={"guardrail": {"blocked": True}})
    assert result.prism.accepted and result.classification_trace.accepted
    assert [path for path, _ in calls] == ["/api/traces", "/api/trajectories"]
    assert all(payload["agent_name"] == p.cfg.agent_reviewer for _, payload in calls)
    assert json.loads(calls[0][1]["output_message"])["classification"] == "blocked"


def test_missing_reference_folder_is_not_success(configured, tmp_path):
    p = configured(search_response([]))
    assert "error" in p.kb_bootstrap(tmp_path / "absent")
