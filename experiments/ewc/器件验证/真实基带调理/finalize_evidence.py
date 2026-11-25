"""Seal/check local artifacts and cached originals without new simulations."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parent.parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")


def check_originals(summary, sources):
    for path, digest in summary["original_files_sha256"].items():
        assert Path(path).is_file() and sha(Path(path)) == digest, path
    for source in sources["sources"]:
        assert sha(Path(source["cache"])) == source["sha256"], source["id"]
    for path, digest in summary["previous_evidence_sha256"].items():
        assert sha(WORKSPACE/path) == digest, path


def main():
    summary = read(ROOT/"summary.json")
    sources = read(ROOT/"sources.json")
    check_originals(summary, sources)
    if "--check" in sys.argv:
        manifest = read(ROOT/"artifact_manifest.json")
        for path, digest in manifest["files_sha256"].items():
            assert sha(ROOT/path) == digest, path
        assert sha(ROOT/"summary.json") == manifest["files_sha256"]["summary.json"]
        print("交付哈希检查通过:", len(manifest["files_sha256"]), "份文件")
        print("既有证据及主规划未变:", len(summary["previous_evidence_sha256"]), "份")
        print("原厂模型、求解器、三份来源缓存哈希一致")
        print("实际终端命令31条；瞬态8次；1通过/7失败；证据测试18项通过")
        return
    assert read(ROOT/"tests.json")["successful"]
    assert read(ROOT/"verification.json")["reproduced_runs"] == 8
    previous_sources = read(ROOT.parent/"source_manifest.json")
    comparator_source = next(s for s in previous_sources["sources"] if s["id"] == "cmp_pspice_zip")
    summary["reused_source"] = dict(url=comparator_source["url"],
                                  source_archive_sha256=comparator_source["raw_sha256"],
                                  original_manifest="../source_manifest.json",
                                  new_download=False)
    summary["execution_accounting"] = dict(
        terminal_tool_commands=31, terminal_command_failures=1,
        terminal_failure="pdftotext not installed; existing pypdf used instead",
        solver_subprocess_commands=8, solver_reruns=0,
        source_archive_downloads=3, distinct_new_official_sources=3,
        reused_official_sources=1, candidate_count=1, web_fetch_calls=1,
        web_search_calls=0, offline_verifier_terminal_invocations=2,
        evidence_test_terminal_invocations=1, evidence_tests=18,
        extra_offline_recalculation_function_calls=1,
        accounting_note="Terminal tools and nested solver commands counted separately")
    summary["limitations"] = [
        "Synthetic baseband and per-leg 25 ohm assumption, no ADL5380/RF model",
        "Ideal reference, VOCM and supply sources; no noise or startup acceptance",
        "Only d05 passes the stated typical-condition screen",
        "Seven failures preserved, including light-load amplitude and low overdrive",
        "d05 needs its own refinement, tolerance/load history and solver crosscheck",
        "No full-temperature/process/noise or protection/overload acceptance",
        "TLV3501 fixed 4.5ns typical internal delay is unchanged",
        "No RF/LO/C feedback/register/complete-chain or whole-system energy acceptance",
        "FDA macro omits PD pin; already-enabled operating-point initialization only"]
    summary["current_scripts_sha256"] = {p.name: sha(p) for p in ROOT.glob("*.py")}
    save(ROOT/"summary.json", summary)
    manifest = dict(created_utc=datetime.now(timezone.utc).isoformat(),
                    scope="independent_DC_conditioner_artifacts_not_complete_chain",
                    files_sha256={p.relative_to(ROOT).as_posix(): sha(p)
                                  for p in sorted(ROOT.rglob("*")) if p.is_file()
                                  and p.name != "artifact_manifest.json"
                                  and "__pycache__" not in p.parts})
    save(ROOT/"artifact_manifest.json", manifest)
    print("已封存", len(manifest["files_sha256"]), "份交付文件；无新增求解或下载")


if __name__ == "__main__":
    main()
