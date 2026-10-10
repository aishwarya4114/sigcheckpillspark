"""Download third-party test images into fixtures/external/ (git-ignored). See fixtures/external/README.md."""
from pathlib import Path
import httpx

BASE = "https://huggingface.co/datasets/Mobiusi/Pharmacy-Prescription-Text-Extraction-Dataset/resolve/main"
NAME = "0ac49b68e4aa3b07499f58ace3d30f22"
out = Path(__file__).resolve().parents[1] / "fixtures" / "external" / "mobiusi"
out.mkdir(parents=True, exist_ok=True)
for ext in ("jpg", "json"):
    r = httpx.get(f"{BASE}/{NAME}.{ext}", follow_redirects=True, timeout=60)
    r.raise_for_status()
    (out / f"sample.{ext}").write_bytes(r.content)
    print("saved", out / f"sample.{ext}")
