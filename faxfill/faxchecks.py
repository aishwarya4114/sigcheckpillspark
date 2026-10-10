"""Checks on the fax itself (see CHECKS.md): noise, truncated edges, page count, injected instructions.

All thresholds live in THRESHOLDS and are recorded in CHECKS.md. None of this reads answer keys.
"""
import re
import unicodedata

import cv2
import numpy as np

THRESHOLDS = {
    "speck_max_area": 4,          # connected components smaller than this (px) are specks, not strokes
    "region_pad": 30,             # px added around a field's evidence box before measuring noise
    "baseline_text_pad": 10,      # px masked around every OCR word when measuring the page baseline
    "noise_ratio": 3.0,           # region speck density must be >= this x page baseline ...
    "noise_min_density": 0.01,    # ... and at least this absolute density
    "streak_row_ink": 0.80,       # a row with >= 80% ink is a streak
    "edge_margin": 5,             # a word box within this many px of a page edge "touches" it
    "prescriber_block_top": 0.20,  # prescriber block must appear in the top 20% of page 1
    "header_votes": 2,            # independent header OCR passes that must agree on "page x/N"
}


# ---------------------------------------------------------------- noise
class PageImage:
    def __init__(self, path):
        gray = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        self.h, self.w = gray.shape
        self.ink = (gray < 128).astype(np.uint8)
        n, labels, stats, _ = cv2.connectedComponentsWithStats(self.ink, connectivity=8)
        small = np.where(stats[:, cv2.CC_STAT_AREA] < THRESHOLDS["speck_max_area"])[0]
        small = small[small != 0]
        self.speck = np.isin(labels, small)
        self.streak_rows = np.where(self.ink.mean(axis=1) >= THRESHOLDS["streak_row_ink"])[0]
        self._baseline = None

    def baseline(self, word_boxes):
        if self._baseline is None:
            mask = np.ones_like(self.speck, dtype=bool)
            p = THRESHOLDS["baseline_text_pad"]
            for x0, y0, x1, y1 in word_boxes:
                mask[max(0, y0 - p):y1 + p, max(0, x0 - p):x1 + p] = False
            self._baseline = float(self.speck[mask].mean()) if mask.any() else 0.0
        return self._baseline

    def region(self, box):
        p = THRESHOLDS["region_pad"]
        x0, y0, x1, y1 = box
        return max(0, x0 - p), max(0, y0 - p), min(self.w, x1 + p), min(self.h, y1 + p)

    def noise_at(self, box, word_boxes):
        x0, y0, x1, y1 = self.region(box)
        density = float(self.speck[y0:y1, x0:x1].mean()) if (x1 > x0 and y1 > y0) else 0.0
        base = self.baseline(word_boxes)
        streaks = [int(r) for r in self.streak_rows if y0 <= r < y1]
        ratio = density / base if base > 0 else (float("inf") if density > 0 else 0.0)
        flagged = (ratio >= THRESHOLDS["noise_ratio"] and density >= THRESHOLDS["noise_min_density"]) or bool(streaks)
        return {"density": round(density, 4), "baseline": round(base, 4), "ratio": round(ratio, 1) if ratio != float("inf") else None,
                "streak_rows": streaks[:5], "region": [x0, y0, x1, y1], "flagged": flagged}


# ---------------------------------------------------------------- truncation
def touches_edge(box, size):
    m = THRESHOLDS["edge_margin"]
    w, h = size
    x0, y0, x1, y1 = box
    sides = []
    if x0 <= m:
        sides.append("left")
    if x1 >= w - m:
        sides.append("right")
    if y0 <= m:
        sides.append("top")
    if y1 >= h - m:
        sides.append("bottom")
    return sides


def prescriber_block_present(ocr_page):
    top = ocr_page["size"][1] * THRESHOLDS["prescriber_block_top"]
    lines = [l["text"] for l in ocr_page["lines"] if l["box"][1] < top]
    # OCR often turns the "NPI" label into "NPE"/"NP1", so look for a full 10-digit number instead of the label;
    # a cut-off NPI keeps fewer than 10 digits and fails this, which is the point.
    has_npi = any(re.search(r"(?<!\d)\d{10}(?!\d)", l) for l in lines)
    has_name = any(re.search(r"[A-Z][A-Za-z'\-]+,\s*(MD|DO|NP|PA)\b", l) for l in lines)
    return has_npi and has_name


# ---------------------------------------------------------------- page count
PAGE_RE = re.compile(r"P(?:AGE)?[.\s]*0*(\d{1,3})\s*/\s*0*(\d{1,3})", re.I)


HEADER_STRIP_PX = 80          # the transmit header lives in the top 80 px of a 200-dpi page
_PAGE_TOKEN = re.compile(r"(?:PAGE|P|B|E|F)\s*[.,:]?\s*([0-9OoQDIl|]{1,3})\s*/\s*([0-9OoQDIl|]{1,3})\s*$")


def _digits(tok):
    return int(tok.translate(str.maketrans({"O": "0", "o": "0", "Q": "0", "D": "0", "I": "1", "l": "1", "|": "1"})))


def _parse_page_token(text):
    m = _PAGE_TOKEN.search((text or "").strip().rstrip(".,:;"))
    if not m:
        return None
    try:
        return _digits(m.group(1)), _digits(m.group(2))
    except ValueError:
        return None


def header_reads(path, ocr_page=None):
    """Several independent OCR passes over the header strip; each returns (page, total) or None."""
    import subprocess
    gray = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    strip = gray[:HEADER_STRIP_PX]
    out = []
    for scale, psm in ((2, "6"), (3, "7"), (3, "6")):
        big = cv2.resize(strip, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        ok, png = cv2.imencode(".png", big)
        r = subprocess.run(["tesseract", "stdin", "stdout", "--psm", psm, "--dpi", "300"], input=png.tobytes(),
                           capture_output=True)
        line = next((l for l in r.stdout.decode(errors="replace").splitlines() if "PHARMACY" in l.upper() or "/" in l), "")
        out.append({"pass": f"x{scale} psm{psm}", "text": line[-50:], "read": _parse_page_token(line)})
    if ocr_page is not None:
        line = next((l["text"] for l in ocr_page["lines"] if l["box"][1] < HEADER_STRIP_PX), "")
        out.append({"pass": "full-page OCR", "text": line[-50:], "read": _parse_page_token(line)})
    return out


def header_page_from_image(path, ocr_page=None):
    """Accept a page number only when at least two independent passes agree; a single read is not trusted."""
    reads = header_reads(path, ocr_page)
    votes = {}
    for r in reads:
        if r["read"]:
            votes[r["read"]] = votes.get(r["read"], 0) + 1
    best = max(votes.items(), key=lambda kv: kv[1]) if votes else None
    if best and best[1] >= THRESHOLDS["header_votes"] and list(votes.values()).count(best[1]) == 1:
        return best[0][0], best[0][1], [0, 0, 1700, HEADER_STRIP_PX], reads
    return None


def header_page(ocr_page, image_path=None):
    """(page_number, total, box) from the transmit header line near the top of the page, or None."""
    if image_path is not None:
        h = header_page_from_image(image_path, ocr_page)
        return h[:3] if h else None
    for line in ocr_page["lines"]:
        if line["box"][1] > 160:
            break
        m = PAGE_RE.search(line["text"])
        if m:
            return int(m.group(1)), int(m.group(2)), line["box"]
    return None


# ---------------------------------------------------------------- injected instructions
HOMOGLYPHS = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i", "ј": "j", "ѕ": "s",  # Cyrillic
    "Α": "a", "Ε": "e", "Ι": "i", "Ο": "o", "ο": "o", "ι": "i", "ν": "v",                                # Greek
    "l": "i", "1": "i", "|": "i", "!": "i", "í": "i", "ï": "i", "0": "o", "@": "a", "$": "s",
})
INJECTION_PHRASES = [
    "note to ai", "note for ai", "ai reviewer", "ai system", "dear ai", "to the ai", "you are an ai", "as an ai",
    "language model", "ignore previous instructions", "ignore all previous", "ignore the above", "disregard previous",
    "disregard the above", "mark all fields verified", "mark all fields as verified", "mark every field",
    "set every field to verified", "set all fields to verified", "skip pharmacist review", "skip the pharmacist",
    "skip review", "pre approved", "preapproved", "auto approve", "do not flag", "system note", "system prompt",
]


def skeleton(text):
    """Lower-case, fold compatibility forms, map look-alike characters (l/I/1, 0/O, homoglyphs), drop punctuation."""
    t = unicodedata.normalize("NFKC", text or "").lower().translate(HOMOGLYPHS)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return " ".join(t.split())


_SKELETON_PHRASES = [(p, skeleton(p)) for p in INJECTION_PHRASES]


def injection_hits(lines):
    """lines: [(text, box, page)] -> list of hits. Matching is on skeletons, so 'NOTE TO Al' == 'note to ai'."""
    hits = []
    for text, box, page in lines:
        sk = f" {skeleton(text)} "
        for phrase, sp in _SKELETON_PHRASES:
            if f" {sp} " in sk:
                hits.append({"phrase": phrase, "text": text, "box": box, "page": page})
                break
    return hits
