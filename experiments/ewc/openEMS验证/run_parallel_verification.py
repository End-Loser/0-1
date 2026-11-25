"""Bounded concurrent rechecks of archived evidence, not joint simulation."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_job(job):
    name, arguments = job
    started = time.perf_counter()
    try:
        process = subprocess.run([sys.executable, "-B", *arguments], cwd=ROOT,
                                 capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", timeout=120)
        return {"name": name, "exit_code": process.returncode,
                "elapsed_s": time.perf_counter() - started,
                "stdout": process.stdout, "stderr": process.stderr}
    except subprocess.TimeoutExpired:
        return {"name": name, "exit_code": None, "elapsed_s": time.perf_counter() - started,
                "stdout": "", "stderr": "Verification exceeded 120 seconds."}


def main(output, workers):
    jobs = [
        ("finite_PEC", ["openEMS验证/verify_finite_pec_cavity.py",
                        "openEMS验证/有限PEC腔体结果"]),
        ("device_evidence", ["器件验证/verify_offline.py"]),
        ("calibration_budget", ["校准验证/verify.py", "--results", "校准验证/结果",
                                "--code-dir", "校准验证"]),
        ("EM_regressions", ["-m", "unittest", "discover", "-s", "openEMS验证", "-p", "test_*.py"]),
    ]
    started = datetime.now(timezone.utc).isoformat()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(run_job, jobs))
    # Execution logs are intermediate evidence; keep them outside the workspace.
    logdir = Path(tempfile.mkdtemp(prefix="ewc-parallel-recheck-"))
    for result in results:
        log = logdir / (result["name"] + ".txt")
        log.write_text(result.pop("stdout") + "\n" + result.pop("stderr"), encoding="utf-8")
        result["log"] = str(log)
        result["log_sha256"] = sha(log)
        print(result["name"], "exit", result["exit_code"])
    artifacts = [
        "openEMS验证/有限PEC腔体结果/summary.json",
        "openEMS验证/有限PEC腔体结果/verification.json",
        "器件验证/source_manifest.json", "器件验证/interface_model_gaps.json",
        "器件验证/model_test_results.json", "器件验证/offline_verification.json",
        "校准验证/结果/summary.json", "校准验证/结果/verify.json",
        "校准验证/结果/tests.json",
    ]
    all_ok = all(r["exit_code"] == 0 for r in results)
    record = {
        "scope": "parallel offline evidence rechecks only, not joint physical simulation",
        "started_utc": started, "finished_utc": datetime.now(timezone.utc).isoformat(),
        "workers": workers, "python": sys.version, "jobs": results,
        "all_recheck_commands_pass": all_ok,
        "evidence_sha256": {p: sha(ROOT / p) for p in artifacts},
        "script_sha256": sha(Path(__file__)),
        "finite_copper_cavity_passed": False, "device_chain_passed": False,
        "real_calibration_hardware_passed": False,
        "full_joint_simulation_passed": False, "BOM_frozen": False,
    }
    output.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0 if all_ok else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, choices=[1, 2, 3], default=3)
    parser.add_argument("--output", type=Path, default=ROOT / "EWC并行验证第一批结果.json")
    args = parser.parse_args()
    raise SystemExit(main(args.output.resolve(), args.workers))
