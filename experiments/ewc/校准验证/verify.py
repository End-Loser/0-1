"""Independent evidence checker. Does not import model or runner."""

import argparse
from decimal import Decimal, localcontext
import hashlib
import json
import math
from pathlib import Path

D = lambda value: Decimal(str(value))
T_KEYS = ("pause_ack_s", "switch_cal_s", "probe_settle_s", "mux_settle_s",
          "adc_transfer_s", "solve_s", "dac_transfer_s", "gain_settle_s",
          "switch_run_s", "restore_s")
Q_KEYS = ("adc_span_V", "input_min_V", "input_max_V", "input_guard_V",
          "noise_sigma_V", "noise_rho", "noise_bound_V", "adc_coeff_sensitivity_per_V",
          "dac_span_V", "fine_slope_dB_per_V", "fine_center_V", "fine_budget_relative",
          "adc_offset_bound_V", "adc_inl_bound_V", "adc_reference_bound_V",
          "dac_static_bound_V", "other_coeff_bound_relative")
EDGES = {
    ("RUN", "request"): "PAUSE", ("PAUSE", "ack"): "SWITCH_CAL",
    ("SWITCH_CAL", "switch"): "PROBE_SETTLE", ("PROBE_SETTLE", "settled"): "MEASURE",
    ("MEASURE", "solve"): "SOLVE", ("SOLVE", "program"): "DAC_SETTLE",
    ("DAC_SETTLE", "settled"): "SWITCH_RUN", ("SWITCH_RUN", "switch"): "RESTORE",
    ("RESTORE", "resume"): "RUN", ("FAULT", "reset"): "PAUSE",
    ("RUN", "data"): "RUN", ("MEASURE", "read"): "MEASURE",
}
STATES = ("RUN", "PAUSE", "SWITCH_CAL", "PROBE_SETTLE", "MEASURE",
          "SOLVE", "DAC_SETTLE", "SWITCH_RUN", "RESTORE", "FAULT")
EVENTS = ("data", "request", "ack", "switch", "settled", "read", "solve",
          "program", "resume", "fault", "reset")


class Check:
    def __init__(self):
        self.count = 0

    def equal(self, actual, expected, label):
        self.count += 1
        if isinstance(expected, Decimal):
            if (isinstance(actual, bool) or not isinstance(actual, (int, float))
                    or not math.isfinite(actual)
                    or abs(D(actual) - expected) > max(D("1e-14"), abs(expected) * D("2e-10"))):
                raise AssertionError(f"{label}: {actual!r} != {expected!r}")
        elif actual != expected:
            raise AssertionError(f"{label}: {actual!r} != {expected!r}")


def amplitude(db):
    return (D(10).ln() * db / D(20)).exp()


def verify_case(row, check):
    p, t, q = row["parameters"], row["timing"], row["quantization"]
    n = 8 * (p["averages"] + p["discard_per_point"])
    h = {key: D(0) if p[key] is None else D(p[key]) for key in T_KEYS}
    dt = [
        h["pause_ack_s"], h["switch_cal_s"],
        8 * (h["probe_settle_s"] + h["mux_settle_s"]), D(n) / D(p["sample_rate_Hz"]),
        n * h["adc_transfer_s"], h["solve_s"],
        p["updates"] * h["dac_transfer_s"], p["updates"] * D("0.000005"),
        p["updates"] * h["gain_settle_s"], h["switch_run_s"], h["restore_s"],
    ]
    total = sum(dt)
    for segment, expected in zip(t["segments"], dt):
        check.equal(segment["duration_s"], expected, row["id"] + "/segment")
    check.equal(len(t["segments"]), 11, "segment count")
    check.equal(t["conversions"], n, "conversion count")
    check.equal(t["retained_readings"], 8 * p["averages"], "retained count")
    check.equal(t["discarded_readings"], 8 * p["discard_per_point"], "discard count")
    check.equal(t["adc_throughput_only_s"], D(n) / D(p["sample_rate_Hz"]), "ADC floor")
    check.equal(t["pause_s"], total, "pause")
    t_missing = [k for k in T_KEYS if p[k] is None]
    check.equal(t["missing"], t_missing, "timing missing")
    check.equal(t["kind"], "lower_bound_with_missing_overheads" if t_missing
                else "assumed_serial_schedule", "timing kind")
    active_fraction = total / (D(p["active_interval_s"]) + total)
    check.equal(t["pause_fraction_active_plus_pause"], active_fraction, "active pause")
    feasible = total < D(p["start_interval_s"])
    check.equal(t["start_to_start_feasible"], feasible, "period feasible")
    check.equal(t["pause_fraction_start_to_start"],
                total / D(p["start_interval_s"]) if feasible else None, "start pause")
    budget = D(p["pause_budget_relative"])
    check.equal(t["active_interval_for_pause_budget_s"], total * (1 - budget) / budget, "minimum active")
    check.equal(t["active_pause_budget_met"], active_fraction <= budget, "pause screen")
    missing = [k for k in Q_KEYS if p[k] is None]
    check.equal(q["missing"], missing, "quantization missing")
    check.equal(q["coarse_positive_relative"], amplitude(D("0.25")) - 1, "positive half step")
    check.equal(q["coarse_negative_relative"], 1 - amplitude(D("-0.25")), "negative half step")
    check.equal(q["coarse_only_within_1pct"], False, "coarse insufficient")
    check.equal(q["fine_step_limit_for_1pct_dB"], 40 * D("1.01").log10(), "1pct fine limit")
    check.equal(q["real_effective_accuracy_status"], "BLOCKED_NO_HARDWARE_MODEL", "hardware blocked")
    adc_ready = all(p[k] is not None for k in Q_KEYS[:8])
    fine_ready = all(p[k] is not None for k in Q_KEYS[8:12])
    failed = []
    if active_fraction > budget:
        failed.append("pause_1pct")
    if not feasible:
        failed.append("start_period")
    adc_ok = fine_coverage = False
    if adc_ready:
        a = q["adc"]
        lsb = D(p["adc_span_V"]) / 65536
        sensitivity = D(p["adc_coeff_sensitivity_per_V"])
        m, rho = D(p["averages"]), D(p["noise_rho"])
        sigma = D(p["noise_sigma_V"]) * (rho + (1 - rho) / m).sqrt()
        adc_ok = (D(p["input_min_V"]) - D(p["input_guard_V"]) >= 0
                  and D(p["input_max_V"]) + D(p["input_guard_V"]) <= D(p["adc_span_V"]) - lsb)
        for key, expected in (
            ("ideal_lsb_V", lsb), ("range_with_guard_ok", adc_ok),
            ("quantization_coeff_bound_relative", lsb * sensitivity / 2),
            ("mean_noise_sigma_V", sigma), ("mean_noise_coeff_sigma_relative", sigma * sensitivity),
            ("bounded_noise_coeff_relative", D(p["noise_bound_V"]) * sensitivity),
            ("quantization_bound_reduced_by_average", False),
            ("noise_sigma_is_not_a_deterministic_bound", True),
        ):
            check.equal(a[key], expected, "ADC/" + key)
        if not adc_ok:
            failed.append("adc_range")
    else:
        check.equal(q["adc"], None, "unknown ADC")
    if fine_ready:
        f = q["fine"]
        span, slope = D(p["dac_span_V"]), D(p["fine_slope_dB_per_V"])
        step = span * slope / 65536
        error = amplitude(step / 2) - 1
        epsilon = D(p["fine_budget_relative"])
        limit = 40 * (1 + epsilon).log10()
        # Independent integer search instead of the runner's log2/ceil formula.
        bits = 1
        while span * slope / (2**bits) > limit:
            bits += 1
        center, high = D(p["fine_center_V"]), span - span / 65536
        fine_coverage = min(center, high - center) * slope >= D("0.25")
        rounding = error <= epsilon
        for key, expected in (
            ("ideal_lsb_V", span / 65536), ("max_code_V", high), ("ideal_step_dB", step),
            ("rounding_bound_relative", error), ("step_limit_dB", limit),
            ("minimum_bits_rounding_only", bits), ("necessary_rounding_condition_ok", rounding),
            ("coarse_residual_coverage_ok", fine_coverage),
            ("conditional_quantization_screen_ok", rounding and fine_coverage),
        ):
            check.equal(f[key], expected, "fine/" + key)
        if not rounding:
            failed.append("fine_rounding")
        if not fine_coverage:
            failed.append("fine_coverage")
    else:
        check.equal(q["fine"], None, "unknown fine")
    combined = None
    if not missing and adc_ok and fine_coverage:
        adc = sum(D(p[k]) for k in ("noise_bound_V", "adc_offset_bound_V",
                  "adc_inl_bound_V", "adc_reference_bound_V"))
        adc = (adc + D(p["adc_span_V"]) / 131072) * D(p["adc_coeff_sensitivity_per_V"])
        dac = amplitude(D(p["fine_slope_dB_per_V"])
                        * (D(p["dac_span_V"]) / 131072 + D(p["dac_static_bound_V"])))
        combined = (1 + adc) * dac * (1 + D(p["other_coeff_bound_relative"])) - 1
    check.equal(q["conditional_combined_bound_relative"], combined, "conditional total")
    check.equal(row["failed_screens"], failed, "failed screens")
    check.equal(row["expectations_match"],
                sorted(failed) == sorted(row["expected_failed_screens"]), "expectations")
    check.equal(row["expectations_match"], True, "all expectations matched")


def oracle(initial, event, now, wait):
    s = dict(initial)
    state = s["state"]
    dest = "FAULT" if event == "fault" else EDGES.get((state, event))
    rejected = (
        dest is None or now < s["now"]
        or (event in ("settled", "resume") and now < s["deadline"])
        or (event == "read" and s["readings"] >= 8 * s["averages"])
        or (event == "solve" and s["readings"] != 8 * s["averages"])
        or (event == "program" and wait < 0.000005)
    )
    if rejected:
        return False, s
    s["state"], s["now"] = dest, now
    if event == "read":
        s["readings"] += 1
    if event == "program":
        s["version"] += 1
    if event in ("request", "reset"):
        s["readings"] = 0
    if event in ("switch", "program"):
        s["deadline"] = now + wait
    return True, s


def check_trace(row, initial, check):
    accepted, final = oracle(initial, row["event"], row["time_s"], row["wait_s"])
    check.equal(accepted, True, "trace accepted")
    for key, value in (("before", initial["state"]), ("after", final["state"]),
                       ("version", final["version"]), ("readings", final["readings"]),
                       ("deadline_s", D(final["deadline"]))):
        check.equal(row[key], value, "trace/" + key)
    return final


def verify(directory, code_dir=None):
    directory = Path(directory)
    check = Check()
    load = lambda name: json.loads((directory / name).read_text(encoding="utf-8"))
    summary, rows, control, cases = (load(name) for name in
                                    ("summary.json", "budgets.json", "control.json", "cases.json"))
    for name, digest in summary["artifact_sha256"].items():
        check.equal(hashlib.sha256((directory / name).read_bytes()).hexdigest(), digest, "artifact hash")
    if code_dir is not None:
        for name, digest in summary["code_sha256"].items():
            check.equal(hashlib.sha256((Path(code_dir) / name).read_bytes()).hexdigest(), digest, "code hash")
    provenance = load("provenance.json")
    check.equal(provenance["adc"]["maximum_sample_rate_Hz"], 100000, "archived ADC")
    check.equal(provenance["dac"]["candidate_settling_s"], D("0.000005"), "archived DAC")
    check.equal(provenance["adc"]["bits"], 16, "ADC bits")
    check.equal(provenance["dac"]["bits"], 16, "DAC bits")
    check.equal(provenance["coarse"]["typical_step_dB"], D("0.5"), "coarse step")
    check.equal(len(rows), len(cases), "cases preserved")
    with localcontext() as ctx:
        ctx.prec = 45
        for case, row in zip(cases, rows):
            for key in ("id", "parameters", "assumption_class", "expected_failed_screens"):
                check.equal(row[key], case[key], "case input/" + key)
            verify_case(row, check)
    matrix = control["transition_matrix"]
    check.equal([(r["state"], r["event"]) for r in matrix],
                [(s, e) for s in STATES for e in EVENTS], "complete transition matrix")
    for row in matrix:
        check.equal(row["initial"]["state"], row["state"], "matrix initial state")
        accepted, final = oracle(row["initial"], row["event"], 1.0, 5e-6)
        check.equal(row["accepted"], accepted, "matrix decision")
        if accepted:
            check_trace(row["trace"], row["initial"], check)
        else:
            check.equal(row["final"], row["initial"], "rejected event leaves state unchanged")
    for row in control["negative_attempts"]:
        accepted, final = oracle(row["initial"], row["event"], row["time_s"], row["wait_s"])
        check.equal(row["rejected"], not accepted, "negative rejection")
        check.equal(accepted, False, "negative must reject")
        check.equal(row["final"], final, "negative leaves state unchanged")
    for name, m in (("nominal_m1", 1), ("nominal_m4", 4)):
        state = dict(averages=m, state="RUN", version=1, readings=0, deadline=0.0, now=0.0)
        reads = []
        for row in control[name]:
            state = check_trace(row, state, check)
            if row["event"] == "read":
                reads.append(row["time_s"])
            if row["event"] == "data":
                check.equal(row["version"], 1 if not reads else 2, "running parameters frozen")
        check.equal(len(reads), 8 * m, "trace readings")
        for a, b in zip(reads, reads[1:]):
            check.equal(b - a >= 1e-5 - 1e-15, True, "nominal read spacing")
        check.equal(state["state"], "RUN", "nominal ends in run")
    for key, expected in (
        ("budget_cases", len(rows)), ("retained_failed_screen_cases", sum(bool(r["failed_screens"]) for r in rows)),
        ("matrix_attempts", len(matrix)), ("matrix_accepted", sum(r["accepted"] for r in matrix)),
        ("matrix_rejected", sum(not r["accepted"] for r in matrix)),
        ("negative_control_attempts", len(control["negative_attempts"])),
        ("expectations_match", True), ("negative_controls_rejected", True),
        ("real_adc_dac_model_passed", False), ("system_1pct_passed", False), ("procurement", False),
    ):
        check.equal(summary[key], expected, "summary/" + key)
    return {"passed": True, "checks": check.count,
            "method": "45-digit Decimal budget recalculation and separate protocol oracle",
            "imports_model_or_runner": False, "hardware_validation": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--code-dir", type=Path)
    args = parser.parse_args()
    try:
        report = verify(args.results, args.code_dir)
        print(json.dumps(report))
    except (AssertionError, KeyError, ValueError, OSError, TypeError) as exc:
        print(json.dumps({"passed": False, "error": str(exc)}))
        raise SystemExit(1)
