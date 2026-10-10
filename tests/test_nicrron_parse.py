import json
import httpx
from sponsors.config import Settings
from sponsors.nicrron import NicrronReader, _parse_json, compare_readers, FIELDS

def fake_client(responses):
    calls = iter(responses)
    def handler(request):
        content = next(calls)
        return httpx.Response(200, json={"id": "req-1", "choices": [{"message": {"content": content}}],
                                         "usage": {"prompt_tokens": 100, "completion_tokens": 50}})
    return httpx.Client(base_url="https://x/v1", transport=httpx.MockTransport(handler))

GOOD = json.dumps({"fields": {"drug_name": {"value": "Metformin", "source_text": "Rx: Metformin 5.0 mg"},
                              "strength": {"value": "5.0 mg", "source_text": "5.0 mg"},
                              "refills": {"value": "", "source_text": ""}},
                   "prescription_count": 1, "suspicious_text": []})

def test_parse_fenced_json():
    assert _parse_json("```json\n{\"a\": 1}\n```") == {"a": 1}
    assert _parse_json("Here you go: {\"a\": 2} thanks") == {"a": 2}

def test_extract_keeps_verbatim_and_nulls_missing():
    r = NicrronReader(Settings(nicrron_api_key="k"), client=fake_client([GOOD])).extract(b"img")
    assert r.ok and r.fields["strength"]["value"] == "5.0 mg"       # trailing zero preserved
    assert r.fields["refills"]["value"] is None                     # blank stays null, not "0"
    assert r.fields["prescriber_npi"]["value"] is None              # absent field is explicit null
    assert set(r.fields) == set(FIELDS) and r.tokens_in == 100

def test_one_repair_retry_then_success():
    r = NicrronReader(Settings(nicrron_api_key="k"), client=fake_client(["not json at all", GOOD])).extract(b"img")
    assert r.ok and r.repaired and r.tokens_in == 200

def test_failure_is_reported_not_raised():
    r = NicrronReader(Settings(nicrron_api_key="k"), client=fake_client(["nope", "still nope"])).extract(b"img")
    assert not r.ok and r.error

def test_agreement_requires_a_value():
    a = NicrronReader(Settings(nicrron_api_key="k"), client=fake_client([GOOD])).extract(b"i")
    b = NicrronReader(Settings(nicrron_api_key="k"), client=fake_client([GOOD])).extract(b"i")
    agree = compare_readers(a, b)
    assert agree["drug_name"] and not agree["refills"]
