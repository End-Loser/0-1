"""Separate second-order integration comparison; never replace original failures."""
import ctypes
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from run_c_saved_history64 import archive_rows, configurations as saved_configurations, sha, save

ROOT = Path(__file__).resolve().parent
PRIOR = ROOT / "C输出64跳转_显式观测保存"
OUT = ROOT / "C输出64跳转_二阶梯形积分对照"
CACHE = ROOT.parent.parent / "ewc-solver-cache"


def configurations():
    return [dict(c, integration_method="trap", integration_max_order=2,
                 original_failure_replaced=False, tolerances_relaxed=False)
            for c in saved_configurations()]


def deck(original):
    old, new = ".options method=gear maxord=2", ".options method=trap maxord=2"
    assert original.splitlines().count(old) == 1
    return original.replace(old, new, 1)


def available_commit():
    class Performance(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_uint32)] + [
            (n, ctypes.c_size_t) for n in (
                "total", "limit", "peak", "physical", "available", "cache",
                "kernel", "paged", "nonpaged", "page")] + [
            (n, ctypes.c_uint32) for n in ("handles", "processes", "threads")]
    data = Performance()
    data.cb = ctypes.sizeof(data)
    assert ctypes.windll.psapi.GetPerformanceInfo(ctypes.byref(data), data.cb)
    return (data.limit - data.total) * data.page


def prepare():
    assert not OUT.exists()
    report_path = PRIOR / "verification_fdab1afa7ad1.json"
    prior_report = json.loads(report_path.read_bytes())
    evidence = dict(prior_report["evidence_sha256"])
    source_path = PRIOR / "running_source.json"
    source = json.loads(source_path.read_bytes())
    evidence.update(source["source_sha256"])
    evidence[str(report_path)] = sha(report_path)
    evidence[str(ROOT / "verify_c_saved_history64.py")] = prior_report["verifier_sha256"]
    for name in ("run_c_trapezoidal_history64.py", "verify_c_trapezoidal_history64.py",
                 "test_c_trapezoidal_history64.py"):
        evidence[str(ROOT / name)] = sha(ROOT / name)
    assert all(sha(Path(p)) == digest for p, digest in evidence.items())
    OUT.mkdir()
    configs = configurations()
    save(OUT / "declared_configurations.json", configs)
    save(OUT / "provenance.json", dict(
        protected_sha256=evidence, columns=source["columns"],
        declaration_sha256=sha(OUT / "declared_configurations.json"),
        only_integration_method_changed=True, actual_RF_model=False,
        real_rails=False, full_system_gates_closed=0, goal_complete=False))
    print("Prepared second-order trap comparison; no solver calls", flush=True)


def run():
    provenance = json.loads((OUT / "provenance.json").read_bytes())
    protected = provenance["protected_sha256"]
    assert all(sha(Path(p)) == digest for p, digest in protected.items())
    configs = json.loads((OUT / "declared_configurations.json").read_bytes())
    assert configs == configurations()
    assert sha(OUT / "declared_configurations.json") == provenance["declaration_sha256"]
    assert not (OUT / configs[0]["id"]).exists(), "Never rerun started evidence"
    simulator = next(Path(p) for p in protected if p.endswith(".exe"))
    assert CACHE.is_dir()
    cache = Path(tempfile.mkdtemp(prefix="ewc-trap64-", dir=CACHE))
    save(OUT / "execution_source.json", dict(cache=str(cache), simulator=str(simulator),
                                           solver_sha256=sha(simulator)))
    runs = []
    for config in configs:
        assert all(sha(Path(p)) == digest for p, digest in protected.items())
        if (available_commit() < 6 * 1024**3 or
                min(shutil.disk_usage(OUT).free, shutil.disk_usage(cache).free) < 10 * 1024**3):
            save(OUT / "resource_stop.json", dict(
                next_configuration=config, completed_runs=runs, no_next_solver_call=True))
            print("Stopped before next solver call for resources", flush=True)
            return
        folder, work = OUT / config["id"], cache / config["id"]
        folder.mkdir()
        work.mkdir()
        net = deck((PRIOR / config["id"] / "test.cir").read_text(encoding="ascii"))
        for target in (folder / "test.cir", work / "test.cir"):
            with target.open("x", encoding="ascii") as stream:
                stream.write(net)
        for target in (folder / "spiceinit.txt", work / ".spiceinit"):
            with target.open("x", encoding="ascii") as stream:
                stream.write("set ngbehavior=ps\n")
        record = dict(config, status="failed_preserve_partial")
        try:
            proc = subprocess.run([str(simulator), "-b", "test.cir"], cwd=work,
                                  timeout=config["solver_timeout_s"], capture_output=True)
            record["returncode"] = proc.returncode
            stdout, stderr = proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as error:
            record["returncode"] = None
            record["reason"] = "Declared solver timeout; outputs retained"
            stdout, stderr = error.stdout or b"", error.stderr or b""
        save(folder / "solver_exit.json", dict(returncode=record["returncode"],
                                              reason=record.get("reason")))
        (folder / "solver_stdout.log").write_bytes(stdout)
        (folder / "solver_stderr.log").write_bytes(stderr)
        wave = work / "wave.txt"
        if wave.exists():
            record["raw_wave"] = dict(path=str(wave), sha256=sha(wave))
            record.update(archive_rows(wave, folder, provenance["columns"]))
            if (record["returncode"] == 0 and record["time_strictly_monotonic"] and
                    record["observed_last_time_s"] >= config["duration_s"] - 1e-12):
                record["status"] = "solver_complete_unverified"
        record["files_sha256"] = {p.name: sha(p) for p in folder.iterdir()}
        save(folder / "run.json", record)
        runs.append(record)
        print(record["id"], record["status"], record.get("observed_last_time_s"), flush=True)
        if len(runs) == 1:
            proc = subprocess.run(
                [sys.executable, "-B", str(ROOT / "verify_c_trapezoidal_history64.py"), "--partial"],
                capture_output=True, text=True)
            (OUT / "probe_verifier_stdout.log").write_text(proc.stdout, encoding="utf-8")
            (OUT / "probe_verifier_stderr.log").write_text(proc.stderr, encoding="utf-8")
            accepted = False
            if proc.returncode == 0:
                report = json.loads(Path(json.loads(proc.stdout)["report"]).read_bytes())
                accepted = report["probe_admitted"]
            save(OUT / "probe_admission.json", dict(
                verifier_returncode=proc.returncode, admitted=accepted,
                phase_alignment_used=False, original_gates_unchanged=True))
            if not accepted:
                print("Probe rejected; no continuous-history solver calls", flush=True)
                return
    save(OUT / "summary.json", dict(runs=runs, provenance_sha256=sha(OUT / "provenance.json"),
                                    full_system_gates_closed=0, goal_complete=False))


if __name__ == "__main__":
    if "--prepare" in sys.argv:
        prepare()
    elif "--run" in sys.argv:
        run()
    else:
        raise SystemExit("Use --prepare or --run")
