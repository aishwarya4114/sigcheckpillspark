"""Deskew, light denoise and Tesseract OCR with word boxes, per page.

    python -m faxfill.ocr rx_001        # writes faxfill/data/ocr/rx_001_p1.json (one file per page)

Pages: a fax is either `rx_001.png` (one page) or `rx_001_p1.png`, `rx_001_p2.png`, ...
Word boxes are reported twice: in the deskewed image Tesseract saw, and mapped back to the ORIGINAL
fax image (`box`), which is what the review UI highlights.
"""
import csv
import io
import json
import math
import re
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

DATA = Path(__file__).parent / "data"
FAXES = DATA / "faxes"
HELDOUT = DATA / "heldout"          # held-out faxes (ids "ho_NNN"), generated after the rules were frozen
OCR_DIR = DATA / "ocr"
OCR_VERSION = "ocr-v1"


def folder_for(fax_id):
    return HELDOUT if fax_id.startswith("ho_") else FAXES


def pages(fax_id, folder=None):
    """Ordered page images for a fax id."""
    folder = folder or folder_for(fax_id)
    multi = sorted(folder.glob(f"{fax_id}_p*.png"), key=lambda p: int(re.search(r"_p(\d+)\.png$", p.name).group(1)))
    if multi:
        return multi
    single = folder / f"{fax_id}.png"
    if single.exists():
        return [single]
    raise FileNotFoundError(f"no pages for {fax_id} in {folder}")


def estimate_skew(gray, max_deg=4.0, step=0.1):
    """Projection-profile deskew: the angle where text rows line up gives the sharpest row histogram."""
    small = cv2.resize(gray, None, fx=0.35, fy=0.35, interpolation=cv2.INTER_AREA)
    ink = (small < 128).astype(np.uint8)
    h, w = ink.shape
    # drop long horizontal lines (fax streaks and form rules): they sit at 0 degrees and would win the search
    lines = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (w // 4, 1)))
    ink = (ink & (1 - lines)).astype(np.float32)
    best, best_score = 0.0, -1.0
    for deg in np.arange(-max_deg, max_deg + step / 2, step):
        m = cv2.getRotationMatrix2D((w / 2, h / 2), deg, 1.0)
        rot = cv2.warpAffine(ink, m, (w, h), flags=cv2.INTER_NEAREST, borderValue=0)
        rows = rot.sum(axis=1)
        score = float(np.square(np.diff(rows)).sum())
        if score > best_score:
            best, best_score = float(deg), score
    return round(best, 2)


def rotate(gray, deg):
    h, w = gray.shape
    m = cv2.getRotationMatrix2D((w / 2, h / 2), deg, 1.0)
    return cv2.warpAffine(gray, m, (w, h), flags=cv2.INTER_LINEAR, borderValue=255), m


def denoise(gray, min_area=4):
    """Remove isolated fax speckles (tiny black components) without touching strokes."""
    ink = (gray < 128).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    small = np.where(stats[:, cv2.CC_STAT_AREA] < min_area)[0]
    small = small[small != 0]
    out = gray.copy()
    out[np.isin(labels, small)] = 255
    return out


def unrotate_box(box, m):
    """Map an axis-aligned box from the deskewed image back to original-image coordinates."""
    inv = cv2.invertAffineTransform(m)
    x0, y0, x1, y1 = box
    pts = np.array([[x0, y0], [x1, y0], [x0, y1], [x1, y1]], dtype=np.float64)
    mapped = (inv[:, :2] @ pts.T).T + inv[:, 2]
    return [int(math.floor(mapped[:, 0].min())), int(math.floor(mapped[:, 1].min())),
            int(math.ceil(mapped[:, 0].max())), int(math.ceil(mapped[:, 1].max()))]


def tesseract_words(img):
    ok, png = cv2.imencode(".png", img)
    r = subprocess.run(["tesseract", "stdin", "stdout", "--dpi", "200", "--psm", "4", "tsv"],
                       input=png.tobytes(), capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode()[-300:])
    rows = list(csv.DictReader(io.StringIO(r.stdout.decode(errors="replace")), delimiter="\t", quoting=csv.QUOTE_NONE))
    words = []
    for row in rows:
        text = (row.get("text") or "").strip()
        if row.get("level") != "5" or not text:
            continue
        x, y, w, h = (int(row[k]) for k in ("left", "top", "width", "height"))
        words.append({"text": text, "conf": float(row["conf"]),
                      "line_key": f"{row['block_num']}.{row['par_num']}.{row['line_num']}",
                      "box_deskewed": [x, y, x + w, y + h]})
    return words


def group_lines(words):
    lines = {}
    for wd in words:
        lines.setdefault(wd["line_key"], []).append(wd)
    out = []
    for key, ws in lines.items():
        ws.sort(key=lambda w: w["box_deskewed"][0])
        xs0, ys0 = [w["box"][0] for w in ws], [w["box"][1] for w in ws]
        xs1, ys1 = [w["box"][2] for w in ws], [w["box"][3] for w in ws]
        out.append({"text": " ".join(w["text"] for w in ws), "box": [min(xs0), min(ys0), max(xs1), max(ys1)],
                    "word_ids": [w["id"] for w in ws]})
    out.sort(key=lambda l: (l["box"][1], l["box"][0]))
    return out


def ocr_page(path, page_no):
    gray = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    skew = estimate_skew(gray)
    # the profile search finds the rotation that straightens the page
    straight, m = rotate(gray, skew)
    clean = denoise(straight)
    words = tesseract_words(clean)
    for i, wd in enumerate(words):
        wd["id"] = f"p{page_no}w{i}"
        wd["box"] = unrotate_box(wd["box_deskewed"], m)
    lines = group_lines(words)
    return {"version": OCR_VERSION, "page": page_no, "image": path.name, "size": [gray.shape[1], gray.shape[0]],
            "deskew_deg": skew, "engine": "tesseract " + tesseract_version(), "words": words, "lines": lines,
            "text": "\n".join(l["text"] for l in lines)}


_TV = None


def tesseract_version():
    global _TV
    if _TV is None:
        _TV = subprocess.run(["tesseract", "--version"], capture_output=True, text=True).stdout.split()[1]
    return _TV


def run(fax_id, refresh=False, folder=None):
    """OCR every page of a fax; cached in data/ocr/<id>_p<N>.json."""
    OCR_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for n, path in enumerate(pages(fax_id, folder), start=1):
        cache = OCR_DIR / f"{fax_id}_p{n}.json"
        if cache.exists() and not refresh:
            res = json.load(open(cache))
            if res.get("version") == OCR_VERSION and res.get("image") == path.name:
                out.append(res)
                continue
        res = ocr_page(path, n)
        json.dump(res, open(cache, "w"), indent=1)
        out.append(res)
    return out


if __name__ == "__main__":
    for fid in sys.argv[1:]:
        for p in run(fid, refresh="--refresh" in sys.argv):
            print(f"{fid} page {p['page']}: deskew {p['deskew_deg']}°, {len(p['words'])} words")
            print(p["text"])
