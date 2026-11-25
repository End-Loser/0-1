"""Generate reproducible event evidence inside the caller-selected directory."""

import copy
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path

from model import Config, Controller, TIMES, budget, nominal


CLAIMS = dict(software_protocol_only=True, real_ads8866_timing_passed=False,
              rtl_passed=False, real_adc_dac_model_passed=False,
              system_1pct_passed=False, procurement=False)


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                          encoding="utf-8")


def execute(p, events):
    ctl = Controller(p)
    trace = []
    for event in events:
        trace.append(dict(event=event, after=ctl.step(event)))
    return trace


def configurations():
    p = Config()
    unknown = replace(p, **dict.fromkeys(TIMES))
    return [
        ("assumed_m1", p), ("assumed_m4", replace(p, m=4)),
        ("assumed_m16", replace(p, m=16)), ("discard_two_each_point", replace(p, d=2)),
        ("m4_d2_three_updates", replace(p, m=4, d=2, updates=3)),
        ("noninteger_rate_slot", replace(p, sample_rate_hz=33333)),
        ("slow_hardware_input", replace(p, adc_ready_ns=20000)),
        ("unknown_all_timings", unknown),
        ("first_batch_unknown_lower_bound", replace(unknown, dac_settle_ns=5000)),
    ]


def counterexamples():
    p = Config()
    events = nominal(p)
    rows = []

    def attempt(label, prefix, event):
        ctl = Controller(p)
        for item in prefix:
            ctl.step(item)
        before = ctl.snapshot()
        try:
            ctl.step(event)
        except ValueError as exc:
            rejected, reason = True, str(exc)
        else:
            rejected, reason = False, "UNEXPECTED_ACCEPT"
        if not rejected or ctl.snapshot() != before:
            raise AssertionError(label)
        rows.append(dict(id=label, prefix=copy.deepcopy(prefix), before=before,
                         event=event, rejected=rejected, after=ctl.snapshot(), reason=reason))

    waiting = {"ack", "cal_ready", "settled", "adc_done", "read_done", "program",
               "dac_done", "dac_settled", "run_ready", "resume"}
    for index, event in enumerate(events):
        name = event["event"]
        if name in waiting:
            early = dict(event, time_ns=event["time_ns"] - 1)
            attempt(f"early_{index}_{name}", events[:index], early)
        if name != "data":
            attempt(f"duplicate_{index}_{name}", events[:index + 1], dict(event))
        if name in ("adc_request", "adc_done", "read_start", "read_done"):
            attempt(f"wrong_point_{index}", events[:index], dict(event, point=(event["point"] + 1) % 8))
            attempt(f"wrong_token_{index}", events[:index], dict(event, token=event["token"] + 1))
        if index and event["time_ns"]:
            attempt(f"time_reverse_{index}", events[:index],
                    dict(event, time_ns=events[index - 1]["time_ns"] - 1))
    request_index = next(i for i, e in enumerate(events) if e["event"] == "adc_request")
    read_index = next(i for i, e in enumerate(events) if e["event"] == "read_done")
    next_point = next(i for i, e in enumerate(events)
                      if e["event"] == "adc_request" and e["point"] == 1)
    attempt("read_without_completion", events[:request_index + 1],
            dict(event="read_start", time_ns=events[request_index]["time_ns"], point=0, token=1))
    attempt("read_without_request", events[:request_index],
            dict(event="read_done", time_ns=events[request_index]["time_ns"], point=0, token=1, value=0))
    attempt("switch_point_with_adc_outstanding", events[:request_index + 1],
            dict(event="select", time_ns=events[request_index]["time_ns"], point=1))
    attempt("solve_after_only_one_point", events[:read_index + 1],
            dict(event="solve", time_ns=events[read_index]["time_ns"]))
    attempt("cross_point_stale_read", events[:next_point + 3],
            dict(events[read_index], time_ns=events[next_point + 2]["time_ns"] + p.adc_spi_ns))
    attempt("same_point_stale_request_token", events[:next_point], dict(events[next_point], token=1))
    attempt("run_parameter_write", [], dict(event="program", time_ns=0, update=0, code=9))
    attempt("paused_data", events[:1], dict(event="data", time_ns=0, input=0))
    attempt("reset_in_run", [], dict(event="reset", time_ns=0))
    attempt("value_out_of_range", events[:read_index], dict(events[read_index], value=65536))
    attempt("boolean_value", events[:read_index], dict(events[read_index], value=True))
    attempt("negative_time", [], dict(event="request", time_ns=-1))
    attempt("wrong_schema", [], dict(event="request", time_ns=0, wait_ns=0))
    attempt("unknown_event", [], dict(event="calibrate", time_ns=0))
    program_index = next(i for i, e in enumerate(events) if e["event"] == "program")
    attempt("wrong_dac_update", events[:program_index], dict(events[program_index], update=1))
    # A fault must not make old completion interrupts valid after a reset.
    prefix = events[:request_index + 1]
    fault_time = prefix[-1]["time_ns"]
    fault = dict(event="fault", time_ns=fault_time)
    attempt("fault_direct_resume", prefix + [fault], dict(event="resume", time_ns=fault_time))
    attempt("fault_stale_adc_done", prefix + [fault],
            dict(event="adc_done", time_ns=fault_time + p.slot_ns, point=0, token=1))
    attempt("reset_stale_adc_done", prefix + [fault, dict(event="reset", time_ns=fault_time)],
            dict(event="adc_done", time_ns=fault_time + p.slot_ns, point=0, token=1))
    return rows


def fault_recoveries():
    p = Config()
    events = nominal(p)
    ctl = Controller(p)
    prefixes = {"RUN": []}
    for index, event in enumerate(events):
        state = ctl.step(event)
        prefixes.setdefault(state["phase"], events[:index + 1])
    rows = []
    for phase, prefix in prefixes.items():
        ctl = Controller(p)
        for event in prefix:
            ctl.step(event)
        s = ctl.snapshot()
        fault = dict(event="fault", time_ns=s["now"])
        recovery = nominal(p, s["now"], s["token"])
        recovery[0] = dict(event="reset", time_ns=s["now"])
        rows.append(dict(phase=phase, trace=execute(p, prefix + [fault] + recovery)))
    return rows


def generate(out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    cases = []
    for name, p in configurations():
        missing = budget(p)["missing"]
        if missing:
            try:
                Controller(p)
            except ValueError:
                blocked = True
            else:
                raise AssertionError("unknown inputs executed")
            trace = None
        else:
            trace, blocked = execute(p, nominal(p)), False
        cases.append(dict(id=name, config=asdict(p), budget=budget(p),
                          trace=trace, execution_blocked=blocked))
    evidence = dict(cases=cases, counterexamples=counterexamples(),
                    fault_recovery=fault_recoveries(), claims=CLAIMS)
    save(out / "evidence.json", evidence)
    code = Path(__file__).resolve().parent
    provenance = json.loads((code / "provenance.json").read_text(encoding="utf-8"))
    save(out / "provenance.json", provenance)
    summary = dict(
        cases=len(cases), executable_cases=sum(r["trace"] is not None for r in cases),
        counterexamples=len(evidence["counterexamples"]),
        fault_recovery_traces=len(evidence["fault_recovery"]),
        nominal_events=sum(len(r["trace"] or []) for r in cases),
        claims=CLAIMS,
        first_batch_alignment_ns={r["id"]: r["budget"]["pause_ns"] for r in cases
                                  if r["id"] in ("assumed_m1", "assumed_m4", "assumed_m16",
                                                "discard_two_each_point", "first_batch_unknown_lower_bound")},
    )
    save(out / "summary.json", summary)
    manifest = dict(
        artifacts={name: hashlib.sha256((out / name).read_bytes()).hexdigest()
                   for name in ("evidence.json", "provenance.json", "summary.json")},
        code={name: hashlib.sha256((code / name).read_bytes()).hexdigest()
              for name in ("model.py", "verify.py", "runner.py", "tests.py", "run_all.py", "provenance.json")},
    )
    save(out / "manifest.json", manifest)
    return summary
