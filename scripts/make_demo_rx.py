"""Generate photo-style prescription pad images for the live upload demo (fictional people only).

    python scripts/make_demo_rx.py    -> fixtures/demo/*.png

Unlike the fax set, these look like a phone photo of a paper Rx pad: tinted paper, blue-ink handwriting,
a slight tilt and a desk-coloured background. They are not part of the scored evaluation.
"""
from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).resolve().parents[1] / "fixtures" / "demo"
FONT_DIR = Path("/System/Library/Fonts/Supplemental")


def font(name: str, size: int):
    try:
        return ImageFont.truetype(str(FONT_DIR / name), size)
    except OSError:
        return ImageFont.load_default()


PADS = [
    ("rx_upload_clean_amoxicillin", {
        "Patient": "Daniel Reyes", "DOB": "07/22/1988", "Medication": "Amoxicillin 500 mg capsule",
        "Sig": "1 cap PO TID x 10 days", "Qty": "30", "Refills": "1",
        "Prescriber": "Dr. Jane Patel", "NPI": "1234567893", "Date": "10/09/2026"}),
    ("rx_upload_trailing_zero_atorvastatin", {
        "Patient": "Grace Kim", "DOB": "11/02/1954", "Medication": "Atorvastatin 20.0 mg tablet",
        "Sig": "1 tab PO at bedtime", "Qty": "30", "Refills": "3",
        "Prescriber": "Dr. Jane Patel", "NPI": "1234567893", "Date": "10/09/2026"}),
]


def render(values: dict, seed: int) -> Image.Image:
    rnd = random.Random(seed)
    W, H = 1400, 1900
    pad = Image.new("RGB", (W, H), (250, 251, 246))
    d = ImageDraw.Draw(pad)
    ink, printed, rule = (28, 52, 140), (40, 60, 70), (170, 190, 205)
    d.rectangle((0, 0, W, 210), fill=(226, 238, 242))
    d.text((70, 40), "RIVERSIDE PRIMARY CARE", font=font("Arial Bold.ttf", 58), fill=printed)
    d.text((70, 118), "88 Hudson St, New York, NY 10013  ·  Tel (212) 555-0188", font=font("Arial.ttf", 32), fill=printed)
    d.text((70, 160), "Synthetic demo clinic. Fictional patient and prescriber.", font=font("Arial Italic.ttf", 26), fill=(90, 110, 120))
    d.text((70, 250), "Rx", font=font("Georgia Bold.ttf", 120), fill=printed)
    lab, hand = font("Arial.ttf", 38), font("Bradley Hand Bold.ttf", 56)
    y = 420
    for label, value in values.items():
        if label == "Prescriber":
            y += 60
        d.text((70, y), f"{label}:", font=lab, fill=printed)
        d.line((380, y + 52, W - 70, y + 52), fill=rule, width=2)
        d.text((400 + rnd.randint(-6, 6), y - 12 + rnd.randint(-3, 3)), value, font=hand, fill=ink)
        y += 118
    d.text((70, H - 110), "Substitution permitted    Dispense as written  [  ]", font=font("Arial.ttf", 30), fill=printed)
    pad = pad.rotate(rnd.uniform(-2.5, 2.5), expand=True, fillcolor=(0, 0, 0, 0), resample=Image.BICUBIC)
    # desk background with a soft shadow, like a phone photo
    bg = Image.new("RGB", (pad.width + 220, pad.height + 220), (118, 98, 80))
    shadow = Image.new("L", bg.size, 0)
    ImageDraw.Draw(shadow).rectangle((130, 140, 130 + pad.width - 20, 140 + pad.height - 20), fill=140)
    bg.paste((60, 48, 38), mask=shadow.filter(ImageFilter.GaussianBlur(28)))
    pad_mask = Image.new("L", pad.size, 0)
    pad_mask.paste(255, (0, 0), pad.convert("L").point(lambda p: 255 if p > 0 else 0))
    bg.paste(pad, (110, 110), pad_mask)
    # gentle lighting gradient and camera softness
    grad = Image.linear_gradient("L").resize(bg.size).point(lambda p: int(p * 0.18))
    bg = Image.composite(Image.new("RGB", bg.size, (0, 0, 0)), bg, grad)
    return bg.filter(ImageFilter.GaussianBlur(0.6)).resize((bg.width * 3 // 4, bg.height * 3 // 4))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for i, (name, values) in enumerate(PADS):
        render(values, seed=100 + i).save(OUT / f"{name}.png")
        print("wrote", name)


if __name__ == "__main__":
    main()
