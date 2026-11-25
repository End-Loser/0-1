"""Causal carry-to-cancellation baseband chain, solved by upstream ngspice."""

import argparse
import csv
import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np

from run_spice_regeneration import BASE, SCALE, SCENARIOS, netlist, solve, write_csv

ROOT = Path(__file__).parent
NOMINAL_RAIL = 3.3
VECTORS = ["v(cout)", "v(sout)", "i(vdd)", "v(cin)", "v(sin)",
           "v(cancel)", "v(a)", "v(b)", "v(d)", "v(ccmp)", "v(scmp)",
           "v(ctail)", "v(stail)"]
NAMES = ["time", "carry", "sum", "supply_current", "carry_input", "sum_input",
         "cancel", "a", "b", "cin_bit", "carry_cmp", "sum_cmp", "carry_tail", "sum_tail"]


def calibration():
    with (ROOT / "第五轮结果" / "wave_full_adder_direct.csv").open(encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    result = {}
    for label in sorted({r["scenario"] for r in rows}):
        selected = [r for r in rows if r["scenario"] == label]
        x1 = np.array([[int(r[k]) for k in ("a", "b", "carry_in")] for r in selected])
        x2 = x1.copy()
        x2[:, 2] -= 2 * np.array([int(r["carry_out"]) for r in selected])
        y1 = np.array([float(r["first_measured_coordinate"]) for r in selected])
        y2 = np.array([float(r["second_measured_coordinate"]) for r in selected])
        g1 = np.linalg.lstsq(x1, y1, rcond=None)[0]
        g2 = np.linalg.lstsq(x2, y2, rcond=None)[0]
        result[label] = {
            "first_gains": g1.tolist(), "second_gains": g2.tolist(),
            "first_max_residual": float(np.max(abs(x1 @ g1 - y1))),
            "second_max_residual": float(np.max(abs(x2 @ g2 - y2))),
        }
    return result


def transition_order():
    """Euler tour of the complete directed 8-state graph, including self edges."""
    remaining = [list(range(7, -1, -1)) for _ in range(8)]
    stack, trail = [0], []
    while stack:
        vertex = stack[-1]
        if remaining[vertex]:
            stack.append(remaining[vertex].pop())
        else:
            trail.append(stack.pop())
    return [f"{v:03b}" for v in reversed(trail)]


def input_pwl(levels, slot, skew=0):
    points = [(0.0, levels[0])]
    for index, value in enumerate(levels[1:], 1):
        edge = index * slot + skew
        points.extend([(edge, levels[index - 1]), (edge + 1e-9, value)])
    points.append((len(levels) * slot, levels[-1]))
    return "PWL(" + " ".join(f"{t:.12g} {v:.12g}" for t, v in points) + ")"


def circuit(inputs, gains, supply=3.3, path_tau_ns=0.5, cancel_tau_ns=1.0,
            cancel_gain_scale=1.0, path_gain_scale=(1, 1, 1),
            cancellation_trim=1.0, **parameters):
    lines = netlist([("c", "DC 0", BASE + SCALE * 1.5),
                     ("s", "DC 0", BASE + SCALE * 0.5)], supply=supply, **parameters)
    lines = [line for line in lines if not line.startswith(("Vcin ", "Vsin "))]
    lines[0] = "Causal carry-cancellation baseband chain; linear EM interfaces are ideal"
    lines.append(f"Vbase base 0 {BASE}")
    for name, source in zip(("a", "b", "d"), inputs):
        lines.append(f"V{name} {name} 0 {source}")
    for prefix, gain_key in (("c", "first_gains"), ("s", "second_gains")):
        gains_here = np.array(gains[gain_key]) * np.array(path_gain_scale)
        for name, gain in zip(("a", "b", "d"), gains_here):
            lines.extend([
                f"E{prefix}{name} {prefix}{name}_drive 0 {name} 0 {SCALE*gain:.12g}",
                f"R{prefix}{name}_path {prefix}{name}_drive {prefix}{name}_path 1000",
                f"C{prefix}{name}_path {prefix}{name}_path 0 {path_tau_ns*1e-12:.12g}",
            ])
        lines.extend([
            f"E{prefix}base {prefix}add0 0 base 0 1",
            f"E{prefix}addA {prefix}add1 {prefix}add0 {prefix}a_path 0 1",
            f"E{prefix}addB {prefix}add2 {prefix}add1 {prefix}b_path 0 1",
        ])
        destination = "csrc" if prefix == "c" else "suncancelled"
        lines.append(f"E{prefix}addD {destination} {prefix}add2 {prefix}d_path 0 1")
    # This branch loads the real MOS carry output; no Boolean carry oracle.
    lines.extend([
        "Rcancel cout cancel 1000",
        f"Ccancel cancel 0 {cancel_tau_ns*1e-12:.12g}",
        f"Ecancel ssrc suncancelled cancel 0 "
        f"{-2*SCALE*gains['second_gains'][2]*cancel_gain_scale*cancellation_trim/NOMINAL_RAIL:.12g}",
    ])
    return lines


def decode(output, expected, supply):
    return output >= 0.7 * supply if expected else output <= 0.3 * supply


def dc_test(executable, state, gains, **parameters):
    lines = circuit([f"DC {b}" for b in state], gains, **parameters)
    data, _, _ = solve(executable, lines, ["op"], VECTORS)
    values = dict(zip(NAMES, data[0]))
    count = sum(map(int, state))
    supply = parameters.get("supply", 3.3)
    carry, summed = int(count >= 2), count % 2
    valid = all(-1e-6 <= values[k] <= supply + 1e-6 for k in
                ("carry", "sum", "carry_cmp", "sum_cmp", "carry_tail", "sum_tail"))
    return {
        "state": state, "expected_carry": carry, "expected_sum": summed,
        "carry_output_V": values["carry"], "sum_output_V": values["sum"],
        "carry_input_V": values["carry_input"], "sum_input_V": values["sum_input"],
        "cancel_voltage_V": values["cancel"],
        "correct": bool(valid and decode(values["carry"], carry, supply)
                        and decode(values["sum"], summed, supply)),
        "internal_voltages_valid": bool(valid),
        "supply_power_W": -supply * values["supply_current"],
    }


def measure(arrays, order, slot_ns, supply):
    times = arrays["time"]
    slot = slot_ns * 1e-9
    rows = []
    for index, state in enumerate(order):
        start, end = index * slot, (index + 1) * slot
        mask = (times >= start) & (times < end)
        local_t = times[mask]
        late = local_t >= start + 0.8 * slot
        count = sum(map(int, state))
        for name, expected in (("carry", int(count >= 2)), ("sum", count % 2)):
            output = arrays[name][mask]
            good = decode(output, expected, supply)
            bad = np.flatnonzero(~good)
            settled = (float(local_t[bad[-1] + 1] - start)
                       if len(bad) and bad[-1] + 1 < len(local_t)
                       else (0.0 if not len(bad) else None))
            sample_time = start + 0.9 * slot
            sampled = float(np.interp(sample_time, times, arrays[name]))
            sample_good = bool(decode(sampled, expected, supply))
            late_good = bool(np.any(late) and np.all(good[late]))
            previous_count = sum(map(int, order[max(0, index - 1)]))
            previous = int(previous_count >= 2) if name == "carry" else previous_count % 2
            rows.append({
                "slot_index": index, "from_state": order[max(0, index - 1)],
                "state": state, "channel": name, "expected": expected,
                "sample_time_s": sample_time, "sampled_output_V": sampled,
                "sample_correct": sample_good, "late_window_correct": late_good,
                "correct": sample_good and late_good,
                "settling_to_30_70_percent_s": settled,
                "unchanged_expected_with_excursion": bool(index and expected == previous and len(bad)),
            })
    return rows


def transient(executable, gains, slot_ns=40, dt_ps=100, capacitance=1e-12,
              input_skew_ns=(0, 0, 0), method="trap", **parameters):
    order = transition_order()
    slot, dt = slot_ns * 1e-9, dt_ps * 1e-12
    inputs = [input_pwl([int(s[bit]) for s in order], slot, input_skew_ns[bit]*1e-9)
              for bit in range(3)]
    lines = circuit(inputs, gains, capacitance=capacitance, **parameters)
    lines.append(f".options method={method}")
    data, log, text = solve(executable, lines,
                           [f"tran {dt:.12g} {len(order)*slot:.12g} 0 {dt:.12g}"], VECTORS)
    arrays = dict(zip(NAMES, data.T))
    supply = parameters.get("supply", 3.3)
    rows = measure(arrays, order, slot_ns, supply)
    energy = float(np.trapz(-supply * arrays["supply_current"], arrays["time"]))
    duration = float(arrays["time"][-1])
    summary = {
        "slot_ns": slot_ns, "dt_ps": dt_ps, "supply_V": supply,
        "capacitance_F": capacitance, "method": method,
        "state_slots": len(order), "covered_directed_transitions": len(set(zip(order, order[1:]))),
        "decisions": len(rows), "passed_decisions": sum(r["correct"] for r in rows),
        "sample_passed_decisions": sum(r["sample_correct"] for r in rows),
        "unsettled_decisions": sum(r["settling_to_30_70_percent_s"] is None for r in rows),
        "max_observed_settling_s": max((r["settling_to_30_70_percent_s"] for r in rows
                                      if r["settling_to_30_70_percent_s"] is not None), default=0),
        "unchanged_expected_excursions": sum(r["unchanged_expected_with_excursion"] for r in rows),
        "duration_s": duration, "supply_energy_J": energy,
        "mean_supply_power_W": energy/duration,
        "peak_supply_power_W": float(np.max(-supply * arrays["supply_current"])),
        "solver_log_mentions_warning": "warning" in log.lower(),
    }
    return summary, rows, arrays, text


def main(args):
    gains = calibration()
    if args.probe:
        print(json.dumps([dc_test(args.executable, f"{i:03b}", gains["nominal"])
                          for i in range(8)], indent=2))
        return
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "coupled_calibration.json").write_text(json.dumps(gains, indent=2), encoding="utf-8")
    static, failures = [], []
    for label, parameters in SCENARIOS:
        print(f"Coupled DC {label}", flush=True)
        for wave, coefficients in gains.items():
            for index in range(8):
                try:
                    static.append({"scenario": label, "wave_scenario": wave, **dc_test(
                        args.executable, f"{index:03b}", coefficients, **parameters)})
                except Exception as error:
                    failures.append({"scope": "dc", "scenario": label, "error": str(error)})
    write_csv(args.output / "coupled_static.csv", static)
    summaries, decisions, fields = [], [], {}
    cases = [
        ("nominal_200ps", "nominal", {"dt_ps": 200}),
        ("nominal_100ps", "nominal", {}),
        ("nominal_50ps", "nominal", {"dt_ps": 50}),
        ("nominal_gear_100ps", "nominal", {"method": "gear"}),
        ("nominal_20ns", "nominal", {"slot_ns": 20}),
        ("nominal_10ns", "nominal", {"slot_ns": 10}),
        ("epsilon_plus_1_percent", "epsilon_plus_1_percent", {}),
        ("heavy_load_20pF", "nominal", {"capacitance": 20e-12}),
        ("heavy_load_20pF_20ns", "nominal", {"capacitance": 20e-12, "slot_ns": 20}),
        ("input_only_330K", "nominal", {"input_temperature": 330}),
        ("supply_3V", "nominal", {"supply": 3.0}),
        ("input_skew_4ns", "nominal", {"input_skew_ns": (0, 2, 4)}),
        ("cancel_gain_plus_10_percent", "nominal", {"cancel_gain_scale": 1.1}),
        ("combined_stress", "epsilon_plus_1_percent",
         {"offset": 0.0065, "input_temperature": 330, "supply": 3.0,
          "capacitance": 20e-12, "path_gain_scale": (1.05, 0.95, 1.05),
          "cancel_gain_scale": 1.05}),
        ("combined_stress_50ns", "epsilon_plus_1_percent",
         {"slot_ns": 50, "offset": 0.0065, "input_temperature": 330, "supply": 3.0,
          "capacitance": 20e-12, "path_gain_scale": (1.05, 0.95, 1.05),
          "cancel_gain_scale": 1.05}),
        ("negative_control_no_cancel", "nominal", {"cancel_gain_scale": 0}),
    ]
    for label, wave, parameters in cases:
        print(f"Coupled transient {label}", flush=True)
        try:
            result, rows, arrays, text = transient(args.executable, gains[wave], **parameters)
            summaries.append({"scenario": label, "wave_scenario": wave,
                              "parameters": parameters, **result})
            decisions.extend({"scenario": label, **r} for r in rows)
            fields.update({label + "_" + k: v for k, v in arrays.items()})
            if label == "nominal_100ps":
                (args.output / "coupled_nominal.cir").write_text(text, encoding="ascii")
            (args.output / f"{label}_summary.json").write_text(
                json.dumps(summaries[-1], indent=2), encoding="utf-8")
        except Exception as error:
            failures.append({"scope": "transient", "scenario": label, "error": str(error)})
            print(f"Recorded failure: {failures[-1]}", flush=True)
    write_csv(args.output / "coupled_decisions.csv", decisions)
    np.savez_compressed(args.output / "coupled_waveforms.npz", **fields)
    version = subprocess.run([str(args.executable), "--version"], capture_output=True,
                             text=True, timeout=10)
    summary = {
        "solver_version": version.stdout + version.stderr,
        "executable_sha256": hashlib.sha256(args.executable.read_bytes()).hexdigest(),
        "static_completed": len(static), "static_correct": sum(r["correct"] for r in static),
        "transient_results": summaries, "solver_failures": failures,
        "order": transition_order(), "base_V": BASE, "scale_V_per_logic_unit": SCALE,
        "cancel_reference_rail_V": NOMINAL_RAIL,
        "scope": "Causal single-bit feed-forward carry/cancellation baseband surrogate; not RF co-simulation",
        "energy_scope": "Vdd including two 9-MOS channels, shared bias and carry cancellation RC load",
        "limits": [
            "EM interfaces are ideal linear VCVS sources calibrated to stored FDTD coordinates",
            "Path RC (0.5 ns) and cancellation RC (1 ns) are assumed, not extracted RF timing",
            "Input, threshold and linear summing/cancellation source energy excluded",
            "No latches, RF emission/receiver, multi-bit ripple or arithmetic controller",
            "Generic MOS/capacitance/temperature model; not hardware zero-error proof",
            "All directed states tested with 1 ns ideal ramps, not every possible skew waveform",
        ],
    }
    (args.output / "coupled_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "第七轮结果")
    parser.add_argument("--probe", action="store_true")
    main(parser.parse_args())
