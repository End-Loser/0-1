"""Fetch only three official PDFs; originals stay in TEMP, text is archived."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import urllib.request

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent
BASE = ("https://www.rogerscorp.com/-/media/project/rogerscorp/documents/"
        "advanced-electronics-solutions/english/")
SOURCES = [
    ("S01", "RT/duroid 5870-5880 Data Sheet",
     BASE + "data-sheets/rt-duroid-5870---5880-data-sheet.pdf"),
    ("S02", "Fabrication Guidelines RT/duroid 5870-5880",
     BASE + "fabrication-information/fabrication-guidelines-rt-duroid-5870-5880-high-frequency-circuit-materials.pdf"),
    ("S03", "Copper Foils for High Frequency Circuit Materials",
     BASE + "properties---detailed-characteristics/copper-foils-for-high-frequency-circuit-materials.pdf"),
]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def collect(item, refresh=False):
    sid, title, url = item
    cache = Path(os.environ["TEMP"]) / "ewc_dielectric_official_pdf"
    cache.mkdir(exist_ok=True)
    original = cache / (sid + ".pdf")
    if refresh or not original.exists():
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read(8_000_001)
            resolved = response.url
        if len(data) > 8_000_000 or not data.startswith(b"%PDF-"):
            raise ValueError(f"{sid}: expected bounded PDF, not HTML")
        original.write_bytes(data)
    else:
        data = original.read_bytes()
        resolved = url
    reader = PdfReader(original)
    text = "\n\n".join(f"=== PDF PAGE {i + 1} ===\n{p.extract_text(extraction_mode='layout')}"
                       for i, p in enumerate(reader.pages))
    snapshots = ROOT / "source_snapshots"
    snapshots.mkdir(exist_ok=True)
    target = snapshots / (sid + ".txt")
    target.write_text(text + "\n", encoding="utf-8")
    return {
        "id": sid, "title": title, "url": url, "resolved_url": resolved,
        "pdf_sha256": sha(data), "pdf_bytes": len(data), "pages": len(reader.pages),
        "cache_path": str(original),
        "snapshot_path": str(target.relative_to(ROOT)),
        "snapshot_sha256": sha(target.read_bytes()),
        "cache_mtime_utc": datetime.fromtimestamp(original.stat().st_mtime, timezone.utc).isoformat(),
        "extracted_at_utc": datetime.now(timezone.utc).isoformat(),
        "version": "Read printed revision/copyright in snapshot; no inferred current revision",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="Explicitly refetch the same three URLs")
    args = parser.parse_args()
    with ThreadPoolExecutor(max_workers=3) as pool:
        rows = list(pool.map(lambda x: collect(x, args.refresh), SOURCES))
    manifest = {"source_count": 3, "originals_redistributed": False, "sources": rows}
    (ROOT / "source_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
