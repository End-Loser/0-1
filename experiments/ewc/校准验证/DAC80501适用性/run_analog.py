"""Unmodified parallel-code vendor macro; not serial or startup acceptance."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import zipfile

import numpy as np

ROOT = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    assert not (ROOT / "analog_summary.json").exists()
    models = json.loads((ROOT / "models.json").read_text(encoding="utf-8"))
    vendor = models["archives"][0]
    assert sha(Path(vendor["path"])) == vendor["sha256"]
    member = next(item for item in vendor["members"] if item["name"].endswith(".lib"))
    assert not member["encrypted"]
    cache = Path(tempfile.mkdtemp(prefix="ewc-dac80501-analog-"))
    with zipfile.ZipFile(vendor["path"]) as archive:
        content = archive.read(member["name"])
    library = cache / "dac80501.lib"
    library.write_bytes(content)
    assert sha(library) == member["sha256"]
    old = json.loads((ROOT.parents[1] /
        "\u5668\u4ef6\u9a8c\u8bc1/\u4f9b\u7535\u5206\u5c42\u8054\u63a5/summary.json")
        .read_text(encoding="utf-8"))
    simulator_path, simulator_hash = next(
        (p, digest) for p, digest in old["original_files_sha256"].items() if p.endswith(".exe")
    )
    assert sha(Path(simulator_path)) == simulator_hash
    configs = [
        dict(id="nominal27_5p5V_200pF", temp_C=27, vdd_V=5.5, cout_F=200e-12),
        dict(id="room25_5p5V_200pF", temp_C=25, vdd_V=5.5, cout_F=200e-12),
        dict(id="nominal27_5V_200pF", temp_C=27, vdd_V=5, cout_F=200e-12),
        dict(id="nominal27_5p5V_100nF", temp_C=27, vdd_V=5.5, cout_F=100e-9),
    ]
    (ROOT / "analog_configurations.json").write_text(
        json.dumps(configs, indent=2) + "\n", encoding="utf-8")
    output = ROOT / "analog_results"
    output.mkdir()

    def execute(config):
        work = cache / config["id"]
        work.mkdir()
        result = output / config["id"]
        result.mkdir()
        lines = ["DAC80501 original analog macro diagnostic",
                 f'.include "{library.as_posix()}"',
                 "XU out ref vdd 0 " + " ".join(f"b{i}" for i in range(15, -1, -1)) + " DAC80501",
                 f"VDD vdd 0 {config['vdd_V']}", "CREF ref 0 150n",
                 "RLOAD out 0 2000", f"COUT out 0 {config['cout_F']}",
                 f".temp {config['temp_C']}"]
        for bit in range(16):
            value = ("PWL(0 0 30u 0 30.002u 1 60u 1 60.002u 0 120u 0)"
                     if bit == 15 else "1" if bit == 14 else "0")
            lines.append(f"VB{bit} b{bit} 0 {value}")
        lines += [".control", "set wr_singlescale", "set wr_vecnames", "set numdgt=15",
                  "tran 20n 120u 0 20n",
                  "wrdata wave.txt v(out) v(ref) v(vdd) i(VDD) v(b15)",
                  "quit", ".endc", ".end"]
        net = "\n".join(lines) + "\n"
        for location in (work, result):
            (location / "test.cir").write_text(net, encoding="ascii")
        (work / ".spiceinit").write_text("set ngbehavior=ps\n", encoding="ascii")
        (result / "spiceinit.txt").write_text("set ngbehavior=ps\n", encoding="ascii")
        record = dict(config, status="incomplete", waveform_complete=False)
        try:
            proc = subprocess.run([simulator_path, "-b", "test.cir"], cwd=work,
                                  capture_output=True, text=True, errors="replace", timeout=120)
            (result / "solver.log").write_text(proc.stdout + "\n" + proc.stderr, encoding="utf-8")
            record["returncode"] = proc.returncode
            assert proc.returncode == 0 and (work / "wave.txt").exists(), "No waveform"
            data = np.loadtxt(work / "wave.txt", skiprows=1)
            assert data.ndim == 2 and data.shape[1] == 6 and np.isfinite(data).all()
            assert np.all(np.diff(data[:, 0]) > 0)
            record["last_time_s"] = float(data[-1, 0])
            assert data[-1, 0] >= 120e-6 - 1e-12, "Incomplete transient"
            np.savetxt(result / "wave.csv", data, fmt="%.15e", delimiter=",",
                       header="time,out,ref,vdd,i_vdd,b15", comments="")
            windows = [(20e-6, 29e-6, .25), (50e-6, 59e-6, .75),
                       (100e-6, 119e-6, .25)]
            errors = []
            for begin, end, fraction in windows:
                mask = (data[:, 0] >= begin) & (data[:, 0] <= end)
                assert mask.sum() > 100
                errors.append(float(abs(data[mask, 1] - fraction * data[mask, 2]).max()))
            record.update(status="observed", waveform_complete=True,
                          rows=len(data), plateau_error_V=errors,
                          output_min_V=float(data[:, 1].min()),
                          output_max_V=float(data[:, 1].max()),
                          reference_last_V=float(data[-1, 2]))
        except Exception as error:
            record["reason"] = str(error) or type(error).__name__
        record["files_sha256"] = {
            path.relative_to(ROOT).as_posix(): sha(path) for path in result.iterdir()}
        return record

    with ThreadPoolExecutor(max_workers=3) as pool:
        runs = list(pool.map(execute, configs))
    assert sha(library) == member["sha256"]
    assert sha(Path(simulator_path)) == simulator_hash
    summary = dict(runs=runs, library=str(library), library_sha256=sha(library),
                   simulator=simulator_path, simulator_sha256=simulator_hash,
                   configuration_sha256=sha(ROOT / "analog_configurations.json"),
                   runner_sha256=sha(Path(__file__)), cache=str(cache),
                   vendor_parameters_modified=False, serial_interface_modeled=False,
                   physical_input_levels_modeled=False, cold_start_modeled=False,
                   full_supply_current_validated=False, device_accuracy_passed=False,
                   full_joint_passed=False, supported_pspice_verified=False)
    (ROOT / "analog_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    for run in runs:
        print(run["id"], run["status"], run.get("plateau_error_V", run.get("reason")))


if __name__ == "__main__":
    main()
