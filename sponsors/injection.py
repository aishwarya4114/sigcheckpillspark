"""Local input guardrail: find text on the fax that is aimed at the SOFTWARE, not the pharmacist.

PRISM's guardrail rules run on recorded traces, so they flag after the fact. This local
check runs BEFORE any model sees the text and is what actually blocks the order.
Ordinary clinical directions ("take one tablet daily", "ignore previous Rx dated 9/1",
"verify with patient") must NOT trigger it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Each pattern targets instructions to an AI/automation, not clinical wording.
_PATTERNS: list[tuple[str, str]] = [
    (r"\b(note|message|instruction)s?\s+(to|for)\s+(the\s+)?(ai|assistant|model|system|bot|llm|software|ocr)\b", "addresses the AI directly"),
    (r"\b(ai|assistant|model|system|bot|llm|chatgpt|claude|gpt)\s*[:,]\s*(please\s+)?(mark|set|approve|ignore|skip|treat|override|return|output)\b", "addresses the AI directly"),
    (r"\bignore\s+(all\s+|any\s+)?(previous|prior|above|earlier|the\s+)?\s*(instructions|rules|checks|warnings|flags|validation|missing\s+fields?)\b", "asks to ignore instructions or checks"),
    (r"\bdisregard\s+(all\s+|any\s+)?(previous|prior|above)?\s*(instructions|rules|checks|warnings|flags)\b", "asks to ignore instructions or checks"),
    (r"\b(mark|set|flag|treat)\s+(all|every|each|this|these|the)?\s*(fields?|items?|order|prescriptions?|values?)?\s*(as\s+)?(verified|approved|valid|correct|complete|green|pass(ed)?)\b", "asks to mark fields verified/approved"),
    (r"\b(auto[- ]?approve|approve\s+(automatically|without\s+review)|skip\s+(pharmacist\s+)?review|bypass\s+(the\s+)?(review|checks?|validation))\b", "asks to skip review"),
    (r"\b(system\s+prompt|developer\s+message|you\s+are\s+(now\s+)?(an?|the)\s+(ai|assistant|model))\b", "prompt-injection phrasing"),
    (r"\b(respond|reply|output|return)\s+(only\s+)?(with\s+)?(json|\{)", "tries to control the output format"),
]
_COMPILED = [(re.compile(p, re.IGNORECASE), why) for p, why in _PATTERNS]


@dataclass
class InjectionFinding:
    text: str        # the matched span, verbatim
    reason: str      # plain-language reason shown to the pharmacist
    start: int
    end: int


def scan(text: str) -> list[InjectionFinding]:
    """Return every suspected instruction-to-software found in OCR text (empty list = clean)."""
    findings: list[InjectionFinding] = []
    for rx, why in _COMPILED:
        for m in rx.finditer(text or ""):
            findings.append(InjectionFinding(text=m.group(0), reason=why, start=m.start(), end=m.end()))
    # de-duplicate overlapping matches, keep the earliest/longest
    findings.sort(key=lambda f: (f.start, -(f.end - f.start)))
    out: list[InjectionFinding] = []
    for f in findings:
        if not out or f.start >= out[-1].end:
            out.append(f)
    return out


def quarantine(text: str, findings: list[InjectionFinding]) -> str:
    """Annotate evidence for display. Wrapping does NOT make instructions safe for a model.

    Callers must stop extraction when findings exist, keeping the original for review.
    """
    if not findings:
        return text
    parts, last = [], 0
    for f in findings:
        parts.append(text[last:f.start])
        parts.append(f"[QUARANTINED-INSTRUCTION: {f.text}]")
        last = f.end
    parts.append(text[last:])
    return "".join(parts)
