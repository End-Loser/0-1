"""Add only zero-voltage feedback-input probes to the retained seven decks."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import tempfile

import numpy as np

ROOT = Path(__file__).resolve().parent
PRIOR = ROOT.parent / "\u53c2\u8003\u8282\u70b9\u53bb\u8026"
LINKED = ROOT.parent / "\u771f\u5b9e\u53c2\u8003\u4f9b\u7535\u8054\u63a5"
sys.path.insert(0, str(LINKED))
import run_linked_reference as linked


def instrument(net):
    for i, node in enumerate(("cm", "refp", "refn"), 1):
        old = f"XB{i} b{i} {node} vb{i} 0 bout{i} EWC_BUFFER"
        assert net.count(old) == 1
        net = net.replace(old, f"VFB{i} {node} fb{i} 0\n"
                          f"XB{i} b{i} fb{i} vb{i} 0 bout{i} EWC_BUFFER")
    assert net.count("i(VCMLOAD)\n") == 1
    return net.replace("i(VCMLOAD)\n", "i(VCMLOAD) i(VFB1) i(VFB2) i(VFB3)\n")


def main():
    assert not (ROOT / "summary.json").exists(), "Retained evidence must not be overwritten"
    prior = json.loads((PRIOR / "summary.json").read_text(encoding="utf-8"))
    protected = {}
    for filename in ("summary.json", "verification.json", "run_bias_decoupling.py",
                     "verify_bias_decoupling.py", "test_bias_decoupling.py"):
        path = PRIOR / filename
        protected[str(path)] = linked.sha(path)
    for run in prior["runs"]:
        for path, digest in run["files"].items():
            protected[str(PRIOR / path)] = digest
    for path, digest in {**protected, **prior["original_files_sha256"]}.items():
        assert linked.sha(Path(path)) == digest, path
    simulator = next(Path(p) for p in prior["original_files_sha256"] if p.endswith(".exe"))
    helper = linked.load(linked.OLD / "run_validation.py")
    cache = Path(tempfile.mkdtemp(prefix="ewc-feedback-current-"))
    (ROOT / "results").mkdir()

    def execute(old):
        config = {key: value for key, value in old.items()
                  if key not in ("files", "measurements", "rows", "status", "reason", "returncode")}
        work, output = cache / old["id"], ROOT / "results" / old["id"]
        work.mkdir()
        output.mkdir()
        net = instrument((PRIOR / "results" / old["id"] / "test.cir").read_text(encoding="ascii"))
        for path in (work / "test.cir", output / "test.cir"):
            path.write_text(net, encoding="ascii")
        (work / ".spiceinit").write_text("set ngbehavior=ps\n", encoding="ascii")
        (output / "spiceinit.txt").write_text("set ngbehavior=ps\n", encoding="ascii")
        result = dict(config, status="blocked")
        try:
            proc = subprocess.run([str(simulator), "-b", "test.cir"], cwd=work,
                                  capture_output=True, text=True, errors="replace", timeout=150)
            (output / "solver.log").write_text(proc.stdout+"\n"+proc.stderr, encoding="utf-8")
            result["returncode"] = proc.returncode
            assert proc.returncode == 0 and (work / "wave.txt").exists(), "No waveform"
            data = np.loadtxt(work / "wave.txt", skiprows=1)
            assert data.ndim == 2 and data.shape[1] == 36 and np.isfinite(data).all()
            assert np.all(np.diff(data[:, 0]) > 0) and data[-1, 0] >= 1e-6-1e-12
            with (PRIOR / "results" / old["id"] / "wave.csv").open(encoding="ascii") as stream:
                header = stream.readline().strip() + ",i_fb1,i_fb2,i_fb3"
            np.savetxt(output / "wave.csv", data, delimiter=",", fmt="%.15e", header=header, comments="")
            measured = linked.measure(config, data[:, :29], helper)
            measured["buffer_output_max_A"] = float(abs(data[:, 29:32]).max())
            measured["feedback_input_max_A"] = float(abs(data[:, 33:36]).max())
            result.update(rows=len(data), measurements=measured,
                          status="fail" if measured["violations"] else "pass")
        except Exception as error:
            result["reason"] = str(error)
        result["files"] = {p.relative_to(ROOT).as_posix(): linked.sha(p) for p in output.iterdir()}
        return result

    with ThreadPoolExecutor(max_workers=3) as pool:
        runs = list(pool.map(execute, prior["runs"]))
    for path, digest in protected.items():
        assert linked.sha(Path(path)) == digest, path
    summary = dict(runs=runs, cache=str(cache), previous_evidence_sha256=protected,
                   original_files_sha256=prior["original_files_sha256"],
                   instrumentation_only=True, vendor_library_edits=False,
                   full_power_passed=False, startup_passed=False, full_joint_passed=False,
                   runner_sha256=linked.sha(Path(__file__)))
    (ROOT / "summary.json").write_text(json.dumps(summary, indent=2)+"\n", encoding="utf-8")
    for run in runs:
        print(run["id"], run["status"], run.get("reason", run.get("measurements", {}).get("dc_errors_V")))


if __name__ == "__main__":
    main()
