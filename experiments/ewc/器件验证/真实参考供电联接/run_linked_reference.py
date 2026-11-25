"""Load the real bias network and rails without altering any vendor library."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import zipfile

import numpy as np

ROOT = Path(__file__).resolve().parent
OLD = ROOT.parent / "\u771f\u5b9e\u57fa\u5e26\u8c03\u7406"
PUBLIC = ROOT.parent / "\u516c\u5f00\u6a21\u578b\u8865\u9f50"
SIM_HASH = "22d5cae2bd32b2e39157a8d27bf457122f68285b72a9ebefdf41551b628233ab"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path):
    spec = importlib.util.spec_from_file_location("old_fda_runner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def deck(config, models, helper):
    net = helper.deck(config, models["fda"], models["comparator"])
    net = net.replace("BP srcp 0", "VPBASE spgen srcp 0\nBP spgen 0")
    net = net.replace("BN srcn 0", "VNBASE sngen srcn 0\nBN sngen 0")
    original_bias = (f"VF vs 0 5\nVC vc 0 3.3\nVCM cm 0 1.65\n"
                     f"VRP refp 0 {1.65-config['threshold_V']/2}\n"
                     f"VRN refn 0 {1.65+config['threshold_V']/2}")
    supply = "VUP upstream 0 6\nVREG5 r5 rail5 0\nVREG3 r3 rail3 0\nVF rail5 vs 0\nVC rail3 vc 0\n"
    supply += "VB1 rail3 vb1 0\nVB2 rail3 vb2 0\nVB3 rail3 vb3 0\n"
    if config["real_rails"]:
        supply += f""".include "{models['regulator'].as_posix()}"
* External documented output selection is scoped per rail, not a library edit.
.subckt EWC_RAIL VIN EN VO PARAMS: LEVEL=3.3
.param V_OUT={{LEVEL}}
XU VIN 0 EN unused VO TPS7A20_ADJ_TRANS
.ends EWC_RAIL
VEN en 0 3.3
XR5 upstream en r5 EWC_RAIL PARAMS: LEVEL=5
XR3 upstream en r3 EWC_RAIL PARAMS: LEVEL=3.3
RCE5 r5 ce5 .05
CCE5 ce5 0 1u
RCE3 r3 ce3 .05
CCE3 ce3 0 1u
CIN upstream 0 1u
"""
    else:
        supply += "V5 r5 0 5\nV3 r3 0 3.3\n"
    if config["bias"] == "ideal":
        supply += ("VREF vref 0 5\nVCM cm 0 1.65\n"
                   f"VRP refp 0 {1.65-config['threshold_V']/2}\n"
                   f"VRN refn 0 {1.65+config['threshold_V']/2}\n")
    else:
        supply += f""".include "{models['reference'].as_posix()}"
VREF upstream refvin 0
XREF refvin tempref 0 trimref vref REF5050
RREFESR vref cref 1
CREF cref 0 1u
"""
        ratios = [(.33, "cm"), ((1.65-config["threshold_V"]/2)/5, "refp"),
                  ((1.65+config["threshold_V"]/2)/5, "refn")]
        for i, (ratio, node) in enumerate(ratios, 1):
            top = 100000*(1-ratio)
            bottom = 100000*ratio
            # Explicit sensitivity assumption, not a guaranteed resistor lot.
            if config["perturbation"]:
                sign = -1 if node == "refp" else 1
                top *= 1 + sign*config["perturbation"]
                bottom *= 1 - sign*config["perturbation"]
            input_node = f"b{i}" if config["bias"] == "buffered" else node
            supply += f"RDT{i} vref {input_node} {top:.15g}\nRDB{i} {input_node} 0 {bottom:.15g}\n"
            if config["bias"] == "buffered":
                supply += f"XB{i} {input_node} {node} vb{i} 0 {node} OPAx320-Q1\nCB{i} {node} 0 10p\n"
        if config["bias"] == "buffered":
            supply += f'.include "{models["buffer"].as_posix()}"\n'
    net = net.replace(original_bias, supply)
    assert original_bias not in net
    old_export = "i(VF) i(VC)"
    net = net.replace(old_export, old_export + " v(vs) v(vc) v(cm) v(refp) v(refn) v(vref)"
                      " v(upstream) i(VUP) i(VREF) i(VB1) i(VB2) i(VB3)"
                      " i(VREG5) i(VREG3) i(VPBASE) i(VNBASE)")
    if config["slow_startup"]:
        net = net.replace("VUP upstream 0 6", "VUP upstream 0 PWL(0 0 100u 0 200u 6 5m 6)")
        begin = net.index("VD raw 0 PWL(")
        end = net.index("\n", begin)
        net = net[:begin] + f"VD raw 0 {config['threshold_V']-config['overdrive_V']}" + net[end:]
        net = net.replace(f"tran {config['step_s']} 1u 0 {config['step_s']}", "tran 2u 5m 0 2u")
    return net


def measure(config, data, helper):
    if config["slow_startup"]:
        window = (data[:, 0] >= .004) & (data[:, 0] <= .0049)
        return dict(scope="slow_startup_terminal_window_not_validated_startup_logic",
                    final_rail5_error_V=float(abs(data[window, 13]-5).max()),
                    final_rail3_error_V=float(abs(data[window, 14]-3.3).max()),
                    final_reference_error_V=float(abs(data[window, 18]-5).max()),
                    final_logic_range_V=[float(data[window, 10].min()), float(data[window, 10].max())])
    measured = helper.measure(config, data[:, :13])
    t = data[:, 0]
    window = t >= 200e-9
    checks = measured["checks"]
    checks.update(rail5=bool(abs(data[window, 13]-5).max() < .075),
                  rail3=bool(abs(data[window, 14]-3.3).max() < .0495),
                  reference=bool(abs(data[window, 18]-5).max() < .005))
    measured["violations"] = [key for key, value in checks.items() if not value]
    measured["bias_threshold_range_V"] = [float((data[window, 17]-data[window, 16]).min()),
                                         float((data[window, 17]-data[window, 16]).max())]
    measured["means_A"] = dict(upstream=float(-data[window, 20].mean()),
                               reference=float(data[window, 21].mean()),
                               buffers=[float(data[window, i].mean()) for i in (22, 23, 24)])
    return measured


def main():
    assert not (ROOT / "summary.json").exists()
    previous = {str(p): sha(p) for p in ROOT.parent.rglob("*")
                if p.is_file() and ROOT not in p.parents and "__pycache__" not in p.parts}
    old = json.loads((OLD / "summary.json").read_text(encoding="utf-8"))
    public = json.loads((PUBLIC / "supply_instrumented/supply_screen.json").read_text(encoding="utf-8"))
    cache = Path(tempfile.mkdtemp(prefix="ewc-linked-reference-"))
    source = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
    row = next(r for r in source["sources"] if r["id"] == "buffer_pspice")
    assert sha(Path(row["cache"])) == row["sha256"]
    buffer = cache / "OPAx320-Q1.LIB"
    with zipfile.ZipFile(row["cache"]) as archive:
        buffer.write_bytes(archive.read("OPAx320-Q1.LIB"))
    assert sha(buffer) == "a272838427e8988bdc662887238d2698d23feb0d39400a2c4c4a929f10c29460"
    models = dict(buffer=buffer)
    for path, digest in old["original_files_sha256"].items():
        assert sha(Path(path)) == digest
        if Path(path).suffix.lower() == ".lib":
            models["fda" if "ths4551" in path.lower() else "comparator"] = Path(path)
    for path, digest in public["original_files_sha256"].items():
        assert sha(Path(path)) == digest
        if Path(path).suffix.lower() == ".lib":
            models["reference" if "ref5050" in path.lower() else "regulator"] = Path(path)
    sim = Path(tempfile.gettempdir()) / "ewc-openems/ngspice47/Spice64/bin/ngspice_con.exe"
    assert sha(sim) == SIM_HASH
    helper = load(OLD / "run_validation.py")
    base = dict(next(c for c in helper.configurations() if c["id"] == "d05_fda_100pF"),
                step_s=10e-12, real_rails=False, bias="ideal", perturbation=0, slow_startup=False)
    configs = [
        dict(base, id="linked_ideal_control"),
        dict(base, id="linked_passive_bias", bias="passive"),
        dict(base, id="linked_buffered_bias", bias="buffered"),
        dict(base, id="linked_real_rails", bias="buffered", real_rails=True),
        dict(base, id="linked_real_rails_5ps", bias="buffered", real_rails=True, step_s=5e-12),
        dict(base, id="linked_ratio_0p1pct", bias="buffered", real_rails=True, perturbation=.001),
        dict(base, id="linked_startup", bias="buffered", real_rails=True, slow_startup=True),
    ]
    (ROOT / "results").mkdir()

    def run(config):
        work = cache / config["id"]
        evidence = ROOT / "results" / config["id"]
        work.mkdir()
        evidence.mkdir()
        net = deck(config, models, helper)
        for path in (work / "test.cir", evidence / "test.cir"):
            path.write_text(net, encoding="ascii")
        (work / ".spiceinit").write_text("set ngbehavior=ps\n", encoding="ascii")
        (evidence / "spiceinit.txt").write_text("set ngbehavior=ps\n", encoding="ascii")
        result = dict(config, status="blocked")
        try:
            proc = subprocess.run([str(sim), "-b", "test.cir"], cwd=work, capture_output=True,
                                  text=True, errors="replace", timeout=150)
            (evidence / "solver.log").write_text(proc.stdout+"\n"+proc.stderr, encoding="utf-8")
            result["returncode"] = proc.returncode
            assert proc.returncode == 0 and (work / "wave.txt").exists(), "No complete waveform"
            data = np.loadtxt(work / "wave.txt", skiprows=1)
            assert data.shape[1] == 29 and np.isfinite(data).all()
            assert np.all(np.diff(data[:, 0]) > 0)
            end = .005 if config["slow_startup"] else 1e-6
            assert data[-1, 0] >= end-1e-12
            np.savetxt(evidence / "wave.csv", data, delimiter=",", fmt="%.15e",
                       header="time,raw,srcp,srcn,ip,im,op,om,cp,cn,logic,i_fda,i_cmp,"
                       "vs,vc,cm,refp,refn,vref,upstream,i_up,i_ref,i_b1,i_b2,i_b3,"
                       "i_reg5,i_reg3,i_srcp,i_srcn", comments="")
            measurements = measure(config, data, helper)
            result.update(rows=len(data), measurements=measurements,
                          status="observed" if config["slow_startup"] else
                          ("fail" if measurements["violations"] else "pass"))
        except Exception as error:
            result["reason"] = str(error)
        result["files"] = {p.relative_to(ROOT).as_posix(): sha(p) for p in evidence.iterdir()}
        return result

    with ThreadPoolExecutor(max_workers=3) as pool:
        runs = list(pool.map(run, configs))
    assert all(sha(Path(path)) == digest for path, digest in previous.items())
    result = dict(scope="R12_P01_P02_linked_typical_screen_not_complete_system",
                  runs=runs, cache=str(cache), previous_device_evidence_sha256=previous,
                  original_files_sha256={str(p): sha(p) for p in list(models.values())+[sim]},
                  old_helper_sha256=sha(OLD / "run_validation.py"),
                  runner_sha256=sha(Path(__file__)), vendor_library_edits=False,
                  synthetic_receiver=True, upstream_ideal=True, full_power_passed=False,
                  resistor_tolerance_is_assumption=True, full_joint_passed=False)
    (ROOT / "summary.json").write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    for run in runs:
        print(run["id"], run["status"], run.get("reason", run.get("measurements", {}).get("violations")))


if __name__ == "__main__":
    main()
