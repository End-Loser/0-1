"""Deterministic reliability/speed tradeoff scan of the baseband surrogate."""

import argparse
import copy
import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np

from run_coupled_logic import calibration, circuit, input_pwl, measure, transition_order, VECTORS, NAMES
from run_spice_regeneration import solve, write_csv

ROOT = Path(__file__).parent
PERIODS_NS = (10, 15, 20, 30, 40, 50, 60, 80)
PROFILES = {
    "nominal": {},
    "heavy_20pF": {"capacitance": 20e-12},
    "supply_3V": {"supply": 3.0},
    "skew_4ns": {"input_skew_ns": (0, 2, 4)},
    "combined": {
        "wave": "epsilon_plus_1_percent", "supply": 3.0, "capacitance": 20e-12,
        "input_temperature": 330, "offset": 0.0065,
        "path_gain_scale": (1.05, 0.95, 1.05), "cancel_gain_scale": 1.05,
    },
    "combined_ref_error": {
        "wave": "epsilon_plus_1_percent", "supply": 3.0, "capacitance": 20e-12,
        "input_temperature": 330, "offset": 0.0065,
        "path_gain_scale": (1.05, 0.95, 1.05), "cancel_gain_scale": 1.05,
        "reference_shifts_V": (0.025, -0.025),
    },
    "compound_gain_loss_10pct": {
        "first_gain_scale": (0.9, 0.9, 1.1),
        "second_gain_scale": (1.1, 1.1, 0.9), "cancel_gain_scale": 0.9,
    },
    "compound_gain_loss_15pct": {
        "first_gain_scale": (1.15, 1.15, 0.85),
        "second_gain_scale": (1.15, 1.15, 0.85), "cancel_gain_scale": 0.85,
    },
}


def setup(profile, calibrations):
    options = copy.deepcopy(profile)
    coefficients = copy.deepcopy(calibrations[options.pop("wave", "nominal")])
    for key, gain_key in (("first_gain_scale", "first_gains"),
                          ("second_gain_scale", "second_gains")):
        scales = options.pop(key, (1, 1, 1))
        coefficients[gain_key] = (np.array(coefficients[gain_key]) * scales).tolist()
    skew = options.pop("input_skew_ns", (0, 0, 0))
    shifts = options.pop("reference_shifts_V", (0, 0))
    return coefficients, options, skew, shifts


def reference_adjust(lines, shifts):
    result = []
    for line in lines:
        if line.startswith("Vcref "):
            line = f"Vcref cref 0 {1.6 + shifts[0]:.12g}"
        elif line.startswith("Vsref "):
            line = f"Vsref sref 0 {1.2 + shifts[1]:.12g}"
        result.append(line)
    return result


def static_test(executable, profile, calibrations):
    coefficients, options, _, shifts = setup(profile, calibrations)
    supply = options.get("supply", 3.3)
    rows = []
    for index in range(8):
        state = f"{index:03b}"
        lines = reference_adjust(circuit([f"DC {bit}" for bit in state], coefficients, **options), shifts)
        data, _, _ = solve(executable, lines, ["op"], VECTORS)
        values = dict(zip(NAMES, data[0]))
        count = sum(map(int, state))
        expected = (int(count >= 2), count % 2)
        margins = [(values[k] - 0.7*supply if e else 0.3*supply-values[k])
                   for k, e in zip(("carry", "sum"), expected)]
        valid = all(-1e-6 <= values[k] <= supply+1e-6 for k in
                    ("carry", "sum", "carry_cmp", "sum_cmp", "carry_tail", "sum_tail"))
        rows.append({
            "state": state, "expected_carry": expected[0], "expected_sum": expected[1],
            "carry_V": values["carry"], "sum_V": values["sum"],
            "carry_margin_V": margins[0], "sum_margin_V": margins[1],
            "internal_valid": valid, "correct": bool(valid and min(margins) >= 0),
            "supply_V": supply, "supply_power_W": -supply*values["supply_current"],
        })
    return rows


def transient(executable, profile, calibrations, period, dt_ps, method):
    coefficients, options, skew, shifts = setup(profile, calibrations)
    order, slot = transition_order(), period*1e-9
    inputs = [input_pwl([int(s[b]) for s in order], slot, skew[b]*1e-9) for b in range(3)]
    lines = reference_adjust(circuit(inputs, coefficients, **options), shifts)
    lines.append(f".options method={method}")
    data, log, text = solve(executable, lines,
                           [f"tran {dt_ps*1e-12:.12g} {len(order)*slot:.12g} 0 {dt_ps*1e-12:.12g}"],
                           VECTORS)
    arrays = dict(zip(NAMES, data.T))
    supply = options.get("supply", 3.3)
    rows = measure(arrays, order, period, supply)
    energy = float(np.trapz(-supply*arrays["supply_current"], arrays["time"]))
    margins = []
    phase_counts = {str(p): 0 for p in (0.6, 0.7, 0.8, 0.9)}
    for row in rows:
        start = row["slot_index"]*slot
        end = (row["slot_index"]+1)*slot
        values = arrays[row["channel"]]
        mask = (arrays["time"] >= start+0.8*slot) & (arrays["time"] < end)
        margin = values[mask]-0.7*supply if row["expected"] else 0.3*supply-values[mask]
        margins.append(float(np.min(margin)))
        for phase in phase_counts:
            value = float(np.interp(start+float(phase)*slot, arrays["time"], values))
            good = value >= 0.7*supply if row["expected"] else value <= 0.3*supply
            phase_counts[phase] += int(good)
    result = {
        "period_ns": period, "dt_ps": dt_ps, "method": method, "supply_V": supply,
        "decisions": len(rows), "passed": sum(r["correct"] for r in rows),
        "sample_passed": sum(r["sample_correct"] for r in rows),
        "unsettled": sum(r["settling_to_30_70_percent_s"] is None for r in rows),
        "max_settling_ns": max((r["settling_to_30_70_percent_s"]*1e9 for r in rows
                               if r["settling_to_30_70_percent_s"] is not None), default=0),
        "minimum_late_margin_V": min(margins),
        "unchanged_expected_excursions": sum(r["unchanged_expected_with_excursion"] for r in rows),
        "duration_s": float(arrays["time"][-1]), "supply_energy_J": energy,
        "mean_supply_power_W": energy/arrays["time"][-1],
        "energy_per_slot_J": energy/len(order),
        "phase_sample_counts": phase_counts, "log_mentions_warning": "warning" in log.lower(),
    }
    return result, rows, arrays, text


def main(args):
    args.output.mkdir(parents=True, exist_ok=True)
    calibrations = calibration()
    static, cases, decisions, failures, profile_results = [], [], [], [], []
    for label, profile in PROFILES.items():
        print(f"Reliability static {label}", flush=True)
        try:
            rows = static_test(args.executable, profile, calibrations)
            static.extend({"profile": label, **r} for r in rows)
            static_good = all(r["correct"] for r in rows)
            static_solved = True
        except Exception as error:
            failures.append({"profile": label, "scope": "static", "error": str(error)})
            static_good = False
            static_solved = False
        primary = []
        minimum = None
        runs = [(p, 200, "trap", "primary") for p in PERIODS_NS]
        while runs:
            period, dt, method, scope = runs.pop(0)
            key = f"{label}_{period}ns_{dt}ps_{method}"
            print(f"Reliability transient {key}", flush=True)
            try:
                result, rows, arrays, text = transient(
                    args.executable, profile, calibrations, period, dt, method)
                cases.append({"case": key, "profile": label, "scope": scope, **result})
                decisions.extend({"case": key, **r} for r in rows)
                np.savez_compressed(args.output / f"{key}.npz", **arrays)
                if scope == "primary":
                    primary.append(result)
                if label in ("nominal", "combined") and dt == 100 and method == "trap":
                    (args.output / f"{label}_candidate.cir").write_text(text, encoding="ascii")
            except Exception as error:
                failures.append({"case": key, "scope": "transient", "error": str(error)})
            if not runs and len(primary) == len(PERIODS_NS) and minimum is None:
                passing = [r["period_ns"] for r in primary if r["passed"] == r["decisions"]]
                minimum = min(passing) if passing else -1
                check_period = minimum if minimum > 0 else 80
                runs.extend([(check_period, 100, "trap", "crosscheck"),
                             (check_period, 50, "trap", "crosscheck"),
                             (check_period, 100, "gear", "crosscheck")])
        check_cases = [r for r in cases if r["profile"] == label and r["scope"] == "crosscheck"]
        crosscheck_good = len(check_cases) == 3 and all(r["passed"] == r["decisions"] for r in check_cases)
        grid_good = minimum is not None and minimum > 0
        profile_results.append({
            "profile": label, "parameters": profile,
            "static_all_correct": static_good,
            "minimum_passing_grid_period_ns": minimum if grid_good else None,
            "candidate_crosschecks_all_correct": crosscheck_good,
            "classification": ("static_solver_failure" if not static_solved
                               else "static_logic_failure" if not static_good
                               else ("tested_grid_candidate" if grid_good and crosscheck_good
                                     else "no_crosschecked_candidate_in_grid")),
        })
        write_csv(args.output / "reliability_static.csv", static)
        write_csv(args.output / "reliability_decisions.csv", decisions)
    version = subprocess.run([str(args.executable), "--version"], capture_output=True, text=True, timeout=10)
    summary = {
        "solver_version": version.stdout+version.stderr,
        "executable_sha256": hashlib.sha256(args.executable.read_bytes()).hexdigest(),
        "period_grid_ns": PERIODS_NS, "order": transition_order(), "calibrations": calibrations,
        "profiles": profile_results, "static_count": len(static),
        "static_passed": sum(r["correct"] for r in static),
        "cases": cases, "failures": failures,
        "scope": "Deterministic corner profiles of an ideal-EM-interface single-bit baseband surrogate",
        "limits": [
            "Not exhaustive corners, random noise, probabilities or lifetime reliability",
            "Period grid minimum is not a continuous guaranteed maximum frequency",
            "No physical clock/latch or multi-bit execution",
            "Energy includes Vdd circuits only; EM/input/reference controlled-source energy excluded",
            "Independent gain perturbations may compound; not a single per-path error bound",
        ],
    }
    (args.output / "reliability_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({"profiles": profile_results, "case_count": len(cases),
                      "static_count": len(static), "failures": failures}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "第八轮结果")
    main(parser.parse_args())
