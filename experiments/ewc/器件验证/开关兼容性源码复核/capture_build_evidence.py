"""Audit official archives without executing a policy-blocked build tool."""
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parent
CACHE = Path(r"C:\Users\13062\AppData\Local\Temp\ewc-build47")
COMMIT = "a80f6e3e95d51534905b1f23410a951802666656"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def api(endpoint):
    process = subprocess.run(["gh", "api", endpoint], capture_output=True)
    if process.returncode:
        raise RuntimeError(process.stderr.decode("utf-8", errors="replace"))
    return json.loads(process.stdout)


def audit_archive(archive, tree):
    """Compare every regular archive file to its fixed-commit Git blob."""
    expected = {item["path"]: item["sha"] for item in tree["tree"]
                if item["type"] == "blob"}
    found = {}
    with tarfile.open(archive, "r:gz") as package:
        for item in package.getmembers():
            if not item.isfile():
                continue
            relative = item.name.split("/", 1)[1]
            assert relative not in found, f"Duplicate archive file: {relative}"
            content = package.extractfile(item).read()
            blob = hashlib.sha1(
                b"blob " + str(len(content)).encode() + b"\0" + content
            ).hexdigest()
            assert relative in expected, f"Unexpected archive file: {relative}"
            assert blob == expected[relative], f"Git blob mismatch: {relative}"
            found[relative] = blob
    assert found == expected, f"Missing archive blobs: {set(expected) - set(found)}"
    return found


def main():
    package = CACHE / "msys2-base-x86_64-20260927.tar.xz"
    release = api("repos/msys2/msys2-installer/releases/tags/2026-09-27")
    asset = next(item for item in release["assets"] if item["name"] == package.name)
    assert package.stat().st_size == asset["size"]
    assert asset["digest"] == "sha256:" + sha(package)
    tree = api(f"repos/imr/ngspice/git/trees/{COMMIT}?recursive=1")
    assert tree["truncated"] is False
    tree_snapshot = CACHE / "official_source_tree.json"
    release_snapshot = CACHE / "official_msys_release.json"
    tree_snapshot.write_text(json.dumps(tree, indent=2) + "\n", encoding="utf-8")
    release_snapshot.write_text(json.dumps(release, indent=2) + "\n", encoding="utf-8")
    source_archive = CACHE / "ngspice-47.tar.gz"
    blobs = audit_archive(source_archive, tree)
    # Preserve original logs; no downgrade, alternate launcher, or policy changes.
    log = CACHE / "msys64/var/log/pacman.log"
    log_text = log.read_text(encoding="utf-8")
    assert "[ALPM] upgraded bash (5.3.020-1 -> 5.3.20-3)" in log_text
    assert "[ALPM] upgraded msys2-runtime (3.6.10-5 -> 3.6.12-1)" in log_text
    command = (
        "Get-WinEvent -FilterHashtable "
        "@{LogName='Microsoft-Windows-CodeIntegrity/Operational';"
        "StartTime=[datetime]'2026-11-18T03:24:00';"
        "EndTime=[datetime]'2026-11-18T03:28:00'} | "
        "Where-Object { $_.Message -match 'ewc-build47' } | "
        "Select-Object @{Name='time';Expression={$_.TimeCreated.ToString('o')}},"
        "Id,Message | ConvertTo-Json -Depth 3 -Compress"
    )
    process = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        capture_output=True, check=True,
    )
    events = json.loads(process.stdout.decode("utf-8-sig", errors="replace"))
    if isinstance(events, dict):
        events = [events]
    assert any(event["Id"] == 3077 and "bash.exe" in event["Message"]
               for event in events)
    original_sources = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
    for item in original_sources["files"]:
        assert blobs[item["path"]] == item["git_blob_sha"]
    originals = json.loads(
        (ROOT.parent / "\u4f9b\u7535\u5206\u5c42\u8054\u63a5/summary.json")
        .read_text(encoding="utf-8")
    )["original_files_sha256"]
    assert all(sha(Path(path)) == digest for path, digest in originals.items())
    partial = CACHE / f"ngspice-{COMMIT}"
    absent = [path for path in blobs if not (partial / path).is_file()]
    complete = CACHE / "source-complete" / f"ngspice-{COMMIT}"
    for path, expected_blob in blobs.items():
        data = (complete / path).read_bytes()
        actual_blob = hashlib.sha1(
            b"blob " + str(len(data)).encode() + b"\0" + data
        ).hexdigest()
        assert actual_blob == expected_blob, path
    result = dict(
        user_authorized_portable_build=True,
        portable_package=dict(
            name=asset["name"], official_asset_url=asset["browser_download_url"],
            actual_download_url="https://repo.msys2.org/distrib/x86_64/" + package.name,
            sha256=sha(package), official_digest=asset["digest"],
            size=asset["size"], checksum_match=True, detached_signature_verified=False,
        ),
        fixed_source=dict(
            commit=COMMIT, archive_sha256=sha(source_archive),
            tree_snapshot=str(tree_snapshot), tree_snapshot_sha256=sha(tree_snapshot),
            url=f"https://codeload.github.com/imr/ngspice/tar.gz/{COMMIT}",
            complete_git_blob_comparison=True, regular_files_verified=len(blobs),
            tree_truncated=False, missing_windows_extracted_files=absent,
            archive_complete=True, windows_extraction_complete=not absent,
            complete_extraction_path=str(complete),
            complete_extraction_git_blobs_verified=len(blobs),
        ),
        package_log=dict(path=str(log), sha256=sha(log)),
        official_release_snapshot=dict(path=str(release_snapshot),
                                       sha256=sha(release_snapshot)),
        blocked_binary=dict(path=str(CACHE / "msys64/usr/bin/bash.exe"),
                            sha256=sha(CACHE / "msys64/usr/bin/bash.exe")),
        code_integrity_events=events,
        portable_base_partially_configured=True,
        compiler_dependencies_installed=False,
        security_policy_bypassed=False,
        global_path_changed=False,
        original_files_sha256=originals,
        compiled_original=False,
        compiled_candidate=False,
        candidate_solver_run=False,
        full_regulator_fix_verified=False,
        full_power_passed=False,
        full_joint_passed=False,
        runner_sha256=sha(Path(__file__)),
    )
    (ROOT / "build_evidence.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print("Official portable archive SHA-256 matched")
    print("Fixed release archive Git blobs verified:", len(blobs))
    print("Windows extracted missing files:", len(absent))
    print("Separate 7-Zip extraction Git blobs verified:", len(blobs))
    print("Application control block preserved; compilation not started")


if __name__ == "__main__":
    main()
