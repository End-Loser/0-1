"""Frozen signal-only cancellation calibration and bounded-error validation."""

import argparse
import copy
import hashlib
import itertools
import json
import time
from pathlib import Path

import numpy as np

from run_coupled_logic import calibration, circuit, input_pwl, NAMES, VECTORS, transition_order
from run_reliability import PROFILES, PERIODS_NS, setup, reference_adjust, static_test, transient
from run_spice_regeneration import solve, write_csv

ROOT = Path(__file__).parent
RESIDUALS = (0, .01, .02, .03, .05, .075, .10)
DRIFTS = tuple(np.arange(-.30, .301, .01).round(8))
CAL_SLOT_NS = 80


def save_run(folder, key, arrays, text):
    np.savez_compressed(folder / (key + ".npz"), **arrays)
    (folder / (key + ".cir")).write_text(text, encoding="ascii")


def acquire(executable, profile, calibrations, states, injected=None):
    gains, options, _, shifts = setup(profile, calibrations)
    slot = CAL_SLOT_NS * 1e-9
    inputs = [input_pwl([int(s[b]) for s in states], slot) for b in range(3)]
    lines = reference_adjust(circuit(inputs, gains, **options), shifts)
    if injected is not None:
        # An ideal calibration switch substitutes a known rail waveform only in training.
        lines = [l for l in lines if not l.startswith("Rcancel ")]
        lines += ["Rcancel probe_rail cancel 1000",
                  "Vprobe probe_rail 0 " + input_pwl(injected, slot)]
    data, log, text = solve(executable, lines,
                           [f"tran 1e-10 {len(states)*slot:.12g} 0 1e-10"], VECTORS)
    assert "warning" not in log.lower(), log
    arrays = dict(zip(NAMES, data.T))
    means, spans = [], []
    for i in range(len(states)):
        mask = (arrays["time"] >= (i+.8)*slot) & (arrays["time"] < (i+1)*slot)
        means.append({k: float(np.mean(arrays[k][mask])) for k in NAMES[1:]})
        spans.append({k: float(np.ptp(arrays[k][mask])) for k in NAMES[1:]})
    supply = options.get("supply", 3.3)
    energy = float(np.trapz(-supply*arrays["supply_current"], arrays["time"]))
    return means, spans, arrays, text, energy


def train(executable, profile, calibrations, folder, label):
    start = time.perf_counter()
    rails, rail_span, arrays, text, e1 = acquire(
        executable, profile, calibrations, ["000", "111"])
    save_run(folder, label+"_training_rails", arrays, text)
    low, high = (row["carry"] for row in rails)
    states = ["000", "100", "010", "001", "000", "000"]
    measured, spans, arrays, text, e2 = acquire(
        executable, profile, calibrations, states, [low]*5+[high])
    save_run(folder, label+"_training_paths", arrays, text)
    first = [measured[i]["carry_input"]-measured[0]["carry_input"] for i in (1, 2, 3)]
    second = [measured[i]["sum_input"]-measured[0]["sum_input"] for i in (1, 2, 3)]
    response = measured[4]["sum_input"]-measured[5]["sum_input"]
    assert high > low and response > 0 and min(second) > 0
    trim = 2*float(np.mean(second))/response
    assert .5 <= trim <= 2, "Required trim outside assumed actuator range."
    frozen = copy.deepcopy(profile)
    frozen["cancellation_trim"] = trim
    result = {
        "profile": label, "carry_low_V": low, "carry_high_V": high,
        "first_steps_V": first, "second_steps_V": second,
        "cancellation_response_V": response, "trim": trim,
        "training_states": states, "training_means": measured,
        "training_spans": spans, "rail_spans": rail_span,
        "acquisition_ns": 8*CAL_SLOT_NS, "local_supply_energy_J": e1+e2,
        "host_wall_seconds": time.perf_counter()-start,
        "frozen_profile": frozen,
        "rule": "trim = 2 * mean(second path unit responses) / measured cancellation rail response",
        "scope": "Ideal sensing, switching and trim; no ADC/controller/programming energy or latency",
    }
    return frozen, result


def endpoint_profile(profile, delta, first_sign, second_sign):
    """Four global DC envelopes cover independent six path and cancellation errors.

    Positive paths bound the first-stage input. For the second stage, positive
    paths and the negative cancellation coefficient take opposite error signs.
    The cancellation coefficient is independent of the second-stage D gain.
    """
    result = copy.deepcopy(profile)
    for key in ("first_gain_scale", "second_gain_scale"):
        sign = first_sign if key.startswith("first") else second_sign
        result[key] = (np.array(result.get(key, (1, 1, 1)))*(1+sign*delta)).tolist()
    result["cancellation_trim"] *= (1-second_sign*delta)/(1+second_sign*delta)
    return result


def envelope_scan(executable, frozen, calibrations, label, delta):
    rows = []
    for csign, ssign in itertools.product((-1, 1), repeat=2):
        profile = endpoint_profile(frozen, delta, csign, ssign)
        for row in static_test(executable, profile, calibrations):
            rows.append({"profile": label, "residual": delta,
                         "first_sign": csign, "second_sign": ssign, **row})
    return rows


def main(args):
    args.output.mkdir(parents=True, exist_ok=True)
    calibrations = calibration()
    training, profiles, static, cases, decisions, envelopes, drift_rows = [], {}, [], [], [], [], []
    summary_profiles = []
    for label, profile in PROFILES.items():
        print("Train "+label, flush=True)
        frozen, record = train(args.executable, profile, calibrations, args.output, label)
        training.append(record)
        profiles[label] = frozen
        static.extend({"profile": label, "mode": mode, **r}
                      for mode, p in (("before", profile), ("after", frozen))
                      for r in static_test(args.executable, p, calibrations))
        primary = []
        for period in PERIODS_NS:
            key = f"{label}_{period}ns_200ps_trap"
            print("Transient "+key, flush=True)
            result, rows, arrays, text = transient(
                args.executable, frozen, calibrations, period, 200, "trap")
            cases.append({"case": key, "profile": label, "scope": "primary", **result})
            decisions.extend({"case": key, **r} for r in rows)
            save_run(args.output, key, arrays, text)
            primary.append(result)
        passing = [r["period_ns"] for r in primary if r["passed"] == 130]
        minimum = min(passing) if passing else None
        for dt, method in ((100, "trap"), (50, "trap"), (100, "gear")):
            period = minimum or 80
            key = f"{label}_{period}ns_{dt}ps_{method}"
            result, rows, arrays, text = transient(
                args.executable, frozen, calibrations, period, dt, method)
            cases.append({"case": key, "profile": label, "scope": "crosscheck", **result})
            decisions.extend({"case": key, **r} for r in rows)
            save_run(args.output, key, arrays, text)
        print("Residual envelopes "+label, flush=True)
        levels = []
        for delta in RESIDUALS:
            rows = envelope_scan(args.executable, frozen, calibrations, label, delta)
            envelopes.extend(rows)
            levels.append({"residual": delta, "all_correct": all(r["correct"] for r in rows),
                           "minimum_output_margin_V": min(min(r["carry_margin_V"], r["sum_margin_V"])
                                                         for r in rows)})
        print("Frozen cancellation drift "+label, flush=True)
        for drift in DRIFTS:
            drifted = copy.deepcopy(frozen)
            drifted["cancel_gain_scale"] = drifted.get("cancel_gain_scale", 1)*(1+drift)
            drift_rows.extend({"profile": label, "drift": float(drift), **r}
                              for r in static_test(args.executable, drifted, calibrations))
        # Reliability acceptance is set before observing residual results.
        for csign, ssign in itertools.product((-1, 1), repeat=2):
            corner = endpoint_profile(frozen, .01, csign, ssign)
            for dt, method in ((100, "trap"), (50, "trap"), (100, "gear")):
                key = f"{label}_residual1pct_c{csign}_s{ssign}_{dt}ps_{method}"
                result, rows, arrays, text = transient(
                    args.executable, corner, calibrations, 80, dt, method)
                cases.append({"case": key, "profile": label, "scope": "residual_corner", **result})
                decisions.extend({"case": key, **r} for r in rows)
                save_run(args.output, key, arrays, text)
        summary_profiles.append({"profile": label, "minimum_grid_period_ns": minimum,
                                 "residual_envelopes": levels})
        write_csv(args.output / "calibration_static.csv", static)
        write_csv(args.output / "residual_envelopes.csv", envelopes)
        write_csv(args.output / "cancellation_drift.csv", drift_rows)
        write_csv(args.output / "calibration_decisions.csv", decisions)
        (args.output / "training.json").write_text(json.dumps(training, indent=2), encoding="utf-8")
    acceptance = (
        all(r["correct"] for r in static if r["mode"] == "after") and
        all(c["passed"] == 130 for c in cases if c["scope"] != "primary") and
        all(p["residual_envelopes"][1]["all_correct"] for p in summary_profiles))
    summary = {
        "executable_sha256": hashlib.sha256(args.executable.read_bytes()).hexdigest(),
        "calibrations": calibrations, "frozen_profiles": profiles, "training": training,
        "profiles": summary_profiles, "cases": cases, "order": transition_order(),
        "residual_grid": RESIDUALS, "drift_grid": DRIFTS,
        "acceptance": acceptance,
        "acceptance_definition": "All 8 profiles: DC envelope at independent 1% residual; "
        "all transitions at four envelope corners, 80 ns, three solver settings; nominal crosschecks",
        "residual_definition": "Independent relative error on all 6 effective positive path "
        "coefficients and absolute cancellation V/V coefficient; references/supply/MOS fixed per profile",
        "envelope_assumptions": "DC feed-forward monotone carry and sum transfer; positive paths, "
        "negative cancellation and nonnegative carry. Not a proof of all transient histories.",
        "limits": ["Baseband proxy only, no new FDTD",
                   "No stochastic noise or drift in threshold/supply/offset during frozen operation",
                   "Residual corners do not exhaust all dynamic coefficient corners or histories",
                   "Local Vdd only; ideal trim/sensing/switching/inputs excluded",
                   "80 ns training dwell assumed; host calculation time is not hardware latency"],
    }
    (args.output / "gain_calibration_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({"acceptance": acceptance, "profiles": summary_profiles}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "第九轮结果")
    main(parser.parse_args())
