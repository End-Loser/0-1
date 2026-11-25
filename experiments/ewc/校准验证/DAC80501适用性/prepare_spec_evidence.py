"""Extract page-scoped specification evidence, preserving the original PDF."""
import hashlib
import json
from pathlib import Path

from pypdf import PdfReader
import pypdfium2

ROOT = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    assert not (ROOT / "spec_sources.json").exists()
    source = next(item for item in json.loads(
        (ROOT / "sources.json").read_text(encoding="utf-8"))["sources"]
        if item["id"] == "datasheet")
    pdf_path = Path(source["path"])
    assert sha(pdf_path) == source["sha256"]
    reader = PdfReader(pdf_path)
    directory = ROOT / "spec_snapshots"
    directory.mkdir()
    records = []
    for page in (4, 5, 6, 7, 8, 9, 21, 22, 23, 30, 31, 32):
        path = directory / f"page_{page:02}.txt"
        path.write_text(reader.pages[page-1].extract_text(extraction_mode="layout"),
                        encoding="utf-8")
        records.append(dict(pdf_page=page, path=path.relative_to(ROOT).as_posix(),
                            sha256=sha(path)))
    document = pypdfium2.PdfDocument(pdf_path)
    for page in (4, 9, 21, 31):
        path = pdf_path.parent / f"page_{page:02}.png"
        document[page-1].render(scale=1.5).to_pil().save(path)
        print(path)
    document.close()
    (ROOT / "spec_sources.json").write_text(json.dumps(dict(
        original_pdf_sha256=source["sha256"], pages=records,
        preparer_sha256=sha(Path(__file__))), indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
