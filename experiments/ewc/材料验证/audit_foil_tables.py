"""Archive original copper-foil table views without changing source evidence."""
import hashlib
import json
from pathlib import Path

import pypdfium2

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "铜箔原表复核"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    manifest = ROOT / "source_manifest.json"
    source = next(x for x in json.loads(manifest.read_bytes())["sources"]
                  if x["id"] == "S03")
    pdf_path = Path(source["cache_path"])
    assert sha(pdf_path) == source["pdf_sha256"], "Original PDF changed"
    snapshot = ROOT / source["snapshot_path"]
    assert sha(snapshot) == source["snapshot_sha256"], "Original text changed"
    OUT.mkdir(exist_ok=True)
    targets = [OUT / f"S03_page_{page}.png" for page in (3, 4, 7, 8)]
    record_path = OUT / "render_manifest.json"
    if record_path.exists():
        record = json.loads(record_path.read_bytes())
        assert record["renderer_sha256"] == sha(Path(__file__))
        assert record["source_manifest_sha256"] == sha(manifest)
        assert record["pdf_sha256"] == sha(pdf_path)
        assert record["snapshot_sha256"] == sha(snapshot)
        assert set(record["images_sha256"]) == {p.name for p in targets}
        assert all(sha(OUT / p) == digest
                   for p, digest in record["images_sha256"].items())
        print(json.dumps(record, indent=2))
        return
    assert not any(p.exists() for p in targets), "Refuse unlisted image overwrite"
    with pypdfium2.PdfDocument(pdf_path) as pdf:
        for page, target in zip((3, 4, 7, 8), targets):
            with target.open("xb") as stream:
                pdf[page - 1].render(scale=3).to_pil().save(stream, format="PNG")
    record = dict(
        scope="Original PDF table views; not material or system acceptance",
        source_url=source["url"], pdf_sha256=sha(pdf_path),
        snapshot_sha256=sha(snapshot), source_manifest_sha256=sha(manifest),
        renderer_sha256=sha(Path(__file__)), render_scale=3,
        images_sha256={p.name: sha(p) for p in targets},
        original_pdf_download_repeated=False, original_text_modified=False,
        G2_closed=False, full_system_gates_closed=0,
    )
    with record_path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
