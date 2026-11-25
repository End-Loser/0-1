"""Generic MOS baseband comparator circuits, not RF or vendor IC models."""

import argparse
import csv
import json
from pathlib import Path

import numpy as np

from run_nonlinear import ahkab
from run_precision_circuit import tolerances
from run_fdtd import write_csv

ROOT = Path(__file__).parent


def normalize_mos_reports(circuit):
    """Adapt legacy OP reporting arrays, without changing device equations."""
    for element in circuit:
        if element.part_id.upper().startswith("M"):
            original = element.get_op_info
            def report(ports, original=original):
                normalized = [np.array([float(np.asarray(v).item()) for v in port])
                              for port in ports]
                return original(normalized)
            element.get_op_info = report


def add_channel(circuit, prefix, initial, threshold, function=None,
                capacitance=1e-12, offset=0, input_kp_scale=1,
                mirror_scale=1, temperature=300, input_temperature=None):
    models = {
        prefix + "N": {"TYPE": "n", "VTO": 0.4, "KP": 200e-6},
        prefix + "NI": {"TYPE": "n", "VTO": 0.4 + offset,
                       "KP": 200e-6 * input_kp_scale},
        prefix + "P": {"TYPE": "p", "VTO": -0.4, "KP": 100e-6},
        prefix + "PM": {"TYPE": "p", "VTO": -0.4,
                       "KP": 100e-6 * mirror_scale},
    }
    old_temperature = ahkab.constants.T
    try:
        ahkab.constants.T = 300
        for name, parameters in models.items():
            circuit.add_model("mosq", name, {
                **parameters, "TNOM": 300, "GAMMA": 0.4, "PHI": 0.7,
                "LAMBDA": 0.03, "TCV": 1e-3, "BEX": -1.5,
            })
            target = (input_temperature if name.endswith("NI")
                      and input_temperature is not None else temperature)
            circuit.models[name].set_device_temperature(target)
    finally:
        ahkab.constants.T = old_temperature

    node = lambda name: prefix + name
    circuit.add_vsource("V" + prefix + "input", node("src"), "0", initial, function=function)
    circuit.add_vsource("V" + prefix + "ref", node("ref"), "0", threshold)
    circuit.add_resistor("R" + prefix + "source", node("src"), node("in"), 500)
    circuit.add_capacitor("C" + prefix + "input", node("in"), "0", 1e-12)
    circuit.add_isource("I" + prefix + "tail", node("tail"), "0", 50e-6)
    circuit.add_mos("M" + prefix + "1", node("mirror"), node("in"), node("tail"), "0",
                    10e-6, 1e-6, prefix + "NI")
    circuit.add_mos("M" + prefix + "2", node("cmp"), node("ref"), node("tail"), "0",
                    10e-6, 1e-6, prefix + "N")
    circuit.add_mos("M" + prefix + "3", node("mirror"), node("mirror"), "vdd", "vdd",
                    20e-6, 1e-6, prefix + "P")
    circuit.add_mos("M" + prefix + "4", node("cmp"), node("mirror"), "vdd", "vdd",
                    20e-6, 1e-6, prefix + "PM")
    for label, gate, drain in (("5", "cmp", "mid"), ("7", "mid", "out")):
        circuit.add_mos("M" + prefix + label, node(drain), node(gate), "0", "0",
                        10e-6, 1e-6, prefix + "N")
        circuit.add_mos("M" + prefix + str(int(label) + 1), node(drain), node(gate),
                        "vdd", "vdd", 20e-6, 1e-6, prefix + "P")
    for name, cap in (("mirror", 0.1e-12), ("cmp", 0.1e-12),
                      ("mid", 0.2e-12), ("out", capacitance)):
        circuit.add_capacitor("C" + prefix + name, node(name), "0", cap)
        circuit.add_resistor("R" + prefix + name, node(name), "0",
                            100e3 if name == "out" else 1e9)


def circuit_for(channels, supply=3.3, **parameters):
    circuit = ahkab.Circuit("Generic MOS baseband decision and regeneration")
    circuit.add_vsource("Vdd", "vdd", "0", supply)
    circuit.initial_guess = {"V(vdd)": supply}
    for prefix, initial, threshold, function in channels:
        add_channel(circuit, prefix, initial, threshold, function, **parameters)
        for suffix, value in (("src", initial), ("in", initial), ("ref", threshold),
                              ("tail", 0.1), ("mirror", supply - 0.55),
                              ("cmp", supply / 2), ("mid", supply / 2), ("out", supply / 2)):
            circuit.initial_guess[f"V({prefix}{suffix})"] = value
    normalize_mos_reports(circuit)
    return circuit


def operating_point(circuit):
    old_limit = ahkab.options.dc_max_nr_iter
    try:
        ahkab.options.dc_max_nr_iter = 300
        guess = ahkab.dc_analysis.build_x0_from_user_supplied_ic(circuit, circuit.initial_guess)
        with tolerances(True):
            result = ahkab.run(circuit, ahkab.new_op(x0=guess, verbose=0))["op"]
    finally:
        ahkab.options.dc_max_nr_iter = old_limit
    if result is None or getattr(result, "gmin", None) != 0:
        raise RuntimeError("No zero-gmin operating point.")
    return result


def scalar(result, name):
    return float(np.asarray(result[name]).item())


def dc_test(voltage, threshold, supply=3.3, **parameters):
    circuit = circuit_for([("a", voltage, threshold, None)], supply=supply, **parameters)
    op = operating_point(circuit)
    output = scalar(op, "Vaout")
    expected = int(voltage > threshold)
    correct = output >= 0.7 * supply if expected else output <= 0.3 * supply
    return {"input_V": voltage, "threshold_V": threshold, "expected": expected,
            "output_V": output, "correct": bool(correct),
            "supply_power_W": -supply * scalar(op, "I(Vdd)"),
            "comparator_node_V": scalar(op, "Vacmp"),
            "tail_node_V": scalar(op, "Vatail")}


def series_source(levels, slot):
    x, y = [0.0], [levels[0]]
    rise = min(1e-9, slot / 5)
    for index, value in enumerate(levels[1:], 1):
        x.extend((index * slot, index * slot + rise))
        y.extend((levels[index - 1], value))
    x.append(len(levels) * slot)
    y.append(levels[-1])
    return ahkab.time_functions.pwl(x, y)


def transient(gates, slot_ns=20, dt_ps=100, capacitance=1e-12, **parameters):
    order = ("000", "001", "011", "010", "110", "111", "101", "100") * 2
    indexed = {f"{row['a']}{row['b']}{row['carry_in']}": row for row in gates}
    count = np.array([float(indexed[key]["first_measured_coordinate"]) for key in order])
    summed = np.array([float(indexed[key]["second_measured_coordinate"]) for key in order])
    slot = slot_ns * 1e-9
    channels = [
        ("c", 0.5 + 0.5 * count[0], 1.25, series_source(0.5 + 0.5 * count, slot)),
        ("s", 0.5 + 0.5 * summed[0], 0.75, series_source(0.5 + 0.5 * summed, slot)),
    ]
    circuit = circuit_for(channels, capacitance=capacitance, **parameters)
    op = operating_point(circuit)
    with tolerances(True):
        result = ahkab.run(circuit, ahkab.new_tran(
            0, len(order) * slot, dt_ps * 1e-12, x0=op, method="TRAP",
            use_step_control=False, verbose=0))["tran"]
    if result is None:
        raise RuntimeError("Transient did not converge.")
    arrays = {name: np.asarray(result[key]).reshape(-1) for name, key in (
        ("time", "T"), ("carry", "Vcout"), ("sum", "Vsout"),
        ("supply_current", "I(Vdd)"), ("carry_input", "Vcin"), ("sum_input", "Vsin"))}
    if not all(np.all(np.isfinite(value)) for value in arrays.values()):
        raise RuntimeError("Nonfinite transient data.")
    times = arrays["time"]
    supply = parameters.get("supply", 3.3)
    rows = []
    for index, key in enumerate(order):
        start, end = index * slot, (index + 1) * slot
        mask = (times >= start) & (times < end - dt_ps * 0.25e-12)
        local_times = times[mask]
        for name, expected_key in (("carry", "carry_out"), ("sum", "sum_bit")):
            expected = int(indexed[key][expected_key])
            output = arrays[name][mask]
            good = output >= 0.7 * supply if expected else output <= 0.3 * supply
            bad = np.flatnonzero(~good)
            settled = (float(local_times[bad[-1] + 1] - start)
                       if len(bad) and bad[-1] + 1 < len(local_times)
                       else (0.0 if not len(bad) else None))
            sample = start + 0.9 * slot
            measured = float(np.interp(sample, times, arrays[name]))
            correct = measured >= 0.7 * supply if expected else measured <= 0.3 * supply
            rows.append({
                "slot_index": index, "state": key, "channel": name, "expected": expected,
                "sample_time_s": sample, "sampled_output_V": measured,
                "correct": bool(correct), "settling_to_30_70_percent_s": settled,
            })
    power = -supply * arrays["supply_current"]
    energy = float(np.trapz(power, times))
    summary = {
        "slot_ns": slot_ns, "dt_ps": dt_ps, "load_capacitance_F": capacitance,
        "slot_count": len(order), "decision_count": len(rows),
        "passed_decisions": sum(row["correct"] for row in rows),
        "total_simulated_time_s": float(times[-1]),
        "supply_energy_J": energy, "mean_supply_power_W": energy / times[-1],
        "peak_supply_power_W": float(np.max(power)),
        "max_observed_settling_s": max(
            (row["settling_to_30_70_percent_s"] for row in rows
             if row["settling_to_30_70_percent_s"] is not None), default=0),
        "unsettled_decisions": sum(row["settling_to_30_70_percent_s"] is None for row in rows),
        "scope": "Two independently driven generic baseband channels; not coupled RF feedback or state-machine execution",
    }
    return summary, rows, arrays, circuit


def main(args):
    if args.probe:
        print(json.dumps([dc_test(v, 0.75) for v in (0.5, 1.0)], indent=2))
        return
    args.output.mkdir(parents=True, exist_ok=True)
    with (ROOT / "第五轮结果" / "wave_full_adder_direct.csv").open(encoding="utf-8-sig") as stream:
        gates = list(csv.DictReader(stream))
    static_rows, failures = [], []
    scenarios = [
        ("nominal", {}), ("common_270K", {"temperature": 270}),
        ("common_330K", {"temperature": 330}), ("supply_3V", {"supply": 3.0}),
        ("input_VTO_plus_6p5mV", {"offset": 6.5e-3}),
        ("input_VTO_minus_6p5mV", {"offset": -6.5e-3}),
        ("input_KP_plus_10_percent", {"input_kp_scale": 1.1}),
        ("mirror_KP_plus_10_percent", {"mirror_scale": 1.1}),
        ("input_only_330K", {"input_temperature": 330}),
    ]
    for label, parameters in scenarios:
        for index, row in enumerate(gates):
            for channel, key, threshold in (
                    ("carry", "first_measured_coordinate", 1.25),
                    ("sum", "second_measured_coordinate", 0.75)):
                print(f"MOS DC {label} {index} {channel}", flush=True)
                voltage = 0.5 + 0.5 * float(row[key])
                try:
                    measured = dc_test(voltage, threshold, **parameters)
                    static_rows.append({"scenario": label, "wave_scenario": row["scenario"],
                                        "state": f"{row['a']}{row['b']}{row['carry_in']}",
                                        "channel": channel, **measured})
                except Exception as error:
                    failures.append({"scope": "dc", "scenario": label, "state_index": index,
                                     "channel": channel, "error": f"{type(error).__name__}: {error}"})
    boundary_rows = []
    for label, parameters in scenarios:
        for threshold in (0.75, 1.25):
            for difference in (-0.025, 0.025):
                try:
                    boundary_rows.append({"scenario": label, **dc_test(
                        threshold + difference, threshold, **parameters)})
                except Exception as error:
                    failures.append({"scope": "boundary", "scenario": label, "error": str(error)})
    write_csv(args.output / "mos_static_gate_states.csv", static_rows)
    write_csv(args.output / "mos_boundary_margin.csv", boundary_rows)
    nominal = [row for row in gates if row["scenario"] == "nominal"]
    transient_rows, transition_rows, arrays = [], [], {}
    cases = [("nominal_200ps", 20, 200, 1e-12, {}),
             ("nominal_100ps", 20, 100, 1e-12, {}),
             ("nominal_50ps", 20, 50, 1e-12, {}),
             ("heavy_load_20pF", 20, 100, 20e-12, {}),
             ("too_fast_5ns", 5, 50, 20e-12, {}),
             ("input_only_330K", 20, 100, 1e-12, {"input_temperature": 330})]
    for label, slot, dt, cap, parameters in cases:
        print(f"MOS transient {label}", flush=True)
        try:
            item, decisions, fields, circuit = transient(nominal, slot, dt, cap, **parameters)
            transient_rows.append({"scenario": label, **item})
            transition_rows.extend({"scenario": label, **row} for row in decisions)
            arrays.update({label + "_" + key: value for key, value in fields.items()})
            if label == "nominal_100ps":
                (args.output / "generic_MOS_topology.txt").write_text(str(circuit), encoding="utf-8")
            write_csv(args.output / "mos_transient_summary.csv", transient_rows)
            write_csv(args.output / "mos_continuous_decisions.csv", transition_rows)
            np.savez_compressed(args.output / "mos_regeneration_waveforms.npz", **arrays)
        except Exception as error:
            failures.append({"scope": "transient", "scenario": label,
                             "error": f"{type(error).__name__}: {error}"})
            print(f"Recording failure: {failures[-1]}", flush=True)
    summary = {
        "solver": "ahkab 0.18 MOSQ",
        "model": "Generic 8-MOS differential-pair/current-mirror/two-inverter channel, assumed lumped capacitances",
        "static_completed": len(static_rows), "static_correct": sum(row["correct"] for row in static_rows),
        "boundary_completed": len(boundary_rows), "boundary_correct": sum(row["correct"] for row in boundary_rows),
        "transient_completed": len(transient_rows), "transient_results": transient_rows,
        "solver_failures": failures,
        "limits": ["No vendor process parameters or measured parasitics",
                   "This MOSQ implementation has no intrinsic charge model; added fixed capacitors are assumptions",
                   "Input held baseband voltages are scaled previous FDTD coordinates, not a solved RF receiver",
                   "Ideal tail current bias, references and voltage input sources",
                   "No stochastic noise, latch, clock, RF wave re-emission or full-adder feedback solved",
                   "Supply energy covers only these two comparator/buffer circuits and loads",
                   "Temperature coefficients are generic assumptions, not material characterization",
                   "No validated unlimited-time zero-error or full-system energy/speed claim"],
    }
    (args.output / "mos_regeneration_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "第六轮结果")
    parser.add_argument("--probe", action="store_true")
    main(parser.parse_args())
