"""Analytical calibration budget and a bounded control protocol, not hardware."""

import math
from dataclasses import dataclass

ADC_HZ = 100_000
ADC_BITS = DAC_BITS = 16
DAC_SETTLE_S = 5e-6
COARSE_DB = 0.5
POINTS = 8
TIMING_FIELDS = (
    "pause_ack_s", "switch_cal_s", "probe_settle_s", "mux_settle_s",
    "adc_transfer_s", "solve_s", "dac_transfer_s", "gain_settle_s",
    "switch_run_s", "restore_s",
)
QUANT_FIELDS = (
    "adc_span_V", "input_min_V", "input_max_V", "input_guard_V",
    "noise_sigma_V", "noise_rho", "noise_bound_V", "adc_coeff_sensitivity_per_V",
    "dac_span_V", "fine_slope_dB_per_V", "fine_center_V", "fine_budget_relative",
    "adc_offset_bound_V", "adc_inl_bound_V", "adc_reference_bound_V",
    "dac_static_bound_V", "other_coeff_bound_relative",
)


def finite(value, name, positive=False):
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError(f"{name}: finite number required")
    if not math.isfinite(value) or (value <= 0 if positive else value < 0):
        raise ValueError(f"{name}: invalid range")
    return value


def count(value, name, minimum=1):
    if type(value) is not int or value < minimum or value > 100_000:
        raise ValueError(f"{name}: integer in [{minimum}, 100000] required")
    return value


def timing(p):
    m = count(p["averages"], "averages")
    discarded = count(p["discard_per_point"], "discard_per_point", 0)
    updates = count(p["updates"], "updates")
    hz = finite(p["sample_rate_Hz"], "sample_rate_Hz", True)
    if hz > ADC_HZ:
        raise ValueError("sample_rate_Hz exceeds archived candidate maximum")
    missing = [key for key in TIMING_FIELDS if p[key] is None]
    # Unknown overhead contributes zero only to a labelled lower bound.
    t = {key: 0.0 if p[key] is None else finite(p[key], key)
         for key in TIMING_FIELDS}
    conversions = POINTS * (m + discarded)
    segments = [
        ("pause_ack", t["pause_ack_s"]),
        ("switch_cal", t["switch_cal_s"]),
        ("probe_and_mux", POINTS * (t["probe_settle_s"] + t["mux_settle_s"])),
        ("adc_conversion_slots", conversions / hz),
        ("adc_serial_nonoverlap", conversions * t["adc_transfer_s"]),
        ("solve", t["solve_s"]),
        ("dac_serial_nonoverlap", updates * t["dac_transfer_s"]),
        ("dac_candidate_settle", updates * DAC_SETTLE_S),
        ("gain_settle", updates * t["gain_settle_s"]),
        ("switch_run", t["switch_run_s"]),
        ("restore", t["restore_s"]),
    ]
    pause = math.fsum(duration for _, duration in segments)
    active = finite(p["active_interval_s"], "active_interval_s", True)
    start_interval = finite(p["start_interval_s"], "start_interval_s", True)
    target = finite(p["pause_budget_relative"], "pause_budget_relative", True)
    if target >= 1:
        raise ValueError("pause budget must be below one")
    feasible = pause < start_interval
    return {
        "conversions": conversions, "retained_readings": POINTS * m,
        "discarded_readings": POINTS * discarded,
        "adc_throughput_only_s": conversions / hz,
        "pause_s": pause, "missing": missing,
        "kind": "lower_bound_with_missing_overheads" if missing else "assumed_serial_schedule",
        "segments": [{"name": name, "duration_s": duration} for name, duration in segments],
        "pause_fraction_active_plus_pause": pause / (active + pause),
        "pause_fraction_start_to_start": pause / start_interval if feasible else None,
        "start_to_start_feasible": feasible,
        "active_interval_for_pause_budget_s": pause * (1 - target) / target,
        "active_pause_budget_met": pause / (active + pause) <= target,
        "conditions": ["one ADC, eight distinct probe points",
                       "per-point repeats, discarded samples included",
                       "serialized transfer, no SPI overlap credited",
                       "DAC 5us is a candidate value, not a guaranteed worst case",
                       "startup and fault recovery outside periodic budget"],
    }


def quantization(p):
    m = count(p["averages"], "averages")
    missing = [key for key in QUANT_FIELDS if p[key] is None]
    coarse_positive = math.expm1(math.log(10) * COARSE_DB / 40)
    coarse_negative = -math.expm1(-math.log(10) * COARSE_DB / 40)
    result = {
        "missing": missing, "coarse_positive_relative": coarse_positive,
        "coarse_negative_relative": coarse_negative,
        "coarse_only_within_1pct": coarse_positive <= 0.01,
        "fine_step_limit_for_1pct_dB": 40 * math.log10(1.01),
        "real_effective_accuracy_status": "BLOCKED_NO_HARDWARE_MODEL",
        "adc": None, "fine": None, "conditional_combined_bound_relative": None,
    }
    for key in QUANT_FIELDS:
        if p[key] is not None:
            finite(p[key], key)
    if p["noise_rho"] is not None and p["noise_rho"] > 1:
        raise ValueError("noise_rho must be in [0,1]")
    if p["fine_budget_relative"] is not None and not 0 < p["fine_budget_relative"] < 1:
        raise ValueError("fine budget must be in (0,1)")
    adc_needed = QUANT_FIELDS[:8]
    if all(p[key] is not None for key in adc_needed):
        span = finite(p["adc_span_V"], "adc_span_V", True)
        lo, hi = p["input_min_V"], p["input_max_V"]
        if lo > hi:
            raise ValueError("input_min_V > input_max_V")
        lsb = span / 2**ADC_BITS
        rho = p["noise_rho"]
        sigma = p["noise_sigma_V"] * math.sqrt((1 + (m - 1) * rho) / m)
        gain = p["adc_coeff_sensitivity_per_V"]
        result["adc"] = {
            "ideal_lsb_V": lsb,
            "range_with_guard_ok": lo - p["input_guard_V"] >= 0
            and hi + p["input_guard_V"] <= span - lsb,
            "quantization_coeff_bound_relative": lsb / 2 * gain,
            "mean_noise_sigma_V": sigma,
            "mean_noise_coeff_sigma_relative": sigma * gain,
            "bounded_noise_coeff_relative": p["noise_bound_V"] * gain,
            "quantization_bound_reduced_by_average": False,
            "noise_sigma_is_not_a_deterministic_bound": True,
        }
    fine_needed = QUANT_FIELDS[8:12]
    if all(p[key] is not None for key in fine_needed):
        span = finite(p["dac_span_V"], "dac_span_V", True)
        slope = finite(p["fine_slope_dB_per_V"], "fine_slope_dB_per_V", True)
        center, epsilon = p["fine_center_V"], p["fine_budget_relative"]
        lsb = span / 2**DAC_BITS
        high = span - lsb
        if center > high:
            raise ValueError("fine center outside representable DAC range")
        half_db = slope * lsb / 2
        error = math.expm1(math.log(10) * half_db / 20)
        required = max(1, math.ceil(math.log2(span * slope / (40 * math.log10(1 + epsilon)))))
        coverage = min(center, high - center) * slope >= COARSE_DB / 2
        result["fine"] = {
            "ideal_lsb_V": lsb, "max_code_V": high,
            "ideal_step_dB": 2 * half_db,
            "rounding_bound_relative": error,
            "step_limit_dB": 40 * math.log10(1 + epsilon),
            "minimum_bits_rounding_only": required,
            "necessary_rounding_condition_ok": error <= epsilon,
            "coarse_residual_coverage_ok": coverage,
            "conditional_quantization_screen_ok": error <= epsilon and coverage,
            "assumption": "affine log-gain slope over full control range, symmetric correction about center",
        }
    adc, fine = result["adc"], result["fine"]
    if not missing and adc and fine and adc["range_with_guard_ok"] and fine["coarse_residual_coverage_ok"]:
        # Triangle bound; random sigma is not silently substituted for bounded noise.
        adc_bound = (p["noise_bound_V"] + p["adc_span_V"] / 2**17
                     + p["adc_offset_bound_V"] + p["adc_inl_bound_V"]
                     + p["adc_reference_bound_V"]) * p["adc_coeff_sensitivity_per_V"]
        dac_bound = math.expm1(math.log(10) / 20 * p["fine_slope_dB_per_V"]
                             * (p["dac_span_V"] / 2**17 + p["dac_static_bound_V"]))
        result["conditional_combined_bound_relative"] = (
            (1 + adc_bound) * (1 + dac_bound)
            * (1 + p["other_coeff_bound_relative"]) - 1
        )
    return result


@dataclass
class Controller:
    """One-cycle event protocol. Time is an explicit scalar, not a hardware clock."""

    averages: int = 1
    state: str = "RUN"
    version: int = 1
    readings: int = 0
    deadline: float = 0.0
    now: float = 0.0

    def step(self, event, now, wait_s=0.0):
        now = finite(now, "now")
        wait_s = finite(wait_s, "wait_s")
        if now < self.now:
            raise ValueError("time reversal")
        old = (self.state, self.version, self.readings, self.deadline, self.now)
        next_state = {
            ("RUN", "request"): "PAUSE",
            ("PAUSE", "ack"): "SWITCH_CAL",
            ("SWITCH_CAL", "switch"): "PROBE_SETTLE",
            ("PROBE_SETTLE", "settled"): "MEASURE",
            ("MEASURE", "solve"): "SOLVE",
            ("SOLVE", "program"): "DAC_SETTLE",
            ("DAC_SETTLE", "settled"): "SWITCH_RUN",
            ("SWITCH_RUN", "switch"): "RESTORE",
            ("RESTORE", "resume"): "RUN",
            ("FAULT", "reset"): "PAUSE",
        }.get((self.state, event))
        if event == "fault":
            next_state = "FAULT"
        elif event == "read" and self.state == "MEASURE":
            if self.readings >= POINTS * self.averages:
                raise ValueError("too many readings")
            next_state = "MEASURE"
        elif event == "data" and self.state == "RUN":
            next_state = "RUN"
        if next_state is None:
            raise ValueError(f"illegal event {event} in {self.state}")
        if event in ("settled", "resume") and now < self.deadline:
            raise ValueError("premature completion")
        if event == "solve" and self.readings != POINTS * self.averages:
            raise ValueError("incomplete readings")
        if event == "program" and wait_s < DAC_SETTLE_S:
            raise ValueError("DAC candidate wait not scheduled")
        if event == "read":
            self.readings += 1
        if event == "program":
            self.version += 1
        if event in ("request", "reset"):
            self.readings = 0
        if event in ("switch", "program"):
            self.deadline = now + wait_s
        self.state, self.now = next_state, now
        return {"event": event, "time_s": now, "wait_s": wait_s,
                "before": old[0], "after": self.state, "version": self.version,
                "readings": self.readings, "deadline_s": self.deadline}


def nominal_trace(m=1):
    ctl = Controller(averages=m)
    trace = []
    def emit(event, time, wait=0):
        trace.append(ctl.step(event, time, wait))
    emit("data", 0)
    emit("request", 0)
    emit("ack", 1e-6)
    emit("switch", 2e-6, 3e-6)
    emit("settled", 5e-6)
    for k in range(POINTS * m):
        emit("read", (15 + 10 * k) * 1e-6)
    now = trace[-1]["time_s"]
    emit("solve", now)
    emit("program", now + 2e-6, DAC_SETTLE_S)
    emit("settled", now + 7e-6)
    emit("switch", now + 8e-6, 2e-6)
    emit("resume", now + 10e-6)
    for _ in range(8):
        emit("data", now + 11e-6)
    return trace
