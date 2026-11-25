"""Run a bounded registry of five independent offline verification lines."""

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


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def execute(job):
    started = time.perf_counter()
    commands, output = [], []
    try:
        for arguments in [job["arguments"], *job.get("post_arguments", [])]:
            p = subprocess.run([sys.executable, "-B", *arguments], cwd=ROOT,
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=180)
            commands.append({"arguments": arguments, "exit_code": p.returncode})
            output.append(p.stdout + "\n" + p.stderr)
            if p.returncode != 0:
                break
        result = {"line": job["line"], "exit_code": p.returncode,
                  "commands": commands, "elapsed_s": time.perf_counter() - started,
                  "output": "\n".join(output)}
    except subprocess.TimeoutExpired:
        result = {"line": job["line"], "exit_code": None,
                  "commands": commands, "elapsed_s": time.perf_counter() - started,
                  "output": "\n".join(output) + "\nTimeout: 180 s"}
    return result


def main(registry, output, workers):
    config = json.loads(registry.read_text(encoding="utf-8"))
    if len(config["verification_lines"]) != 5:
        raise ValueError("The second-batch registry must declare five independent lines.")
    jobs = config["verification_lines"] + config["regressions"]
    started = datetime.now(timezone.utc).isoformat()
    logdir = Path(tempfile.mkdtemp(prefix="ewc-expanded-verification-"))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(execute, jobs))
    for result in results:
        log = logdir / (result["line"] + ".txt")
        log.write_text(result.pop("output"), encoding="utf-8")
        result.update({"log": str(log), "log_sha256": digest(log)})
        print(result["line"], "exit", result["exit_code"])
    evidence, missing = {}, []
    for relative in config["evidence"]:
        path = (ROOT / relative).resolve()
        if not path.is_relative_to(ROOT):
            raise ValueError("Evidence must stay inside the project workspace.")
        if path.is_file():
            evidence[relative] = digest(path)
        else:
            missing.append(relative)
    passed = all(r["exit_code"] == 0 for r in results) and not missing
    record = {
        "scope": "five-line independent evidence rechecks, not coupled physical simulation",
        "started_utc": started, "finished_utc": datetime.now(timezone.utc).isoformat(),
        "workers": workers, "verification_lines": 5, "jobs": results,
        "all_recheck_commands_pass": passed, "evidence_sha256": evidence,
        "missing_evidence": missing, "registry_sha256": digest(registry),
        "script_sha256": digest(Path(__file__)),
        "copper_Q_accuracy_passed": False, "real_copper_cavity_passed": False,
        "real_RF_port_passed": False, "real_receiver_chain_passed": False,
        "real_calibration_hardware_passed": False,
        "dielectric_3GHz_guarantee_passed": False,
        "full_joint_simulation_passed": False, "BOM_frozen": False,
    }
    output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if passed else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--registry", type=Path, default=ROOT / "EWC扩展并行验证任务.json")
    p.add_argument("--output", type=Path, default=ROOT / "EWC扩展并行验证结果.json")
    p.add_argument("--workers", type=int, choices=range(1, 6), default=4)
    a = p.parse_args()
    raise SystemExit(main(a.registry.resolve(), a.output.resolve(), a.workers))
