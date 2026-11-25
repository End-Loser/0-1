"""Prespecified finer steps for five original failed capacitor-accounting cases."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

import numpy as np

ROOT = Path(__file__).resolve().parent
PRIOR = ROOT.parent / "\u4f9b\u7535\u5206\u5c42\u8054\u63a5"
CASE_IDS = ("dual_enable_cycle", "dual_ramp", "dual_reference_ramp",
            "buffer_real_rails", "buffer_real_ramp")
STEPS = (50e-9, 20e-9)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_prior_runner():
    spec = importlib.util.spec_from_file_location("supply_fixture", PRIOR / "run_supply_layers.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def configurations():
    declared = json.loads((PRIOR / "declared_configurations.json").read_text(encoding="utf-8"))
    return [dict(config, id=f"{config['id']}_{round(step * 1e9)}ns",
                 baseline_id=config["id"], step_s=step)
            for config in declared if config["id"] in CASE_IDS for step in STEPS]


def main():
    assert not (ROOT / "results").exists() and not (ROOT / "summary.json").exists()
    fixture = load_prior_runner()
    original = json.loads((PRIOR / "summary.json").read_text(encoding="utf-8"))
    original_checks = json.loads((PRIOR / "verification.json").read_text(encoding="utf-8"))
    assert {r["id"] for r in original_checks["runs"] if not r["capacitor_accounting_passed"]} == set(CASE_IDS)
    originals = original["original_files_sha256"]
    assert all(sha(Path(p)) == h for p, h in originals.items())
    models = {}
    for path in originals:
        for token, name in (("tps7a20", "regulator"), ("ref5050", "reference"), ("opax320", "buffer")):
            if token in path.lower():
                models[name] = Path(path)
    simulator = next(Path(p) for p in originals if p.lower().endswith(".exe"))
    protected = {str(p): sha(p) for p in PRIOR.rglob("*") if p.is_file()}
    configs = configurations()
    assert len(configs) == 10
    (ROOT / "declared_configurations.json").write_text(json.dumps(configs, indent=2) + "\n", encoding="utf-8")
    (ROOT / "results").mkdir()
    cache = Path(tempfile.mkdtemp(prefix="ewc-supply-capacitor-fine-"))
    runs = []
    for config in configs:
        assert all(sha(Path(p)) == h for p, h in originals.items())
        baseline = next(r for r in original["runs"] if r["id"] == config["baseline_id"])
        baseline_net = (PRIOR / "results" / baseline["id"] / "test.cir").read_text(encoding="ascii")
        reconstructed, _ = fixture.deck(baseline, models)
        assert reconstructed == baseline_net, "Baseline fixture changed"
        net, columns = fixture.deck(config, models)
        old_tran = f"tran {baseline['step_s']} {baseline['finish_s']} 0 {baseline['step_s']}"
        new_tran = f"tran {config['step_s']} {config['finish_s']} 0 {config['step_s']}"
        assert net.replace(new_tran, old_tran) == baseline_net, "Only declared time steps may change"
        work, output = cache / config["id"], ROOT / "results" / config["id"]
        work.mkdir()
        output.mkdir()
        for folder in (work, output):
            (folder / "test.cir").write_text(net, encoding="ascii")
        (work / ".spiceinit").write_text("set ngbehavior=ps\n", encoding="ascii")
        (output / "spiceinit.txt").write_text("set ngbehavior=ps\n", encoding="ascii")
        row = dict(config, status="blocked", columns=columns)
        try:
            proc = subprocess.run([str(simulator), "-b", "test.cir"], cwd=work,
                                  capture_output=True, text=True, errors="replace", timeout=240)
            (output / "solver.log").write_text(proc.stdout + "\n" + proc.stderr, encoding="utf-8")
            row["returncode"] = proc.returncode
            assert proc.returncode == 0 and (work / "wave.txt").exists(), "No complete solver output"
            data = np.loadtxt(work / "wave.txt", skiprows=1)
            assert data.ndim == 2 and data.shape[1] == len(columns) and np.isfinite(data).all()
            assert np.all(np.diff(data[:, 0]) > 0)
            row["observed_last_time_s"] = float(data[-1, 0])
            assert data[-1, 0] >= config["finish_s"] - 1e-12, "Incomplete transient"
            np.savetxt(output / "wave.csv", data, delimiter=",", fmt="%.15e",
                       header=",".join(columns), comments="")
            measured = fixture.measure(config, data, columns)
            row.update(rows=len(data), measurements=measured,
                       status="pass" if all(measured["checks"].values()) else "fail")
        except Exception as error:
            row["reason"] = str(error) or type(error).__name__
            if isinstance(error, subprocess.TimeoutExpired):
                text = error.stdout or b""
                if isinstance(text, bytes):
                    text = text.decode("utf-8", errors="replace")
                (output / "solver.log").write_text(text + "\nTIMEOUT\n", encoding="utf-8")
            if (work / "wave.txt").exists():
                shutil.copyfile(work / "wave.txt", output / "partial_wave.txt")
                row["partial_accepted"] = False
        row["files"] = {p.relative_to(ROOT).as_posix(): sha(p) for p in output.iterdir()}
        runs.append(row)
        print(config["id"], row["status"], row.get("reason", ""), flush=True)
    assert all(sha(Path(p)) == h for p, h in protected.items())
    summary = dict(
        scope="same_circuit_finer_steps_only_not_supply_repair", runs=runs,
        original_files_sha256=originals, previous_evidence_sha256=protected,
        declared_configurations_sha256=sha(ROOT / "declared_configurations.json"),
        runner_sha256=sha(Path(__file__)), cache=str(cache),
        vendor_library_edits=False, full_G4_G5_passed=False, full_power_passed=False,
        startup_safety_passed=False, full_joint_passed=False)
    (ROOT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
