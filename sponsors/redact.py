"""Minimum-necessary telemetry: remove patient and prescriber identifiers before anything leaves for PRISM.

Two layers:
1. By design, traces carry field STATUSES and issue CODES, never field values (see prism.py).
2. As a safety net, every outgoing string passes through mask_text(), which replaces likely identifiers:
   names (title-case pairs, "Dr. X", values after Patient/Name/Prescriber labels), dates, NPI and DEA
   numbers, phone numbers, emails and long digit runs. Over-masking is acceptable; leaking is not.
This is a prototype safeguard, not a certified HIPAA de-identification method (Safe Harbor / Expert Determination).
"""
from __future__ import annotations

import json
import re
from typing import Any

_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?im)^(\s*(?:patient(?: name)?|pt(?: name)?|name|prescriber|physician|provider|doctor|signed|address)\s*[:#.]\s*)(.+)$"), r"\1[REDACTED]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[EMAIL]"),
    (re.compile(r"\b[A-Za-z]{2}\s?\d{7}\b"), "[DEA]"),
    (re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,5}\b"), "[DATE]"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}\b"), "[DATE]"),
    (re.compile(r"\(?\b\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b"), "[PHONE]"),
    (re.compile(r"\b\d{9,}\b"), "[ID]"),
    (re.compile(r"\bDr\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?"), "Dr. [NAME]"),
    (re.compile(r"\b[A-Z][a-z]{1,}\s+[A-Z][a-z]{1,}(?:\s+[A-Z][a-z]{1,})?\b"), "[NAME]"),
]


def mask_text(text: Any) -> str:
    s = "" if text is None else str(text)
    for rx, repl in _RULES:
        s = rx.sub(repl, s)
    return s


def mask_obj(obj: Any) -> Any:
    """Recursively mask every string in a JSON-like structure (keys kept)."""
    if isinstance(obj, str):
        return mask_text(obj)
    if isinstance(obj, list):
        return [mask_obj(x) for x in obj]
    if isinstance(obj, dict):
        return {k: mask_obj(v) for k, v in obj.items()}
    return obj


def field_summary(fields: dict) -> dict:
    """Statuses only: {field: {"status": ..., "present": bool, "codes": [...]}}. No values leave."""
    out = {}
    for k, f in (fields or {}).items():
        if not isinstance(f, dict):
            continue
        out[k] = {"status": f.get("status") or ("present" if f.get("value") else "absent"),
                  "present": bool(f.get("value")),
                  "conflict": bool(f.get("conflict", False))}
        codes = [c.get("rule") for c in f.get("checks", []) if isinstance(c, dict) and not c.get("passed")]
        if codes:
            out[k]["failed_checks"] = codes
    return out


def dumps(obj: Any, limit: int = 20000) -> str:
    return mask_text(json.dumps(obj, default=str))[:limit]
