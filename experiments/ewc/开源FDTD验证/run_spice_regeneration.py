"""Run generic finite-bias MOS baseband channels with upstream ngspice."""

import argparse
import csv
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).parent
BASE, SCALE = 1.0, 0.4
SCENARIOS = [
    ("nominal", {}), ("common_270K", {"temperature": 270}),
    ("common_330K", {"temperature": 330}), ("supply_3V", {"supply": 3.0}),
    ("input_VTO_plus_6p5mV", {"offset": 0.0065}),
    ("input_VTO_minus_6p5mV", {"offset": -0.0065}),
    ("input_KP_plus_10_percent", {"input_kp_scale": 1.1}),
    ("mirror_KP_plus_10_percent", {"mirror_scale": 1.1}),
    ("input_only_330K", {"input_temperature": 330}),
]


def write_csv(path, rows):
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def channel(prefix, voltage, threshold, capacitance=1e-12, offset=0,
            input_kp_scale=1, mirror_scale=1, temperature=300,
            input_temperature=None):
    lines = []
    for suffix, kind, kp, temp, shift in (
        ("N", "NMOS", 200e-6, temperature, 0),
        ("NI", "NMOS", 200e-6 * input_kp_scale,
         input_temperature if input_temperature is not None else temperature, offset),
        ("P", "PMOS", 100e-6, temperature, 0),
        ("PM", "PMOS", 100e-6 * mirror_scale, temperature, 0),
    ):
        # Explicit generic temperature law, not measured process parameters.
        vto = (0.4 + shift - 1e-3 * (temp - 300))
        if kind == "PMOS":
            vto = -vto
        kp *= (temp / 300) ** -1.5
        lines.append(f".model {prefix}{suffix} {kind} "
                     f"(level=1 vto={vto:.12g} kp={kp:.12g} gamma=0.4 "
                     "phi=0.7 lambda=0.03 cgso=0 cgdo=0 cgbo=0)")
    n = lambda name: prefix + name
    lines.extend([
        f"V{prefix}in {n('src')} 0 {voltage}",
        f"V{prefix}ref {n('ref')} 0 {threshold:.12g}",
        f"R{prefix}source {n('src')} {n('in')} 500",
        f"C{prefix}input {n('in')} 0 1p",
        f"M{prefix}1 {n('mirror')} {n('in')} {n('tail')} 0 {prefix}NI w=10u l=1u",
        f"M{prefix}2 {n('cmp')} {n('ref')} {n('tail')} 0 {prefix}N w=10u l=1u",
        f"M{prefix}3 {n('mirror')} {n('mirror')} vdd vdd {prefix}P w=20u l=1u",
        f"M{prefix}4 {n('cmp')} {n('mirror')} vdd vdd {prefix}PM w=20u l=1u",
        f"M{prefix}tail {n('tail')} bias 0 0 {prefix}N w=10u l=1u",
    ])
    for label, gate, drain in (("5", "cmp", "mid"), ("7", "mid", "out")):
        lines.extend([
            f"M{prefix}{label} {n(drain)} {n(gate)} 0 0 {prefix}N w=10u l=1u",
            f"M{prefix}{int(label)+1} {n(drain)} {n(gate)} vdd vdd {prefix}P w=20u l=1u",
        ])
    for name, cap in (("mirror", 0.1e-12), ("cmp", 0.1e-12),
                      ("mid", 0.2e-12), ("out", capacitance)):
        lines.extend([
            f"C{prefix}{name} {n(name)} 0 {cap:.12g}",
            f"R{prefix}{name} {n(name)} 0 {100e3 if name == 'out' else 1e9:.12g}",
        ])
    return lines


def netlist(channels, supply=3.3, **parameters):
    lines = [
        "Generic finite-bias baseband comparator; not RF receiver",
        f"Vdd vdd 0 {supply}",
        "Rbias_top vdd bias 120k", "Rbias_bottom bias 0 27k",
        ".options reltol=1e-6 abstol=1e-12 vntol=1e-8",
        # Keep intrinsic SPICE temperature fixed; temperature law is above.
        ".temp 26.85",
    ]
    for prefix, voltage, threshold in channels:
        lines.extend(channel(prefix, voltage, threshold, **parameters))
    return lines


def solve(executable, lines, commands, vectors):
    with tempfile.TemporaryDirectory(prefix="ewc-ngspice-") as directory:
        folder = Path(directory)
        text = "\n".join(lines + [
            ".control", "set noaskquit", "set wr_vecnames", "set wr_singlescale",
            *commands, "wrdata data.txt " + " ".join(vectors),
            "quit", ".endc", ".end", "",
        ])
        (folder / "model.cir").write_text(text, encoding="ascii")
        completed = subprocess.run(
            [str(executable), "-b", "model.cir"], cwd=folder,
            capture_output=True, text=True, timeout=45,
        )
        log = completed.stdout + completed.stderr
        if completed.returncode or not (folder / "data.txt").exists():
            raise RuntimeError(log[-4000:])
        data = np.loadtxt(folder / "data.txt", skiprows=1, ndmin=2)
        if data.shape[1] != len(vectors) + 1 or not np.isfinite(data).all():
            raise RuntimeError("Invalid output array.")
        return data, log, text


def dc_test(executable, voltage, threshold, expected, supply=3.3, **parameters):
    lines = netlist([("a", f"DC {voltage:.12g}", threshold)], supply, **parameters)
    vectors = ["v(aout)", "i(vdd)", "v(acmp)", "v(atail)", "v(amirror)", "v(amid)"]
    data, _, _ = solve(executable, lines, ["op"], vectors)
    output, current, cmp, tail, mirror, mid = data[0, 1:]
    valid = bool(all(-1e-6 <= value <= supply + 1e-6
                     for value in (output, cmp, tail, mirror, mid)))
    good = output >= 0.7 * supply if expected else output <= 0.3 * supply
    return {
        "input_V": voltage, "threshold_V": threshold, "expected": expected,
        "output_V": output, "correct": bool(good and valid),
        "internal_voltages_valid": valid, "supply_power_W": -supply * current,
        "comparator_node_V": cmp, "tail_node_V": tail,
    }


def pwl(levels, slot):
    points = [(0, levels[0])]
    for index, value in enumerate(levels[1:], 1):
        points.extend([(index * slot, levels[index - 1]),
                       (index * slot + min(1e-9, slot / 5), value)])
    points.append((len(levels) * slot, levels[-1]))
    return "PWL(" + " ".join(f"{t:.12g} {v:.12g}" for t, v in points) + ")"


def transient(executable, gates, slot_ns, dt_ps, capacitance, **parameters):
    order = ("000", "001", "011", "010", "110", "111", "101", "100") * 2
    indexed = {f"{r['a']}{r['b']}{r['carry_in']}": r for r in gates}
    slot, dt = slot_ns * 1e-9, dt_ps * 1e-12
    count = [BASE + SCALE * float(indexed[k]["first_measured_coordinate"]) for k in order]
    summed = [BASE + SCALE * float(indexed[k]["second_measured_coordinate"]) for k in order]
    supply = parameters.get("supply", 3.3)
    lines = netlist([("c", pwl(count, slot), BASE + SCALE * 1.5),
                     ("s", pwl(summed, slot), BASE + SCALE * 0.5)],
                    capacitance=capacitance, **parameters)
    vectors = ["v(cout)", "v(sout)", "i(vdd)", "v(cin)", "v(sin)",
               "v(ctail)", "v(stail)", "v(ccmp)", "v(scmp)"]
    data, _, text = solve(
        executable, lines, [f"tran {dt:.12g} {len(order)*slot:.12g} 0 {dt:.12g}"], vectors)
    arrays = dict(zip(["time", "carry", "sum", "supply_current", "carry_input",
                       "sum_input", "carry_tail", "sum_tail", "carry_cmp", "sum_cmp"],
                      data.T))
    times = arrays["time"]
    decisions = []
    for index, key in enumerate(order):
        start, end = index * slot, (index + 1) * slot
        mask = (times >= start) & (times < end)
        for name, expected_key in (("carry", "carry_out"), ("sum", "sum_bit")):
            expected = int(indexed[key][expected_key])
            local_t, local_y = times[mask], arrays[name][mask]
            good = local_y >= 0.7 * supply if expected else local_y <= 0.3 * supply
            bad = np.flatnonzero(~good)
            settled = (float(local_t[bad[-1]+1] - start)
                       if len(bad) and bad[-1]+1 < len(local_t)
                       else (0.0 if not len(bad) else None))
            sampled = float(np.interp(start + 0.9 * slot, times, arrays[name]))
            correct = sampled >= 0.7 * supply if expected else sampled <= 0.3 * supply
            decisions.append({
                "slot_index": index, "state": key, "channel": name, "expected": expected,
                "sample_time_s": start + 0.9 * slot, "sampled_output_V": sampled,
                "correct": bool(correct), "settling_to_30_70_percent_s": settled,
            })
    power = -supply * arrays["supply_current"]
    energy = float(np.trapz(power, times))
    duration = float(times[-1] - times[0])
    summary = {
        "slot_ns": slot_ns, "dt_ps": dt_ps, "load_capacitance_F": capacitance,
        "decision_count": len(decisions), "passed_decisions": sum(r["correct"] for r in decisions),
        "duration_s": duration, "supply_energy_J": energy,
        "mean_supply_power_W": energy / duration,
        "peak_supply_power_W": float(power.max()),
        "max_observed_settling_s": max((r["settling_to_30_70_percent_s"] for r in decisions
                                      if r["settling_to_30_70_percent_s"] is not None), default=0),
        "unsettled_decisions": sum(r["settling_to_30_70_percent_s"] is None for r in decisions),
        "internal_voltage_min_V": min(float(arrays[k].min()) for k in
                                      ("carry_tail", "sum_tail", "carry_cmp", "sum_cmp")),
        "internal_voltage_max_V": max(float(arrays[k].max()) for k in
                                      ("carry_tail", "sum_tail", "carry_cmp", "sum_cmp")),
    }
    return summary, decisions, arrays, text


def main(args):
    version = subprocess.run([str(args.executable), "--version"], capture_output=True,
                             text=True, timeout=10)
    if args.probe:
        print(json.dumps([dc_test(args.executable, v, 1.2, int(v > 1.2))
                          for v in (1.0, 1.4)], indent=2))
        return
    args.output.mkdir(parents=True, exist_ok=True)
    with (ROOT / "第五轮结果" / "wave_full_adder_direct.csv").open(encoding="utf-8-sig") as stream:
        gates = list(csv.DictReader(stream))
    static, boundary, failures = [], [], []
    for label, parameters in SCENARIOS:
        print(f"SPICE DC {label}", flush=True)
        for row in gates:
            for name, key, threshold, expected_key in (
                ("carry", "first_measured_coordinate", BASE + SCALE * 1.5, "carry_out"),
                ("sum", "second_measured_coordinate", BASE + SCALE * 0.5, "sum_bit"),
            ):
                try:
                    result = dc_test(args.executable, BASE + SCALE * float(row[key]),
                                     threshold, int(row[expected_key]), **parameters)
                    static.append({"scenario": label, "wave_scenario": row["scenario"],
                                   "state": f"{row['a']}{row['b']}{row['carry_in']}",
                                   "channel": name, **result})
                except Exception as error:
                    failures.append({"scope": "dc", "scenario": label, "error": str(error)})
        for threshold in (BASE + SCALE * 0.5, BASE + SCALE * 1.5):
            for difference in (-0.025, 0.025):
                try:
                    boundary.append({"scenario": label, **dc_test(
                        args.executable, threshold + difference, threshold,
                        int(difference > 0), **parameters)})
                except Exception as error:
                    failures.append({"scope": "boundary", "scenario": label, "error": str(error)})
    write_csv(args.output / "spice_static_gate_states.csv", static)
    write_csv(args.output / "spice_boundary_margin.csv", boundary)
    nominal = [r for r in gates if r["scenario"] == "nominal"]
    summaries, decisions, fields = [], [], {}
    for label, slot, dt, cap, parameters in (
        ("nominal_200ps", 20, 200, 1e-12, {}),
        ("nominal_100ps", 20, 100, 1e-12, {}),
        ("nominal_50ps", 20, 50, 1e-12, {}),
        ("heavy_load_20pF", 20, 100, 20e-12, {}),
        ("too_fast_5ns", 5, 50, 20e-12, {}),
        ("input_only_330K", 20, 100, 1e-12, {"input_temperature": 330}),
    ):
        print(f"SPICE transient {label}", flush=True)
        try:
            result, rows, arrays, text = transient(args.executable, nominal, slot, dt, cap, **parameters)
            summaries.append({"scenario": label, **result})
            decisions.extend({"scenario": label, **r} for r in rows)
            fields.update({label + "_" + k: v for k, v in arrays.items()})
            if label == "nominal_100ps":
                (args.output / "generic_MOS_regeneration.cir").write_text(text, encoding="ascii")
        except Exception as error:
            failures.append({"scope": "transient", "scenario": label, "error": str(error)})
    write_csv(args.output / "spice_transient_summary.csv", summaries)
    write_csv(args.output / "spice_continuous_decisions.csv", decisions)
    np.savez_compressed(args.output / "spice_regeneration_waveforms.npz", **fields)
    summary = {
        "solver_version": version.stdout + version.stderr,
        "executable_sha256": hashlib.sha256(args.executable.read_bytes()).hexdigest(),
        "base_V": BASE, "scale_V_per_logic_unit": SCALE,
        "static_completed": len(static), "static_correct": sum(r["correct"] for r in static),
        "boundary_completed": len(boundary), "boundary_correct": sum(r["correct"] for r in boundary),
        "transient_results": summaries, "solver_failures": failures,
        "model": "9-MOS per channel with shared resistive gate bias, explicit fixed capacitances",
        "prior_unsuccessful_trials": [
            "ahkab MOSQ and EKV original ideal-tail circuits: no acceptable complete OP",
            "ahkab minimal CMOS OP works after legacy report-array normalization",
            "Some isolated-body trials had negative internal voltages; rejected",
        ],
        "scope": "Two independent held-baseband channels; no RF receiver, carry feedback or arithmetic controller",
        "energy_scope": "Vdd source including both channels, shared bias divider and output loads only",
        "limitations": [
            "Generic level-1 MOS parameters, temperature laws and capacitors, not measured fabrication process",
            "Ideal voltage input and threshold sources; their energy excluded",
            "No stochastic noise, RF re-emission, clock, latch or full-system zero-error guarantee",
            "Second-stage input is stored FDTD data, not this circuit's carry output",
        ],
    }
    (args.output / "spice_regeneration_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "第六轮结果")
    parser.add_argument("--probe", action="store_true")
    main(parser.parse_args())
