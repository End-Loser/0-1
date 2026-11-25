"""Independent contract oracle and Decimal budget. Imports no DUT or runner."""

import argparse
import copy
from decimal import Decimal, ROUND_CEILING
import hashlib
import json
from pathlib import Path


TIME_KEYS = (
    "pause_ack_ns", "switch_cal_ns", "probe_settle_ns", "mux_settle_ns",
    "adc_ready_ns", "adc_spi_ns", "solve_ns", "dac_spi_ns", "dac_settle_ns",
    "gain_settle_ns", "switch_run_ns", "restore_ns",
)
FIELDS = {
    "data": ["input"], "request": [], "ack": [], "cal_ready": [],
    "select": ["point"], "settled": [],
    "adc_request": ["point", "token"], "adc_done": ["point", "token"],
    "read_start": ["point", "token"], "read_done": ["point", "token", "value"],
    "solve": [], "program": ["update", "code"], "dac_done": ["update"],
    "dac_settled": ["update"], "run_ready": [], "resume": [], "fault": [], "reset": [],
}
NEXT = {
    "PAUSE": ("ack", "SWITCH_CAL"),
    "SWITCH_CAL": ("cal_ready", "SELECT"),
    "SETTLE": ("settled", "READY"),
    "ADC_BUSY": ("adc_done", "ADC_DONE"),
    "ADC_DONE": ("read_start", "READING"),
    "READING": ("read_done", None),
    "PROGRAM": ("program", "DAC_BUSY"),
    "DAC_BUSY": ("dac_done", "DAC_WAIT"),
    "DAC_WAIT": ("dac_settled", None),
    "SWITCH_RUN": ("run_ready", "RESTORE"),
    "RESTORE": ("resume", "RUN"),
    "SELECT": ("select", "SETTLE"),
    "READY": ("adc_request", "ADC_BUSY"),
}


def initial(p):
    return dict(phase="RUN", now=0, due=0, point=-1, sample=0, token=0,
                pending_token=None, kept=[[] for _ in range(8)], discarded=0,
                update=0, staged=[], parameters=[0] * p["updates"], version=1)


def oracle(p, before, e):
    """Pure decision: a refused event returns the original snapshot."""
    original = copy.deepcopy(before)
    if any(p.get(key) is None for key in TIME_KEYS):
        raise ValueError("oracle cannot execute unknown waits")
    if not isinstance(e, dict) or e.get("event") not in FIELDS:
        return False, original
    name = e["event"]
    if set(e) != {"event", "time_ns", *FIELDS[name]}:
        return False, original
    for key in ["time_ns"] + FIELDS[name]:
        high = 65535 if key in ("code", "value") else 10**15
        if type(e[key]) is not int or not 0 <= e[key] <= high:
            return False, original
    if name == "data" and e["input"] > 7:
        return False, original
    t, phase = e["time_ns"], before["phase"]
    if t < before["now"]:
        return False, original
    if name == "fault":
        destination = "FAULT"
    elif phase == "RUN":
        if name not in ("request", "data"):
            return False, original
        destination = "PAUSE" if name == "request" else "RUN"
    elif phase == "FAULT":
        if name != "reset":
            return False, original
        destination = "PAUSE"
    elif phase == "POINT_DONE":
        expected = "solve" if before["point"] == 7 else "select"
        if name != expected:
            return False, original
        destination = "PROGRAM" if name == "solve" else "SETTLE"
    else:
        expected, destination = NEXT[phase]
        if name != expected or t < before["due"]:
            return False, original
    if name == "select" and not (e["point"] == before["point"] + 1 and e["point"] < 8):
        return False, original
    if name in ("adc_request", "adc_done", "read_start", "read_done"):
        identity = before["token"] + 1 if name == "adc_request" else before["pending_token"]
        if e["point"] != before["point"] or e["token"] != identity:
            return False, original
    if name in ("program", "dac_done", "dac_settled"):
        if e["update"] != before["update"] or before["update"] >= p["updates"]:
            return False, original
    if name == "solve" and any(len(row) != p["m"] for row in before["kept"]):
        return False, original
    if name == "resume" and len(before["staged"]) != p["updates"]:
        return False, original
    s = copy.deepcopy(before)
    if name in ("request", "reset", "fault"):
        s.update(kept=[[] for _ in range(8)], discarded=0, staged=[], pending_token=None)
        if name != "fault":
            s.update(point=-1, sample=0, update=0)
    if name == "select":
        s.update(point=e["point"], sample=0)
    if name == "adc_request":
        s.update(token=e["token"], pending_token=e["token"])
    if name == "read_done":
        if before["sample"] >= p["d"]:
            s["kept"][before["point"]].append(e["value"])
        else:
            s["discarded"] += 1
        s["sample"] += 1
        s["pending_token"] = None
        destination = "POINT_DONE" if s["sample"] == p["m"] + p["d"] else "READY"
    if name == "program":
        s["staged"].append(e["code"])
    if name == "dac_settled":
        s["update"] += 1
        destination = "PROGRAM" if s["update"] < p["updates"] else "SWITCH_RUN"
    if name == "resume":
        s["parameters"] = list(s["staged"])
        s["version"] += 1
    slot = max((10**9 + p["sample_rate_hz"] - 1) // p["sample_rate_hz"], p["adc_ready_ns"])
    delay = {
        "request": p["pause_ack_ns"], "reset": p["pause_ack_ns"],
        "ack": p["switch_cal_ns"],
        "select": p["probe_settle_ns"] + p["mux_settle_ns"],
        "adc_request": slot, "read_start": p["adc_spi_ns"],
        "solve": p["solve_ns"], "program": p["dac_spi_ns"],
        "dac_done": p["dac_settle_ns"] + p["gain_settle_ns"],
        "dac_settled": p["switch_run_ns"] if destination == "SWITCH_RUN" else 0,
        "run_ready": p["restore_ns"],
    }.get(name, 0)
    # Data events are observational and leave the scheduled deadline alone.
    s.update(phase=destination, now=t, due=before["due"] if name == "data" else t + delay)
    return True, s


def independent_budget(p):
    d = lambda key: Decimal(0 if p[key] is None else p[key])
    slot = (Decimal(10**9) / Decimal(p["sample_rate_hz"])).to_integral_value(rounding=ROUND_CEILING)
    slot = max(slot, d("adc_ready_ns"))
    n = 8 * (p["m"] + p["d"])
    pieces = [
        d("pause_ack_ns"), d("switch_cal_ns"),
        8 * (d("probe_settle_ns") + d("mux_settle_ns")),
        n * slot, n * d("adc_spi_ns"), d("solve_ns"),
        p["updates"] * d("dac_spi_ns"), p["updates"] * d("dac_settle_ns"),
        p["updates"] * d("gain_settle_ns"), d("switch_run_ns"), d("restore_ns"),
    ]
    return int(sum(pieces)), [int(v) for v in pieces]


def verify(directory, code_dir=None):
    directory = Path(directory)
    rows = json.loads((directory / "evidence.json").read_text(encoding="utf-8"))
    checks = 0

    def equal(a, b):
        nonlocal checks
        checks += 1
        if a != b:
            raise AssertionError(f"evidence mismatch: {a!r} != {b!r}")

    equal([r["id"] for r in rows["cases"]], [
        "assumed_m1", "assumed_m4", "assumed_m16", "discard_two_each_point",
        "m4_d2_three_updates", "noninteger_rate_slot", "slow_hardware_input",
        "unknown_all_timings", "first_batch_unknown_lower_bound",
    ])
    for row in rows["cases"]:
        p, b = row["config"], row["budget"]
        for key in ("m", "d", "updates", "sample_rate_hz") + TIME_KEYS:
            value = p[key]
            if value is None and key in TIME_KEYS:
                continue
            equal(type(value) is int, True)
            low = 1 if key in ("m", "updates", "sample_rate_hz") else 0
            high = 100000 if key == "sample_rate_hz" else 10000 if key in ("m", "d", "updates") else 10**15
            equal(low <= value <= high, True)
        total, pieces = independent_budget(p)
        equal(b["pause_ns"], total)
        equal(list(b["segments_ns"].values()), pieces)
        equal(b["missing"], [k for k in TIME_KEYS if p[k] is None])
        equal(b["kind"], "lower_bound_only" if b["missing"] else "synthetic_serial_schedule")
        equal(b["conversions"], 8 * (p["m"] + p["d"]))
        equal(b["retained"], 8 * p["m"])
        equal(b["discarded"], 8 * p["d"])
        equal(b["active_for_one_percent_ns"], 99 * total)
        equal(b["minimum_rate_slot_ns"], (10**9 + p["sample_rate_hz"] - 1) // p["sample_rate_hz"])
        equal(b["effective_slot_ns"], pieces[3] // b["conversions"])
        if row["trace"] is None:
            equal(bool(b["missing"]), True)
            equal(row["execution_blocked"], True)
            continue
        equal(row["execution_blocked"], False)
        state = initial(p)
        for step in row["trace"]:
            accepted, state = oracle(p, state, step["event"])
            equal(accepted, True)
            equal(step["after"], state)
        equal(state["phase"], "RUN")
        equal(state["now"] - row["trace"][0]["event"]["time_ns"], total)
        equal(state["kept"], [[1000 * point + k for k in range(p["d"], p["d"] + p["m"])]
                              for point in range(8)])
        equal(state["discarded"], 8 * p["d"])
        equal(state["version"], 2)
        equal(state["parameters"], list(range(100, 100 + p["updates"])))
        for step in row["trace"]:
            if step["event"]["event"] not in ("resume", "data"):
                equal(step["after"]["parameters"], [0] * p["updates"])
                equal(step["after"]["version"], 1)
    p = rows["cases"][0]["config"]
    baseline_events = [step["event"] for step in rows["cases"][0]["trace"]]
    required_ids = set()
    waiting = {"ack", "cal_ready", "settled", "adc_done", "read_done", "program",
               "dac_done", "dac_settled", "run_ready", "resume"}
    for index, event in enumerate(baseline_events):
        name = event["event"]
        if name in waiting:
            required_ids.add(f"early_{index}_{name}")
        if name != "data":
            required_ids.add(f"duplicate_{index}_{name}")
        if name in ("adc_request", "adc_done", "read_start", "read_done"):
            required_ids.update((f"wrong_point_{index}", f"wrong_token_{index}"))
        if index and event["time_ns"]:
            required_ids.add(f"time_reverse_{index}")
    required_ids.update((
        "read_without_completion", "read_without_request", "switch_point_with_adc_outstanding",
        "solve_after_only_one_point", "cross_point_stale_read", "same_point_stale_request_token",
        "run_parameter_write", "paused_data", "reset_in_run", "value_out_of_range", "boolean_value",
        "negative_time", "wrong_schema", "unknown_event", "wrong_dac_update", "fault_direct_resume",
        "fault_stale_adc_done", "reset_stale_adc_done",
    ))
    equal({r["id"] for r in rows["counterexamples"]}, required_ids)
    equal(len(rows["counterexamples"]), len(required_ids))
    for row in rows["counterexamples"]:
        state = initial(p)
        for event in row["prefix"]:
            accepted, state = oracle(p, state, event)
            equal(accepted, True)
        equal(row["before"], state)
        accepted, after = oracle(p, state, row["event"])
        equal(accepted, False)
        equal(row["rejected"], True)
        equal(row["after"], after)
        equal(after, state)
    phases = {"RUN"} | {step["after"]["phase"] for step in rows["cases"][0]["trace"]}
    equal({r["phase"] for r in rows["fault_recovery"]}, phases)
    equal(len(rows["fault_recovery"]), len(phases))
    for row in rows["fault_recovery"]:
        state = initial(p)
        for step in row["trace"]:
            old_parameters, old_version = state["parameters"], state["version"]
            accepted, state = oracle(p, state, step["event"])
            equal(accepted, True)
            equal(step["after"], state)
            if step["event"]["event"] == "fault":
                equal(state["kept"], [[] for _ in range(8)])
                equal(state["pending_token"], None)
                equal(state["staged"], [])
                equal(state["parameters"], old_parameters)
                equal(state["version"], old_version)
        equal(state["phase"], "RUN")
    equal(rows["claims"], {
        "software_protocol_only": True, "real_ads8866_timing_passed": False,
        "rtl_passed": False, "real_adc_dac_model_passed": False,
        "system_1pct_passed": False, "procurement": False,
    })
    summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    for name, expected in (
        ("cases", len(rows["cases"])),
        ("executable_cases", sum(r["trace"] is not None for r in rows["cases"])),
        ("counterexamples", len(rows["counterexamples"])),
        ("fault_recovery_traces", len(rows["fault_recovery"])),
        ("nominal_events", sum(len(r["trace"] or []) for r in rows["cases"])),
        ("claims", rows["claims"]),
        ("first_batch_alignment_ns", {"assumed_m1": 161200, "assumed_m4": 439600,
                                     "assumed_m16": 1553200, "discard_two_each_point": 346800,
                                     "first_batch_unknown_lower_bound": 85000}),
    ):
        equal(summary[name], expected)
    provenance = json.loads((directory / "provenance.json").read_text(encoding="utf-8"))
    equal(provenance["adc"]["rate_ceiling_hz"], 100000)
    equal(provenance["dac"]["illustrative_settle_ns"], 5000)
    equal(provenance["dac"]["worst_case_guarantee"], False)
    equal(provenance["new_downloads"], 0)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    equal(set(manifest["artifacts"]), {"evidence.json", "provenance.json", "summary.json"})
    equal(set(manifest["code"]), {"model.py", "verify.py", "runner.py", "tests.py",
                                 "run_all.py", "provenance.json"})
    for name, digest in manifest["artifacts"].items():
        equal(hashlib.sha256((directory / name).read_bytes()).hexdigest(), digest)
    if code_dir is not None:
        for name, digest in manifest["code"].items():
            equal(hashlib.sha256((Path(code_dir) / name).read_bytes()).hexdigest(), digest)
    return dict(passed=True, checks=checks, independent_oracle=True, decimal_budget=True,
                hardware_validation=False, imports_model_or_runner=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--code-dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.results, args.code_dir), ensure_ascii=False))
