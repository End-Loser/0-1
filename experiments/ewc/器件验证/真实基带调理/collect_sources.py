"""Bounded official TI source collection; originals remain in TEMP."""
import hashlib
import json
from pathlib import Path
import re
import tempfile
import urllib.request
import zipfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
CACHE = Path(tempfile.mkdtemp(prefix="ewc-dc-official-"))
records = []


def fetch(key, url):
    record = dict(id=key, url=url, retrieved_utc=datetime.now(timezone.utc).isoformat())
    try:
        with urllib.request.urlopen(url, timeout=35) as response:
            data = response.read()
            record.update(final_url=response.url, status=response.status,
                          content_type=response.headers.get("Content-Type"))
        path = CACHE / key
        path.write_bytes(data)
        record.update(cache=str(path), bytes=len(data),
                      sha256=hashlib.sha256(data).hexdigest())
        return data
    except Exception as exc:
        record["error"] = str(exc)
        return b""
    finally:
        records.append(record)


page = fetch("ths4551_page.html", "https://www.ti.com/product/THS4551")
text = page.decode("utf-8", errors="replace")
links = sorted(set(re.findall(r'https://www\.ti\.com/lit/(?:zip|[a-z]+/[a-z]+/[a-z0-9]+)[^"<> \t\n]*', text)))
model_links = sorted(set(re.findall(r'[^"<> \t\n]*[Ss][Bb][Oo][^"<> \t\n]*', text)))
snapshot = dict(candidate="THS4551", links=links, model_links=model_links,
                snippets=[text[max(0, m.start()-400):m.end()+600]
                          for m in re.finditer("PSpice", text, re.I)])
fetch("ths4551_datasheet.pdf", "https://www.ti.com/lit/gpn/THS4551")
(ROOT / "discovery.json").write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
(ROOT / "sources.json").write_text(json.dumps(dict(cache=str(CACHE), sources=records),
                                            indent=2), encoding="utf-8")
print(json.dumps(snapshot, indent=2))
print("CACHE", CACHE)
