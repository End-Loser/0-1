"""Generate transparent assumptions, numerical budgets and protocol evidence."""

import argparse
import copy
import hashlib
import json
from pathlib import Path

from model import (Controller, TIMING_FIELDS, QUANT_FIELDS, nominal_trace,
                   quantization, timing)

HERE = Path(__file__).resolve().parent
STATES = ("RUN", "PAUSE", "SWITCH_CAL", "PROBE_SETTLE", "MEASURE",
          "SOLVE", "DAC_SETTLE", "SWITCH_RUN", "RESTORE", "FAULT")
EVENTS = ("data", "request", "ack", "switch", "settled", "read", "solve",
          "program", "resume", "fault", "reset")


def scenarios():
    base = {key: None for key in TIMING_FIELDS + QUANT_FIELDS}
    base.update(averages=1, discard_per_point=0, updates=1, sample_rate_Hz=100000,
                active_interval_s=0.01, start_interval_s=0.01, pause_budget_relative=0.01)
    assumed = dict(base)
    assumed.update(dict(zip(TIMING_FIELDS, (
        2e-6, 3e-6, 4e-6, 1e-6, 1.6e-6, 6e-6, 2.4e-6, 3e-6, 3e-6, 4e-6))))
    assumed.update(adc_span_V=2.5, input_min_V=0.5, input_max_V=2.0,
                   input_guard_V=0.01, noise_sigma_V=0.001, noise_rho=0.0,
                   noise_bound_V=0.003, adc_coeff_sensitivity_per_V=2.0,
                   dac_span_V=2.5, fine_slope_dB_per_V=1.0, fine_center_V=1.25,
                   fine_budget_relative=0.002)
    rows = []
    def add(name, source, changes, failed_screens=()):
        p = dict(source, **changes)
        rows.append({"id": name, "parameters": p,
                     "assumption_class": "synthetic_design_example" if source is assumed
                     else "archived_values_with_unknown_interfaces",
                     "expected_failed_screens": list(failed_screens)})
    add("unknown_lower_bound", base, {})
    add("assumed_m1", assumed, {}, ("pause_1pct",))
    add("assumed_m4", assumed, {"averages": 4}, ("pause_1pct",))
    add("assumed_m16", assumed, {"averages": 16}, ("pause_1pct",))
    add("short_recalibration_period", assumed,
        {"averages": 16, "start_interval_s": 0.001}, ("pause_1pct", "start_period"))
    add("discard_two_each_point", assumed, {"discard_per_point": 2}, ("pause_1pct",))
    add("fine_too_steep", assumed, {"fine_slope_dB_per_V": 20000},
        ("pause_1pct", "fine_rounding"))
    add("fine_insufficient_range", assumed, {"fine_slope_dB_per_V": 0.1},
        ("pause_1pct", "fine_coverage"))
    add("adc_overrange", assumed, {"input_max_V": 2.6}, ("pause_1pct", "adc_range"))
    add("correlated_m16", assumed, {"averages": 16, "noise_rho": 1.0}, ("pause_1pct",))
    add("synthetic_bounded_budget", assumed,
        {"adc_offset_bound_V": 10e-6, "adc_inl_bound_V": 20e-6,
         "adc_reference_bound_V": 10e-6, "dac_static_bound_V": 30e-6,
         "other_coeff_bound_relative": 0.002, "active_interval_s": 0.02})
    add("small_signal_high_sensitivity", assumed,
        {"input_min_V": 0.0001, "input_guard_V": 0.0,
         "adc_coeff_sensitivity_per_V": 10000}, ("pause_1pct",))
    return rows


def failed_screens(t, q):
    failures = []
    if not t["active_pause_budget_met"]:
        failures.append("pause_1pct")
    if not t["start_to_start_feasible"]:
        failures.append("start_period")
    if q["adc"] and not q["adc"]["range_with_guard_ok"]:
        failures.append("adc_range")
    if q["fine"]:
        if not q["fine"]["necessary_rounding_condition_ok"]:
            failures.append("fine_rounding")
        if not q["fine"]["coarse_residual_coverage_ok"]:
            failures.append("fine_coverage")
    return failures


def protocol_evidence():
    matrix = []
    for state in STATES:
        for event in EVENTS:
            ctl = Controller(state=state, readings=8 if event == "solve" else 0)
            before = vars(ctl).copy()
            try:
                trace = ctl.step(event, 1.0, 5e-6)
                matrix.append({"state": state, "event": event, "accepted": True,
                               "initial": before, "trace": trace})
            except ValueError as exc:
                matrix.append({"state": state, "event": event, "accepted": False,
                               "initial": before, "final": vars(ctl).copy(), "error": str(exc)})
    negatives = []
    attempts = [
        ("program_during_run", Controller(), "program", 0, 5e-6),
        ("data_during_pause", Controller(state="PAUSE"), "data", 0, 0),
        ("solve_after_seven", Controller(state="MEASURE", readings=7), "solve", 1, 0),
        ("ninth_read", Controller(state="MEASURE", readings=8), "read", 1, 0),
        ("early_dac_completion", Controller(state="DAC_SETTLE", deadline=5e-6), "settled", 4e-6, 0),
        ("short_dac_wait", Controller(state="SOLVE"), "program", 0, 4e-6),
        ("early_restore", Controller(state="RESTORE", deadline=5e-6), "resume", 4e-6, 0),
        ("resume_from_fault", Controller(state="FAULT"), "resume", 1, 0),
        ("time_reversal", Controller(now=2), "request", 1, 0),
    ]
    for name, ctl, event, now, wait in attempts:
        before = vars(ctl).copy()
        try:
            ctl.step(event, now, wait)
            rejected, error = False, None
        except ValueError as exc:
            rejected, error = True, str(exc)
        negatives.append({"id": name, "initial": before, "event": event,
                          "time_s": now, "wait_s": wait, "rejected": rejected,
                          "final": vars(ctl).copy(), "error": error})
    return {"scope": "10-state scalar-time protocol, not RTL, CDC or analog simulation",
            "nominal_m1": nominal_trace(), "nominal_m4": nominal_trace(4),
            "transition_matrix": matrix, "negative_attempts": negatives}


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                    encoding="utf-8")


def run(out, supplied=None):
    out.mkdir(parents=True, exist_ok=True)
    cases = scenarios() if supplied is None else supplied
    results = []
    for case in cases:
        p = case["parameters"]
        t, q = timing(p), quantization(p)
        failed = failed_screens(t, q)
        results.append(dict(case, timing=t, quantization=q, failed_screens=failed,
                            expectations_match=sorted(failed) == sorted(case["expected_failed_screens"])))
    protocol = protocol_evidence()
    write_json(out / "cases.json", cases)
    write_json(out / "budgets.json", results)
    write_json(out / "control.json", protocol)
    provenance = json.loads((HERE / "provenance.json").read_text(encoding="utf-8"))
    write_json(out / "provenance.json", provenance)
    summary = {
        "schema": 1, "scope": "analytical timing, ideal quantization and bounded control tests only",
        "budget_cases": len(results), "retained_failed_screen_cases": sum(bool(x["failed_screens"]) for x in results),
        "matrix_attempts": len(protocol["transition_matrix"]),
        "matrix_accepted": sum(x["accepted"] for x in protocol["transition_matrix"]),
        "matrix_rejected": sum(not x["accepted"] for x in protocol["transition_matrix"]),
        "negative_control_attempts": len(protocol["negative_attempts"]),
        "expectations_match": all(x["expectations_match"] for x in results),
        "negative_controls_rejected": all(x["rejected"] for x in protocol["negative_attempts"]),
        "real_adc_dac_model_passed": False, "system_1pct_passed": False,
        "procurement": False, "model_status": "LIMITED_ANALYTICAL_AND_DISCRETE_ONLY",
        "artifact_sha256": {name: hashlib.sha256((out / name).read_bytes()).hexdigest()
                            for name in ("cases.json", "budgets.json", "control.json", "provenance.json")},
        "code_sha256": {name: hashlib.sha256((HERE / name).read_bytes()).hexdigest()
                        for name in ("model.py", "runner.py", "verify.py", "tests.py", "run_all.py", "provenance.json")},
    }
    write_json(out / "summary.json", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cases", type=Path, help="explicit case array; default is the built-in suite")
    args = parser.parse_args()
    supplied = json.loads(args.cases.read_text(encoding="utf-8")) if args.cases else None
    result = run(args.out, supplied)
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result["expectations_match"] and result["negative_controls_rejected"] else 1)
