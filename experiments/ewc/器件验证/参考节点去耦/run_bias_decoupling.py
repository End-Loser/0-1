"""Physical bias capacitance sweep using unchanged, scoped official device models."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile

import numpy as np

ROOT = Path(__file__).resolve().parent
LINKED = ROOT.parent / "\u771f\u5b9e\u53c2\u8003\u4f9b\u7535\u8054\u63a5"
sys.path.insert(0, str(LINKED))
import run_linked_reference as linked
from run_scoped_linkage import scoped_deck


def main():
    assert not (ROOT / "summary.json").exists()
    record = json.loads((LINKED / "nodeset/summary.json").read_text(encoding="utf-8"))
    models = {}
    for path, digest in record["original_files_sha256"].items():
        assert linked.sha(Path(path)) == digest
        lower = path.lower()
        for token, name in (("ths4551", "fda"), ("tlv3501", "comparator"), ("ref5050", "reference"),
                            ("tps7a20", "regulator"), ("opax320", "buffer")):
            if token in lower:
                models[name] = Path(path)
    simulator = next(Path(p) for p in record["original_files_sha256"] if p.endswith(".exe"))
    old_evidence = {str(p): linked.sha(p) for p in ROOT.parent.rglob("*")
                    if p.is_file() and ROOT not in p.parents and "__pycache__" not in p.parts}
    helper = linked.load(linked.OLD / "run_validation.py")
    base = dict(next(c for c in helper.configurations() if c["id"] == "d05_fda_100pF"),
                bias="buffered", real_rails=False, perturbation=0, slow_startup=False, step_s=10e-12)
    configs = [dict(base, id=f"bias_{pf}pF", bias_cap_F=pf*1e-12) for pf in (10, 100, 470, 1000)]
    configs += [dict(base, id="bias_1000pF_5ps", bias_cap_F=1e-9, step_s=5e-12),
                dict(base, id="bias_1000pF_realrails", bias_cap_F=1e-9, real_rails=True),
                dict(base, id="bias_1000pF_ratio", bias_cap_F=1e-9, perturbation=.001)]
    cache = Path(tempfile.mkdtemp(prefix="ewc-bias-decoupling-"))
    (ROOT / "results").mkdir()

    def run(config):
        work = cache / config["id"]
        output = ROOT / "results" / config["id"]
        work.mkdir()
        output.mkdir()
        net = scoped_deck(config, models, helper)
        for i, node in enumerate(("cm", "refp", "refn"), 1):
            net = net.replace(f"XB{i} b{i} {node} vb{i} 0 {node} EWC_BUFFER",
                              f"VBOUT{i} bout{i} {node} 0\nXB{i} b{i} {node} vb{i} 0 bout{i} EWC_BUFFER")
            net = net.replace(f"CB{i} {node} 0 10p", f"CB{i} {node} 0 {config['bias_cap_F']}")
        net = net.replace("XF ip im op om cm vs 0", "VCMLOAD cm cmload 0\nXF ip im op om cmload vs 0")
        net = net.replace("i(VNBASE)\n", "i(VNBASE) i(VBOUT1) i(VBOUT2) i(VBOUT3) i(VCMLOAD)\n")
        net = net.replace(".temp 25\n", ".options method=gear maxord=2\n.temp 25\n")
        if config["real_rails"]:
            net = net.replace(".temp 25\n", ".nodeset v(r5)=5 v(r3)=3.3 v(vref)=5\n.temp 25\n")
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
            assert data.shape[1] == 33 and np.isfinite(data).all(), "Malformed waveform"
            assert np.all(np.diff(data[:, 0]) > 0) and data[-1, 0] >= 1e-6-1e-12, "Incomplete transient"
            np.savetxt(output / "wave.csv", data, delimiter=",", fmt="%.15e",
                       header="time,raw,srcp,srcn,ip,im,op,om,cp,cn,logic,i_fda,i_cmp,vs,vc,cm,refp,refn,"
                       "vref,upstream,i_up,i_ref,i_b1,i_b2,i_b3,i_reg5,i_reg3,i_srcp,i_srcn,"
                       "i_bout1,i_bout2,i_bout3,i_cmload", comments="")
            measured = linked.measure(config, data[:, :29], helper)
            measured["buffer_output_max_A"] = float(abs(data[:, 29:32]).max())
            result.update(rows=len(data), measurements=measured,
                          status="fail" if measured["violations"] else "pass")
        except Exception as error:
            result["reason"] = str(error)
        result["files"] = {p.relative_to(ROOT).as_posix(): linked.sha(p) for p in output.iterdir()}
        return result

    with ThreadPoolExecutor(max_workers=3) as pool:
        runs = list(pool.map(run, configs))
    assert all(linked.sha(Path(path)) == digest for path, digest in old_evidence.items())
    result = dict(runs=runs, cache=str(cache), original_files_sha256=record["original_files_sha256"],
                  previous_device_evidence_sha256=old_evidence, bias_capacitance_is_design_candidate=True,
                  guidance="OPA320-Q1 SLOS884B p14: unity gain pure capacitive load approximately 1nF",
                  threshold_or_vendor_parameters_adjusted=False, full_power_passed=False,
                  startup_passed=False, full_joint_passed=False, runner_sha256=linked.sha(Path(__file__)))
    (ROOT / "summary.json").write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    for run in runs:
        print(run["id"], run["status"], run.get("reason", run.get("measurements", {}).get("dc_errors_V")))


if __name__ == "__main__":
    main()
