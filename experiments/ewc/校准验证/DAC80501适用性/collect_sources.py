"""Bounded official DAC source collection; originals remain in TEMP."""
import hashlib
import json
from pathlib import Path
import re
import tempfile
from urllib.request import Request, urlopen

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    assert not (ROOT / "sources.json").exists(), "Preserve original collection"
    cache = Path(tempfile.mkdtemp(prefix="ewc-dac80501-official-"))
    sources = []
    for name, url in (
        ("product", "https://www.ti.com/product/DAC80501"),
        ("datasheet", "https://www.ti.com/lit/gpn/dac80501"),
    ):
        with urlopen(Request(url, headers={"User-Agent": "EWC source verification"}),
                     timeout=45) as response:
            content = response.read(20_000_001)
            assert len(content) <= 20_000_000
            record = dict(id=name, url=url, final_url=response.url, status=response.status,
                          content_type=response.headers.get("Content-Type"))
        path = cache / (name + (".pdf" if content.startswith(b"%PDF") else ".html"))
        path.write_bytes(content)
        record.update(path=str(path), sha256=sha(path), bytes=len(content))
        if name == "product":
            text = content.decode("utf-8", errors="replace")
            record["model_archive_links"] = sorted(set(re.findall(
                r'(?:https://www\.ti\.com)?/lit/(?:zip|tsc)/[A-Za-z0-9_.-]+', text)))
        else:
            assert content.startswith(b"%PDF")
            reader = PdfReader(path)
            pages = [page.extract_text(extraction_mode="layout") for page in reader.pages]
            text_path = cache / "datasheet_layout.txt"
            text_path.write_text("\n".join(
                f"\n=== PDF PAGE {i+1} ===\n" + text for i, text in enumerate(pages)
            ), encoding="utf-8")
            record.update(pages=len(pages), text_path=str(text_path),
                          text_sha256=sha(text_path))
        sources.append(record)
    result = dict(sources=sources, cache=str(cache), originals_redistributed=False,
                  collector_sha256=sha(Path(__file__)), device_model_run=False)
    (ROOT / "sources.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
