"""Bounded official-resource collection; no device model synthesis."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import tempfile
import zipfile

from bs4 import BeautifulSoup
from pypdf import PdfReader
import requests


ROOT = Path(__file__).resolve().parent
CACHE = Path(tempfile.gettempdir()) / "ewc-device-audit-20261118"
SOURCES = {
    "tx_page": "https://www.analog.com/en/products/adl5375.html",
    "rx_page": "https://www.analog.com/en/products/adl5380.html",
    "cmp_page": "https://www.ti.com/product/TLV3501",
    "tx_pdf": "https://www.analog.com/media/en/technical-documentation/data-sheets/ADL5375.pdf",
    "rx_pdf": "https://www.analog.com/media/en/technical-documentation/data-sheets/ADL5380.pdf",
    "cmp_pdf": "https://www.ti.com/lit/gpn/TLV3501",
    "tipd_page": "https://www.ti.com/tool/TIPD105",
    "tipd_zip": "https://www.ti.com/lit/zip/SLAC576",
    "tina_page": "https://www.ti.com/tool/TINA-TI",
    "rf_tool": "https://www.analog.com/en/lp/resources/adisimrf.html",
    "cmp_pspice_zip": "https://www.ti.com/lit/zip/SBOMBU5",
    "cmp_tina_zip": "https://www.ti.com/lit/zip/SBOMBU6",
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def collect(item):
    key, url = item
    record = {"id": key, "url": url,
              "retrieved_utc": datetime.now(timezone.utc).isoformat()}
    try:
        response = requests.get(url, timeout=(15, 45))
        record.update(status=response.status_code, final_url=response.url,
                      content_type=response.headers.get("Content-Type"),
                      bytes=len(response.content), raw_sha256=sha(response.content))
        raw = response.content
        suffix = ".pdf" if raw.startswith(b"%PDF") else (
            ".zip" if raw.startswith(b"PK") else ".html")
        raw_path = CACHE / (key + suffix)
        raw_path.write_bytes(raw)
        record["cache"] = str(raw_path)
        response.raise_for_status()
        if suffix == ".pdf":
            reader = PdfReader(io.BytesIO(raw))
            text = "\n".join(f"\n--- PDF page {i + 1} ---\n{page.extract_text()}"
                             for i, page in enumerate(reader.pages))
            record["pages"] = len(reader.pages)
            record["format"] = "official PDF extracted text; original in TEMP"
        elif suffix == ".zip":
            with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
                entries = []
                for info in bundle.infolist():
                    if info.is_dir():
                        continue
                    if info.file_size > 20_000_000:
                        raise ValueError("Archive member exceeds bounded inspection size")
                    data = bundle.read(info)
                    entry = {"name": info.filename, "bytes": len(data),
                             "sha256": sha(data), "header_hex": data[:32].hex()}
                    ext = Path(info.filename).suffix.lower()
                    if ext in {".cir", ".lib", ".sub", ".sp", ".spice", ".txt", ".mod"}:
                        try:
                            decoded = data.decode("utf-8")
                            entry["text_readable"] = True
                            entry["subckt_lines"] = [
                                line for line in decoded.splitlines()
                                if line.lstrip().lower().startswith(".subckt")]
                        except UnicodeDecodeError:
                            entry["text_readable"] = False
                    entries.append(entry)
                record["entries"] = entries
            record["format"] = "ZIP inventory only; no proprietary model conversion"
            text = json.dumps(entries, ensure_ascii=False, indent=2)
        else:
            soup = BeautifulSoup(raw, "html.parser")
            record["links"] = [{"label": a.get_text(" ", strip=True),
                                "href": a.get("href")}
                               for a in soup.find_all("a", href=True)]
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            text = soup.get_text("\n", strip=True)
            record["format"] = "official HTML extracted text; not a device model"
        snapshot = ROOT / "source_snapshots" / (key + ".txt")
        snapshot.write_text(text, encoding="utf-8")
        record["snapshot"] = snapshot.relative_to(ROOT).as_posix()
        record["snapshot_sha256"] = sha(snapshot.read_bytes())
        record["result"] = "retrieved"
    except Exception as exc:
        record["result"] = "blocked"
        record["error"] = f"{type(exc).__name__}: {exc}"
    return record


def main():
    CACHE.mkdir(exist_ok=True)
    (ROOT / "source_snapshots").mkdir(exist_ok=True)
    with ThreadPoolExecutor(max_workers=5) as pool:
        records = list(pool.map(collect, SOURCES.items()))
    output = {"scope": "official-source and format audit, not transient acceptance",
              "collector_sha256": sha(Path(__file__).read_bytes()),
              "sources": records}
    (ROOT / "source_manifest.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for record in records:
        print(record["id"], record["result"], record.get("status"),
              record.get("error", ""), record.get("cache", ""))


if __name__ == "__main__":
    main()
