"""Collect five fixed official resources; never build an electrical model."""

import concurrent.futures
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader


ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent.parent
SOURCES = (
    ("S1", "132139", "html", "https://www.amphenolrf.com/132139.html"),
    ("S2", "901-9211", "html", "https://www.amphenolrf.com/en-us/part/901-9211/4281/"),
    ("S3", "132139", "pdf", "https://www.amphenolrf.com/en-us/assets/file/4065786252/"),
    ("S4", "901-9211", "pdf", "https://www.amphenolrf.com/en-us/assets/file/4065788912/"),
    ("S5", "901-9211", "step", "https://www.amphenolrf.com/en-us/assets/file/4068628677/"),
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def collect(source, raw_dir):
    sid, part, kind, url = source
    result = {"id": sid, "part": part, "kind": kind, "url": url,
              "retrieved_utc": datetime.now(timezone.utc).isoformat()}
    try:
        response = requests.get(url, timeout=(15, 45), headers={
            "User-Agent": "Mozilla/5.0 (compatible; EWC-document-audit)"})
        raw = response.content
        raw_path = raw_dir / (sid + "." + kind)
        raw_path.write_bytes(raw)
        result.update(status_code=response.status_code, final_url=response.url,
                      redirects=[item.url for item in response.history],
                      content_type=response.headers.get("Content-Type", ""),
                      raw_path=str(raw_path), raw_bytes=len(raw), raw_sha256=sha(raw))
        response.raise_for_status()
        if kind == "html":
            soup = BeautifulSoup(raw, "html.parser")
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            text = soup.get_text("\n", strip=True)
            links = [{"text": a.get_text(" ", strip=True), "href": a.get("href")}
                     for a in soup.find_all("a", href=True)
                     if "assets/file/" in a["href"]]
            text += "\n\nEXTRACTED_ASSET_LINKS\n" + json.dumps(
                links, ensure_ascii=False, indent=2)
        elif kind == "pdf":
            if not raw.startswith(b"%PDF-"):
                raise ValueError("Response is not PDF")
            reader = PdfReader(io.BytesIO(raw))
            text = "\n\n".join(
                f"PAGE {number}\n{page.extract_text() or ''}"
                for number, page in enumerate(reader.pages, 1))
            result["pages"] = len(reader.pages)
        else:
            text = raw.decode("ascii")
            if "ISO-10303-21;" not in text or "END-ISO-10303-21;" not in text:
                raise ValueError("Response is not a STEP exchange file")
            result["entity_counts"] = {
                entity: text.count(entity + "(") for entity in
                ("MANIFOLD_SOLID_BREP", "CLOSED_SHELL", "ADVANCED_FACE",
                 "MATERIAL_DESIGNATION", "SURFACE_STYLE_USAGE")}
        evidence = ROOT / f"{sid}_{part}_{kind}.txt"
        evidence.write_text(text, encoding="utf-8")
        result.update(acquired=True, evidence=evidence.name,
                      evidence_sha256=sha(evidence.read_bytes()),
                      evidence_bytes=evidence.stat().st_size)
    except Exception as exc:
        result.update(acquired=False, error=f"{type(exc).__name__}: {exc}")
    return result


def main():
    if (ROOT / "collection.json").exists():
        raise SystemExit("collection.json exists; refusing to overwrite evidence")
    raw_dir = Path(tempfile.mkdtemp(prefix="ewc-sma-audit-", dir=os.environ["TEMP"]))
    baseline = []
    for path in sorted(ROOT.parent.rglob("*")):
        if path.is_file() and ROOT not in path.parents:
            baseline.append({"path": str(path.relative_to(PROJECT)),
                             "sha256": sha(path.read_bytes())})
    plan = PROJECT / "EWC真实材料与器件统一规划.md"
    baseline.append({"path": str(plan.relative_to(PROJECT)),
                     "sha256": sha(plan.read_bytes())})
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(lambda src: collect(src, raw_dir), SOURCES))
    payload = {"schema": 1, "source_limit": 5, "candidate_limit": 2,
               "raw_directory": str(raw_dir), "sources": results,
               "baseline": baseline,
               "note": "原始资料仅存TEMP；文本抽取不是电磁场模型。"}
    (ROOT / "collection.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps([{"id": x["id"], "acquired": x["acquired"],
                       "error": x.get("error"), "bytes": x.get("raw_bytes")}
                      for x in results], ensure_ascii=False, indent=2))
    print("TEMP:", raw_dir)


if __name__ == "__main__":
    main()
