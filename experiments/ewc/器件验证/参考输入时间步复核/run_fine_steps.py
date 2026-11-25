"""Four separate fine-step attempts retain the original nine-case evidence."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import tempfile

import numpy as np

ROOT = Path(__file__).resolve().parent
PRIOR = ROOT.parent / "\u53c2\u8003\u8f93\u5165\u53bb\u8026"
LINKED = ROOT.parent / "\u771f\u5b9e\u53c2\u8003\u4f9b\u7535\u8054\u63a5"
sys.path.insert(0, str(LINKED))
import run_linked_reference as linked


def main():
    assert not (ROOT / "summary.json").exists()
    prior = json.loads((PRIOR / "summary.json").read_text(encoding="utf-8"))
    protected = {str(p): linked.sha(p) for p in PRIOR.rglob("*")
                 if p.is_file() and p.suffix in (".py", ".json", ".cir", ".log", ".csv", ".txt")}
    for path, digest in {**protected, **prior["original_files_sha256"]}.items():
        assert linked.sha(Path(path)) == digest
    by_id = {r["id"]: r for r in prior["runs"]}
    cases = [("input_100pF", 5e-12), ("input_100pF", 2e-12),
             ("input_1000pF", 2e-12), ("input_1000pF_output100pF", 5e-12)]
    configs = []
    for identity, step in cases:
        config = {k: v for k, v in by_id[identity].items()
                  if k not in ("files", "measurements", "rows", "status", "reason",
                               "returncode", "observed_last_time_s")}
        config.update(id=f"{identity}_{int(step/1e-12)}ps", base_id=identity, step_s=step)
        configs.append(config)
    (ROOT / "declared_configurations.json").write_text(json.dumps(configs, indent=2)+"\n", encoding="utf-8")
    simulator = next(Path(p) for p in prior["original_files_sha256"] if p.endswith(".exe"))
    helper = linked.load(linked.OLD / "run_validation.py")
    cache = Path(tempfile.mkdtemp(prefix="ewc-input-fine-"))
    (ROOT / "results").mkdir()

    def execute(config):
        source = PRIOR / "results" / config["base_id"]
        work, output = cache / config["id"], ROOT / "results" / config["id"]
        work.mkdir()
        output.mkdir()
        old = (source / "test.cir").read_text(encoding="ascii")
        assert old.count("tran 1e-11 1u 0 1e-11") == 1
        net = old.replace("tran 1e-11 1u 0 1e-11",
                          f"tran {config['step_s']} 1u 0 {config['step_s']}")
        for path in (work / "test.cir", output / "test.cir"):
            path.write_text(net, encoding="ascii")
        (work / ".spiceinit").write_text("set ngbehavior=ps\n", encoding="ascii")
        (output / "spiceinit.txt").write_text("set ngbehavior=ps\n", encoding="ascii")
        result = dict(config, status="blocked")
        try:
            proc = subprocess.run([str(simulator), "-b", "test.cir"], cwd=work, capture_output=True,
                                  text=True, errors="replace", timeout=300)
            (output / "solver.log").write_text(proc.stdout+"\n"+proc.stderr, encoding="utf-8")
            result["returncode"] = proc.returncode
            assert proc.returncode == 0 and (work / "wave.txt").exists(), "No waveform"
            data = np.loadtxt(work / "wave.txt", skiprows=1)
            assert data.ndim == 2 and data.shape[1] == 42 and np.isfinite(data).all(), "Invalid vectors"
            assert np.all(np.diff(data[:, 0]) > 0), "Non-monotonic time"
            result["observed_last_time_s"] = float(data[-1, 0])
            assert data[-1, 0] >= 1e-6-1e-12, "Incomplete transient"
            with (source / "wave.csv").open(encoding="ascii") as stream:
                header = stream.readline().strip()
            np.savetxt(output / "wave.csv", data, delimiter=",", fmt="%.15e", header=header, comments="")
            measured = linked.measure(config, data[:, :29], helper)
            measured.update(buffer_output_max_A=float(abs(data[:, 29:32]).max()),
                            feedback_input_max_A=float(abs(data[:, 33:36]).max()),
                            positive_input_max_A=float(abs(data[:, 39:42]).max()))
            result.update(rows=len(data), measurements=measured,
                          status="fail" if measured["violations"] else "pass")
        except Exception as error:
            result["reason"] = str(error) or type(error).__name__
            if isinstance(error, subprocess.TimeoutExpired):
                text = error.stdout or b""
                if isinstance(text, bytes):
                    text = text.decode("utf-8", errors="replace")
                (output / "solver.log").write_text(text+"\nTIMEOUT\n", encoding="utf-8")
        if (work / "wave.txt").exists() and "rows" not in result:
            result["partial_waveform"] = dict(path=str(work / "wave.txt"),
                                             sha256=linked.sha(work / "wave.txt"), accepted=False)
        result["files"] = {p.relative_to(ROOT).as_posix(): linked.sha(p) for p in output.iterdir()}
        return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        runs = list(pool.map(execute, configs))
    assert all(linked.sha(Path(p)) == h for p, h in protected.items())
    summary = dict(runs=runs, cache=str(cache), previous_evidence_sha256=protected,
                   original_files_sha256=prior["original_files_sha256"],
                   physical_network_changed=False, vendor_library_edits=False,
                   solver_timeout_s=300, tolerances_changed=False,
                   full_G4_G5_passed=False, full_power_passed=False, startup_passed=False,
                   full_joint_passed=False, runner_sha256=linked.sha(Path(__file__)),
                   declared_configurations_sha256=linked.sha(ROOT / "declared_configurations.json"))
    (ROOT / "summary.json").write_text(json.dumps(summary, indent=2)+"\n", encoding="utf-8")
    for run in runs:
        print(run["id"], run["status"], run.get("reason", run.get("measurements", {}).get("dc_errors_V")))


if __name__ == "__main__":
    main()
