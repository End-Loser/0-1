"""Serialized software contract. Not an ADS8866 pin-level or RTL model."""

import copy
from dataclasses import asdict, dataclass, fields
import math


def integer(value, minimum=0, maximum=10**15):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("integer outside contract range")
    return value


TIMES = (
    "pause_ack_ns", "switch_cal_ns", "probe_settle_ns", "mux_settle_ns",
    "adc_ready_ns", "adc_spi_ns", "solve_ns", "dac_spi_ns",
    "dac_settle_ns", "gain_settle_ns", "switch_run_ns", "restore_ns",
)


@dataclass(frozen=True)
class Config:
    m: int = 1
    d: int = 0
    updates: int = 1
    sample_rate_hz: int = 100000
    pause_ack_ns: int | None = 2000
    switch_cal_ns: int | None = 3000
    probe_settle_ns: int | None = 4000
    mux_settle_ns: int | None = 1000
    adc_ready_ns: int | None = 10000
    adc_spi_ns: int | None = 1600
    solve_ns: int | None = 6000
    dac_spi_ns: int | None = 2400
    dac_settle_ns: int | None = 5000
    gain_settle_ns: int | None = 3000
    switch_run_ns: int | None = 3000
    restore_ns: int | None = 4000

    def __post_init__(self):
        integer(self.m, 1, 10000)
        integer(self.d, 0, 10000)
        integer(self.updates, 1, 10000)
        integer(self.sample_rate_hz, 1, 100000)
        for name in TIMES:
            if getattr(self, name) is not None:
                integer(getattr(self, name))

    @property
    def rate_slot_ns(self):
        return (10**9 + self.sample_rate_hz - 1) // self.sample_rate_hz

    @property
    def slot_ns(self):
        return max(self.rate_slot_ns, self.adc_ready_ns or 0)


def budget(config):
    p = asdict(config)
    missing = [k for k in TIMES if p[k] is None]
    t = {k: p[k] or 0 for k in TIMES}
    n = 8 * (config.m + config.d)
    segments = {
        "pause_ack": t["pause_ack_ns"], "switch_cal": t["switch_cal_ns"],
        "point_settle": 8 * (t["probe_settle_ns"] + t["mux_settle_ns"]),
        "adc_slots": n * config.slot_ns, "adc_spi": n * t["adc_spi_ns"],
        "solve": t["solve_ns"], "dac_spi": config.updates * t["dac_spi_ns"],
        "dac_settle": config.updates * t["dac_settle_ns"],
        "gain_settle": config.updates * t["gain_settle_ns"],
        "switch_run": t["switch_run_ns"], "restore": t["restore_ns"],
    }
    total = sum(segments.values())
    return {
        "conversions": n, "retained": 8 * config.m, "discarded": 8 * config.d,
        "minimum_rate_slot_ns": config.rate_slot_ns,
        "effective_slot_ns": config.slot_ns, "pause_ns": total,
        "active_for_one_percent_ns": 99 * total,
        "segments_ns": segments, "missing": missing,
        "kind": "lower_bound_only" if missing else "synthetic_serial_schedule",
    }


SCHEMA = {
    "data": ("input",), "request": (), "ack": (), "cal_ready": (),
    "select": ("point",), "settled": (),
    "adc_request": ("point", "token"), "adc_done": ("point", "token"),
    "read_start": ("point", "token"), "read_done": ("point", "token", "value"),
    "solve": (), "program": ("update", "code"), "dac_done": ("update",),
    "dac_settled": ("update",), "run_ready": (), "resume": (),
    "fault": (), "reset": (),
}


class Controller:
    def __init__(self, config):
        if any(getattr(config, k) is None for k in TIMES):
            raise ValueError("unknown timing blocks executable protocol")
        self.config = config
        self.s = dict(
            phase="RUN", now=0, due=0, point=-1, sample=0, token=0,
            pending_token=None, kept=[[] for _ in range(8)], discarded=0,
            update=0, staged=[], parameters=[0] * config.updates, version=1,
        )

    def snapshot(self):
        return copy.deepcopy(self.s)

    def step(self, event):
        if not isinstance(event, dict) or event.get("event") not in SCHEMA:
            raise ValueError("unknown event")
        name = event["event"]
        if set(event) != {"event", "time_ns", *SCHEMA[name]}:
            raise ValueError("event schema mismatch")
        now = integer(event["time_ns"])
        for key in SCHEMA[name]:
            integer(event[key], maximum=65535 if key in ("value", "code") else 10**15)
        if name == "data" and event["input"] > 7:
            raise ValueError("input label outside 0..7")
        s = self.snapshot()
        p = self.config
        if now < s["now"]:
            raise ValueError("time reversal")
        phase = s["phase"]

        def require(condition):
            if not condition:
                raise ValueError("event violates state, identity or deadline")

        def enter(dest, delay=0):
            s["phase"], s["due"] = dest, now + delay

        if name == "fault":
            enter("FAULT")
            s["pending_token"] = None
            s["kept"], s["discarded"], s["staged"] = [[] for _ in range(8)], 0, []
        elif name in ("request", "reset"):
            require(phase == ("RUN" if name == "request" else "FAULT"))
            s["point"], s["sample"], s["update"] = -1, 0, 0
            s["kept"], s["discarded"], s["staged"] = [[] for _ in range(8)], 0, []
            s["pending_token"] = None
            enter("PAUSE", p.pause_ack_ns)
        elif name == "data":
            require(phase == "RUN")
        elif name == "ack":
            require(phase == "PAUSE" and now >= s["due"])
            enter("SWITCH_CAL", p.switch_cal_ns)
        elif name == "cal_ready":
            require(phase == "SWITCH_CAL" and now >= s["due"])
            enter("SELECT")
        elif name == "select":
            require(phase in ("SELECT", "POINT_DONE") and
                    event["point"] == s["point"] + 1 and event["point"] < 8)
            s["point"], s["sample"] = event["point"], 0
            enter("SETTLE", p.probe_settle_ns + p.mux_settle_ns)
        elif name == "settled":
            require(phase == "SETTLE" and now >= s["due"])
            enter("READY")
        elif name in ("adc_request", "adc_done", "read_start", "read_done"):
            require(event["point"] == s["point"])
            if name == "adc_request":
                require(phase == "READY" and event["token"] == s["token"] + 1)
                s["token"] = event["token"]
                s["pending_token"] = event["token"]
                enter("ADC_BUSY", p.slot_ns)
            else:
                require(event["token"] == s["pending_token"])
                if name == "adc_done":
                    require(phase == "ADC_BUSY" and now >= s["due"])
                    enter("ADC_DONE")
                elif name == "read_start":
                    require(phase == "ADC_DONE")
                    enter("READING", p.adc_spi_ns)
                else:
                    require(phase == "READING" and now >= s["due"])
                    if s["sample"] < p.d:
                        s["discarded"] += 1
                    else:
                        s["kept"][s["point"]].append(event["value"])
                    s["sample"] += 1
                    s["pending_token"] = None
                    enter("POINT_DONE" if s["sample"] == p.m + p.d else "READY")
        elif name == "solve":
            require(phase == "POINT_DONE" and s["point"] == 7 and
                    all(len(values) == p.m for values in s["kept"]))
            enter("PROGRAM", p.solve_ns)
        elif name == "program":
            require(phase == "PROGRAM" and now >= s["due"] and
                    event["update"] == s["update"] and s["update"] < p.updates)
            s["staged"].append(event["code"])
            enter("DAC_BUSY", p.dac_spi_ns)
        elif name == "dac_done":
            require(phase == "DAC_BUSY" and now >= s["due"] and event["update"] == s["update"])
            enter("DAC_WAIT", p.dac_settle_ns + p.gain_settle_ns)
        elif name == "dac_settled":
            require(phase == "DAC_WAIT" and now >= s["due"] and event["update"] == s["update"])
            s["update"] += 1
            enter("PROGRAM" if s["update"] < p.updates else "SWITCH_RUN",
                  0 if s["update"] < p.updates else p.switch_run_ns)
        elif name == "run_ready":
            require(phase == "SWITCH_RUN" and now >= s["due"])
            enter("RESTORE", p.restore_ns)
        elif name == "resume":
            require(phase == "RESTORE" and now >= s["due"] and len(s["staged"]) == p.updates)
            s["parameters"] = list(s["staged"])
            s["version"] += 1
            enter("RUN")
        else:
            raise ValueError("unhandled event")
        s["now"] = now
        self.s = s
        return self.snapshot()


def nominal(config, start=0, token_base=0):
    """Generate the earliest serial schedule without consulting Controller."""
    events = []
    t = start

    def emit(name, **payload):
        events.append(dict(event=name, time_ns=t, **payload))

    emit("request")
    t += config.pause_ack_ns
    emit("ack")
    t += config.switch_cal_ns
    emit("cal_ready")
    token = token_base
    for point in range(8):
        emit("select", point=point)
        t += config.probe_settle_ns + config.mux_settle_ns
        emit("settled")
        for sample in range(config.d + config.m):
            token += 1
            payload = dict(point=point, token=token)
            emit("adc_request", **payload)
            t += config.slot_ns
            emit("adc_done", **payload)
            emit("read_start", **payload)
            t += config.adc_spi_ns
            emit("read_done", value=1000 * point + sample, **payload)
    emit("solve")
    t += config.solve_ns
    for update in range(config.updates):
        emit("program", update=update, code=100 + update)
        t += config.dac_spi_ns
        emit("dac_done", update=update)
        t += config.dac_settle_ns + config.gain_settle_ns
        emit("dac_settled", update=update)
    t += config.switch_run_ns
    emit("run_ready")
    t += config.restore_ns
    emit("resume")
    for label in range(8):
        emit("data", input=label)
    return events
