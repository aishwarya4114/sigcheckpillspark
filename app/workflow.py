"""Operational workflow: turns a document record into the pharmacist's work.

Three genuinely different kinds of issue:
- verify_transcription: the software is unsure what the fax SAYS. Resolved by confirming or correcting
  against the fax (or a clearer scan).
- prescriber_clarification: the fax itself is missing, ambiguous or unsafe. Looking at the fax again can't
  fix it, so "confirm matches fax" does NOT resolve it. Resolved only by recording the prescriber's answer.
- blocked: the document can't continue through normal intake.

Everything here is DERIVED from the record on every read, so states can't drift out of sync.
"""
from __future__ import annotations

from datetime import datetime, timezone

# check rule -> (category, issue code, resolution)
RULES = {
    "ocr.legible": ("verify_transcription", "LOW_RECOGNIZER_SCORE", "confirm_or_correct"),
    "field.single_reading": ("verify_transcription", "CONFLICTING_READINGS", "confirm_or_correct"),
    "date.valid": ("verify_transcription", "INVALID_DATE_READING", "confirm_or_correct"),
    "sig.unambiguous_chars": ("verify_transcription", "POSSIBLE_CHARACTER_MISREAD", "confirm_or_correct"),
    "npi.check_digit": ("verify_transcription", "NPI_CHECK_DIGIT_FAILED", "confirm_or_correct"),
    "name.format": ("verify_transcription", "UNEXPECTED_CHARACTERS", "confirm_or_correct"),
    "quantity.whole_number": ("verify_transcription", "QUANTITY_NOT_A_NUMBER", "confirm_or_correct"),
    "refills.whole_number": ("verify_transcription", "REFILLS_NOT_A_NUMBER", "confirm_or_correct"),
    "field.present": ("prescriber_clarification", "MISSING_REQUIRED_VALUE", "contact_prescriber"),
    "refills.not_stated": ("prescriber_clarification", "REFILLS_NOT_STATED", "contact_prescriber"),
    "strength.trailing_zero": ("prescriber_clarification", "AMBIGUOUS_TRAILING_ZERO", "contact_prescriber"),
    "strength.leading_zero": ("prescriber_clarification", "MISSING_LEADING_ZERO", "contact_prescriber"),
    "strength.units_u": ("prescriber_clarification", "UNITS_ABBREVIATION_U", "contact_prescriber"),
    "strength.catalogued": ("prescriber_clarification", "STRENGTH_NOT_IN_CATALOGUE", "contact_prescriber"),
    "drug.catalogue": ("prescriber_clarification", "DRUG_NOT_IN_CATALOGUE", "contact_prescriber"),
    "drug.lookalike": ("prescriber_clarification", "LOOK_ALIKE_DRUG", "contact_prescriber"),
    "sig.not_as_directed": ("prescriber_clarification", "DIRECTIONS_AS_DIRECTED", "contact_prescriber"),
    "sig.dose_and_frequency": ("prescriber_clarification", "DIRECTIONS_INCOMPLETE", "contact_prescriber"),
    "dea.check_digit": ("prescriber_clarification", "DEA_CHECK_DIGIT_FAILED", "contact_prescriber"),
    "dea.initial": ("prescriber_clarification", "DEA_PRESCRIBER_MISMATCH", "contact_prescriber"),
    "date.not_future": ("prescriber_clarification", "DATE_IN_FUTURE", "contact_prescriber"),
    "rx.not_expired": ("prescriber_clarification", "PRESCRIPTION_TOO_OLD", "contact_prescriber"),
    "dob.plausible": ("prescriber_clarification", "IMPLAUSIBLE_DOB", "contact_prescriber"),
    "refills.max": ("prescriber_clarification", "TOO_MANY_REFILLS", "contact_prescriber"),
}
SOURCES = {"ocr": "Apple Vision", "npi": "check-digit formula", "dea": "check-digit formula",
           "drug": "demonstration catalogue", "strength": "intake policy + catalogue", "sig": "intake policy",
           "field": "intake policy", "refills": "intake policy", "date": "intake policy", "rx": "intake policy",
           "dob": "intake policy", "name": "format check", "quantity": "format check"}

# Clarifications a recorded prescriber confirmation may resolve without changing the value.
NOTE_RESOLVABLE = {"LOOK_ALIKE_DRUG", "DRUG_NOT_IN_CATALOGUE", "STRENGTH_NOT_IN_CATALOGUE", "PRESCRIPTION_TOO_OLD",
                   "DEA_CHECK_DIGIT_FAILED", "DEA_PRESCRIBER_MISMATCH", "IMPLAUSIBLE_DOB", "DATE_IN_FUTURE", "TOO_MANY_REFILLS"}

STATUS_LABEL = {
    "new": "New",
    "needs_verification": "Needs verification",
    "needs_clarification": "Needs prescriber",
    "waiting_for_prescriber": "Waiting for response",
    "ready_for_approval": "Ready to approve",
    "approved": "Approved",
    "blocked": "Blocked",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def issue_key(field: str | None, code: str) -> str:
    return f"{field or 'document'}:{code}"


def issues(rec: dict) -> list[dict]:
    out: list[dict] = []
    res = rec.get("resolutions", {})
    review = rec.get("review", {})
    # document blockers
    if rec.get("status") == "blocked":
        finds = rec.get("findings", []) or [{"reason": "Document blocked."}]
        out.append({"key": issue_key(None, "INSTRUCTIONS_TO_SOFTWARE"), "code": "INSTRUCTIONS_TO_SOFTWARE",
                    "category": "blocked", "resolution": "manual_review_or_new_document", "field": None,
                    "severity": "blocking", "source": "local guardrail + PRISM trace",
                    "message": "This fax contains text that tries to instruct the software. It was stopped before any automated reading.",
                    "details": [f.get("reason", "") for f in finds],
                    "next_step": "Quarantine it, review the original manually, or upload a corrected document.",
                    "state": "quarantined" if rec.get("quarantined") else "open", "box": finds[0].get("box")})
        return out
    if rec.get("status") == "processing_failed":
        return [{"key": issue_key(None, "EXTRACTION_FAILED"), "code": "EXTRACTION_FAILED", "category": "blocked",
                 "resolution": "new_document", "field": None, "severity": "blocking", "source": "reader",
                 "message": "Extraction failed. Nothing was guessed.", "next_step": "Upload a clearer copy of the fax.",
                 "state": "open", "box": None}]
    if (rec.get("prescription_count") or 1) > 1:
        out.append({"key": issue_key(None, "MULTIPLE_PRESCRIPTIONS"), "code": "MULTIPLE_PRESCRIPTIONS",
                    "category": "blocked", "resolution": "split_or_manual", "field": None, "severity": "blocking",
                    "source": "reader", "message": f"This page contains {rec['prescription_count']} prescriptions.",
                    "next_step": "Review the original manually; enter each prescription separately.",
                    "state": "open", "box": None})
    for name, f in (rec.get("fields") or {}).items():
        for c in f.get("checks", []):
            if c.get("passed") or c["rule"] not in RULES:
                continue
            cat, code, resolution = RULES[c["rule"]]
            key = issue_key(name, code)
            r = res.get(key) or {}
            if cat == "verify_transcription":
                state = "resolved" if review.get(name) else "open"
            else:
                state = r.get("state", "open")
            out.append({"key": key, "code": code, "category": cat, "resolution": resolution, "field": name,
                        "severity": "blocking" if f.get("required") or cat == "prescriber_clarification" else "advisory",
                        "source": SOURCES.get(c["rule"].split(".")[0], "check"), "message": c.get("detail", ""),
                        "label": c.get("label", ""), "next_step": c.get("next_step", ""), "state": state,
                        "resolved_by": r.get("by"), "resolved_at": r.get("at"), "note": r.get("note"),
                        "box": f.get("box")})
    return out


def compute(rec: dict) -> dict:
    iss = issues(rec)
    open_ = [i for i in iss if i["state"] in ("open", "quarantined")]
    waiting = [i for i in iss if i["state"] == "waiting"]
    fields = rec.get("fields") or {}
    required = [k for k, f in fields.items() if f.get("required")]
    reviewed = [k for k in required if rec.get("review", {}).get(k) and str(fields[k].get("value") or "").strip()]
    unreviewed = [k for k in required if k not in reviewed]
    open_tx = [i for i in open_ if i["category"] == "verify_transcription"]
    open_cl = [i for i in open_ if i["category"] == "prescriber_clarification"]
    blockers = [i for i in iss if i["category"] == "blocked"]

    if blockers or rec.get("status") in ("blocked", "processing_failed"):
        status = "blocked"
        nxt = "This document can't continue. Quarantine it, review the original manually, or upload a corrected document."
    elif rec.get("status") == "approved" and rec.get("approved", {}).get("revision") == rec.get("revision"):
        status = "approved"
        nxt = "Approved draft order. Export it for pharmacy entry, then return to the queue."
    elif waiting and not open_cl:
        status = "waiting_for_prescriber"
        nxt = f"Waiting for the prescriber on {len(waiting)} item{'s' if len(waiting) != 1 else ''}. Record their answer or attach the new fax when it arrives."
    elif open_cl:
        status = "needs_clarification"
        nxt = f"Ask the prescriber about {len(open_cl)} item{'s' if len(open_cl) != 1 else ''}. Re-reading the fax can't resolve {'these' if len(open_cl) != 1 else 'this'}."
    elif not rec.get("opened_at") and rec.get("status") != "approved":
        status = "new"
        nxt = "Open the case to start review."
    elif open_tx or unreviewed:
        status = "needs_verification"
        n = len(unreviewed)
        nxt = (f"Verify {len(open_tx)} flagged transcription{'s' if len(open_tx) != 1 else ''} against the fax, then confirm the remaining required fields."
               if open_tx else f"Confirm {n} required field{'s' if n != 1 else ''} against the fax.")
    else:
        status = "ready_for_approval"
        nxt = "Everything is resolved. Review the order summary and approve the draft order."
    # one action per open issue, plus one "confirm the remaining fields" step if any remain unreviewed
    remaining = [k for k in unreviewed if not any(i["field"] == k for i in open_)]
    actions = len(open_) + (1 if remaining and status not in ("approved", "blocked") else 0)
    return {"status": status, "label": STATUS_LABEL[status], "next_action": nxt, "issues": iss,
            "counts": {"open_transcription": len(open_tx), "open_clarification": len(open_cl), "waiting": len(waiting),
                       "blockers": len(blockers), "required": len(required), "reviewed": len(reviewed)},
            "actions": actions,
            "ready": status == "ready_for_approval"}


def source_kind(src: str) -> str:
    s = (src or "").lower()
    if "rescan" in s:
        return "Rescan"
    if "phone" in s:
        return "Phone capture"
    return "Fax"
