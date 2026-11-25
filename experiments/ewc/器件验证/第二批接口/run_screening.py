"""Bounded screening of an unchanged TI macro; no RF/conditioner model claims."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

import numpy as np

SOURCE = Path(__file__).resolve().parent
ROOT = SOURCE
EXPECTED = "4e4345dc2929ee91a99a0b5437369c32b3817e9c8673a7ae90310d5485a81ba9"
EDGES = [(200e-9, True), (320e-9, False), (440e-9, True), (560e-9, False)]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def cases():
    base = dict(supply_V=3.3, common_mode_V=1.65, threshold_V=.1,
                overdrive_V=.02, load_F=1e-12, load_ohm=100000,
                ramp_s=100e-12, max_step_s=50e-12)
    return [
        dict(base, id="c01_3p3_mid"),
        dict(base, id="c02_5_mid", supply_V=5.0, common_mode_V=2.5),
        dict(base, id="c03_3p3_low", common_mode_V=.25, threshold_V=-.05),
        dict(base, id="c04_3p3_upper", common_mode_V=3.0),
        dict(base, id="c05_5_rx_cm", supply_V=5.0, common_mode_V=3.2),
        dict(base, id="c06_3p3_cm_conflict", common_mode_V=3.2, overdrive_V=1.0),
        dict(base, id="c07_3p3_2mV", overdrive_V=.002),
        dict(base, id="c08_3p3_20pF", load_F=20e-12),
        dict(base, id="c09_3p3_1000pF", load_F=1000e-12),
        dict(base, id="c10_3p3_slow", ramp_s=20e-9),
        dict(base, id="c11_3p3_refined", max_step_s=25e-12),
    ]


def deck(c, model):
    lo, hi = c["threshold_V"] - c["overdrive_V"], c["threshold_V"] + c["overdrive_V"]
    points = [(0, lo)]
    for time, rising in EDGES:
        points += [(time, lo if rising else hi),
                   (time + c["ramp_s"], hi if rising else lo)]
    points.append((800e-9, lo))
    pwl = " ".join(f"{t:.14g} {v:.14g}" for t, v in points)
    return f"""Unchanged TLV3501; explicit ideal differential threshold conditioner
.include "{model.as_posix()}"
VS vcc 0 {c['supply_V']}
VCM cm 0 {c['common_mode_V']}
VTH th 0 {c['threshold_V']}
VD raw 0 PWL({pwl})
* These ideal sources are NOT ADL5380 or a physical conditioning amplifier.
BP inp 0 V=v(cm)+(v(raw)-v(th))/2
BN inn 0 V=v(cm)-(v(raw)-v(th))/2
VSH shdn 0 0
XDUT inp inn shdn vcc 0 out TLV3501
RL out 0 {c['load_ohm']}
CL out 0 {c['load_F']}
.temp 25
.control
set wr_singlescale
set wr_vecnames
set numdgt=15
tran {c['max_step_s']} 800n 0 {c['max_step_s']}
wrdata wave.txt v(raw) v(th) v(inp) v(inn) v(out) i(VS)
quit
.endc
.end
"""


def crossings(t, y, level, rising, start, stop):
    a, b = y[:-1] - level, y[1:] - level
    mask = (a < 0) & (b >= 0) if rising else (a > 0) & (b <= 0)
    ids = np.flatnonzero(mask & (t[1:] >= start) & (t[1:] <= stop))
    return [float(t[i] + (t[i + 1] - t[i]) * (-a[i]) / (b[i] - a[i]))
            for i in ids]


def measure(c, data):
    t, raw, th, inp, inn, out, current = data.T
    if not np.isfinite(data).all() or not np.all(np.diff(t) > 0):
        raise ValueError("Nonfinite or nonmonotonic waveform")
    if t[0] > 1e-12 or t[-1] < 800e-9 - 1e-12:
        raise ValueError("Incomplete observation window")
    events = []
    for start, rising in EDGES:
        ins = crossings(t, raw - th, 0, rising, start, start + c["ramp_s"] + 1e-9)
        outs = crossings(t, out, c["supply_V"] / 2, rising, start, start + 110e-9)
        plateau = out[(t >= start + 70e-9) & (t <= start + 100e-9)]
        low, high = float(plateau.min()), float(plateau.max())
        delay = outs[0] - ins[0] if len(ins) == len(outs) == 1 else None
        level_ok = low > .7 * c["supply_V"] if rising else high < .3 * c["supply_V"]
        events.append(dict(start_s=start, rising=rising,
                           input_crossings_s=ins, output_crossings_s=outs,
                           delay_s=delay, plateau_min_V=low, plateau_max_V=high,
                           level_ok=bool(level_ok),
                           delay_ok=delay is not None and 0 < delay < 30e-9))
    pin_min = float(min(inp.min(), inn.min()))
    pin_max = float(max(inp.max(), inn.max()))
    range_ok = pin_min >= -.2 and pin_max <= c["supply_V"] - .2
    # A conservative planning guard, not a modeled worst-case offset sweep.
    margin = c["overdrive_V"] - (.0065 + .003)
    function_ok = all(e["level_ok"] and e["delay_ok"] for e in events)
    violations = []
    if not range_ok:
        violations.append("pin_range_outside_conservative_EC")
    if margin <= 0:
        violations.append("no_6p5mV_offset_plus_3mV_hysteresis_margin")
    if not all(e["level_ok"] for e in events):
        violations.append("unstable_or_incorrect_30_70_output")
    if not all(e["delay_ok"] for e in events):
        violations.append("missing_multiple_or_outside_0_30ns_crossing")
    return dict(events=events, pin_min_V=pin_min, pin_max_V=pin_max,
                range_ok=bool(range_ok), planning_margin_V=margin,
                function_pass=bool(function_ok), violations=violations,
                conditioning_identity_error_V=float(np.max(np.abs(inp - inn - raw + th))),
                common_mode_error_V=float(np.max(np.abs((inp + inn) / 2 - c["common_mode_V"]))),
                active_supply_current_A=float(np.mean(-current[(t >= 270e-9) & (t <= 300e-9)])))


def run_one(c, simulator, model, temporary):
    work = temporary / c["id"]
    work.mkdir()
    init = work / ".spiceinit"
    init.write_text("set ngbehavior=ps\n", encoding="ascii")
    netlist = work / "test.cir"
    netlist.write_text(deck(c, model), encoding="ascii")
    evidence = ROOT / "results" / c["id"]
    evidence.mkdir(parents=True, exist_ok=True)
    # Archive our own test deck, never the included manufacturer model.
    (evidence / "test.cir").write_bytes(netlist.read_bytes())
    (evidence / "spiceinit.txt").write_bytes(init.read_bytes())
    r = dict(c, status="blocked", attempted=True, temporary_directory=str(work))
    try:
        p = subprocess.run([str(simulator), "-b", str(netlist)], cwd=work,
                           capture_output=True, text=True, errors="replace", timeout=90)
        log = p.stdout + "\n" + p.stderr
        (evidence / "solver.log").write_text(log, encoding="utf-8")
        r["returncode"] = p.returncode
        wave = work / "wave.txt"
        if p.returncode != 0 or not wave.exists() or "No. of Data Rows" not in log:
            raise RuntimeError("No successful transient dataset; see solver.log")
        data = np.loadtxt(wave, skiprows=1)
        if data.ndim != 2 or data.shape[1] != 7:
            raise ValueError("Unexpected waveform shape")
        np.savetxt(evidence / "wave.csv", data, delimiter=",", fmt="%.15e",
                   header="time_s,raw_V,threshold_V,inp_V,inn_V,out_V,supply_current_A",
                   comments="")
        r["rows"] = len(data)
        r["measurements"] = measure(c, data)
        r["status"] = "pass" if not r["measurements"]["violations"] else "fail"
    except Exception as exc:
        r["reason"] = f"{type(exc).__name__}: {exc}"
    r["files"] = {p.relative_to(ROOT).as_posix(): sha(p)
                  for p in evidence.iterdir() if p.is_file()}
    return r


def main(args):
    if not 1 <= args.workers <= 3:
        raise ValueError("workers must be 1..3")
    result_dir = ROOT / "results"
    if result_dir.exists():
        raise RuntimeError("results already exists; use a fresh --output directory for reruns")
    result_dir.mkdir()
    model, simulator = args.model.resolve(), args.simulator.resolve()
    first_batch = {p.relative_to(SOURCE.parent).as_posix(): sha(p)
                   for p in SOURCE.parent.rglob("*")
                   if p.is_file() and SOURCE not in p.parents and "__pycache__" not in p.parts}
    summary = dict(created_utc=datetime.now(timezone.utc).isoformat(),
                   scope="typical_macro_interface_screen_only",
                   source_model_url="https://www.ti.com/lit/zip/SBOMBU5",
                   model_path=str(model), expected_model_sha256=EXPECTED,
                   model_redistributed=False, temperature_C=25,
                   ideal_conditioner=True, rf_model_simulations=0,
                   physical_tests=0, procurement=0,
                   maximum_workers=args.workers, first_batch_sha256=first_batch,
                   limitations=["fixed_internal_4.5ns_delay", "no_validated_low_overdrive_timing",
                                "no_temperature_process_noise_acceptance",
                                "ideal_zero_impedance_conditioner_and_reference",
                                "no_RF_LO_or_C_feedback_or_level_shifter",
                                "no_full_system_energy_or_20ns_claim"],
                   criteria=dict(low_fraction=.3, high_fraction=.7, delay_limit_s=30e-9,
                                 input_lower_V=-.2, input_upper="supply_V - 0.2",
                                 offset_guard_V=.0065, half_hysteresis_guard_V=.003,
                                 refinement_limit_s=20e-12))
    blockers = []
    if not model.is_file() or sha(model) != EXPECTED:
        blockers.append("Original model missing or hash changed; no model fabricated or downloaded")
    if not simulator.is_file():
        blockers.append("Existing simulator missing; no installation or policy bypass attempted")
    if blockers:
        summary.update(blockers=blockers, runs=[dict(c, status="blocked", attempted=False,
                                                   reason="; ".join(blockers)) for c in cases()])
    else:
        summary["model_sha256_before"] = sha(model)
        summary["simulator_sha256"] = sha(simulator)
        summary["simulator_path"] = str(simulator)
        version = subprocess.run([str(simulator), "--version"], capture_output=True,
                                 text=True, timeout=15)
        summary["simulator_version"] = version.stdout + version.stderr
        temporary = Path(tempfile.mkdtemp(prefix="ewc-device-second-"))
        summary["temporary_directory"] = str(temporary)
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            summary["runs"] = list(pool.map(
                lambda c: run_one(c, simulator, model, temporary), cases()))
        summary["model_sha256_after"] = sha(model)
        if sha(model) != EXPECTED:
            raise RuntimeError("Original model changed during execution; evidence invalid")
    runs = summary["runs"]
    summary["counts"] = dict(configurations=len(runs),
                            solver_attempts=sum(r["attempted"] for r in runs),
                            datasets=sum("rows" in r for r in runs),
                            function_pass=sum(r.get("measurements", {}).get("function_pass", False)
                                              for r in runs),
                            **{s: sum(r["status"] == s for r in runs)
                               for s in ("pass", "fail", "blocked")})
    available = {r["id"]: r for r in runs if "measurements" in r}
    if all(k in available for k in ("c01_3p3_mid", "c11_3p3_refined")):
        differences = [abs(a["delay_s"] - b["delay_s"])
                       for a, b in zip(available["c01_3p3_mid"]["measurements"]["events"],
                                       available["c11_3p3_refined"]["measurements"]["events"])
                       if a["delay_s"] is not None and b["delay_s"] is not None]
        summary["refinement"] = dict(event_differences_s=differences,
                                    pass_check=len(differences) == 4 and max(differences) < 20e-12)
    summary["scripts_sha256"] = {p.name: sha(p) for p in SOURCE.glob("*.py")}
    summary["executed_runner_sha256"] = sha(Path(__file__))
    save_json(ROOT / "summary.json", summary)
    print(json.dumps(summary["counts"], ensure_ascii=False))
    for r in runs:
        print(r["id"], r["status"], r.get("measurements", {}).get("violations", r.get("reason")))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--model", type=Path, default=Path(tempfile.gettempdir()) /
                        "ewc-device-audit-20261118/tlv3501.lib")
    parser.add_argument("--simulator", type=Path, default=Path(tempfile.gettempdir()) /
                        "ewc-openems/ngspice47/Spice64/bin/ngspice_con.exe")
    parser.add_argument("--output", type=Path)
    options = parser.parse_args()
    if options.output:
        output = options.output.resolve()
        if ROOT != output and ROOT not in output.parents:
            parser.error("--output must stay inside the second-batch directory")
        output.mkdir(parents=True, exist_ok=True)
        ROOT = output
    main(options)
