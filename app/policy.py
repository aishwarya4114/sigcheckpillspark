"""Review policy: the few settings a pharmacy may tune. Safety rules are NOT settings and cannot be switched off."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "data" / "policy.json"
VERSION = "2026-10-10"
DEFAULTS = {"version": VERSION, "recognizer_threshold": 0.60, "max_rx_age_days": 365}
LIMITS = {"recognizer_threshold": (0.60, 0.95), "max_rx_age_days": (7, 365)}

# Shown read-only in the Rules panel. Each is enforced in code; none has an off switch.
LOCKED_RULES = [
    "Missing required values remain blank (never guessed)",
    "Trailing zeros and missing leading zeros require review",
    "Look-alike drug names require review",
    "NPI check-digit validation",
    "DEA validation for controlled substances",
    "Unclear directions require clarification",
    "Instructions aimed at the software block processing",
    "Pharmacist approval required before export",
]
DATA_SOURCES = [
    "Synthetic intake policy (reference/intake_policy.md)",
    "Curated demonstration catalogue (reference/catalog.md)",
    "Mathematical NPI / DEA check-digit formulas",
    "Apple Vision recognizer score (coarse: often 0.3 / 0.5 / 1.0)",
]


def current() -> dict:
    if PATH.exists():
        try:
            return {**DEFAULTS, **json.loads(PATH.read_text()), "version": VERSION}
        except (ValueError, OSError):
            pass
    return dict(DEFAULTS)


def validate(settings: dict) -> dict:
    out = current()
    for k, (lo, hi) in LIMITS.items():
        if k in settings and settings[k] is not None:
            v = float(settings[k])
            if not lo <= v <= hi:
                raise ValueError(f"{k} must be between {lo} and {hi}")
            out[k] = round(v, 2) if k == "recognizer_threshold" else int(v)
    return out


def save(p: dict) -> dict:
    snap = {**p, "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(snap, indent=1))
    return snap
