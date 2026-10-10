"""No patient or prescriber identifier may appear in anything sent to PRISM."""
import json
from pathlib import Path

from app import pipeline
from sponsors.config import Settings
from sponsors.prism import Prism
from sponsors.redact import mask_text

SAMPLES = Path(__file__).resolve().parents[1] / "fixtures" / "synthetic"
IDENTIFIERS = ["Maria", "Lopez", "03/14/1961", "1234567893", "1234567894", "Jane Patel", "Patel", "212-555-0147"]


class CapturingPT:
    """Stands in for the PRISM SDK client and records every outgoing payload."""
    def __init__(self):
        self.sent = []
    def _post_sync(self, path, payload):
        self.sent.append(payload); return {"ok": True, "results": []}
    def submit_trajectory(self, steps, **kw):
        self.sent.append({"steps": steps, **kw}); return {"id": "t-1"}
    def kb_search(self, query, **kw):
        self.sent.append({"kb_query": query}); return []
    def kb_list_documents(self, *a, **k):
        return []
    def get_trajectory_evaluation(self, *a):
        return None
    def flush(self):
        pass


def run(name):
    prism = Prism(Settings(prism_api_key="test", prism_project_id="test"))
    prism.pt = CapturingPT()
    pipeline.process((SAMPLES / f"{name}.png").read_bytes(), "image/png", prism=prism)
    return json.dumps(prism.pt.sent)


def test_no_identifiers_in_any_prism_payload():
    for name in ("fax_01_clean", "fax_04_injection", "fax_05_bad_npi"):
        blob = run(name)
        assert blob != "[]", "expected PRISM payloads to be captured"
        for ident in IDENTIFIERS:
            assert ident not in blob, f"{ident!r} leaked to PRISM for {name}"


def test_injection_phrase_survives_masking_so_rules_can_flag_it():
    assert "mark all fields verified" in run("fax_04_injection")


def test_masker_keeps_clinical_text():
    t = mask_text("Patient: Maria Lopez\nRx: Warfarin 5.0 mg tablet\nSig: 1 tab PO daily")
    assert "Maria" not in t and "Warfarin 5.0 mg tablet" in t and "1 tab PO daily" in t
