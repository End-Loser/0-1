"""Offline verification of build provenance; not a solver execution gate."""
import hashlib
import json
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit():
    evidence = json.loads((ROOT / "build_evidence.json").read_text(encoding="utf-8"))
    binary = Path(evidence["blocked_binary"]["path"])
    cache = binary.parents[3]
    assert sha(binary) == evidence["blocked_binary"]["sha256"]
    assert evidence["user_authorized_portable_build"]
    assert evidence["portable_base_partially_configured"]
    assert not evidence["compiler_dependencies_installed"]
    for key in ("security_policy_bypassed", "global_path_changed", "compiled_original",
                "compiled_candidate", "candidate_solver_run", "full_regulator_fix_verified",
                "full_power_passed", "full_joint_passed"):
        assert evidence[key] is False, key
    for path, digest in evidence["original_files_sha256"].items():
        assert sha(Path(path)) == digest
    for record in (evidence["official_release_snapshot"], evidence["package_log"]):
        assert sha(Path(record["path"])) == record["sha256"]
    release = json.loads(Path(evidence["official_release_snapshot"]["path"])
                         .read_text(encoding="utf-8"))
    package = evidence["portable_package"]
    official = next(item for item in release["assets"] if item["name"] == package["name"])
    assert official["digest"] == package["official_digest"] == "sha256:" + package["sha256"]
    assert official["size"] == (cache / package["name"]).stat().st_size == package["size"]
    assert sha(cache / package["name"]) == package["sha256"]
    source = evidence["fixed_source"]
    assert sha(Path(source["tree_snapshot"])) == source["tree_snapshot_sha256"]
    tree = json.loads(Path(source["tree_snapshot"]).read_text(encoding="utf-8"))
    assert tree["truncated"] is False
    expected = {item["path"]: item["sha"] for item in tree["tree"] if item["type"] == "blob"}
    assert sha(cache / "ngspice-47.tar.gz") == source["archive_sha256"]
    actual = {}
    with tarfile.open(cache / "ngspice-47.tar.gz", "r:gz") as archive:
        for entry in archive:
            if not entry.isfile():
                continue
            path = entry.name.removeprefix("ngspice-" + source["commit"] + "/")
            assert path != entry.name and path not in actual
            data = archive.extractfile(entry).read()
            actual[path] = hashlib.sha1(
                ("blob %d\0" % len(data)).encode("ascii") + data
            ).hexdigest()
    assert actual == expected
    assert len(actual) == source["regular_files_verified"]
    partial = cache / ("ngspice-" + source["commit"])
    missing = {path for path in actual if not (partial / path).is_file()}
    assert missing == set(source["missing_windows_extracted_files"])
    assert source["archive_complete"] and not source["windows_extraction_complete"]
    assert missing
    complete = Path(source["complete_extraction_path"])
    assert source["complete_extraction_git_blobs_verified"] == len(expected)
    for path, digest in expected.items():
        content = (complete / path).read_bytes()
        assert hashlib.sha1(
            ("blob %d\0" % len(content)).encode("ascii") + content
        ).hexdigest() == digest, path
    assert any(item["Id"] == 3077 and "bash.exe" in item["Message"]
               and "signing level" in item["Message"]
               for item in evidence["code_integrity_events"])
    return dict(
        build_provenance_verified=True, source_regular_files_verified=len(actual),
        windows_extracted_missing_files=len(missing),
        separate_complete_extraction_verified=True,
        application_control_block_recorded=True, compiled_candidate=False,
        candidate_solver_run=False, full_power_passed=False, full_joint_passed=False,
        evidence_sha256=sha(ROOT / "build_evidence.json"),
        verifier_sha256=sha(Path(__file__)),
    )


if __name__ == "__main__":
    result = audit()
    (ROOT / "build_verification.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    print("Build provenance verified; candidate execution NOT verified")
