"""Download identified official buffer artifacts without redistributing libraries."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent
HELPER = ROOT.parent / "\u516c\u5f00\u6a21\u578b\u8865\u9f50" / "collect_public_models.py"


def main():
    assert not (ROOT / "sources.json").exists()
    spec = importlib.util.spec_from_file_location("official_collector", HELPER)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    cache = Path(tempfile.mkdtemp(prefix="ewc-buffer-official-"))
    requests = [
        ("buffer_page", "https://www.ti.com/product/OPA320"),
        ("buffer_pspice", "https://www.ti.com/lit/zip/sbom437"),
        ("buffer_datasheet", "https://www.ti.com/lit/gpn/opa320"),
    ]
    with ThreadPoolExecutor(max_workers=3) as pool:
        sources = list(pool.map(lambda item: helper.collect(item, cache), requests))
    for row in sources:
        if "archive_members" in row:
            with zipfile.ZipFile(row["cache"]) as archive:
                for member in row["archive_members"]:
                    if member["name"].lower().endswith((".lbr", ".lib", ".cir")):
                        raw = archive.read(member["name"])
                        member["header_preview"] = raw[:600].decode("ascii", errors="replace")
    output = dict(sources=sources, cache=str(cache), full_chain_accepted=False,
                  collector_sha256=helper.sha(Path(__file__)), helper_sha256=helper.sha(HELPER))
    (ROOT / "sources.json").write_text(json.dumps(output, indent=2)+"\n", encoding="utf-8")
    for row in sources:
        print(row["id"], row.get("status"))
        for member in row.get("archive_members", []):
            print(member)


if __name__ == "__main__":
    main()
