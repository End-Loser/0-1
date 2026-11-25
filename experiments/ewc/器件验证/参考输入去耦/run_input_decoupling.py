"""Predeclared bias-input capacitor candidates; no altered vendor internals or gates."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import tempfile

import numpy as np

ROOT = Path(__file__).resolve().parent
PRIOR = ROOT.parent / "\u53c2\u8003\u53cd\u9988\u7535\u6d41\u590d\u6838"
LINKED = ROOT.parent / "\u771f\u5b9e\u53c2\u8003\u4f9b\u7535\u8054\u63a5"
sys.path.insert(0, str(LINKED))
import run_linked_reference as linked


def configurations(prior):
    base = {k: v for k, v in prior["runs"][0].items()
            if k not in ("files", "measurements", "rows", "status", "reason", "returncode")}
    base.update(real_rails=False, perturbation=0, bias_cap_F=10e-12, step_s=10e-12)
    configs = [dict(base, id=f"input_{pf}pF", input_cap_F=pf*1e-12)
               for pf in (0, 100, 1000, 10000)]
    configs += [
        dict(base, id="input_1000pF_5ps", input_cap_F=1e-9, step_s=5e-12),
        dict(base, id="input_10000pF_5ps", input_cap_F=10e-9, step_s=5e-12),
        dict(base, id="input_1000pF_ratio", input_cap_F=1e-9, perturbation=.001),
        dict(base, id="input_1000pF_realrails", input_cap_F=1e-9, real_rails=True),
        dict(base, id="input_1000pF_output100pF", input_cap_F=1e-9, bias_cap_F=100e-12),
    ]
    return configs


def deck(config):
    old_id = ("bias_1000pF_realrails" if config["real_rails"] else
              "bias_1000pF_ratio" if config["perturbation"] else "bias_10pF")
    net = (PRIOR / "results" / old_id / "test.cir").read_text(encoding="ascii")
    old_cap = 1e-9 if old_id != "bias_10pF" else 10e-12
    for i, node in enumerate(("cm", "refp", "refn"), 1):
        old = f"XB{i} b{i} fb{i} vb{i} 0 bout{i} EWC_BUFFER"
        assert net.count(old) == 1
        replacement = f"VBIN{i} b{i} bin{i} 0\nXB{i} bin{i} fb{i} vb{i} 0 bout{i} EWC_BUFFER"
        if config["input_cap_F"]:
            replacement += f"\nCBIN{i} b{i} 0 {config['input_cap_F']}"
        net = net.replace(old, replacement)
        old = f"CB{i} {node} 0 {old_cap}"
        assert net.count(old) == 1
        net = net.replace(old, f"CB{i} {node} 0 {config['bias_cap_F']}")
    old_tran = "tran 1e-11 1u 0 1e-11"
    assert net.count(old_tran) == 1
    net = net.replace(old_tran, f"tran {config['step_s']} 1u 0 {config['step_s']}")
    assert net.count("i(VFB3)\n") == 1
    return net.replace("i(VFB3)\n", "i(VFB3) v(b1) v(b2) v(b3) i(VBIN1) i(VBIN2) i(VBIN3)\n")


def main():
    assert not (ROOT / "summary.json").exists(), "Do not overwrite retained evidence"
    prior = json.loads((PRIOR / "summary.json").read_text(encoding="utf-8"))
    protected = {str(p): linked.sha(p) for p in PRIOR.rglob("*")
                 if p.is_file() and p.suffix in (".py", ".json", ".cir", ".log", ".csv", ".txt")}
    for path, digest in {**protected, **prior["original_files_sha256"]}.items():
        assert linked.sha(Path(path)) == digest, path
    simulator = next(Path(p) for p in prior["original_files_sha256"] if p.endswith(".exe"))
    helper = linked.load(linked.OLD / "run_validation.py")
    cache = Path(tempfile.mkdtemp(prefix="ewc-input-decoupling-"))
    (ROOT / "results").mkdir()
    configs = configurations(prior)
    (ROOT / "declared_configurations.json").write_text(json.dumps(configs, indent=2)+"\n", encoding="utf-8")
    with (PRIOR / "results/bias_10pF/wave.csv").open(encoding="ascii") as stream:
        header = stream.readline().strip()+",b1,b2,b3,i_bin1,i_bin2,i_bin3"

    def execute(config):
        work, output = cache / config["id"], ROOT / "results" / config["id"]
        work.mkdir()
        output.mkdir()
        net = deck(config)
        for path in (work / "test.cir", output / "test.cir"):
            path.write_text(net, encoding="ascii")
        (work / ".spiceinit").write_text("set ngbehavior=ps\n", encoding="ascii")
        (output / "spiceinit.txt").write_text("set ngbehavior=ps\n", encoding="ascii")
        result = dict(config, status="blocked")
        try:
            proc = subprocess.run([str(simulator), "-b", "test.cir"], cwd=work, capture_output=True,
                                  text=True, errors="replace", timeout=180)
            (output / "solver.log").write_text(proc.stdout+"\n"+proc.stderr, encoding="utf-8")
            result["returncode"] = proc.returncode
            assert proc.returncode == 0 and (work / "wave.txt").exists(), "No waveform"
            data = np.loadtxt(work / "wave.txt", skiprows=1)
            assert data.ndim == 2 and data.shape[1] == 42 and np.isfinite(data).all(), "Invalid vectors"
            assert np.all(np.diff(data[:, 0]) > 0), "Non-monotonic time"
            result["observed_last_time_s"] = float(data[-1, 0])
            assert data[-1, 0] >= 1e-6-1e-12, "Incomplete transient"
            np.savetxt(output / "wave.csv", data, delimiter=",", fmt="%.15e", header=header, comments="")
            measured = linked.measure(config, data[:, :29], helper)
            measured["buffer_output_max_A"] = float(abs(data[:, 29:32]).max())
            measured["feedback_input_max_A"] = float(abs(data[:, 33:36]).max())
            measured["positive_input_max_A"] = float(abs(data[:, 39:42]).max())
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

    with ThreadPoolExecutor(max_workers=3) as pool:
        runs = list(pool.map(execute, configs))
    assert all(linked.sha(Path(p)) == h for p, h in protected.items())
    summary = dict(runs=runs, cache=str(cache), previous_evidence_sha256=protected,
                   original_files_sha256=prior["original_files_sha256"], vendor_library_edits=False,
                   candidate_components_ideal=True, synthetic_receiver=True, upstream_ideal=True,
                   input_capacitors_require_startup_and_tolerance_validation=True,
                   full_G4_G5_passed=False, full_power_passed=False, startup_passed=False,
                   full_joint_passed=False, runner_sha256=linked.sha(Path(__file__)),
                   declared_configurations_sha256=linked.sha(ROOT / "declared_configurations.json"))
    (ROOT / "summary.json").write_text(json.dumps(summary, indent=2)+"\n", encoding="utf-8")
    for run in runs:
        print(run["id"], run["status"], run.get("reason", run.get("measurements", {}).get("dc_errors_V")))


if __name__ == "__main__":
    main()
