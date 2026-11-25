"""Offline original-download, ZIP-member and sampled model-type verification.

v2 keeps every identity/type assertion of verify_public_sources.py but stops
treating %TEMP% captures that 原始资料/manifest.json already records as
unrecoverable as hard preconditions. Untouched captures are still re-hashed;
a missing capture is only tolerated when its recorded digest is registered,
and it is reported in unverified_anchors. A new output file name keeps the
pinned source_verification.json byte-identical.
"""
import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent.parent
ANCHOR_MANIFEST = PROJECT / "\u539f\u59cb\u8d44\u6599" / "manifest.json"
OUTPUT = ROOT / "source_verification_env_missing.json"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def registered_digests():
    """Digests the anchor manifest already records as unrecoverable."""
    if not ANCHOR_MANIFEST.is_file():
        return set()
    data = json.loads(ANCHOR_MANIFEST.read_text(encoding="utf-8"))
    return {row["sha256"] for row in data.get("unrecoverable", []) if row.get("sha256")}


def audit():
    first = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
    followup = json.loads((ROOT / "followup.json").read_text(encoding="utf-8"))
    registered = registered_digests()
    unverified = []

    def anchor(path, digest, label):
        """Re-hash an anchor, or accept it as registered-unrecoverable when gone."""
        path = Path(path)
        if path.is_file():
            assert sha(path) == digest, label
            return True
        assert digest in registered, (
            label + " is missing and its digest is not registered as unrecoverable")
        unverified.append({"anchor": label, "recorded": str(path), "sha256": digest})
        return False

    count, members, absent = 0, 0, 0
    present = {}
    for row in first["sources"] + followup["sources"]:
        assert row["url"].startswith("https://www.ti.com/")
        path = Path(row["cache"])
        present[row["id"]] = anchor(path, row["sha256"], "source:" + row["id"])
        if not present[row["id"]]:
            absent += 1
            continue
        assert path.stat().st_size == row["bytes"]
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
    assert corrected["status"] == 200
    if present["ref_family_datasheet"]:
        assert Path(corrected["cache"]).read_bytes().startswith(b"%PDF")
    ibis = next(r for r in first["sources"] if r["id"] == "adc_family_ibis")
    components = [c.strip().lower() for m in ibis["archive_members"] for c in m.get("ibis_components", [])]
    assert components == ["ads8881dgs", "ads8881drc"]
    assert not any("ads8866" in c for c in components)
    for row in followup["github"] + followup["github_searches"]:
        anchor(Path(row["cache"]), row["sha256"], "capture:" + Path(row["cache"]).name)
    hits = json.loads((ROOT / "open_hits.json").read_text(encoding="utf-8"))
    for row in hits["hits"]:
        path = Path(row["cache"])
        if anchor(path, row["sha256"], "symbol:" + path.name):
            assert path.read_text(encoding="utf-8").startswith("EESchema-LIBRARY")
        assert row["kicad_symbol_library"] and row["spice_subcircuits"] == []
        assert row["dynamic_model_accepted"] is False
    anchor(Path(hits["repository_listing_cache"]), hits["repository_listing_sha256"],
           "repository_listing")
    result = dict(offline_verification_pass=True, original_downloads_checked=count,
                  original_downloads_registered_unrecoverable=absent,
                  environment_missing=bool(unverified), unverified_anchors=unverified,
                  zip_members_checked=members, sampled_symbol_hits_rejected=2,
                  ads8881_to_ads8866_substitution_accepted=False,
                  executable_rf_models_accepted=0, whole_chain_passed=False,
                  source_manifest_sha256=sha(ROOT / "sources.json"),
                  followup_manifest_sha256=sha(ROOT / "followup.json"),
                  open_hits_sha256=sha(ROOT / "open_hits.json"),
                  anchor_manifest_sha256=(sha(ANCHOR_MANIFEST)
                                          if ANCHOR_MANIFEST.is_file() else None),
                  verifier_sha256=sha(Path(__file__)))
    OUTPUT.write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    audit()
