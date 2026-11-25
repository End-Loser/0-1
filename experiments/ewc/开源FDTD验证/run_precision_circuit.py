"""Tolerance, analytic DC, and capacitive transient tests with upstream ahkab."""

import argparse
from contextlib import contextmanager
import json
from pathlib import Path

import numpy as np
from scipy.optimize import brentq
from scipy.special import lambertw

from run_nonlinear import ahkab, make_circuit, UNIT_CURRENT as U, SATURATION_CURRENT as IS
from run_fdtd import write_csv

VT = ahkab.constants.Vth(300)


@contextmanager
def tolerances(tight=False):
    changes = {
        "vea": 1e-10, "ver": 1e-8, "iea": 1e-15, "ier": 1e-8,
        "gmin": 1e-15, "transient_max_nr_iter": 100,
    } if tight else {}
    old = {key: getattr(ahkab.options, key) for key in changes}
    for key, value in changes.items():
        setattr(ahkab.options, key, value)
    try:
        yield
    finally:
        for key, value in old.items():
            setattr(ahkab.options, key, value)


def dc(circuit):
    op = ahkab.run(circuit, ahkab.new_op(verbose=0))["op"]
    if op is None or getattr(op, "gmin", None) != 0:
        raise RuntimeError("No converged zero-gmin operating point.")
    current = float(np.asarray(op["I(Vsense)"]).item())
    voltages = {name: float(np.asarray(op["V" + name]).item()) for name in ("a", "b", "r", "drive")}
    return op, current, voltages


def analytic_dc(a, b, operation, resistance=0, output_n=1, reference_scale=1):
    drops = [VT * np.log1p(value / IS) + resistance * value
             for value in (a * U, b * U, reference_scale * U)]
    drive = drops[0] + drops[1] - drops[2] if operation == "multiply" else drops[0] - drops[1] + drops[2]
    vth = output_n * VT
    if not resistance:
        return float(IS * np.expm1(drive / vth))
    argument = (resistance * IS / vth) * np.exp((drive + resistance * IS) / vth)
    return float(vth / resistance * lambertw(argument).real - IS)


def transient_theory(times, a0, a1, operation, capacitance=0.2e-12, output_capacitance=0, td=1e-9):
    il, ih, ib, ir = a0 * U, a1 * U, 15 * U, U
    tau = capacitance * VT / (ih + IS)
    w0, wh = IS / (il + IS), IS / (ih + IS)
    elapsed = np.maximum(times - td, 0)
    w = wh + (w0 - wh) * np.exp(-elapsed / tau)
    diode_a_current = IS * (1 / w - 1)
    gain = (ib + IS) / (ir + IS) if operation == "multiply" else (ir + IS) / (ib + IS)
    sensor_current = gain * (diode_a_current + IS) - IS
    displacement = output_capacitance / capacitance * (ih - diode_a_current)
    sensor_current += np.where(times >= td, displacement, 0)
    return sensor_current / U, tau


def analytic_settling(a0, a1, operation, capacitance=0.2e-12, output_capacitance=0):
    target = a1 * 15 if operation == "multiply" else a1 / 15
    tolerance = 0.5 if operation == "multiply" else 0.001
    _, tau = transient_theory(np.array([1e-9]), a0, a1, operation, capacitance, output_capacitance)

    def margin(elapsed):
        coordinate, _ = transient_theory(np.array([1e-9 + elapsed]), a0, a1,
                                         operation, capacitance, output_capacitance)
        return abs(coordinate[0] - target) - tolerance

    if margin(0) <= 0:
        return 0.0
    return float(brentq(margin, 0, 100 * tau, xtol=1e-20))


def run_transient(a0, a1, operation, dt_ps, output_capacitance=0, method="TRAP"):
    pulse = ahkab.time_functions.pulse(v1=a0 * U, v2=a1 * U, td=1e-9,
                                       tr=0, pw=1e-3, tf=0, per=1)
    circuit = make_circuit(a0, 15, operation, input_functions={"a": pulse})
    for node in ("a", "b", "r"):
        circuit.add_capacitor("C" + node, node, "0", 0.2e-12)
    if output_capacitance:
        circuit.add_capacitor("Cout", "drive", "sense", output_capacitance)
    with tolerances(True):
        op, _, _ = dc(circuit)
        result = ahkab.run(circuit, ahkab.new_tran(
            tstart=0, tstop=40e-9, tstep=dt_ps * 1e-12, x0=op,
            method=method, use_step_control=(method == "IMPLICIT_EULER"), verbose=0,
        ))["tran"]
    if result is None:
        raise RuntimeError("Transient solver failed.")
    times = np.asarray(result["T"]).reshape(-1)
    measured = np.asarray(result["I(Vsense)"]).reshape(-1) / U
    if not np.all(np.isfinite(measured)):
        raise RuntimeError("Invalid transient current.")
    theory, tau = transient_theory(times, a0, a1, operation, output_capacitance=output_capacitance)
    target = a1 * 15 if operation == "multiply" else a1 / 15
    tol = 0.5 if operation == "multiply" else 0.001
    active = times >= 1e-9
    bad = np.flatnonzero(active & (np.abs(measured - target) > tol))
    if len(bad) and bad[-1] + 1 < len(times):
        sampled_settling = float(times[bad[-1] + 1] - 1e-9)
    elif not len(bad):
        sampled_settling = 0.0
    else:
        sampled_settling = None
    expected_settling = analytic_settling(a0, a1, operation,
                                         output_capacitance=output_capacitance)
    # A discontinuous ideal step is first-order localized; report its full error,
    # and separately an error excluding the first three step intervals.
    away = times >= 1e-9 + 3 * dt_ps * 1e-12
    summary = {
        "operation": operation, "A_before": a0, "A_after": a1, "B": 15,
        "dt_ps": dt_ps, "method": method,
        "adaptive_step_control": method == "IMPLICIT_EULER",
        "maximum_actual_step_ps": float(np.max(np.diff(times)) * 1e12),
        "input_shunt_capacitance_F": 0.2e-12, "output_shunt_capacitance_F": output_capacitance,
        "tau_s": tau, "analytic_settling_s": expected_settling,
        "sampled_settling_s": sampled_settling,
        "settling_difference_s": None if sampled_settling is None else sampled_settling - expected_settling,
        "maximum_coordinate_difference_from_analytic": float(np.max(np.abs(measured[active] - theory[active]))),
        "max_difference_excluding_step_intervals": float(np.max(np.abs(measured[away] - theory[away]))),
        "last_coordinate": float(measured[-1]), "target": target,
        "time_samples": len(times), "allowed_absolute_error": tol,
    }
    return times, measured, theory, summary


def main(args):
    args.output.mkdir(parents=True, exist_ok=True)
    rows, samples, dc_runs = [], {}, 0
    for tight in (False, True):
        label = "tight" if tight else "default"
        with tolerances(tight):
            for operation in ("multiply", "divide"):
                for a in range(16):
                    print(f"ahkab DC precision: {label} {operation} A={a}", flush=True)
                    for b in range(0 if operation == "multiply" else 1, 16):
                        _, current, voltage = dc(make_circuit(a, b, operation))
                        dc_runs += 1
                        theory = analytic_dc(a, b, operation)
                        residuals = [abs(IS * np.expm1(voltage[node] / VT) - value * U)
                                     for node, value in (("a", a), ("b", b), ("r", 1))]
                        rows.append({
                            "setting": label, "operation": operation, "A": a, "B": b,
                            "current_A": current, "analytic_current_A": theory,
                            "absolute_current_error_A": abs(current - theory),
                            "normalized_coordinate": current / U,
                            "maximum_input_KCL_residual_A": max(residuals),
                        })
                        samples[(label, operation, a, b)] = current
    write_csv(args.output / "dc_precision_all_inputs.csv", rows)
    nonideal_rows = []
    with tolerances(True):
        for params in ({"series_resistance": 1}, {"series_resistance": 10},
                       {"output_ideality": 1.001}, {"reference_scale": 1.01}):
            for a, b in ((1, 1), (5, 3), (15, 15)):
                _, current, _ = dc(make_circuit(a, b, "multiply", **params))
                dc_runs += 1
                theory = analytic_dc(a, b, "multiply",
                                     resistance=params.get("series_resistance", 0),
                                     output_n=params.get("output_ideality", 1),
                                     reference_scale=params.get("reference_scale", 1))
                nonideal_rows.append({
                    "parameters": json.dumps(params), "A": a, "B": b,
                    "current_A": current, "closed_form_current_A": theory,
                    "absolute_current_error_A": abs(current - theory),
                })
    write_csv(args.output / "nonideal_lambertW_checks.csv", nonideal_rows)
    transient_summaries, arrays = [], {}
    for operation in ("multiply", "divide"):
        for a0, a1 in ((1, 15), (15, 1)):
            for dt in (20, 10, 5):
                print(f"ahkab transient: {operation} {a0}->{a1}, dt={dt} ps", flush=True)
                t, y, ref, item = run_transient(a0, a1, operation, dt)
                transient_summaries.append(item)
                key = f"{operation}_{a0}_{a1}_{dt}ps"
                arrays[key + "_time"], arrays[key + "_coordinate"], arrays[key + "_analytic"] = t, y, ref
    for operation, a0, a1 in (("divide", 1, 15), ("divide", 15, 1)):
        print(f"ahkab transient including output capacitance: {operation} {a0}->{a1}", flush=True)
        t, y, ref, item = run_transient(a0, a1, operation, 10, output_capacitance=0.2e-12)
        transient_summaries.append(item)
        key = f"{operation}_{a0}_{a1}_outputC"
        arrays[key + "_time"], arrays[key + "_coordinate"], arrays[key + "_analytic"] = t, y, ref
    print("ahkab independent implicit-Euler transient comparison", flush=True)
    euler_failure = None
    try:
        t, y, ref, item = run_transient(15, 1, "multiply", 10, method="IMPLICIT_EULER")
        transient_summaries.append(item)
        arrays["euler_time"], arrays["euler_coordinate"], arrays["euler_analytic"] = t, y, ref
    except (ValueError, TypeError, RuntimeError) as error:
        euler_failure = f"{type(error).__name__}: {error}"
        print(f"Euler comparison failed; preserving TRAP results: {euler_failure}", flush=True)
    write_csv(args.output / "capacitive_transient_summary.csv", transient_summaries)
    np.savez_compressed(args.output / "capacitive_transient_waveforms.npz", **arrays)
    scaling = []
    for c in (0.02e-12, 0.2e-12, 2e-12):
        for operation in ("multiply", "divide"):
            for a0, a1 in ((1, 15), (15, 1)):
                scaling.append({
                    "capacitance_F": c, "operation": operation, "A_before": a0, "A_after": a1,
                    "analytic_settling_s": analytic_settling(a0, a1, operation, capacitance=c),
                    "method": "analytic forecast only; not another transient run",
                })
    write_csv(args.output / "capacitance_scaling_analytic.csv", scaling)
    max_change = max(abs(samples[("tight", op, a, b)] - value)
                     for (setting, op, a, b), value in samples.items() if setting == "default")
    r = IS / U
    theory_error_rows = []
    for a in range(16):
        for b in range(16):
            theory_error_rows.append({
                "operation": "multiply", "A": a, "B": b,
                "exact_normalized_bias": -r * (a - 1) * (b - 1) / (1 + r),
            })
        for b in range(1, 16):
            theory_error_rows.append({
                "operation": "divide", "A": a, "B": b,
                "exact_normalized_bias": r * (b - 1) * (a - b) / (b * (b + r)),
            })
    write_csv(args.output / "exact_finite_IS_bias.csv", theory_error_rows)
    summary = {
        "solver": "ahkab 0.18", "dc_runs": dc_runs, "transient_runs": len(transient_summaries),
        "failed_euler_comparison": euler_failure,
        "thermal_voltage_library_V": VT,
        "thermal_voltage_current_SI_V": 1.380649e-23 * 300 / 1.602176634e-19,
        "nominal_IS_over_unit_current": r,
        "max_default_tight_current_difference_A": max_change,
        "max_DC_closed_form_error_A": max(row["absolute_current_error_A"] for row in rows),
        "max_input_KCL_residual_A": max(row["maximum_input_KCL_residual_A"] for row in rows),
        "max_nonideal_closed_form_error_A": max(row["absolute_current_error_A"] for row in nonideal_rows),
        "tight_settings": {"vea_V": 1e-10, "ver": 1e-8, "iea_A": 1e-15, "ier": 1e-8, "gmin_S": 1e-15},
        "transient_results": transient_summaries,
        "limits": ["Assumed constant capacitances, not measured device curves",
                   "Ideal current sources and unlimited-bandwidth linear buffers",
                   "No EM coupling, package inductance, supply rails or physical noise",
                   "Fixed Euler fails None/int buffer comparison; adaptive Euler failure recorded separately"],
    }
    (args.output / "precision_circuit_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(11, 4), constrained_layout=True)
    for ax, a0, a1 in zip(axes, (1, 15), (15, 1)):
        for dt in (20, 10, 5):
            key = f"multiply_{a0}_{a1}_{dt}ps"
            ax.plot((arrays[key + "_time"] - 1e-9) * 1e9, arrays[key + "_coordinate"],
                    label=f"TRAP {dt} ps", lw=1)
        key = f"multiply_{a0}_{a1}_5ps"
        ax.plot((arrays[key + "_time"] - 1e-9) * 1e9, arrays[key + "_analytic"],
                "k--", label="Closed form", lw=1)
        ax.set(xlim=(-0.5, 22), xlabel="Time after input step (ns)", ylabel="Multiplication coordinate",
               title=f"Input A {a0} -> {a1}, B=15, C=0.2 pF")
        ax.legend()
        ax.grid(alpha=0.25)
    fig.savefig(args.output / "capacitive_transient_convergence.png", dpi=150)
    plt.close(fig)
    print(json.dumps({key: value for key, value in summary.items()
                      if key != "transient_results"}, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "第三轮结果")
    main(parser.parse_args())
