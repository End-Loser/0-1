"""Original public REF5050/TPS7A20 models, bounded typical standalone screens."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import tempfile
import zipfile

import numpy as np
from collect_public_models import ROOT, sha

SIM_HASH = "22d5cae2bd32b2e39157a8d27bf457122f68285b72a9ebefdf41551b628233ab"


def extract_record(collection, identity, suffix, destination):
    source = next(s for s in collection["sources"] if s["id"] == identity)
    assert source["status"] == 200 and sha(Path(source["cache"])) == source["sha256"]
    with zipfile.ZipFile(source["cache"]) as archive:
        name = next(n for n in archive.namelist() if n.lower().endswith(suffix))
        target = destination / Path(name).name
        target.write_bytes(archive.read(name))
    expected = next(m["sha256"] for m in source["archive_members"] if m["name"] == name)
    assert sha(target) == expected
    return target


def deck(config, library):
    kind, vin, load = config["kind"], config["vin_V"], config["load_A"]
    if kind == "reference":
        finish, step = .005, 1e-6
        upstream = f"PWL(0 {vin} 2m {vin} 2.001m {config['second_vin_V']} 5m {config['second_vin_V']})"
        extra = f"XU vin temp 0 trim out REF5050\nRESR out cap 1\nCOUT cap 0 1u"
        auxiliary = "temp"
    else:
        finish, step = .010, 2e-6
        upstream = str(vin)
        extra = """.param V_OUT=3.3
VEN en 0 PWL(0 0 100u 0 101u 3.3 6m 3.3 6.001m 0 7m 0 7.001m 3.3 10m 3.3)
XU vin 0 en nc out TPS7A20_ADJ_TRANS
RESR out cap .05
COUT cap 0 1u"""
        auxiliary = "en"
    second_load = config["second_load_A"]
    load_wave = f"PWL(0 {load} 2m {load} 2.002m {second_load} {finish} {second_load})"
    if kind == "regulator":
        load_wave = (f"PWL(0 0 1.5m 0 1.502m {load} 2m {load} 2.002m {second_load} "
                     f"5.998m {second_load} 6m 0 8.5m 0 8.502m {second_load} 10m {second_load})")
    return f"""Public supply model; ideal upstream and load, NOT complete power chain
.include "{library.as_posix()}"
VUP upstream 0 {upstream}
RS upstream vin .05
CIN vin 0 1u
{extra}
ILOAD out 0 {load_wave}
.temp 25
.control
set wr_singlescale
set wr_vecnames
set numdgt=15
tran {step} {finish} 0 {step}
wrdata wave.txt v(vin) v(out) v(cap) v({auxiliary}) i(VUP) i(ILOAD)
quit
.endc
.end
"""


def main():
    assert not (ROOT / "supply_screen.json").exists()
    sources = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
    followup = json.loads((ROOT / "followup.json").read_text(encoding="utf-8"))
    cache = Path(tempfile.mkdtemp(prefix="ewc-public-supply-"))
    reference = extract_record(sources, "ref_pspice", "ref5050.lib", cache)
    ldo = extract_record(followup, "ldo_pspice", "tps7a20-adj_trans.lib", cache)
    sim = Path(tempfile.gettempdir()) / "ewc-openems/ngspice47/Spice64/bin/ngspice_con.exe"
    assert sha(sim) == SIM_HASH
    configs = [
        dict(id="ref_6V_2mA", kind="reference", vin_V=6, second_vin_V=6, load_A=.002, second_load_A=.002),
        dict(id="ref_line_step", kind="reference", vin_V=6, second_vin_V=7, load_A=.002, second_load_A=.002),
        dict(id="ref_load_step", kind="reference", vin_V=6, second_vin_V=6, load_A=.002, second_load_A=.008),
        dict(id="ref_5V_headroom", kind="reference", vin_V=5, second_vin_V=5, load_A=.002, second_load_A=.002),
        dict(id="ldo_5V_50mA", kind="regulator", vin_V=5, second_vin_V=5, load_A=.05, second_load_A=.05),
        dict(id="ldo_load_step", kind="regulator", vin_V=5, second_vin_V=5, load_A=.05, second_load_A=.2),
        dict(id="ldo_3V3_headroom", kind="regulator", vin_V=3.3, second_vin_V=3.3, load_A=.05, second_load_A=.05),
    ]
    output = ROOT / "supply_results"
    output.mkdir()

    def run(config):
        work = cache / config["id"]
        work.mkdir()
        evidence = output / config["id"]
        evidence.mkdir()
        net = deck(config, reference if config["kind"] == "reference" else ldo)
        for path in (work / "test.cir", evidence / "test.cir"):
            path.write_text(net, encoding="ascii")
        (work / ".spiceinit").write_text("set ngbehavior=ps\n", encoding="ascii")
        (evidence / "spiceinit.txt").write_text("set ngbehavior=ps\n", encoding="ascii")
        result = dict(config, status="blocked")
        try:
            proc = subprocess.run([str(sim), "-b", "test.cir"], cwd=work, capture_output=True,
                                  text=True, errors="replace", timeout=90)
            (evidence / "solver.log").write_text(proc.stdout+"\n"+proc.stderr, encoding="utf-8")
            result["returncode"] = proc.returncode
            assert proc.returncode == 0 and (work / "wave.txt").exists(), "No solver waveform"
            data = np.loadtxt(work / "wave.txt", skiprows=1)
            assert data.shape[1] == 7 and np.isfinite(data).all()
            assert np.all(np.diff(data[:, 0]) > 0)
            np.savetxt(evidence / "wave.csv", data, delimiter=",", fmt="%.15e",
                       header="time_s,vin_V,out_V,cap_V,aux_V,upstream_A,load_A", comments="")
            target, tolerance = (5, .005) if config["kind"] == "reference" else (3.3, .0495)
            begin, end = (.004, .0049) if config["kind"] == "reference" else (.009, .0099)
            window = (data[:, 0] >= begin) & (data[:, 0] <= end)
            assert window.sum() > 100
            error = float(np.max(abs(data[window, 2] - target)))
            headroom = bool(data[window, 1].min() >= target + (.2 if target == 5 else .3))
            checks = dict(output_screen=error < tolerance, input_headroom=headroom)
            if config["kind"] == "regulator":
                off = (data[:, 0] >= .0065) & (data[:, 0] <= .0069)
                checks["enable_discharge"] = bool(abs(data[off, 2]).max() < .05)
            result.update(rows=len(data), checks=checks, output_error_V=error,
                          target_V=target, tolerance_V=tolerance, window_s=[begin, end],
                          minimum_window_vin_V=float(data[window, 1].min()),
                          mean_upstream_A=float(np.mean(-data[window, 5])),
                          status="pass" if all(checks.values()) else "fail")
        except Exception as error:
            result["reason"] = str(error)
        result["files_sha256"] = {p.relative_to(ROOT).as_posix(): sha(p) for p in evidence.iterdir()}
        return result

    with ThreadPoolExecutor(max_workers=3) as pool:
        runs = list(pool.map(run, configs))
    originals = {str(p): sha(p) for p in (reference, ldo, sim)}
    assert originals[str(reference)] == "ed2d2602cfee90ae26eb7bf3644a4bcb6ce71f3eba71a7d014c53b87cc20f3ab"
    assert originals[str(ldo)] == "154acfdd2199ac44259908b90b07d15bc7e535a45c8c51d1413894c900718b69"
    result = dict(scope="standalone_typical_supply_screens", runs=runs,
                  original_files_sha256=originals, sources_sha256=sha(ROOT / "sources.json"),
                  followup_sha256=sha(ROOT / "followup.json"), cache=str(cache),
                  model_library_edits=False, documented_output_selection_V=3.3,
                  whole_power_chain_passed=False, regulator_quiescent_current_modeled=False,
                  full_temperature_accuracy_passed=False, runner_sha256=sha(Path(__file__)))
    (ROOT / "supply_screen.json").write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    for run in runs:
        print(run["id"], run["status"], run.get("output_error_V"), run.get("checks", run.get("reason")))


if __name__ == "__main__":
    main()
