"""FDTD wave-adder primitives and exhaustive bit-serial arithmetic validation.

Comparators, storage, switching, and regenerated emitters remain ideal.
This is not transistor-level validation or continuous-clock co-simulation.
"""

import argparse
import itertools
import json
from pathlib import Path

import numpy as np

from run_fdtd import write_csv
from run_precision_fdtd import simulate


def ripple(a, b, width, table, carry=0):
    result = 0
    for bit in range(width):
        value, carry = table[((a >> bit) & 1, (b >> bit) & 1, carry)]
        result |= value << bit
    return result, carry


def compute(a, b, operation, table):
    if operation == "add":
        value, carry = ripple(a, b, 4, table)
        return {"value": value | (carry << 4), "cells": 4}
    if operation == "subtract":
        value, _ = ripple(a, b ^ 31, 5, table, 1)
        return {"value": value if value < 16 else value - 32, "cells": 5}
    if operation == "multiply":
        accumulator = 0
        for bit in range(4):
            operand = a << bit if (b >> bit) & 1 else 0
            accumulator, _ = ripple(accumulator, operand, 8, table)
        return {"value": accumulator, "cells": 32}
    if b == 0:
        return {"error": "division_by_zero", "cells": 0}
    quotient, remainder = 0, 0
    for bit in range(3, -1, -1):
        shifted = (remainder << 1) | ((a >> bit) & 1)
        difference, no_borrow = ripple(shifted, b ^ 31, 5, table, 1)
        # Fixed schedule: restore B only after a borrow; otherwise add zero.
        remainder, _ = ripple(difference, b if not no_borrow else 0, 5, table)
        quotient |= no_borrow << bit
    return {"quotient": quotient, "remainder": remainder, "cells": 40}


def main(output):
    output.mkdir(parents=True, exist_ok=True)
    calls, bases, arrays = 0, {}, {}
    for scenario, epsilon in (("nominal", 2.1), ("epsilon_plus_1_percent", 2.121)):
        fields = []
        for index in range(3):
            print(f"Wave logic basis: {scenario}, source {index}", flush=True)
            result = simulate(1.25, np.eye(3)[index], epsilon_r=epsilon)
            calls += 1
            fields.append(result["field"])
            arrays[f"{scenario}_basis_{index}"] = result["field"]
            arrays["time"] = result["time"]
        bases[scenario] = np.column_stack(fields)
    template = bases["nominal"][:, 0]
    weights = template / np.dot(template, template)
    calibration = weights @ bases["nominal"]
    if np.any(np.abs(calibration) < 0.1):
        raise RuntimeError("A source path has insufficient calibrated projection.")
    gate_rows, tables = [], {}
    for scenario, epsilon in (("nominal", 2.1), ("epsilon_plus_1_percent", 2.121)):
        table = {}
        for a, b, cin in itertools.product((0, 1), repeat=3):
            print(f"Direct wave full-adder: {scenario} {a}{b}{cin}", flush=True)
            first_amplitudes = np.array((a, b, cin)) / calibration
            first = simulate(1.25, first_amplitudes, epsilon_r=epsilon)
            calls += 1
            count_signal = float(weights @ first["field"])
            carry = int(count_signal >= 1.5)
            second_amplitudes = np.array((a, b, cin - 2 * carry)) / calibration
            second = simulate(1.25, second_amplitudes, epsilon_r=epsilon)
            calls += 1
            sum_signal = float(weights @ second["field"])
            sum_bit = int(sum_signal >= 0.5)
            expected = a + b + cin
            table[(a, b, cin)] = (sum_bit, carry)
            gate_rows.append({
                "scenario": scenario, "a": a, "b": b, "carry_in": cin,
                "first_measured_coordinate": count_signal,
                "second_measured_coordinate": sum_signal,
                "sum_bit": sum_bit, "carry_out": carry,
                "carry_threshold_margin": abs(count_signal - 1.5),
                "sum_threshold_margin": abs(sum_signal - 0.5),
                "correct": sum_bit == (expected & 1) and carry == (expected >> 1),
                "maximum_direct_reconstruction_difference": max(
                    float(np.max(np.abs(first["field"] - bases[scenario] @ first_amplitudes))),
                    float(np.max(np.abs(second["field"] - bases[scenario] @ second_amplitudes)))),
            })
            arrays[f"{scenario}_{a}{b}{cin}_first"] = first["field"]
            arrays[f"{scenario}_{a}{b}{cin}_second"] = second["field"]
        tables[scenario] = table
    write_csv(output / "wave_full_adder_direct.csv", gate_rows)
    np.savez_compressed(output / "wave_logic_fields.npz", **arrays,
                        projection_weights=weights, input_calibration=calibration)

    arithmetic_rows, summaries = [], []
    for scenario, table in tables.items():
        for operation in ("add", "subtract", "multiply", "divide"):
            start = len(arithmetic_rows)
            for a in range(16):
                for b in range(16):
                    actual = compute(a, b, operation, table)
                    if operation == "add":
                        correct = actual["value"] == a + b
                    elif operation == "subtract":
                        correct = actual["value"] == a - b
                    elif operation == "multiply":
                        correct = actual["value"] == a * b
                    elif b == 0:
                        correct = actual.get("error") == "division_by_zero"
                    else:
                        correct = (actual["quotient"] * b + actual["remainder"] == a
                                   and 0 <= actual["remainder"] < b)
                    arithmetic_rows.append({
                        "scenario": scenario, "operation": operation, "A": a, "B": b,
                        "value": actual.get("value"), "quotient": actual.get("quotient"),
                        "remainder": actual.get("remainder"), "error": actual.get("error"),
                        "cells": actual["cells"], "correct": bool(correct),
                        "method": "Discrete-event algorithm using measured 8-state gate; not fresh FDTD per operation",
                    })
            selected = arithmetic_rows[start:]
            summaries.append({
                "scenario": scenario, "operation": operation, "cases": len(selected),
                "passed": sum(row["correct"] for row in selected),
            })
    write_csv(output / "wave_arithmetic_exhaustive.csv", arithmetic_rows)

    corner_rows = []
    # Independent projected gain errors for three paths in each of two stages.
    # +/- noise samples are exhaustive extrema of an assumed bounded error,
    # not a physical stochastic noise model.
    for delta in (0.05, 0.10, 0.12, 0.15):
        tested, passed, minimum_margin = 0, 0, np.inf
        for a, b, cin in itertools.product((0, 1), repeat=3):
            ideal_count = a + b + cin
            for signs in itertools.product((-1, 1), repeat=6):
                g1 = 1 + delta * np.array(signs[:3])
                g2 = 1 + delta * np.array(signs[3:])
                for n1, n2 in itertools.product((-0.05, 0.05), repeat=2):
                    count = float(np.dot((a, b, cin), g1) + n1)
                    carry = int(count >= 1.5)
                    summed = float(np.dot((a, b, cin - 2 * carry), g2) + n2)
                    sum_bit = int(summed >= 0.5)
                    tested += 1
                    passed += int(carry == (ideal_count >> 1) and sum_bit == (ideal_count & 1))
                    minimum_margin = min(minimum_margin, abs(count - 1.5), abs(summed - 0.5))
        corner_rows.append({
            "relative_projected_gain_bound": delta,
            "combined_noise_offset_bound_logic_units": 0.05,
            "cases": tested, "passed": passed,
            "minimum_sampled_threshold_distance": float(minimum_margin),
            "carry_sufficient_error_bound": 3 * delta + 0.05,
            "sum_sufficient_error_bound": 4 * delta + 0.05,
            "theoretical_guarantee": 4 * delta + 0.05 < 0.5,
            "scope": "Scalar projected-gain and bounded-offset model, not new Maxwell or comparator simulations",
        })
    write_csv(output / "wave_logic_bounded_error_corners.csv", corner_rows)

    comparator_power = 2 * 3.3 * 3.2e-3
    budgets = []
    for name, cell_ns, power_W in (("low_assumption", 50, 0.1),
                                   ("central_assumption", 100, 0.2),
                                   ("high_assumption", 200, 0.5)):
        for operation, cells in (("add", 4), ("subtract", 5), ("multiply", 32), ("divide", 40)):
            duration = cells * cell_ns * 1e-9
            budgets.append({
                "scenario": name, "operation": operation, "full_adder_cell_count": cells,
                "assumed_two_stage_cell_time_ns": cell_ns,
                "assumed_active_core_power_W": power_W,
                "core_latency_s": duration, "no_overlap_core_operations_per_s": 1 / duration,
                "active_core_energy_J": power_W * duration,
                "two_comparators_typical_quiescent_energy_J": comparator_power * duration,
                "scope": "Conditional engineering budget; excludes external link, startup, calibration, thermal control and idle energy",
            })
    write_csv(output / "speed_energy_assumptions.csv", budgets)
    summary = {
        "solver": "flaport/fdtd 0.3.5",
        "architecture": "Bit-serial wave interference, threshold carry regeneration, second-stage interference cancellation, ideal state controller",
        "direct_fdtd_runs": calls,
        "direct_gate_states": len(gate_rows),
        "source_projection_calibration": calibration.tolist(),
        "minimum_direct_gate_threshold_margin": min(
            min(row["carry_threshold_margin"], row["sum_threshold_margin"]) for row in gate_rows),
        "arithmetic_cases": len(arithmetic_rows), "arithmetic_results": summaries,
        "bounded_error_results": corner_rows,
        "two_comparator_typical_quiescent_power_W": comparator_power,
        "comparator_reference": {
            "part": "TI TLV3501", "product_url": "https://www.ti.com/product/TLV3501",
            "typical_Iq_A_per_channel": 3.2e-3, "assumed_supply_V": 3.3,
            "advertised_delay_ns": 4.5, "max_offset_25C_V": 6.5e-3,
            "note": "Product-page values, not a validated 3.3 V corner specification or an RF detector model",
        },
        "limits": [
            "Hard thresholds and emitted regenerated levels are ideal software interface models",
            "Majority decision is active electronics, so this is not passive or all-wave computation",
            "Fixed matched projection stands for an unsimulated coherent receiver",
            "Two FDTD stages independently start from zero; no physical stage switching or feedback transient solved",
            "Exhaustive arithmetic uses measured gate states as a primitive cache, not a whole-operation answer table",
            "Bit inversion, routing, storage, shift and mode FSM are not transistor-level verified",
            "No physical noise, supply, package, load or RF port-power calibration",
            "Timing and total-power estimates are explicit assumptions, not measured simulation outputs",
            "No guarantee of unlimited-time zero error outside the stated bounded model",
        ],
    }
    (output / "wave_logic_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    assert calls == 38
    assert all(row["correct"] for row in gate_rows)
    assert all(row["correct"] for row in arithmetic_rows)
    assert all(row["passed"] == row["cases"] for row in corner_rows[:2])
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "第五轮结果")
    main(parser.parse_args().output)
