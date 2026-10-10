"""Block Convey PRISM integration: traces, input guardrail, output evaluator, knowledge base.

How PRISM actually behaves (from prismtrace-sdk 0.5.1 source, read on 10 Oct):
- Guardrail and evaluator RULES are configured in the PRISM dashboard and scoped to an agent_id.
  They run on what we record, so they FLAG; they do not stop our code. Our own local check
  (sponsors/injection.py) is what blocks an order. We say so plainly in the UI and README.
- Trajectories (ordered step lists) get PRISM's automatic evaluation (goal adherence, tool
  compliance, efficiency, safety), fetched later with get_trajectory_evaluation.
- The knowledge base is searchable from code (kb_upload / kb_search).
- The SDK fails open (prints a warning, returns None). Every method here reports whether
  PRISM actually accepted the data, so the UI never shows a trace that didn't land.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import injection, redact
from .config import Settings, settings as default_settings

try:
    from prismtrace import PRISMtrace
except ImportError:  # keep the app runnable without the SDK installed
    PRISMtrace = None  # type: ignore


@dataclass
class Delivery:
    """Did PRISM accept it? Shown in the developer view, never faked."""
    sent: bool
    accepted: bool
    detail: str = ""


@dataclass
class GuardrailResult:
    blocked: bool                                  # decided by the LOCAL check
    findings: list[injection.InjectionFinding]
    safe_text: str                                 # display-only when blocked; do not send to a model
    prism: Delivery = field(default_factory=lambda: Delivery(False, False, "not sent"))
    note: str = "Local check blocks; PRISM guardrail rules flag the recorded trace."


@dataclass
class KBResult:
    supported: bool                  # exact tuple/rule found in a retrieved chunk
    claim: str                       # the sentence we show the pharmacist
    citation: str = ""               # the retrieved text that supports it
    document: str = ""
    score: Optional[float] = None
    source: str = "PRISM knowledge base"
    prism: Delivery = field(default_factory=lambda: Delivery(False, False, "not sent"))
    status: str = "unavailable"       # supported | retrieved | no_match | unavailable


@dataclass
class EvaluationRequest:
    trajectory_id: Optional[str]
    prism: Delivery
    classification: str
    classification_trace: Delivery = field(default_factory=lambda: Delivery(False, False, "not sent"))


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


class Prism:
    def __init__(self, cfg: Settings = default_settings):
        self.cfg = cfg
        self.pt = None
        self.init_error = ""
        if not cfg.prism_enabled:
            self.init_error = "PRISMTRACE_API_KEY / PRISMTRACE_PROJECT_ID not set"
        elif PRISMtrace is None:
            self.init_error = "prismtrace-sdk not installed"
        else:
            try:
                self.pt = PRISMtrace(api_key=cfg.prism_api_key, host=cfg.prism_host, project_id=cfg.prism_project_id)
            except Exception as exc:
                self.init_error = f"PRISM initialization failed ({type(exc).__name__})"

    @property
    def enabled(self) -> bool:
        return self.pt is not None

    # ------------------------------------------------------------------ traces
    def _trace(self, *, agent: str, session_id: str, model: str, input_messages: list, output: str,
               latency_ms: int, tokens_in: int = 0, tokens_out: int = 0, metadata: Optional[dict] = None) -> Delivery:
        if not self.enabled:
            return Delivery(False, False, self.init_error)
        # Minimum necessary: every string leaving for PRISM is masked (names, dates, NPI/DEA, phones, emails).
        input_messages = redact.mask_obj(input_messages)
        output = redact.mask_text(output)
        metadata = {**redact.mask_obj(metadata or {}), "telemetry": "redacted"}
        payload = {
            "project_id": self.cfg.prism_project_id, "model": model, "input_messages": input_messages,
            "output_message": output, "latency_ms": int(latency_ms), "token_count_input": tokens_in,
            "token_count_output": tokens_out, "agent_id": agent, "agent_name": agent,
            "session_id": session_id, "metadata": metadata,
        }
        # Same payload and endpoint as PRISMtrace.trace_llm, but synchronous so we learn whether it landed.
        try:
            resp = self.pt._post_sync("/api/traces", payload)
        except Exception as exc:
            return Delivery(True, False, f"PRISM acknowledgement unavailable ({type(exc).__name__})")
        return Delivery(True, resp is not None, "accepted" if resp is not None else "PRISM did not acknowledge")

    def trace_reader_run(self, doc_id: str, run: Any) -> Delivery:
        """Record one Nicrron extraction call (sponsors.nicrron.ReaderRun)."""
        out = redact.dumps({"ok": run.ok, "fields": redact.field_summary(run.fields),
                            "prescription_count": run.prescription_count,
                            "suspicious_text_count": len(run.suspicious_text or []), "error": run.error})
        return self._trace(
            agent=self.cfg.agent_extractor, session_id=doc_id, model=run.model,
            input_messages=[{"role": "user", "content": f"[fax image {doc_id}] extract fields (prompt {run.prompt_version})"}],
            output=out, latency_ms=run.latency_ms, tokens_in=run.tokens_in, tokens_out=run.tokens_out,
            metadata={"doc_id": doc_id, "run_id": run.run_id, "request_id": run.request_id,
                      "repaired_json": run.repaired, "gateway": "nicrron"},
        )

    # --------------------------------------------------------------- guardrail
    def guardrail(self, doc_id: str, ocr_text: str) -> GuardrailResult:
        """Run BEFORE any model sees the OCR text."""
        t0 = time.monotonic()
        findings = injection.scan(ocr_text)
        result = GuardrailResult(blocked=bool(findings), findings=findings,
                                 safe_text=injection.quarantine(ocr_text, findings))
        verdict = {"blocked": result.blocked, "findings": [{"text": f.text, "reason": f.reason} for f in findings]}
        result.prism = self._trace(
            agent=self.cfg.agent_guardrail, session_id=doc_id, model="local-injection-check",
            input_messages=[{"role": "user", "content": redact.mask_text(ocr_text)[:20000]}],
            output=json.dumps(verdict), latency_ms=int((time.monotonic() - t0) * 1000),
            metadata={"doc_id": doc_id, "stage": "input_guardrail"},
        )
        return result

    # --------------------------------------------------------------- evaluator
    def request_evaluation(self, doc_id: str, *, classification: str, facts: dict, model: str = "",
                           stage_ms: Optional[dict] = None) -> EvaluationRequest:
        """Submit a classification trace and trajectory for configured PRISM rules.

        Configure and verify the output rubric in the dashboard. These acknowledgements
        do not establish that an evaluator ran or passed; evaluation never approves a draft.
        """
        if not self.enabled:
            return EvaluationRequest(None, Delivery(False, False, self.init_error), classification)
        if classification not in {"complete_for_review", "needs_clarification", "extraction_failed", "blocked"}:
            raise ValueError("unknown review classification")
        facts = {**facts, "fields": redact.field_summary(facts.get("fields", {})),
                 "checks": {k: len(v) for k, v in (facts.get("checks") or {}).items()}}
        facts = redact.mask_obj(facts)
        # Dashboard output evaluators attach to traces; a trajectory submission alone
        # does not prove that the classification-specific evaluator ran.
        classification_trace = self._trace(
            agent=self.cfg.agent_reviewer, session_id=doc_id, model=model or "review-classifier",
            input_messages=[{"role": "user", "content": json.dumps(facts)}],
            output=json.dumps({"classification": classification}), latency_ms=0,
            metadata={"doc_id": doc_id, "stage": "output_classification"},
        )
        ms = stage_ms or {}
        steps = [
            {"step_type": "tool_call", "label": "ocr", "tool_name": "ocr",
             "input_summary": f"fax {doc_id}", "output_summary": f"{facts.get('ocr_lines', '?')} lines read",
             "duration_ms": ms.get("ocr", 0)},
            {"step_type": "tool_call", "label": "input_guardrail", "tool_name": "injection_check",
             "output_summary": json.dumps(facts.get("guardrail", {}))[:2000], "duration_ms": ms.get("guardrail", 0)},
            {"step_type": "tool_call", "label": "extraction", "tool_name": "nicrron_reader",
             "output_summary": json.dumps(facts.get("fields", {}))[:4000], "duration_ms": ms.get("extract", 0)},
            {"step_type": "tool_call", "label": "deterministic_checks", "tool_name": "checks",
             "output_summary": json.dumps(facts.get("checks", {}))[:4000], "duration_ms": ms.get("checks", 0)},
            {"step_type": "tool_call", "label": "knowledge_base_search", "tool_name": "knowledge_base_search",
             "output_summary": json.dumps(facts.get("kb", {}))[:2000], "duration_ms": ms.get("kb", 0)},
            {"step_type": "final_answer", "label": "classification",
             "output_summary": f"classification={classification}. Rule: complete_for_review only if every required field "
                               f"has evidence and no blocking finding; missing values stay null; units match the source.",
             "duration_ms": 0},
        ]
        steps = redact.mask_obj(steps)
        resp = self.pt.submit_trajectory(steps, agent_name=self.cfg.agent_reviewer, agent_id=self.cfg.agent_reviewer,
                                         conversation_id=doc_id, model=model or None,
                                         final_status="success" if classification != "extraction_failed" else "error")
        tid = resp.get("id") if isinstance(resp, dict) else None
        return EvaluationRequest(tid, Delivery(True, tid is not None, "accepted" if tid else "PRISM did not acknowledge"),
                                 classification, classification_trace)

    def evaluation_result(self, trajectory_id: str) -> Optional[dict]:
        """None means pending/unavailable: show 'evaluation pending', never a pass."""
        if not self.enabled or not trajectory_id:
            return None
        return self.pt.get_trajectory_evaluation(trajectory_id)

    # ----------------------------------------------------------- knowledge base
    def kb_bootstrap(self, folder: Path) -> dict[str, str]:
        """Upload reference/*.md once (skips names already present). Returns name -> status."""
        if not self.enabled:
            return {"error": self.init_error}
        if not folder.is_dir() or not list(folder.glob("*.md")):
            return {"error": "reference folder missing or contains no Markdown documents"}
        existing = {d.get("name") or d.get("filename") for d in (self.pt.kb_list_documents() or [])}
        status = {}
        for p in sorted(folder.glob("*.md")):
            if p.name in existing:
                status[p.name] = "already present"
                continue
            doc = self.pt.kb_upload(filename=p.name, content=p.read_text(), content_type="text/markdown",
                                    description=f"SigCheck reference: {p.stem}")
            status[p.name] = f"uploaded ({doc.get('chunk_count')} chunks)" if doc else "upload failed"
        return status

    def _kb_search(self, query: str, limit: int) -> tuple[list[dict], Delivery]:
        """Use the pinned SDK's transport to distinguish an empty search from an outage.

        SDK kb_search collapses both cases to [], losing acknowledgement information.
        """
        if not self.enabled:
            return [], Delivery(False, False, self.init_error)
        try:
            response = self.pt._post_sync("/api/knowledge-base/search", {
                "query": query, "project_id": self.cfg.prism_project_id, "limit": limit,
            })
        except Exception as exc:
            return [], Delivery(True, False, f"KB unavailable ({type(exc).__name__})")
        if not isinstance(response, dict) or not isinstance(response.get("results"), list):
            return [], Delivery(True, False, "KB did not return valid search results")
        chunks = response["results"]
        if any(not isinstance(c, dict) or not isinstance(c.get("content", ""), str) for c in chunks):
            return [], Delivery(True, False, "KB returned malformed chunks")
        return chunks, Delivery(True, True, f"{len(chunks)} chunks retrieved")

    def kb_verify_catalog(self, drug: str, strength: str, form: str) -> KBResult:
        """Retrieve from the PRISM KB, then verify the EXACT row ourselves. A fuzzy hit is not support."""
        row = f"{_norm(drug)} | {_norm(strength)} | {_norm(form)}"
        claim_yes = f"{drug} {strength} {form} appears in the reference catalogue"
        claim_no = f"No exact match for {drug} {strength} {form} in the retrieved catalogue passages; catalogue absence and medication safety are not established"
        chunks, delivery = self._kb_search(f"{drug} {strength} {form}", limit=5)
        if not delivery.accepted:
            return KBResult(False, "Catalogue verification unavailable", source="PRISM KB unavailable", prism=delivery)
        for c in chunks:
            name = str(c.get("doc_name") or c.get("document_name") or c.get("filename") or "")
            # Other team KB documents must not be allowed to impersonate reference data.
            if name != "catalog.md":
                continue
            # PRISM returns each chunk flattened to one line, so match the exact row between pipe boundaries.
            flat = re.sub(r"\s+", " ", (c.get("content") or "").lower())
            m = re.search(r"\|\s*" + re.escape(_norm(drug)) + r"\s*\|\s*" + re.escape(_norm(strength)) + r"\s*\|\s*"
                          + re.escape(_norm(form)) + r"\s*\|([^|]*)\|([^|]*)\|", flat)
            if m:
                citation = f"{_norm(drug)} | {_norm(strength)} | {_norm(form)} | {m.group(1).strip()} | {m.group(2).strip()}".rstrip(" |")
                return KBResult(True, claim_yes, citation=citation, document=name,
                                score=c.get("relevance_score", c.get("score")), prism=delivery, status="supported")
        return KBResult(False, claim_no, prism=delivery, status="no_match")

    def kb_policy(self, question: str) -> KBResult:
        """Fetch the intake-policy passage relevant to a missing/unclear field, shown with its source."""
        chunks, delivery = self._kb_search(question, limit=3)
        if not delivery.accepted:
            return KBResult(False, "Policy retrieval unavailable", source="PRISM KB unavailable", prism=delivery)
        policy = [c for c in chunks if (c.get("document_name") or c.get("filename")) == "intake_policy.md"
                  and c.get("content", "").strip()]
        if not policy:
            return KBResult(False, "No intake-policy passage retrieved", prism=delivery, status="no_match")
        top = policy[0]
        # Retrieval supplies evidence for review, not an entailment verdict on an arbitrary question.
        return KBResult(False, "Retrieved intake-policy passage; relevance requires review", citation=top["content"],
                        document=str(top.get("doc_name") or top.get("document_name") or top.get("filename") or ""),
                        score=top.get("score"), prism=delivery, status="retrieved")

    def flush(self) -> None:
        if self.pt is not None:
            self.pt.flush()
