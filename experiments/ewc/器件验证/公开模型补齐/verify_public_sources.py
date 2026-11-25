"""Offline original-download, ZIP-member and sampled model-type verification."""
import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit():
    first = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
    followup = json.loads((ROOT / "followup.json").read_text(encoding="utf-8"))
    count, members = 0, 0
    for row in first["sources"] + followup["sources"]:
        assert row["url"].startswith("https://www.ti.com/")
        path = Path(row["cache"])
        assert sha(path) == row["sha256"] and path.stat().st_size == row["bytes"]
        count += 1
        if "archive_members" in row:
            with zipfile.ZipFile(path) as archive:
                for member in row["archive_members"]:
                    raw = archive.read(member["name"])
                    assert hashlib.sha256(raw).hexdigest() == member["sha256"]
                    members += 1
                    if "subcircuits" in member:
                        text = raw.decode("utf-8", errors="replace")
                        assert re.findall(r"(?im)^\s*\.subckt\s+([^\r\n]+)", text) == member["subcircuits"]
                    if "ibis_components" in member:
                        text = raw.decode("utf-8", errors="replace")
                        assert re.findall(r"(?im)^\s*\[Component\]\s+([^\r\n]+)", text) == member["ibis_components"]
    assert next(r for r in first["sources"] if r["id"] == "ref_datasheet")["status"] == 404
    corrected = next(r for r in followup["sources"] if r["id"] == "ref_family_datasheet")
    assert corrected["status"] == 200 and Path(corrected["cache"]).read_bytes().startswith(b"%PDF")
    ibis = next(r for r in first["sources"] if r["id"] == "adc_family_ibis")
    components = [c.strip().lower() for m in ibis["archive_members"] for c in m.get("ibis_components", [])]
    assert components == ["ads8881dgs", "ads8881drc"]
    assert not any("ads8866" in c for c in components)
    for row in followup["github"] + followup["github_searches"]:
        assert sha(Path(row["cache"])) == row["sha256"]
    hits = json.loads((ROOT / "open_hits.json").read_text(encoding="utf-8"))
    for row in hits["hits"]:
        path = Path(row["cache"])
        assert sha(path) == row["sha256"]
        assert path.read_text(encoding="utf-8").startswith("EESchema-LIBRARY")
        assert row["kicad_symbol_library"] and row["spice_subcircuits"] == []
        assert row["dynamic_model_accepted"] is False
    assert sha(Path(hits["repository_listing_cache"])) == hits["repository_listing_sha256"]
    result = dict(offline_verification_pass=True, original_downloads_checked=count,
                  zip_members_checked=members, sampled_symbol_hits_rejected=2,
                  ads8881_to_ads8866_substitution_accepted=False,
                  executable_rf_models_accepted=0, whole_chain_passed=False,
                  source_manifest_sha256=sha(ROOT / "sources.json"),
                  followup_manifest_sha256=sha(ROOT / "followup.json"),
                  open_hits_sha256=sha(ROOT / "open_hits.json"),
                  verifier_sha256=sha(Path(__file__)))
    (ROOT / "source_verification.json").write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    audit()
