"""Fetch linked vendor archives; do not decrypt or redistribute models."""
import hashlib
import json
from pathlib import Path
import re
from urllib.request import Request, urlopen
import zipfile

ROOT = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    assert not (ROOT / "models.json").exists()
    sources = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
    cache = Path(sources["cache"])
    records = []
    for identifier in ("SBAM485", "SBAM420"):
        url = "https://www.ti.com/lit/zip/" + identifier
        assert url in sources["sources"][0]["model_archive_links"]
        with urlopen(Request(url, headers={"User-Agent": "EWC public model verification"}),
                     timeout=45) as response:
            content = response.read(20_000_001)
            assert len(content) <= 20_000_000
            record = dict(id=identifier, url=url, final_url=response.url,
                          status=response.status)
        archive_path = cache / (identifier + ".zip")
        archive_path.write_bytes(content)
        record.update(path=str(archive_path), sha256=sha(archive_path), bytes=len(content))
        members = []
        with zipfile.ZipFile(archive_path) as archive:
            assert sum(item.file_size for item in archive.infolist()) < 100_000_000
            for member in archive.infolist():
                assert member.file_size < 80_000_000
                digest = hashlib.sha256()
                with archive.open(member) as stream:
                    for chunk in iter(lambda: stream.read(65536), b""):
                        digest.update(chunk)
                item = dict(name=member.filename, bytes=member.file_size,
                            sha256=digest.hexdigest())
                if Path(member.filename).suffix.lower() in (".lib", ".cir", ".ibs", ".txt"):
                    assert member.file_size < 2_000_000
                    data = archive.read(member)
                    text = data.decode("utf-8", errors="replace")
                    item.update(
                        subcircuits=re.findall(r"(?im)^\s*\.subckt\s+([^\r\n]+)", text),
                        encrypted=bool(re.search(
                            r"(?im)^\s*\.?(?:protect|encrypt)|\$CDNENC", text)),
                        ibis_components=re.findall(r"(?im)^\[Component\]\s+([^\r\n]+)", text),
                        header=text[:2500],
                    )
                members.append(item)
        record["members"] = members
        records.append(record)
    (ROOT / "models.json").write_text(json.dumps(dict(
        archives=records, originals_redistributed=False, model_executed=False,
        first_attempt_failed="20 MB member bound rejected vendor 69 MB binary waveform; "
                             "subsequent inventory streams hashes without decoding waveform",
        supported_pspice_verified=False, collector_sha256=sha(Path(__file__)),
    ), indent=2) + "\n", encoding="utf-8")
    for record in records:
        print(record["id"], record["status"], "members", len(record["members"]))
        for member in record["members"]:
            if "header" in member:
                print(member["name"], member["header"][:1800])


if __name__ == "__main__":
    main()
