"""One-way FDTD-to-ahkab cascade with an explicitly ideal receive front end.

This is not self-consistent RF/circuit co-simulation. Arithmetic is performed
by circuits in the active receiver, not by the passive cavity or the decoder.
"""

import argparse
import json
from pathlib import Path

import numpy as np

from run_fdtd import write_csv
from run_nonlinear import ahkab, make_circuit, UNIT_CURRENT as U
from run_precision_circuit import tolerances
from run_precision_fdtd import simulate

FREQUENCIES = (3e9, 3.6e9, 4.5e9)
OPERATIONS = ("add", "subtract", "multiply", "divide")
CODES = {operation: (index + 1) / 4 for index, operation in enumerate(OPERATIONS)}


class Receiver:
    """Fixed linear waveform demultiplexer; no arithmetic targets are available."""

    def __init__(self, basis):
        self.matrix = np.asarray(basis)
        if np.linalg.matrix_rank(self.matrix) != 3:
            raise ValueError("Three input channels cannot be distinguished.")
        self.weights = np.linalg.pinv(self.matrix)

    def recover(self, waveform):
        coefficients = self.weights @ waveform
        a, b = 15 * coefficients[:2]
        distances = np.abs(np.array(list(CODES.values())) - coefficients[2])
        index = int(np.argmin(distances))
        residual = float(np.linalg.norm(waveform - self.matrix @ coefficients))
        if distances[index] >= 0.125:
            return None, None, None, "operator_out_of_range", coefficients, residual
        # Remove only numerical roundoff near zero, not integer input errors.
        a = 0.0 if abs(a) < 1e-9 else float(a)
        b = 0.0 if abs(b) < 1e-9 else float(b)
        operation = OPERATIONS[index]
        if a < 0 or b < 0:
            status = "negative_input"
        elif operation == "divide" and b < 0.5:
            status = "division_by_zero"
        else:
            status = "ok"
        return a, b, operation, status, coefficients, residual


def solve_receiver_circuit(a, b, operation):
    if operation in ("multiply", "divide"):
        circuit = make_circuit(a, b, operation)
    else:
        circuit = ahkab.Circuit("ideal current summing branch")
        circuit.add_isource("IA", "0", "sense", dc_value=a * U)
        circuit.add_isource("IB", "0", "sense",
                            dc_value=b * U * (1 if operation == "add" else -1))
        circuit.add_vsource("Vsense", "sense", "0", dc_value=0)
    with tolerances(True):
        result = ahkab.run(circuit, ahkab.new_op(verbose=0))["op"]
    if result is None or getattr(result, "gmin", None) != 0:
        raise RuntimeError("No converged zero-gmin receiver operating point.")
    current = float(np.asarray(result["I(Vsense)"]).item())
    if not np.isfinite(current):
        raise RuntimeError("Non-finite output.")
    return current


def decode_output(current):
    """One common linear current readout, with no input or operation arguments."""
    return current / U


def evaluate(receiver, waveform, scenario, a, b, requested):
    recovered_a, recovered_b, measured_op, status, coefficients, residual = receiver.recover(waveform)
    current = solve_receiver_circuit(recovered_a, recovered_b, measured_op) if status == "ok" else None
    coordinate = decode_output(current) if current is not None else None
    # Arithmetic targets are used only after the physical output has been solved.
    if requested == "divide" and b == 0:
        target, error = None, None
        passed = status == "division_by_zero" and measured_op == requested
    else:
        if requested == "add":
            target = a + b
        elif requested == "subtract":
            target = a - b
        elif requested == "multiply":
            target = a * b
        else:
            target = a / b
        error = abs(coordinate - target) if coordinate is not None else None
        passed = (status == "ok" and measured_op == requested
                  and (error <= 0.001 if requested == "divide"
                       else int(np.rint(coordinate)) == target))
    return {
        "scenario": scenario, "A": a, "B": b, "requested_operation": requested,
        "measured_operation": measured_op, "measured_A": recovered_a, "measured_B": recovered_b,
        "measured_operator_code": float(coefficients[2]), "status": status,
        "output_current_A": current, "common_output_coordinate": coordinate,
        "target_for_validation_only": target, "absolute_result_error": error,
        "waveform_fit_residual_norm": residual, "pass": bool(passed),
    }


def main(output):
    output.mkdir(parents=True, exist_ok=True)
    arrays, calls = {}, {"fdtd": 0, "dc": 0}

    def em(amplitudes, frequencies=FREQUENCIES, epsilon_r=2.1):
        calls["fdtd"] += 1
        return simulate(1.25, amplitudes, frequencies=frequencies, epsilon_r=epsilon_r)

    bases, actual_frequencies = {}, None
    for name, epsilon in (("nominal", 2.1), ("epsilon_plus_1_percent", 2.121)):
        fields = []
        for index in range(3):
            print(f"FDTD cascade basis: {name}, source={index}", flush=True)
            amplitudes = np.eye(3)[index]
            result = em(amplitudes, epsilon_r=epsilon)
            actual_frequencies = result["actual_source_frequencies_Hz"]
            fields.append(result["field"])
            arrays[f"{name}_source_{index}"] = result["field"]
            arrays["time"] = result["time"]
        bases[name] = np.column_stack(fields)
    receiver = Receiver(bases["nominal"])
    singular_values = np.linalg.svd(receiver.matrix, compute_uv=False)
    np.savez_compressed(output / "fixed_frontend.npz",
                        templates=receiver.matrix, weights=receiver.weights, time=arrays["time"])
    scenarios = (
        ("nominal", bases["nominal"], 1),
        ("B_gain_plus_0_1_percent", bases["nominal"], 1.001),
        ("B_gain_plus_1_percent", bases["nominal"], 1.01),
        ("epsilon_plus_1_percent", bases["epsilon_plus_1_percent"], 1),
    )
    rows = []
    for name, basis, b_gain in scenarios:
        for operation in OPERATIONS:
            print(f"Cascade sweep: {name} {operation}", flush=True)
            for a in range(16):
                for b in range(16):
                    waveform = basis @ np.array((a / 15, b / 15 * b_gain, CODES[operation]))
                    item = evaluate(receiver, waveform, name, a, b, operation)
                    rows.append(item)
                    calls["dc"] += int(item["output_current_A"] is not None)
    write_csv(output / "cascade_all_inputs.csv", rows)

    direct = []
    for operation in OPERATIONS:
        for a, b in ((5, 3), (3, 5), (15, 15), (0, 15), (5, 0)):
            print(f"Direct FDTD cascade: {a} {operation} {b}", flush=True)
            result = em((a / 15, b / 15, CODES[operation]))
            item = evaluate(receiver, result["field"], "direct_nominal", a, b, operation)
            reconstructed = bases["nominal"] @ np.array((a / 15, b / 15, CODES[operation]))
            item["relative_superposition_error"] = float(
                np.linalg.norm(result["field"] - reconstructed) / np.linalg.norm(result["field"]))
            direct.append(item)
            arrays[f"direct_{operation}_{a}_{b}"] = result["field"]
            calls["dc"] += int(item["output_current_A"] is not None)
    for operation in OPERATIONS:
        result = em((5 / 15, 3 / 15, CODES[operation]), epsilon_r=2.121)
        item = evaluate(receiver, result["field"], "direct_epsilon_plus_1_percent", 5, 3, operation)
        reconstructed = bases["epsilon_plus_1_percent"] @ np.array((5 / 15, 3 / 15, CODES[operation]))
        item["relative_superposition_error"] = float(
            np.linalg.norm(result["field"] - reconstructed) / np.linalg.norm(result["field"]))
        direct.append(item)
        arrays[f"direct_perturbed_{operation}"] = result["field"]
        calls["dc"] += int(item["output_current_A"] is not None)
    write_csv(output / "cascade_direct_cases.csv", direct)

    # A negative control preserves the old same-frequency mirror ambiguity.
    old_a = em((1, 0, 0), frequencies=(3e9,) * 3)["field"]
    old_b = em((0, 1, 0), frequencies=(3e9,) * 3)["field"]
    old_first = em((2 / 15, 8 / 15, CODES["multiply"]), frequencies=(3e9,) * 3)["field"]
    old_second = em((5 / 15, 5 / 15, CODES["multiply"]), frequencies=(3e9,) * 3)["field"]
    new_first = bases["nominal"] @ np.array((2 / 15, 8 / 15, CODES["multiply"]))
    new_second = bases["nominal"] @ np.array((5 / 15, 5 / 15, CODES["multiply"]))
    collision = {
        "first_input": [2, 8], "second_input": [5, 5], "products": [16, 25],
        "same_frequency_data_basis_rank": int(np.linalg.matrix_rank(np.column_stack((old_a, old_b)))),
        "same_frequency_relative_waveform_difference": float(
            np.linalg.norm(old_first - old_second) / np.linalg.norm(old_first)),
        "FDM_relative_waveform_difference": float(
            np.linalg.norm(new_first - new_second) / np.linalg.norm(new_first)),
        "FDM_difference_method": "Reconstruction from independently solved linear bases",
    }
    arrays["same_frequency_collision_first"] = old_first
    arrays["same_frequency_collision_second"] = old_second
    np.savez_compressed(output / "cascade_waveforms.npz", **arrays)

    summaries = []
    for name, _, _ in scenarios:
        for operation in OPERATIONS:
            subset = [item for item in rows if item["scenario"] == name
                      and item["requested_operation"] == operation
                      and item["target_for_validation_only"] is not None]
            errors = [item["absolute_result_error"] for item in subset
                      if item["absolute_result_error"] is not None]
            summaries.append({
                "scenario": name, "operation": operation, "cases": len(subset),
                "passed": sum(item["pass"] for item in subset),
                "wrong_operator": sum(item["measured_operation"] != operation for item in subset),
                "rejected": sum(item["status"] != "ok" for item in subset),
                "maximum_absolute_result_error": max(errors) if errors else None,
            })
    write_csv(output / "cascade_summary.csv", summaries)
    invalid = [item for item in rows if item["scenario"] == "nominal"
               and item["requested_operation"] == "divide" and item["B"] == 0]
    summary = {
        "solvers": ["flaport/fdtd 0.3.5", "ahkab 0.18"],
        "architecture": "Three external RF sources; one active receiver with linear demux, ideal controlled arithmetic branches, one common current readout",
        "coupling": "One-way waveform handoff to ideal baseband current sources; not RF/circuit co-simulation",
        "computation_location": "Active receiver circuit, not passive cavity and not output decoder",
        "frequencies_Hz": list(FREQUENCIES), "grid_mm": 1.25, "frame_ns": 5,
        "source_actual_frequencies_Hz": actual_frequencies,
        "direct_fdtd_runs": calls["fdtd"], "dc_runs": calls["dc"],
        "reconstructed_sweep_cases": len(rows),
        "frontend_rank": int(np.linalg.matrix_rank(receiver.matrix)),
        "frontend_singular_values": singular_values.tolist(),
        "frontend_condition_number": float(singular_values[0] / singular_values[-1]),
        "operator_codes": CODES, "output_scale_A_per_coordinate": U,
        "input_zero_cleanup_threshold": 1e-9,
        "division_zero_guard_coordinate_threshold": 0.5,
        "nominal_division_zero_rejections": sum(item["pass"] for item in invalid),
        "maximum_direct_superposition_error": max(item["relative_superposition_error"] for item in direct),
        "collision_control": collision,
        "results": summaries,
        "limits": [
            "Fixed pseudoinverse is an ideal software linear demultiplexer, not a simulated RF mixer",
            "Mode selection rebuilds the ideal branch topology, not a simulated transistor switch",
            "Ideal current conversion and common zero-volt current sensor",
            "No ADC, output quantization, noise, supply rails, loads, finite bandwidth or EM feedback",
            "Only isolated zero-initialized 5 ns frames; no consecutive-frame transient test",
            "DC after waveform recovery; previous capacitive timing is not revalidated here",
            "No result RF retransmission or passive nonlinear material computation",
        ],
    }
    (output / "cascade_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    nominal = [item for item in summaries if item["scenario"] == "nominal"]
    assert all(item["passed"] == item["cases"] for item in nominal)
    assert all(item["pass"] for item in direct if item["scenario"] == "direct_nominal")
    assert summary["maximum_direct_superposition_error"] < 1e-10
    assert summary["nominal_division_zero_rejections"] == 16
    assert collision["same_frequency_data_basis_rank"] == 1
    assert collision["same_frequency_relative_waveform_difference"] < 1e-10
    assert summary["frontend_rank"] == 3
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "第四轮结果")
    main(parser.parse_args().output)
