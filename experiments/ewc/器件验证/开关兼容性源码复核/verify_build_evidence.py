"""Offline verification of build provenance; not a solver execution gate."""
import hashlib
import json
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent.parent
ANCHOR_MANIFEST = PROJECT / "原始资料" / "manifest.json"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def registered_digests():
    """Digests the anchor manifest already records as unrecoverable."""
    if not ANCHOR_MANIFEST.is_file():
        return set()
    data = json.loads(ANCHOR_MANIFEST.read_text(encoding="utf-8"))
    return {row["sha256"] for row in data.get("unrecoverable", []) if row.get("sha256")}


def audit():
    evidence = json.loads((ROOT / "build_evidence.json").read_text(encoding="utf-8"))
    binary = Path(evidence["blocked_binary"]["path"])
    cache = binary.parents[3]
    registered = registered_digests()
    unverified = []

    def anchor(path, digest, label):
        """Verify an anchor, or accept it as registered-unrecoverable when gone."""
        path = Path(path)
        if path.is_file():
            assert sha(path) == digest, label
            return True
        assert digest in registered, (
            label + " is missing and its digest is not registered as unrecoverable")
        unverified.append({"anchor": label, "recorded": str(path), "sha256": digest})
        return False

    assert evidence["user_authorized_portable_build"]
    assert evidence["portable_base_partially_configured"]
    assert not evidence["compiler_dependencies_installed"]
    for key in ("security_policy_bypassed", "global_path_changed", "compiled_original",
                "compiled_candidate", "candidate_solver_run", "full_regulator_fix_verified",
                "full_power_passed", "full_joint_passed"):
        assert evidence[key] is False, key
    for path, digest in evidence["original_files_sha256"].items():
        assert sha(Path(path)) == digest

    anchor(binary, evidence["blocked_binary"]["sha256"], "blocked_binary")
    release_ok = anchor(evidence["official_release_snapshot"]["path"],
                        evidence["official_release_snapshot"]["sha256"],
                        "official_release_snapshot")
    anchor(evidence["package_log"]["path"], evidence["package_log"]["sha256"], "package_log")

    package = evidence["portable_package"]
    assert package["official_digest"] == "sha256:" + package["sha256"]
    package_path = cache / package["name"]
    package_ok = anchor(package_path, package["sha256"], "portable_package")
    if release_ok:
        release = json.loads(Path(evidence["official_release_snapshot"]["path"])
                             .read_text(encoding="utf-8"))
        official = next(item for item in release["assets"]
                        if item["name"] == package["name"])
        assert official["digest"] == package["official_digest"]
        assert official["size"] == package["size"]
        if package_ok:
            assert official["size"] == package_path.stat().st_size
    else:
        assert package["checksum_match"] is True

    source = evidence["fixed_source"]
    tree_ok = anchor(source["tree_snapshot"], source["tree_snapshot_sha256"], "tree_snapshot")
    archive_path = cache / "ngspice-47.tar.gz"
    archive_ok = anchor(archive_path, source["archive_sha256"], "source_archive")
    complete = Path(source["complete_extraction_path"])
    extraction_ok = complete.is_dir()
    if not extraction_ok:
        unverified.append({"anchor": "complete_extraction",
                           "recorded": str(complete), "sha256": None})

    expected = None
    if tree_ok:
        tree = json.loads(Path(source["tree_snapshot"]).read_text(encoding="utf-8"))
        assert tree["truncated"] is False
        assert source["tree_truncated"] is False
        expected = {item["path"]: item["sha"]
                    for item in tree["tree"] if item["type"] == "blob"}
        assert source["regular_files_verified"] == len(expected)

    actual = None
    missing = None
    if archive_ok:
        actual = {}
        with tarfile.open(archive_path, "r:gz") as archive:
            for entry in archive:
                if not entry.isfile():
                    continue
                path = entry.name.removeprefix("ngspice-" + source["commit"] + "/")
                assert path != entry.name and path not in actual
                data = archive.extractfile(entry).read()
                actual[path] = hashlib.sha1(
                    ("blob %d\0" % len(data)).encode("ascii") + data
                ).hexdigest()
        if expected is not None:
            assert actual == expected
        assert len(actual) == source["regular_files_verified"]
        partial = cache / ("ngspice-" + source["commit"])
        missing = {path for path in actual if not (partial / path).is_file()}
        assert missing == set(source["missing_windows_extracted_files"])
        assert source["archive_complete"] and not source["windows_extraction_complete"]
        assert missing

    if expected is not None and extraction_ok:
        assert source["complete_extraction_git_blobs_verified"] == len(expected)
        for path, digest in expected.items():
            content = (complete / path).read_bytes()
            assert hashlib.sha1(
                ("blob %d\0" % len(content)).encode("ascii") + content
            ).hexdigest() == digest, path
    assert any(item["Id"] == 3077 and "bash.exe" in item["Message"]
               and "signing level" in item["Message"]
               for item in evidence["code_integrity_events"])
    environment_missing = not cache.is_dir()
    return dict(
        build_provenance_verified=True,
        provenance_scope=("byte-identical anchors re-verified on this host"
                          if not environment_missing else
                          "permanent copies only; %TEMP% toolchain anchors registered "
                          "unrecoverable in 原始资料/manifest.json"),
        environment_missing=environment_missing,
        toolchain_temp_root_present=cache.is_dir(),
        unverified_anchors=unverified,
        source_regular_files_verified=len(actual) if actual is not None else 0,
        claimed_source_regular_files=source["regular_files_verified"],
        windows_extracted_missing_files=len(missing) if missing is not None else
        len(source["missing_windows_extracted_files"]),
        separate_complete_extraction_verified=bool(expected is not None and extraction_ok),
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
