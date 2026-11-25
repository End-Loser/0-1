"""Download one linked original package, inspect text without transforming it."""
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import urllib.request
import zipfile
import sys

ROOT = Path(__file__).resolve().parent
manifest = json.loads((ROOT / "sources.json").read_text())
cache = Path(manifest["cache"])
url = "https://www.ti.com/lit/zip/SBOMB92"
record = dict(id="ths4551_pspice.zip", url=url,
              retrieved_utc=datetime.now(timezone.utc).isoformat())
inspection = dict(candidate="THS4551", members=[], readable_models=[])
try:
    if "--cached" in sys.argv:
        record = next(s for s in manifest["sources"] if s["id"] == "ths4551_pspice.zip")
        data = Path(record["cache"]).read_bytes()
    else:
        with urllib.request.urlopen(url, timeout=35) as response:
            data = response.read()
            record.update(final_url=response.url, status=response.status)
    path = cache / record["id"]
    path.write_bytes(data)
    record.update(cache=str(path), bytes=len(data),
                  sha256=hashlib.sha256(data).hexdigest())
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            content = archive.read(member)
            inspection["members"].append(dict(name=member.filename, bytes=len(content),
                                               sha256=hashlib.sha256(content).hexdigest()))
            if member.filename.lower().endswith((".lib", ".cir", ".txt")):
                target = cache / Path(member.filename).name
                target.write_bytes(content)
                text = content.decode("ascii", errors="replace")
                inspection["readable_models"].append(dict(
                    path=str(target), sha256=hashlib.sha256(content).hexdigest(),
                    encrypted_marker="$CDNENC" in text or "BEGIN PROTECTED" in text,
                    subcircuits=[line for line in text.splitlines()
                                 if line.lower().startswith(".subckt")],
                    header=text.split(".subckt")[0][:1600]))
except Exception as exc:
    record["error"] = str(exc)
if "--cached" not in sys.argv:
    manifest["sources"].append(record)
(ROOT / "sources.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
(ROOT / "model_inspection.json").write_text(json.dumps(inspection, indent=2), encoding="utf-8")
print(json.dumps(inspection, indent=2))
try:
    from pypdf import PdfReader
    doc = PdfReader(cache / "ths4551_datasheet.pdf")
    text = "\n".join(page.extract_text() for page in doc.pages)
    (ROOT / "datasheet_snapshot.txt").write_text(text, encoding="utf-8")
    print("PDF text extracted", len(text))
except Exception as exc:
    print("PDF extraction unavailable", exc)
