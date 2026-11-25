"""Scoped original models, resistive fixtures and declared supply sequencing screens."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

import numpy as np

ROOT = Path(__file__).resolve().parent
PRIOR = ROOT.parent / "\u53c2\u8003\u8f93\u5165\u65f6\u95f4\u6b65\u590d\u6838"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def configurations():
    base = dict(reference=False, buffers=False, ideal_rails=False, ramp=False,
                enable_cycle=False, load_step=False, finish_s=.002, step_s=100e-9)
    return [
        dict(base, id="dual_steady"),
        dict(base, id="dual_load_step", load_step=True),
        dict(base, id="dual_enable_cycle", enable_cycle=True, finish_s=.008),
        dict(base, id="dual_ramp", ramp=True),
        dict(base, id="dual_reference", reference=True),
        dict(base, id="dual_reference_ramp", reference=True, ramp=True),
        dict(base, id="buffer_ideal_control", reference=True, buffers=True, ideal_rails=True),
        dict(base, id="buffer_real_rails", reference=True, buffers=True),
        dict(base, id="buffer_real_ramp", reference=True, buffers=True, ramp=True),
    ]


def deck(config, models):
    end = config["finish_s"]
    upstream = f"PWL(0 0 100u 0 200u 6 {end} 6)" if config["ramp"] else "6"
    enable = ("PWL(0 0 100u 0 102u 3.3 3m 3.3 3.002m 0 5m 0 5.002m 3.3 8m 3.3)"
              if config["enable_cycle"] else "3.3")
    lines = [
        "Supply layers; resistive fixture, NOT complete RF/FDA power chain",
        f"VUP upstream 0 {upstream}", "RS upstream vin .05", "CIN vin 0 1u",
        "BEN en 0 V=.55*v(vin)" if config["ramp"] else f"VEN en 0 {enable}",
        "VRIN5 vin regin5 0", "VRIN3 vin regin3 0",
        "VROUT5 regout5 r5 0", "VROUT3 regout3 r3 0",
        "RESR5 r5 c5 .05", "C5 c5 0 1u", "RESR3 r3 c3 .05", "C3 c3 0 1u",
        "VLD5 r5 load5 0", "VLD3 r3 load3 0", "RLD5 load5 0 1000", "RLD3 load3 0 330",
    ]
    if config["ideal_rails"]:
        lines += ["V5 regout5 0 5", "V3 regout3 0 3.3",
                  "RNULL5 regin5 0 1e15", "RNULL3 regin3 0 1e15"]
    else:
        lines += [
            ".subckt EWC_RAIL VIN EN VO PARAMS: LEVEL=3.3", ".param V_OUT={LEVEL}",
            f'.include "{models["regulator"].as_posix()}"',
            "XDEVICE VIN 0 EN unused VO TPS7A20_ADJ_TRANS", ".ends EWC_RAIL",
            "XR5 regin5 en regout5 EWC_RAIL PARAMS: LEVEL=5",
            "XR3 regin3 en regout3 EWC_RAIL PARAMS: LEVEL=3.3",
        ]
    if config["load_step"]:
        lines += ["VLOADCTL ctl 0 PWL(0 0 1m 0 1.002m 1 2m 1)",
                  "BLOAD5 load5 0 I=v(load5)*v(ctl)/1000",
                  "BLOAD3 load3 0 I=v(load3)*v(ctl)/330"]
    vectors = [
        ("upstream", "v(upstream)"), ("vin", "v(vin)"), ("en", "v(en)"),
        ("r5", "v(r5)"), ("c5", "v(c5)"), ("r3", "v(r3)"), ("c3", "v(c3)"),
        ("i_up", "i(VUP)"), ("i_reg5in", "i(VRIN5)"), ("i_reg3in", "i(VRIN3)"),
        ("i_reg5out", "i(VROUT5)"), ("i_reg3out", "i(VROUT3)"),
        ("i_load5", "i(VLD5)"), ("i_load3", "i(VLD3)"),
    ]
    if config["reference"]:
        lines += [
            ".subckt EWC_REF VIN TEMP GND TRIM OUT",
            f'.include "{models["reference"].as_posix()}"',
            "XDEVICE VIN TEMP GND TRIM OUT REF5050", ".ends EWC_REF",
            "VREFIN vin refvin 0", "VREFOUT refout vref 0",
            "XREF refvin tempref 0 trimref refout EWC_REF",
            "RREFESR vref cref 1", "CREF cref 0 1u",
        ]
        vectors += [("vref", "v(vref)"), ("cref", "v(cref)"),
                    ("i_refin", "i(VREFIN)"), ("i_refout", "i(VREFOUT)")]
        if not config["buffers"]:
            lines.append("RREFLOAD vref 0 2500")
    if config["buffers"]:
        lines += [
            ".subckt EWC_BUFFER IP IM VP VN OUT",
            f'.include "{models["buffer"].as_posix()}"',
            "XDEVICE IP IM VP VN OUT OPAx320-Q1", ".ends EWC_BUFFER",
        ]
        for i, ratio in enumerate((.33, .32, .34), 1):
            lines += [
                f"RDT{i} vref b{i} {100000*(1-ratio):.15g}",
                f"RDB{i} b{i} 0 {100000*ratio:.15g}", f"CBIN{i} b{i} 0 1n",
                f"VBRAIL{i} r3 vp{i} 0", f"VBOUT{i} bout{i} o{i} 0",
                f"VBIN{i} b{i} bp{i} 0", f"VFB{i} o{i} bn{i} 0",
                f"XB{i} bp{i} bn{i} vp{i} 0 bout{i} EWC_BUFFER", f"CBOUT{i} o{i} 0 100p",
            ]
            vectors += [(f"b{i}", f"v(b{i})"), (f"o{i}", f"v(o{i})"),
                        (f"i_brail{i}", f"i(VBRAIL{i})"), (f"i_bout{i}", f"i(VBOUT{i})"),
                        (f"i_bin{i}", f"i(VBIN{i})"), (f"i_fb{i}", f"i(VFB{i})")]
        common = "PWL(0 0 1m 0 1.01m 3.2 2m 3.2)" if config["ramp"] else "3.2"
        diff = ("PWL(0 0 1m 0 1.01m .02 1.4m .02 1.402m .18 2m .18)"
                if config["ramp"] else "PWL(0 .02 1m .02 1.002m .18 2m .18)")
        lines += [f"VSC scm 0 {common}", f"VD raw 0 {diff}",
                  "BP srcp 0 V=v(scm)+v(raw)/2", "BN srcn 0 V=v(scm)-v(raw)/2",
                  "RCM o1 0 100000", "RBACKP o2 srcp 200", "RBACKN o3 srcn 200"]
        vectors += [("srcp", "v(srcp)"), ("srcn", "v(srcn)")]
    lines += [
        ".options method=gear maxord=2", ".temp 25", ".control",
        "set wr_singlescale", "set wr_vecnames", "set numdgt=15",
        f"tran {config['step_s']} {end} 0 {config['step_s']}",
        "wrdata wave.txt "+" ".join(vector for _, vector in vectors), "quit", ".endc", ".end",
    ]
    return "\n".join(lines)+"\n", ["time"]+[name for name, _ in vectors]


def measure(config, data, headers):
    values = dict(zip(headers, data.T))
    end = config["finish_s"]
    window = (values["time"] >= end-.0003) & (values["time"] <= end-.0001)
    assert window.sum() > 100
    errors = {node: float(abs(values[node][window]-target).max())
              for node, target in (("r5", 5), ("r3", 3.3))}
    checks = dict(rail5=errors["r5"] < .075, rail3=errors["r3"] < .0495,
                  headroom=bool(values["vin"][window].min() > 5.3))
    if config["reference"]:
        errors["vref"] = float(abs(values["vref"][window]-5).max())
        checks["reference"] = errors["vref"] < .005
    if config["buffers"]:
        for node, target, limit in (("o1", 1.65, .005), ("o2", 1.6, .001), ("o3", 1.7, .001)):
            errors[node] = float(abs(values[node][window]-target).max())
            checks[node] = errors[node] < limit
        errors["threshold"] = float(abs(values["o3"][window]-values["o2"][window]-.1).max())
        checks["threshold"] = errors["threshold"] < .002
    if config["enable_cycle"]:
        off = (values["time"] >= .0045) & (values["time"] <= .0049)
        errors["off5"] = float(abs(values["r5"][off]).max())
        errors["off3"] = float(abs(values["r3"][off]).max())
        checks["off5"] = errors["off5"] < .05
        checks["off3"] = errors["off3"] < .05
    return dict(checks=checks, errors_V=errors, window_s=[end-.0003, end-.0001],
                scope="resistive_fixture_terminal_window_not_complete_supply_acceptance")


def main():
    assert not (ROOT / "summary.json").exists()
    prior = json.loads((PRIOR / "summary.json").read_text(encoding="utf-8"))
    models = {}
    for path, digest in prior["original_files_sha256"].items():
        assert sha(Path(path)) == digest
        for token, name in (("tps7a20", "regulator"), ("ref5050", "reference"), ("opax320", "buffer")):
            if token in path.lower():
                models[name] = Path(path)
    simulator = next(Path(p) for p in prior["original_files_sha256"] if p.endswith(".exe"))
    protected = {str(p): sha(p) for p in PRIOR.rglob("*") if p.is_file()
                 and p.suffix in (".py", ".json", ".cir", ".log", ".csv", ".txt")}
    configs = configurations()
    (ROOT / "declared_configurations.json").write_text(json.dumps(configs, indent=2)+"\n", encoding="utf-8")
    cache = Path(tempfile.mkdtemp(prefix="ewc-supply-layers-"))
    (ROOT / "results").mkdir()

    def execute(config):
        work, output = cache / config["id"], ROOT / "results" / config["id"]
        work.mkdir()
        output.mkdir()
        net, headers = deck(config, models)
        for path in (work / "test.cir", output / "test.cir"):
            path.write_text(net, encoding="ascii")
        (work / ".spiceinit").write_text("set ngbehavior=ps\n", encoding="ascii")
        (output / "spiceinit.txt").write_text("set ngbehavior=ps\n", encoding="ascii")
        result = dict(config, status="blocked", columns=headers)
        try:
            proc = subprocess.run([str(simulator), "-b", "test.cir"], cwd=work,
                                  capture_output=True, text=True, errors="replace", timeout=180)
            (output / "solver.log").write_text(proc.stdout+"\n"+proc.stderr, encoding="utf-8")
            result["returncode"] = proc.returncode
            assert proc.returncode == 0 and (work / "wave.txt").exists(), "No waveform"
            data = np.loadtxt(work / "wave.txt", skiprows=1)
            assert data.ndim == 2 and data.shape[1] == len(headers) and np.isfinite(data).all()
            assert np.all(np.diff(data[:, 0]) > 0)
            result["observed_last_time_s"] = float(data[-1, 0])
            assert data[-1, 0] >= config["finish_s"]-1e-12, "Incomplete transient"
            np.savetxt(output / "wave.csv", data, delimiter=",", fmt="%.15e",
                       header=",".join(headers), comments="")
            measured = measure(config, data, headers)
            result.update(rows=len(data), measurements=measured,
                          status="pass" if all(measured["checks"].values()) else "fail")
        except Exception as error:
            result["reason"] = str(error) or type(error).__name__
            if isinstance(error, subprocess.TimeoutExpired):
                text = error.stdout or b""
                if isinstance(text, bytes):
                    text = text.decode("utf-8", errors="replace")
                (output / "solver.log").write_text(text+"\nTIMEOUT\n", encoding="utf-8")
        if (work / "wave.txt").exists() and "rows" not in result:
            result["partial_waveform"] = dict(path=str(work / "wave.txt"),
                                             sha256=sha(work / "wave.txt"), accepted=False)
        result["files"] = {p.relative_to(ROOT).as_posix(): sha(p) for p in output.iterdir()}
        return result

    with ThreadPoolExecutor(max_workers=3) as pool:
        runs = list(pool.map(execute, configs))
    assert all(sha(Path(p)) == h for p, h in protected.items())
    summary = dict(runs=runs, cache=str(cache), original_files_sha256=prior["original_files_sha256"],
                   previous_evidence_sha256=protected, fixture_not_full_conditioner=True,
                   vendor_library_edits=False, regulator_quiescent_current_modeled=False,
                   source_control_ideal=True, external_components_ideal=True,
                   startup_safety_passed=False, full_G4_G5_passed=False,
                   full_power_passed=False, full_joint_passed=False,
                   declared_configurations_sha256=sha(ROOT / "declared_configurations.json"),
                   runner_sha256=sha(Path(__file__)))
    (ROOT / "summary.json").write_text(json.dumps(summary, indent=2)+"\n", encoding="utf-8")
    for run in runs:
        print(run["id"], run["status"], run.get("reason", run.get("measurements", {}).get("errors_V")))


if __name__ == "__main__":
    main()
