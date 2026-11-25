"""Minimal scope/input-voltage controls; original vendor bytes are immutable."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

import numpy as np

ROOT = Path(__file__).resolve().parent
PRIOR = ROOT.parent / "\u4f9b\u7535\u5206\u5c42\u8054\u63a5"
BASELINE = ROOT.parent / "\u516c\u5f00\u6a21\u578b\u8865\u9f50" / "supply_instrumented" / "supply_results" / "ldo_5V_50mA"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def configurations():
    return [dict(id="original_single_control", baseline=True, finish_s=.01, target_V=3.3)] + [
        dict(id=f"{scope}_{vin:g}Vin_{target:g}Vout", baseline=False, scope=scope,
             vin_V=vin, target_V=target, finish_s=.002)
        for scope in ("direct", "scoped") for vin, target in ((5, 3.3), (6, 3.3), (6, 5))
    ]


def deck(config, library):
    if config["baseline"]:
        return (BASELINE / "test.cir").read_text(encoding="ascii"), [
            "time", "vin", "out", "cap", "en", "i_up", "i_load"]
    target = config["target_V"]
    if config["scope"] == "direct":
        model = [f'.include "{library.as_posix()}"', f".param V_OUT={target}",
                 "XU vin 0 en nc out TPS7A20_ADJ_TRANS"]
        instance = "xu"
    else:
        model = [".subckt EWC_RAIL VIN EN VO PARAMS: LEVEL=3.3", ".param V_OUT={LEVEL}",
                 f'.include "{library.as_posix()}"',
                 "XDEVICE VIN 0 EN unused VO TPS7A20_ADJ_TRANS", ".ends EWC_RAIL",
                 f"XR vin en out EWC_RAIL PARAMS: LEVEL={target}"]
        instance = "xr.xdevice"
    names = ["vin", "out", "cap", "en", "i_up", "i_load",
             "vyy", "vzz", "vxx", "vout_int", "en_ok", "vin_pre", "load_dis"]
    vectors = ["v(vin)", "v(out)", "v(cap)", "v(en)", "i(VUP)", "i(VLS)"] + [
        f"v({instance}.{node})" for node in names[6:]]
    lines = ["Single regulator control; no vendor changes"] + model + [
        f"VUP upstream 0 {config['vin_V']}", "RS upstream vin .05", "CIN vin 0 1u",
        "VEN en 0 3.3", "RESR out cap .05", "COUT cap 0 1u", "VLS out load 0",
        f"RLOAD load 0 {330 if target == 3.3 else 1000}",
        ".options method=gear maxord=2", ".temp 25", ".control",
        "set wr_singlescale", "set wr_vecnames", "set numdgt=15",
        "tran 100n 2m 0 100n", "wrdata wave.txt " + " ".join(vectors),
        "quit", ".endc", ".end"]
    return "\n".join(lines) + "\n", ["time"] + names


def main():
    assert not (ROOT / "summary.json").exists()
    old = json.loads((PRIOR / "summary.json").read_text(encoding="utf-8"))
    originals = old["original_files_sha256"]
    for path, digest in originals.items():
        assert sha(Path(path)) == digest
    library = next(Path(p) for p in originals if "tps7a20" in p.lower())
    simulator = next(Path(p) for p in originals if p.endswith(".exe"))
    protected = {str(p): sha(p) for folder in (PRIOR, BASELINE) for p in folder.rglob("*")
                 if p.is_file() and p.suffix in (".json", ".py", ".cir", ".log", ".csv", ".txt")}
    configs = configurations()
    (ROOT / "declared_configurations.json").write_text(json.dumps(configs, indent=2) + "\n", encoding="utf-8")
    cache = Path(tempfile.mkdtemp(prefix="ewc-regulator-controls-"))
    (ROOT / "results").mkdir()

    def execute(config):
        work, output = cache / config["id"], ROOT / "results" / config["id"]
        work.mkdir()
        output.mkdir()
        net, columns = deck(config, library)
        (work / ".spiceinit").write_text("set ngbehavior=ps\n", encoding="ascii")
        (output / "spiceinit.txt").write_text("set ngbehavior=ps\n", encoding="ascii")
        for path in (work / "test.cir", output / "test.cir"):
            path.write_text(net, encoding="ascii")
        record = dict(config, columns=columns, status="blocked")
        try:
            proc = subprocess.run([str(simulator), "-b", "test.cir"], cwd=work,
                                  capture_output=True, text=True, errors="replace", timeout=180)
            (output / "solver.log").write_text(proc.stdout + "\n" + proc.stderr, encoding="utf-8")
            record["returncode"] = proc.returncode
            assert proc.returncode == 0 and (work / "wave.txt").exists(), "No waveform"
            data = np.loadtxt(work / "wave.txt", skiprows=1)
            assert data.ndim == 2 and data.shape[1] == len(columns) and np.isfinite(data).all()
            assert np.all(np.diff(data[:, 0]) > 0)
            record["last_time_s"] = float(data[-1, 0])
            assert data[-1, 0] >= config["finish_s"] - 1e-12, "Incomplete transient"
            np.savetxt(output / "wave.csv", data, delimiter=",", fmt="%.15e",
                       header=",".join(columns), comments="")
            mask = (data[:, 0] >= config["finish_s"] - .0003) & (data[:, 0] <= config["finish_s"] - .0001)
            record["rows"] = len(data)
            record["output_error_V"] = float(abs(data[mask, 2] - config["target_V"]).max())
            record["status"] = "pass" if record["output_error_V"] < .015 * config["target_V"] else "fail"
        except Exception as error:
            record["reason"] = str(error) or type(error).__name__
        record["files_sha256"] = {p.relative_to(ROOT).as_posix(): sha(p) for p in output.iterdir()}
        return record

    with ThreadPoolExecutor(max_workers=3) as pool:
        runs = list(pool.map(execute, configs))
    assert all(sha(Path(p)) == h for p, h in protected.items())
    result = dict(runs=runs, original_files_sha256=originals, previous_evidence_sha256=protected,
                  declared_configurations_sha256=sha(ROOT / "declared_configurations.json"),
                  runner_sha256=sha(Path(__file__)), cache=str(cache),
                  vendor_library_edits=False, root_cause_proven=False,
                  full_supply_acceptance_passed=False, supported_pspice_verified=False)
    (ROOT / "summary.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    for run in runs:
        print(run["id"], run["status"], run.get("output_error_V", run.get("reason")))


if __name__ == "__main__":
    main()
