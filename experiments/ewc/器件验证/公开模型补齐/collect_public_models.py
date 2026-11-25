"""Bounded official downloads; proprietary libraries remain in the local TEMP cache."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import tempfile
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import zipfile

ROOT = Path(__file__).resolve().parent
SOURCES = [
    ("ref_page", "https://www.ti.com/product/REF5050"),
    ("adc_page", "https://www.ti.com/product/ADS8866"),
    ("ldo_page", "https://www.ti.com/product/TPS7A20"),
    ("ref_pspice", "https://www.ti.com/lit/zip/slim175"),
    ("ref_datasheet", "https://www.ti.com/lit/gpn/ref5050"),
    ("adc_family_ibis", "https://www.ti.com/lit/zip/sbam172"),
]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collect(item, cache):
    name, url = item
    row = dict(id=name, url=url, models_accepted=False)
    path = cache / (name + ".raw")
    try:
        with urlopen(Request(url, headers={"User-Agent": "EWC public model audit"}),
                     timeout=40) as response:
            content = response.read(25_000_001)
            assert len(content) <= 25_000_000, "Response exceeds collection bound"
            row.update(status=response.status, final_url=response.url,
                       content_type=response.headers.get("Content-Type"))
    except HTTPError as error:
        content = error.read()
        row.update(status=error.code, error=str(error))
    except Exception as error:
        row["error"] = str(error)
        return row
    path.write_bytes(content)
    row.update(cache=str(path), sha256=sha(path), bytes=len(content))
    text = content.decode("utf-8", errors="replace")
    row["official_lit_links"] = sorted(set(re.findall(
        r'(?:https://www\.ti\.com)?/lit/(?:zip|tsc|gpn)/[a-zA-Z0-9_.-]+', text)))
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            members = []
            for info in archive.infolist():
                assert info.file_size <= 10_000_000, "Member exceeds collection bound"
                data = archive.read(info)
                record = dict(name=info.filename, bytes=len(data),
                              sha256=hashlib.sha256(data).hexdigest())
                if Path(info.filename).suffix.lower() in (".lib", ".cir", ".ibs", ".txt", ".mod"):
                    decoded = data.decode("utf-8", errors="replace")
                    record.update(
                        encrypted=bool(re.search(r"(?im)^\s*\.?(?:protect|encrypt)|\$CDNENC", decoded)),
                        subcircuits=re.findall(r"(?im)^\s*\.subckt\s+([^\r\n]+)", decoded),
                        ibis_components=re.findall(r"(?im)^\s*\[Component\]\s+([^\r\n]+)", decoded),
                        ibis_version=re.findall(r"(?im)^\s*\[IBIS Ver\]\s+([^\r\n]+)", decoded))
                members.append(record)
            row["archive_members"] = members
    return row


def main():
    assert not (ROOT / "sources.json").exists(), "Refuse to overwrite source collection"
    cache = Path(tempfile.mkdtemp(prefix="ewc-public-models-"))
    with ThreadPoolExecutor(max_workers=3) as pool:
        rows = list(pool.map(lambda item: collect(item, cache), SOURCES))
    result = dict(scope="official_public_model_availability_not_device_acceptance",
                  initial_request_count=len(SOURCES), sources=rows, cache=str(cache),
                  redistribution=False, procurement=False, full_chain_accepted=False,
                  collector_sha256=sha(Path(__file__)))
    (ROOT / "sources.json").write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    for row in rows:
        print(row["id"], row.get("status"), row.get("error", ""),
              row.get("official_lit_links", []), row.get("archive_members", []))


if __name__ == "__main__":
    main()
