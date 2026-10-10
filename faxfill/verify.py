"""Verifier: turns three reads of each page into field verdicts, an order status and a danger score.

    python -m faxfill.verify rx_001 [rx_002 ...]     # or --all; writes data/verdicts/<fax>.json

Inputs: reads A/B (models) and C (regex), Tesseract words + boxes, the page images and reference data
through faxfill.lookup. It NEVER reads answer keys (data/faxes/*.json); only faxfill/eval.py does.
"""
import datetime as dt
import json
import re
import sys
from pathlib import Path

from rapidfuzz import fuzz

import time

from faxfill import faxchecks as fc
from faxfill import ocr
from faxfill import prism
from faxfill.ids import dea_valid, npi_valid
from faxfill.lookup import LocalLookup
from faxfill.readers import FIELDS, MODELS, read_pages

ROOT = Path(__file__).parent
VERDICTS = ROOT / "data" / "verdicts"
WEIGHTS = json.load(open(ROOT / "danger_weights.json"))["weights"]
VERIFIER_VERSION = "verifier-v1"

CRITICAL = {"drug_name", "strength", "directions", "quantity", "refills"}
REQUIRED = ["patient_name", "patient_dob", "drug_name", "strength", "dosage_form", "directions", "quantity",
            "refills", "prescriber_name", "prescriber_npi", "date_written"]
OPTIONAL = ["prescriber_dea", "substitution"]
INFO = ["notes"]
EDGE_FIELDS = ["drug_name", "strength", "patient_name", "patient_dob", "prescriber_name", "prescriber_npi",
               "prescriber_dea", "directions", "quantity"]
NOISE_FIELDS = ["drug_name", "strength", "directions"]
DIGIT_CONFUSIONS = {"0": "68", "1": "7", "3": "8", "4": "9", "5": "6", "6": "58", "7": "1", "8": "063", "9": "4"}
SCHEDULED = {"C-I", "C-II", "C-III", "C-IV", "C-V"}


# ---------------------------------------------------------------- normalisation
def _ws(s):
    return " ".join(str(s).split())


def parse_strength(s):
    m = re.search(r"(\d*\.?\d+)\s*(mg|mcg|µg|g|ml|units?|u|iu)\b", str(s or ""), re.I)
    if not m:
        return None
    unit = m.group(2).lower().replace("µg", "mcg")
    unit = {"u": "unit", "iu": "unit", "units": "unit"}.get(unit, unit)
    return float(m.group(1)), unit


def parse_date(s):
    s = str(s or "").strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


FORM_MAP = {"tab": "tablet", "tabs": "tablet", "tablet": "tablet", "tablets": "tablet",
            "cap": "capsule", "caps": "capsule", "capsule": "capsule", "capsules": "capsule",
            "solution": "solution", "soln": "solution"}


def norm(field, v):
    """Comparable form of a value. Notation hazards (5.0, .5, U) are detected on RAW values before this."""
    if v is None:
        return None
    s = _ws(v).strip(" .;:")
    if not s:
        return None
    low = s.lower()
    if field == "strength":
        p = parse_strength(s)
        return f"{p[0]:g} {p[1]}" if p else low
    if field == "dosage_form":
        return FORM_MAP.get(low.split()[0], low)
    if field == "quantity":
        m = re.search(r"#?\s*(\d+)", s)
        return m.group(1).lstrip("0") or "0" if m else low
    if field == "refills":
        if low in ("none", "zero", "no", "nr", "no refills"):
            return "0"
        m = re.fullmatch(r"\d+", low.split()[0])
        return str(int(m.group(0))) if m else low
    if field in ("patient_dob", "date_written"):
        d = parse_date(s)
        return d.isoformat() if d else low
    if field == "prescriber_npi":
        return re.sub(r"\D", "", s)
    if field == "prescriber_dea":
        return re.sub(r"[^A-Z0-9]", "", s.upper())
    if field in ("patient_name", "prescriber_name"):
        return " ".join(re.sub(r"[.,]", " ", low).split())
    if field == "substitution":
        return low.replace(" ", "_")
    return low


# ---------------------------------------------------------------- directions
FREQ_PATTERNS = [
    (r"\b(once weekly|every week|weekly|q ?week|qwk)\b", ("weekly", 1)),
    (r"\b(four times (a )?daily|qid)\b", ("daily", 4)),
    (r"\b(three times (a )?daily|tid)\b", ("daily", 3)),
    (r"\b(twice (a )?daily|two times (a )?daily|bid)\b", ("daily", 2)),
    (r"\bq ?(\d{1,2}) ?h(ou)?rs?\b|\bevery (\d{1,2}) hours\b", ("hours", None)),
    (r"\b(once daily|daily|every day|qd|once a day|every morning|qam|at bedtime|qhs|nightly|every evening|qpm)\b",
     ("daily", 1)),
]
WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "half": 0.5, "1/2": 0.5}


def parse_sig(text):
    """{n, per_day, weekly, days, prn} or None if dose count or frequency can't be read."""
    if not text:
        return None
    t = text.lower()
    m = re.search(r"(\d+(?:\.\d+)?|one|two|three|four|half|1/2)\s*(tab|tablet|cap|capsule|ml|unit)s?\b", t)
    n = None
    if m:
        n = float(WORD_NUM.get(m.group(1), m.group(1))) if m.group(1) not in WORD_NUM else WORD_NUM[m.group(1)]
    freq = None
    for pat, (kind, k) in FREQ_PATTERNS:
        fm = re.search(pat, t)
        if fm:
            if kind == "hours":
                hrs = int(next(g for g in fm.groups() if g and g.isdigit()))
                freq = ("daily", 24 / hrs)
            else:
                freq = (kind, k)
            break
    days = re.search(r"(?:for|x)\s*(\d+)\s*days?", t)
    if n is None or freq is None:
        return None
    return {"n": n, "per_day": freq[1] if freq[0] == "daily" else None, "weekly": freq[0] == "weekly",
            "days": int(days.group(1)) if days else None, "prn": bool(re.search(r"\b(prn|as needed)\b", t))}


NUM_WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
             "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
             "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
             "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100}


def words_to_int(s):
    total, cur = 0, 0
    for w in re.split(r"[\s\-]+", s.lower()):
        if w not in NUM_WORDS:
            continue
        v = NUM_WORDS[w]
        if v == 100:
            cur = max(cur, 1) * 100
        else:
            cur += v
    total += cur
    return total or None


# ---------------------------------------------------------------- evidence boxes
def _box_norm(s):
    return re.sub(r"[^a-z0-9./#]+", " ", str(s).lower()).strip()


LABELS = {"refills": r"refi", "quantity": r"disp", "directions": r"sig", "patient_name": r"patient",
          "patient_dob": r"dob", "date_written": r"^\W*date", "prescriber_npi": r"np[i1le]", "prescriber_dea": r"dea",
          "strength": r"^\W*r\s?x\b", "dosage_form": r"^\W*r\s?x\b", "notes": r"notes"}


STRICT_LABEL_FIELDS = {"refills"}   # a lone digit only counts on its own labelled line


def find_box(value, ocr_page, min_score=85, field=None):
    """Box of the OCR words that best match `value` (consecutive words on one line). None if no match.

    If the field has a printed label ("Refills:", "Disp:", ...), lines carrying that label are searched first,
    and a short refills value (e.g. "1") may ONLY match on its labelled line: otherwise the "1" in
    "Take 1 tablet" would be reported as the refills location."""
    if value is None or ocr_page is None:
        return None
    target = _box_norm(value)
    if not target:
        return None
    lines_by_key = {}
    for w in ocr_page["words"]:
        lines_by_key.setdefault(w["line_key"], []).append(w)
    label = LABELS.get(field)
    if label:
        labelled = {k: ws for k, ws in lines_by_key.items()
                    if re.search(label, " ".join(w["text"] for w in sorted(ws, key=lambda w: w["box_deskewed"][0])), re.I)}
        strict = field in STRICT_LABEL_FIELDS and len(target) <= 4
        if labelled:
            hit = _best_window(target, labelled, min_score)
            if hit or strict:
                return hit
        elif strict:
            return None
    return _best_window(target, lines_by_key, min_score)


def _best_window(target, by_line, min_score):
    best, best_score = None, 0
    n_target = len(target.split())
    for ws in by_line.values():
        ws = sorted(ws, key=lambda w: w["box_deskewed"][0])
        for i in range(len(ws)):
            for j in range(i + 1, min(len(ws), i + n_target + 3) + 1):
                cand = _box_norm(" ".join(w["text"] for w in ws[i:j]))
                if not cand:
                    continue
                score = fuzz.ratio(target, cand)
                if score > best_score:
                    best_score, best = score, ws[i:j]
    threshold = 92 if len(target) <= 4 else min_score
    if best is None or best_score < threshold:
        return None
    return [min(w["box"][0] for w in best), min(w["box"][1] for w in best),
            max(w["box"][2] for w in best), max(w["box"][3] for w in best)]


# ---------------------------------------------------------------- field verdict building
class Field:
    def __init__(self, name):
        self.name = name
        self.value = None
        self.agreed = False
        self.status = None
        self.codes, self.reasons, self.alternatives = [], [], []
        self.checks_passed, self.checks_failed, self.notes = [], [], []
        self.evidence_box = None
        self.reads = {}
        self.c_only = False

    def flag(self, code, reason, evidence=None):
        if code not in self.codes:
            self.codes.append(code)
            self.reasons.append(reason)
            self.checks_failed.append({"check": code, "evidence": evidence})

    def ok(self, check, evidence=None):
        self.checks_passed.append({"check": check, "evidence": evidence})

    def alt(self, v):
        if v is not None and v not in self.alternatives:
            self.alternatives.append(v)

    def out(self):
        return {"value": self.value, "status": self.status, "codes": self.codes, "reasons": self.reasons,
                "alternatives": self.alternatives, "evidence_box": self.evidence_box,
                "evidence_note": None if self.evidence_box else "location not found",
                "checks_passed": self.checks_passed, "checks_failed": self.checks_failed, "notes": self.notes,
                "reads": self.reads}


def reconcile(name, a, b, c):
    f = Field(name)
    f.reads = {"A": a, "B": b, "C": c}
    na, nb, nc = norm(name, a), norm(name, b), norm(name, c)
    if na is None and nb is None:
        f.value = None
        if c is not None:
            f.alt(c)
        return f
    if (na is None) != (nb is None):
        who, other = ("A", b) if na is None else ("B", a)
        f.flag("READER_ABSTAINED", f"Reader {who} could not read the {name.replace('_', ' ')}; the other reader gave "
                                   f"'{other}'. A value one reader could not see is not taken.")
        f.alt(a)
        f.alt(b)
        f.alt(c)
        return f
    if na != nb:
        f.flag("READER_DISAGREEMENT", f"Readers disagree: A read '{a}', B read '{b}'.")
        f.alt(a)
        f.alt(b)
        f.alt(c)
        return f
    f.value, f.agreed = a, True
    if nc is not None and nc != na:
        if name in CRITICAL:
            f.flag("OCR_DISAGREEMENT", f"Both models read '{a}' but the OCR text reads '{c}'.")
            f.alt(c)
            f.c_only = True
        else:
            f.notes.append(f"OCR text reads '{c}' (models agree on '{a}'); not a critical field.")
    elif nc is not None:
        f.ok("ocr_text_agrees")
    return f


# ---------------------------------------------------------------- per-page verification
def verify_page(reads, ocr_page, page_image, lookup, page_no=1, today=None):
    """reads: {"A": fields|None, "B": fields|None, "C": fields}. Returns {fields: {...}, page_flags: [...]}"""
    today = today or dt.date.today()
    A, B, C = (reads.get(k) or {} for k in "ABC")
    F = {name: reconcile(name, A.get(name), B.get(name), C.get(name)) for name in FIELDS}
    page_flags = []

    # evidence boxes: match values (or, if unresolved, the first alternative) to Tesseract words
    for f in F.values():
        f.evidence_box = find_box(f.value if f.value is not None else (f.alternatives[0] if f.alternatives else None),
                                  ocr_page, field=f.name)

    raw = lambda name: [v for v in (A.get(name), B.get(name), C.get(name)) if v]

    # ---- notation hazards on RAW strength / directions text (before normalising) ----
    for name in ("strength", "directions"):
        for v in raw(name):
            if re.search(r"\b\d+\.0+\s*(mg|mcg|g|ml|unit)", v, re.I):
                F[name].flag("TRAILING_ZERO", f"Printed '{v}'. A trailing zero (ISMP error-prone notation) can be "
                                              f"misread as 10x the dose. Confirm the intended amount.")
            if re.search(r"(?<![\d.])\.\d", v):
                F[name].flag("LEADING_DECIMAL", f"Printed '{v}' with no leading zero; '.5' is easily read as '5'.")
            if re.search(r"\b\d+\s*(u|iu)\b", v, re.I):
                F[name].flag("U_VS_0", f"'{v}' uses 'U'/'IU' for units, which is misread as a zero (ISMP).")

    # ---- drug ----
    drug = None
    fd = F["drug_name"]
    if fd.value is not None:
        ans = lookup.drug(fd.value)
        if ans.found:
            drug = ans.value
            fd.ok("drug_in_rxnorm", ans.source)
        else:
            close = lookup.closest_drugs(fd.value)
            fd.flag("DRUG_UNKNOWN", f"'{fd.value}' is not in the drug reference. Closest names: "
                                    f"{', '.join(n for n, _ in close)} (not substituted).")
    sched = drug["schedule"] if drug else None

    # ---- strength / form / unit ----
    fs, ff = F["strength"], F["dosage_form"]
    form = norm("dosage_form", ff.value) if ff.value else None
    if drug and ff.value is not None:
        forms = drug.get("strengths_by_form") or {}
        if any(k == form or k.startswith(f"{form}_") for k in forms):
            ff.ok("form_in_rxnorm", "rxnorm")
        else:
            ff.flag("STRENGTH_NOT_IN_RXNORM", f"RxNorm has no oral {form} for {drug['name']}.")
    if drug and fs.value is not None:
        p = parse_strength(fs.value)
        if p is None:
            fs.flag("STRENGTH_NOT_IN_RXNORM", f"Strength '{fs.value}' could not be parsed.")
        elif p[1] != drug["unit"]:
            fs.flag("UNIT_MISMATCH", f"{drug['name']} is dosed in {drug['unit']}; '{fs.value}' is in {p[1]}"
                                     f"{' (1000x)' if {p[1], drug['unit']} == {'mg', 'mcg'} else ''}.")
        else:
            strengths = lookup.strengths(drug["name"], form if form in (drug.get("strengths_by_form") or {}) else None)
            vals = [float(x) for x in (strengths.value or []) if not isinstance(x, str)]
            if p[0] in vals:
                fs.ok("strength_in_rxnorm", strengths.source)
            else:
                fs.flag("STRENGTH_NOT_IN_RXNORM", f"{p[0]:g} {p[1]} is not an RxNorm strength of {drug['name']} "
                                                  f"(RxNorm: {', '.join(f'{v:g}' for v in vals)} {drug['unit']}).")

    # ---- directions: frequency rules and max dose ----
    fdir = F["directions"]
    sig = parse_sig(fdir.value) if fdir.value else None
    if fdir.value is not None:
        if sig:
            fdir.ok("directions_parsed")
        if drug and drug.get("frequency_rule") == "weekly" and sig and not sig["weekly"]:
            fdir.flag("METHOTREXATE_DAILY", f"{drug['name']} is dosed once WEEKLY (manual rule, unverified); "
                                            f"'{fdir.value}' is not weekly. Daily dosing is a known fatal error.")
        sv = parse_strength(fs.value) if fs.value else None
        if drug and sig and sv and sv[1] == drug["unit"] and drug.get("max_daily"):
            per = sig["n"] * sv[0] * (1 if sig["weekly"] else (sig["per_day"] or 0))
            limit = drug["max_daily"]
            if per > limit:
                fdir.flag("DOSE_ABOVE_MAX", f"{per:g} {drug['unit']}/{'week' if sig['weekly'] else 'day'} exceeds the "
                                            f"usual maximum of {limit:g} (manual, unverified value). Confirm intent.")
            else:
                fdir.ok("within_usual_max (manual, unverified)")

    # ---- quantity ----
    fq = F["quantity"]
    if fq.value is not None:
        num = re.search(r"#?\s*(\d+)", fq.value)
        words = re.search(r"\(([a-z\s\-]+)\)", fq.value.lower())
        if num and words:
            if words_to_int(words.group(1)) == int(num.group(1)):
                fq.ok("numeral_matches_words")
            else:
                fq.flag("QTY_NUMERAL_WORDS_MISMATCH", f"'{fq.value}': the number and the words disagree.")
        if sig and sig["days"] and sig["per_day"] and num and not sig["prn"]:
            need = sig["n"] * sig["per_day"] * sig["days"]
            if abs(need - int(num.group(1))) > 0.01:
                fq.flag("QTY_DURATION_MISMATCH", f"Directions give {sig['n']:g} x {sig['per_day']:g}/day x "
                                                 f"{sig['days']} days = {need:g}, but quantity is {num.group(1)}.")
            else:
                fq.ok("quantity_matches_duration")

    # ---- refills (format + federal limits for controlled substances) ----
    fr = F["refills"]
    if fr.value is not None:
        nr = norm("refills", fr.value)
        if nr.isdigit():
            fr.ok("refills_whole_number")
            if sched == "C-II" and int(nr) > 0:
                fr.flag("C2_REFILLS", f"{drug['name']} is Schedule II: refills are not allowed (21 CFR 1306.12).")
            elif sched in ("C-III", "C-IV", "C-V") and int(nr) > 5:
                fr.flag("C3_5_REFILLS_OVER_LIMIT", f"{sched}: at most 5 refills (21 CFR 1306.22); fax says {nr}.")
        else:
            fr.flag("REFILLS_UNPARSEABLE", f"Refills '{fr.value}' is not a number.")

    # ---- prescriber ----
    fn, fnpi, fdea = F["prescriber_name"], F["prescriber_npi"], F["prescriber_dea"]
    directory_entry = None
    if fnpi.value is not None:
        npi = norm("prescriber_npi", fnpi.value)
        if not npi_valid(npi):
            fnpi.flag("NPI_INVALID", f"NPI '{fnpi.value}' fails the Luhn check digit (or is not 10 digits).")
        else:
            fnpi.ok("npi_luhn")
            ans = lookup.prescriber_by_npi(npi)
            if ans.found:
                directory_entry = ans.value
                fnpi.ok("npi_in_directory", ans.source)
    if fn.value is not None:
        by_name = lookup.prescriber_by_name(fn.value)
        if directory_entry:
            if by_name.found and by_name.value["npi"] == directory_entry["npi"]:
                fn.ok("prescriber_matches_npi", by_name.source)
            else:
                fn.flag("PRESCRIBER_UNKNOWN", f"NPI belongs to {directory_entry['first_name']} "
                                              f"{directory_entry['last_name']}, not '{fn.value}'.")
        elif by_name.found:
            fn.ok("prescriber_in_directory", by_name.source)
            if fnpi.value is not None and "NPI_INVALID" not in fnpi.codes:
                fnpi.flag("PRESCRIBER_UNKNOWN", f"Directory NPI for {fn.value} is {by_name.value['npi']}, not "
                                                f"{fnpi.value}.")
        elif not (fnpi.value and "NPI_INVALID" in fnpi.codes):
            fn.flag("PRESCRIBER_UNKNOWN", f"'{fn.value}' is not in the prescriber directory. Verify before filling.")
    if fdea.value is not None:
        last = (fn.value or "").split(",")[0].split()[-1] if fn.value else None
        if dea_valid(norm("prescriber_dea", fdea.value), last):
            fdea.ok("dea_checksum")
        else:
            fdea.flag("DEA_INVALID", f"DEA '{fdea.value}' fails its check digit or registrant-initial check.")
    elif sched in SCHEDULED and fdea.agreed is False and not fdea.codes:
        fdea.flag("DEA_MISSING", f"{drug['name']} is {sched} but no DEA number was found on the fax.")

    # ---- patient (per page; never carried over from another page) ----
    fp, fdob = F["patient_name"], F["patient_dob"]
    patient = None
    if fp.value is None and fdob.value is None and not fp.codes and not fdob.codes:
        for f_ in (fp, fdob):
            f_.flag("MISSING_PATIENT_ID", f"No patient {'name' if f_ is fp else 'date of birth'} on page {page_no}. "
                                          f"It is not carried over from any other page.")
    else:
        if fp.value is not None:
            ans = lookup.patient(fp.value)
            if ans.found:
                patient = ans.value
                fp.ok("patient_in_profiles", ans.source)
            else:
                fp.flag("PATIENT_NOT_FOUND", f"No patient profile for '{fp.value}'.")
        elif not fp.codes:
            fp.flag("MISSING_PATIENT_ID", f"No patient name on page {page_no}.")
        if fdob.value is not None:
            d = parse_date(fdob.value)
            if not d or d >= today:
                fdob.flag("DOB_MISMATCH", f"DOB '{fdob.value}' is not a valid past date.")
            elif patient:
                if d.isoformat() == patient["dob"]:
                    fdob.ok("dob_matches_profile", "patient profiles (patients.csv)")
                else:
                    fdob.flag("DOB_MISMATCH", f"Profile DOB is {patient['dob']}, fax says {fdob.value}.")
            else:
                fdob.ok("dob_valid_date")
        elif not fdob.codes:
            fdob.flag("MISSING_PATIENT_ID", f"No date of birth on page {page_no}.")
    if patient and drug:
        allergies = {a for a in patient["allergies"].split(";") if a and a != "none"}
        hit = allergies & set(drug["classes"])
        if hit:
            fd.flag("ALLERGY_CONFLICT", f"Patient profile lists a {', '.join(sorted(hit))} allergy; "
                                        f"{drug['name']} is in that class.")

    # ---- date written / substitution: deterministic format checks ----
    fdate = F["date_written"]
    if fdate.value is not None:
        d = parse_date(fdate.value)
        if not d:
            fdate.flag("DATE_INVALID", f"'{fdate.value}' is not a valid date.")
        elif d > today:
            fdate.flag("DATE_IN_FUTURE", f"Written date {d} is after today ({today}).")
        elif (today - d).days > 365:
            fdate.flag("DATE_TOO_OLD", f"Written {d}, more than a year ago.")
        else:
            fdate.ok("date_valid_not_future")
    fsub = F["substitution"]
    if fsub.value is not None:
        if norm("substitution", fsub.value) in ("permitted", "dispense_as_written"):
            fsub.ok("substitution_known_value")
        else:
            fsub.flag("SUBSTITUTION_UNCLEAR", f"Substitution box reading '{fsub.value}' is not recognised.")

    # ---- conflicting directions (Sig vs Notes) ----
    fnotes = F["notes"]
    if fnotes.codes:   # notes are informational: a reader difference is a note, not a flag on its own
        fnotes.notes.append(f"Readers differ on the notes: {fnotes.reads}")
        fnotes.codes, fnotes.reasons, fnotes.checks_failed = [], [], []
        fnotes.value = next((v for v in (A.get("notes"), B.get("notes")) if v), None)
    note_sig = parse_sig(fnotes.value) if fnotes.value else None
    if not note_sig and fnotes.value:
        # a note with a frequency but an implied dose ("then TID") still counts as a second set of directions
        fm = next((k for p, k in FREQ_PATTERNS if re.search(p, fnotes.value.lower())), None)
        if fm and re.search(r"\b(take|tab|cap|then|increase|decrease|after)\b", fnotes.value.lower()):
            note_sig = {"n": None, "per_day": fm[1], "weekly": fm[0] == "weekly"}
    if note_sig and sig and (note_sig["per_day"] != sig["per_day"] or note_sig["weekly"] != sig["weekly"]
                             or (note_sig["n"] is not None and note_sig["n"] != sig["n"])):
        fdir.flag("CONFLICTING_DIRECTIONS", f"Two sets of directions: Sig '{fdir.value}' vs Notes '{fnotes.value}'. "
                                            f"Neither is chosen; confirm with the prescriber.",
                  evidence={"sig_box": fdir.evidence_box, "notes_box": fnotes.evidence_box})
        fdir.alt(fnotes.value)

    # ---- ISMP fax checks on the image ----
    if ocr_page is not None:
        size = ocr_page["size"]
        for name in EDGE_FIELDS:
            f_ = F[name]
            if f_.evidence_box and "left" in fc.touches_edge(f_.evidence_box, size):
                visible = f_.value or (f_.alternatives[0] if f_.alternatives else "")
                reason = f"The {name.replace('_', ' ')} starts at the cut-off left edge; leading characters may be missing."
                if name == "drug_name" and visible:
                    longer = [d for d in lookup.drugs if d.endswith(norm("drug_name", visible)) and d != norm("drug_name", visible)] \
                        if hasattr(lookup, "drugs") else []
                    if lookup.drug(visible).found:
                        reason += f" The visible '{visible}' is itself a real drug, so it must not be trusted."
                    for d in longer:
                        f_.alt(d)
                    if longer:
                        reason += f" Names it could be cut from: {', '.join(longer)}."
                f_.flag("TRUNCATED_EDGE", reason, evidence=f_.evidence_box)
        if page_no == 1 and not fc.prescriber_block_present(ocr_page):
            page_flags.append({"code": "TRUNCATED_EDGE", "field": "prescriber_name", "page": page_no,
                               "reason": "Prescriber block (name with credential + 10-digit NPI) not found in the top "
                                         "of page 1: it may be cut off."})
            if "TRUNCATED_EDGE" not in fn.codes:
                fn.flag("TRUNCATED_EDGE", page_flags[-1]["reason"])
        if page_image is not None:
            word_boxes = [w["box"] for w in ocr_page["words"]]
            for name in NOISE_FIELDS:
                f_ = F[name]
                box = f_.evidence_box or fallback_region(name, ocr_page)
                if not box:
                    f_.notes.append("Noise not measured: field location not found on the page.")
                    continue
                m = page_image.noise_at(box, word_boxes)
                m["measured_on"] = "evidence box" if f_.evidence_box else "fallback region (field not found by OCR)"
                if m["flagged"]:
                    why = (f"{m['density']:.3f} speck density vs page baseline {m['baseline']:.3f}"
                           + (f"; streak through rows {m['streak_rows']}" if m["streak_rows"] else ""))
                    f_.flag("NOISE_NEAR_CRITICAL", f"Fax noise over the {name.replace('_', ' ')} ({why}). Readers can "
                                                   f"agree and still be wrong here (ISMP: 0.6 mg read as 0.8 mg).",
                            evidence=m)
                    if name == "strength" and drug:
                        add_digit_alternatives(f_, drug)

    # ---- look-alike / sound-alike: flag only on a sign of confusion ----
    if drug and drug["lasa"]:
        partners = drug["lasa"]
        signs = []
        for who, v in (("A", A.get("drug_name")), ("B", B.get("drug_name")), ("C", C.get("drug_name"))):
            if v:
                for p in partners:
                    if fuzz.ratio(v.lower(), p) > fuzz.ratio(v.lower(), drug["name"]):
                        signs.append(f"reader {who} '{v}' is closer to {p}")
        if "READER_DISAGREEMENT" in fd.codes:
            signs.append("readers disagree on the name")
        if "NOISE_NEAR_CRITICAL" in fd.codes:
            signs.append("noise over the drug name")
        if ocr_page and fd.evidence_box:
            confs = [w["conf"] for w in ocr_page["words"]
                     if w["box"][0] >= fd.evidence_box[0] - 2 and w["box"][2] <= fd.evidence_box[2] + 2
                     and w["box"][1] >= fd.evidence_box[1] - 2 and w["box"][3] <= fd.evidence_box[3] + 2]
            if confs and min(confs) < LASA_MIN_OCR_CONF:
                signs.append(f"drug name is unclear on the page (Tesseract confidence {min(confs):.0f})")
        if signs:
            fd.flag("LOOKALIKE_RISK", f"{drug['name']} has look-alike/sound-alike partner(s) {', '.join(partners)}; "
                                      f"sign of confusion: {'; '.join(signs)}. Confirm the drug.")
            for p in partners:
                fd.alt(p)
        else:
            fd.notes.append(f"Look-alike partner(s): {', '.join(partners)}. No sign of confusion on this fax.")

    # ---- final per-field status ----
    for name, f_ in F.items():
        if f_.codes:
            f_.status = "needs_pharmacist"
        elif name in INFO:
            f_.status = "info" if f_.value else "not_present"
        elif f_.value is None:
            if name in OPTIONAL:
                f_.status = "not_present"
            else:
                f_.flag("UNREADABLE", f"No reader could read the {name.replace('_', ' ')}. Left empty, not guessed.")
                f_.status = "needs_pharmacist"
        elif f_.agreed and f_.checks_passed:
            f_.status = "verified"
        else:
            f_.flag("NO_INDEPENDENT_CHECK", "Readers agree, but no independent check could confirm this field.")
            f_.status = "needs_pharmacist"
    return {"fields": {k: v.out() for k, v in F.items()}, "page_flags": page_flags, "drug": drug,
            "c_only_fields": [k for k, v in F.items() if v.c_only and v.codes == ["OCR_DISAGREEMENT"]]}


LASA_MIN_OCR_CONF = 60   # recorded in CHECKS.md


def fallback_region(name, ocr_page):
    """Where a field should be when OCR could not find it: the drug line sits between the 'Date' and 'Sig'
    lines; directions sit on the 'Sig' line. Noise that hides a field must not also hide the noise check."""
    lines = ocr_page["lines"]
    w = ocr_page["size"][0]
    find = lambda pat: next((l for l in lines if re.match(pat, l["text"], re.I)), None)
    date_l, sig_l = find(r"^\W*date\b"), find(r"^\W*sig\b")
    if name in ("drug_name", "strength"):
        rx_l = find(r"^\W*r\s?x\b")
        if rx_l:
            return [0, rx_l["box"][1], w, rx_l["box"][3]]
        if date_l and sig_l and sig_l["box"][1] > date_l["box"][3]:
            return [0, date_l["box"][3] + 20, w, sig_l["box"][1] - 20]
        return None
    if name == "directions" and sig_l:
        return [0, sig_l["box"][1], w, sig_l["box"][3]]
    return None


def add_digit_alternatives(f_, drug):
    """Under noise, list every real strength reachable by one confusable digit (5<->6, 6<->8, ...) from ANY
    reading (agreed value or each reader's alternative): readers may agree on the wrong digit."""
    readings = [v for v in [f_.value, *f_.alternatives, *f_.reads.values()] if v]
    parsed = [p for p in (parse_strength(v) for v in readings) if p]
    if not parsed:
        return
    unit = parsed[0][1]
    real = {float(v) for v in drug["strengths"] if not isinstance(v, str)}
    alts = set()
    for val, _ in parsed:
        if val in real:
            alts.add(val)
        s = f"{val:g}"
        for i, ch in enumerate(s):
            for alt in DIGIT_CONFUSIONS.get(ch, ""):
                try:
                    v = float(s[:i] + alt + s[i + 1:])
                except ValueError:
                    continue
                if v in real:
                    alts.add(v)
    if len(alts) > 1:
        for v in sorted(alts):
            f_.alt(f"{v:g} {unit}")
        f_.flag("AMBIGUOUS_DIGIT", f"Under this noise the strength could be any of "
                                   f"{', '.join(f'{v:g} {unit}' for v in sorted(alts))}: all are real strengths of "
                                   f"{drug['name']}, so reference data cannot settle it.")


# ---------------------------------------------------------------- order level
CONTROLLED_CODES = set(WEIGHTS["controlled_substance_issue"]["codes"])
LASA_CODES = set(WEIGHTS["lookalike_risk"]["codes"])


def danger(order_status, prescriptions, lookup):
    parts = {}
    codes = [c for rx in prescriptions for f in rx["fields"].values() for c in f["codes"]]
    flagged_fields = sum(1 for rx in prescriptions for f in rx["fields"].values() if f["codes"])
    if order_status == "BLOCKED":
        parts["blocked"] = WEIGHTS["blocked"]["points"]
    if "ALLERGY_CONFLICT" in codes:
        parts["allergy_conflict"] = WEIGHTS["allergy_conflict"]["points"]
    if CONTROLLED_CODES & set(codes):
        parts["controlled_substance_issue"] = WEIGHTS["controlled_substance_issue"]["points"]
    if flagged_fields and any(rx["drug"] and lookup.high_alert(rx["drug"]["name"]).found for rx in prescriptions):
        parts["high_alert_drug"] = WEIGHTS["high_alert_drug"]["points"]
    if LASA_CODES & set(codes):
        parts["lookalike_risk"] = WEIGHTS["lookalike_risk"]["points"]
    special = {"ALLERGY_CONFLICT"} | CONTROLLED_CODES | LASA_CODES
    other = sum(1 for rx in prescriptions for f in rx["fields"].values() if f["codes"] and not set(f["codes"]) <= special)
    if other:
        parts["other_flag"] = min(WEIGHTS["other_flag"]["cap"], other * WEIGHTS["other_flag"]["points_each"])
    return sum(parts.values()), parts


QUESTION = {
    "READER_ABSTAINED": "Please confirm the {f} for {drug}: part of the fax is not legible.",
    "READER_DISAGREEMENT": "Please confirm the {f} for {drug}: we read it two ways ({alts}).",
    "OCR_DISAGREEMENT": "Please confirm the {f} for {drug}: the faxed text is unclear ({alts}).",
    "UNREADABLE": "Please resend or confirm the {f}: it is not legible on the fax.",
    "TRAILING_ZERO": "Please confirm the strength of {drug}: written '{v}'. Is it {plain}?",
    "LEADING_DECIMAL": "Please confirm the {f} for {drug}: written '{v}' without a leading zero.",
    "U_VS_0": "Please confirm the dose of {drug}: 'U' was used for units.",
    "DRUG_UNKNOWN": "Please confirm the drug name: '{v}' does not match a drug we can verify.",
    "STRENGTH_NOT_IN_RXNORM": "Please confirm the strength of {drug}: '{v}' is not an available strength.",
    "UNIT_MISMATCH": "Please confirm the unit for {drug}: written '{v}'.",
    "METHOTREXATE_DAILY": "Please confirm the frequency for {drug}: the directions say daily, but it is usually weekly.",
    "LOOKALIKE_RISK": "Please confirm the drug: is it {drug} or {alts}?",
    "DOSE_ABOVE_MAX": "Please confirm the dose of {drug}: '{v}' is above the usual maximum.",
    "QTY_DURATION_MISMATCH": "Please confirm the quantity for {drug}: it does not match the directions and duration.",
    "QTY_NUMERAL_WORDS_MISMATCH": "Please confirm the quantity for {drug}: the number and the words differ.",
    "DEA_INVALID": "Please confirm your DEA number: the one on the fax does not validate.",
    "DEA_MISSING": "Please provide your DEA number for this controlled-substance prescription.",
    "C2_REFILLS": "{drug} is Schedule II and cannot have refills. Please send a new prescription without refills.",
    "C3_5_REFILLS_OVER_LIMIT": "Please reduce refills for {drug} to 5 or fewer.",
    "NPI_INVALID": "Please confirm your NPI: the number on the fax does not validate.",
    "PRESCRIBER_UNKNOWN": "Please confirm the prescriber's identity and NPI.",
    "PATIENT_NOT_FOUND": "Please confirm the patient's full name and date of birth.",
    "DOB_MISMATCH": "Please confirm the patient's date of birth.",
    "MISSING_PATIENT_ID": "Page {page} has no patient name or date of birth. Which patient is it for?",
    "NOISE_NEAR_CRITICAL": "The {f} for {drug} is obscured by fax noise. Please confirm it{alts_q}.",
    "AMBIGUOUS_DIGIT": "Please confirm the strength of {drug}: is it {alts}?",
    "TRUNCATED_EDGE": "The left edge of the fax was cut off. Please confirm the {f}{alts_q}.",
    "CONFLICTING_DIRECTIONS": "The prescription has two sets of directions ({alts}). Which one should the patient follow?",
}


def questions(prescriptions, order_flags):
    qs = []
    for rx in prescriptions:
        drug = (rx["drug"] or {}).get("name") or rx["fields"]["drug_name"]["value"] or "this prescription"
        for name, f in rx["fields"].items():
            for code in f["codes"]:
                if code in ("ALLERGY_CONFLICT", "NO_INDEPENDENT_CHECK", "INJECTION"):
                    continue  # internal: pharmacist decides; not a question about what was written
                t = QUESTION.get(code)
                if not t:
                    continue
                alts = " or ".join(str(a) for a in f["alternatives"]) or "see fax"
                v = f["value"] or (f["alternatives"][0] if f["alternatives"] else "")
                p = parse_strength(v) if v else None
                qs.append({"page": rx["page"], "field": name, "code": code, "question": t.format(
                    f=name.replace("_", " "), drug=drug, alts=alts, v=v, page=rx["page"],
                    plain=f"{p[0]:g} {p[1]}" if p else v,
                    alts_q=f" (could be {alts})" if f["alternatives"] else "")})
                break  # one question per field
    for fl in order_flags:
        if fl["code"] == "PAGE_COUNT_MISMATCH":
            qs.append({"page": None, "field": "_order", "code": fl["code"], "question": fl["question"]})
    return qs


def page_count_consensus(p, img_path):
    """Page x of N for one received page: accepted only when two independent sources agree
    (reader A, reader B, and several Tesseract passes over the header strip). Otherwise None = unknown."""
    from faxfill.readers import HEADER_FIELD
    sources = []
    for k in "AB":
        v = (p[k].get("fields") or {}).get(HEADER_FIELD)
        sources.append((f"reader {k}", fc._parse_page_token(v) if v else None))
    for r in fc.header_reads(img_path, p["ocr"]):
        sources.append((r["pass"], r["read"]))
    votes = {}
    for _, r in sources:
        if r:
            votes[r] = votes.get(r, 0) + 1
    if not votes:
        return None
    best, n = max(votes.items(), key=lambda kv: kv[1])
    if n >= fc.THRESHOLDS["header_votes"] and list(votes.values()).count(n) == 1:
        return best[0], best[1], [0, 0, p["ocr"]["size"][0], fc.HEADER_STRIP_PX]
    return None


def page_count_flags(headers, n_received):
    """headers: per received page, (page, total, box) or None. Page count comes from the files received."""
    totals = [h[1] for h in headers if h]
    declared = max(totals) if totals else None
    seen = sorted(h[0] for h in headers if h)
    if declared is None or any(h is None for h in headers):
        return [{"code": "PAGE_COUNT_UNKNOWN", "status": "needs_pharmacist", "page": None, "declared": declared,
                 "reason": "Page numbers could not be confirmed from the fax header on every page "
                           "(two independent reads must agree)."}]
    if n_received < declared or seen != list(range(1, n_received + 1)):
        missing = [n for n in range(1, declared + 1) if n not in seen]
        return [{"code": "PAGE_COUNT_MISMATCH", "status": "blocked", "page": None, "declared": declared,
                 "reason": f"Header says {declared} page(s) but {n_received} received"
                           f"{'; missing page ' + ', '.join(map(str, missing)) if missing else ''}. "
                           f"Blocked until the rest arrives.",
                 "question": f"We received {n_received} of {declared} pages. Please resend "
                             f"page {', '.join(map(str, missing)) or 'the missing pages'}."}]
    return []


def verify_fax(fax_id, lookup=None, today=None, refresh_reads=False):
    lookup = lookup or LocalLookup()
    t0 = time.time()
    pages = read_pages(fax_id, refresh_reads)                     # pages actually received
    images = ocr.pages(fax_id)
    use_prism = prism.enabled()
    futures = []
    if use_prism:
        # input guardrail: every page's OCR text goes to PRISM (agent sigcheck-intake) BEFORE verification
        futures += [prism.trace_intake(fax_id, p["page"], p["ocr"]["text"]) for p in pages]
        # one trace per model read
        futures += [prism.trace_read(fax_id, p[k]) for p in pages for k in "AB"]
    prescriptions, order_flags = [], []

    # page count from the headers vs the files received (never from an answer key)
    headers = [page_count_consensus(p, img) for p, img in zip(pages, images)]
    order_flags += page_count_flags(headers, len(pages))

    # injected instructions: OCR text of every page + any notes the readers transcribed
    lines = [(l["text"], l["box"], p["page"]) for p in pages for l in p["ocr"]["lines"]]
    lines += [(p[k]["fields"].get("notes") or "", None, p["page"]) for p in pages for k in "ABC"
              if p[k].get("fields")]
    local_hits = fc.injection_hits(lines)
    prism_flag = prism.prism_guardrail("\n".join(t for t, _, _ in lines))
    if local_hits or prism_flag:
        order_flags.append({"code": "INJECTION", "status": "blocked", "page": local_hits[0]["page"] if local_hits else None,
                            "reason": "The fax contains text addressed to an AI or reviewer "
                                      f"({'; '.join(repr(h['text']) for h in local_hits[:2])}). Instructions on a fax are "
                                      "never followed; the order is blocked for a person to review.",
                            "evidence": local_hits, "guardrails": {"local": bool(local_hits), "prism": prism_flag}})

    for p, img_path in zip(pages, images):
        reads = {k: (p[k].get("fields") if k != "C" else p[k]["fields"]) for k in "ABC"}
        res = verify_page(reads, p["ocr"], fc.PageImage(img_path), lookup, p["page"], today)
        flagged = [k for k, f in res["fields"].items() if f["codes"]]
        prescriptions.append({"page": p["page"], "image": p["image"], "drug": res["drug"],
                              "status": "NEEDS_PHARMACIST" if flagged else "READY", "flagged_fields": flagged,
                              "fields": res["fields"], "page_flags": res["page_flags"],
                              "c_only_fields": res["c_only_fields"]})

    if any(fl["status"] == "blocked" for fl in order_flags):
        status = "BLOCKED"
    elif order_flags or any(rx["flagged_fields"] for rx in prescriptions):
        status = "NEEDS_PHARMACIST"
    else:
        status = "READY"
    score, parts = danger(status, prescriptions, lookup)
    verdict = {
        "fax_id": fax_id, "verifier": VERIFIER_VERSION, "readers": {**MODELS, "C": "regex over tesseract"},
        "lookup": lookup.name, "pages_received": len(pages),
        "pages_declared_in_header": max([h[1] for h in headers if h], default=None),
        "order_status": status, "order_flags": order_flags,
        "danger_score": score, "danger_breakdown": parts,
        "prescriptions": [{k: v for k, v in rx.items() if k != "drug"} | {"drug_ref": (rx["drug"] or {}).get("name")}
                          for rx in prescriptions],
        "clarification_questions": questions(prescriptions, order_flags),
        "thresholds": fc.THRESHOLDS | {"lasa_min_ocr_conf": LASA_MIN_OCR_CONF},
        "lookup_sources": {"drug": lookup.name, "prescriber": lookup.name, "patient": lookup.name,
                           "prism_kb": prism.KB_STATUS},
    }
    if use_prism:
        futures.append(prism.trace_verifier(verdict, (time.time() - t0) * 1000))
        receipts = prism.collect(futures)
        prism.save_receipts(fax_id, receipts)
        verdict["prism"] = {
            "traces_sent": sum(1 for r in receipts if r.get("ok")),
            "traces_failed": [r for r in receipts if not r.get("ok")],
            "intake_trace_ids": [r["trace_id"] for r in receipts if r.get("kind") == "intake" and r.get("ok")],
            "reader_trace_ids": [r["trace_id"] for r in receipts if r.get("kind") == "reader" and r.get("ok")],
            "verifier_trace_id": next((r["trace_id"] for r in receipts if r.get("kind") == "verifier" and r.get("ok")), None),
            "guardrail": {"local_injection_check": bool(local_hits), "prism": prism_flag,
                          "prism_status": prism.GUARDRAIL_STATUS},
            "knowledge_base": prism.KB_STATUS,
        }
    else:
        verdict["prism"] = {"traces_sent": 0, "note": "PRISM not configured (.env)"}
    VERDICTS.mkdir(parents=True, exist_ok=True)
    json.dump(verdict, open(VERDICTS / f"{fax_id}.json", "w"), indent=1, default=str)
    return verdict


def all_fax_ids():
    ids = set()
    for p in ocr.FAXES.glob("rx_*.png"):
        m = re.match(r"(rx_\d+)", p.name)
        ids.add(m.group(1))
    return sorted(ids)


if __name__ == "__main__":
    ids = all_fax_ids() if "--all" in sys.argv else [a for a in sys.argv[1:] if not a.startswith("--")]
    lk = LocalLookup()
    for fid in ids:
        v = verify_fax(fid, lk)
        flags = [(rx["page"], k, f["codes"]) for rx in v["prescriptions"] for k, f in rx["fields"].items() if f["codes"]]
        print(f"{fid}: {v['order_status']:<16} danger {v['danger_score']:>5}  order {[f['code'] for f in v['order_flags']]}  "
              f"fields {flags}")
