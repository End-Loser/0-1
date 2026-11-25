"""Selected 3-wire CS contract, integer picoseconds; not ADC silicon."""
from copy import deepcopy

CONV_MAX = 8_800_000
ACQ_MIN = 1_200_000
RATE_MIN = 10_000_000
PERIOD = 50_000
ENABLE = 12_300
DELAY = 13_400
HOLD = 3_000
HZ = 13_200
QUIET = 20_000


def generate(words, policy="datasheet", conv=CONV_MAX, start=ACQ_MIN,
             interval=None, period=PERIOD):
    events = [{"t_ps": 0, "signal": "ready", "value": True}]
    def put(t, signal, value):
        events.append({"t_ps": t, "signal": signal, "value": value})
    t = start
    for word in words:
        put(t, "CNV", 1)
        put(t + HZ, "DOUT", "Z")
        put(t + conv, "conversion_done", True)
        put(t + conv, "acquire_start", True)
        # No busy indication in this mode: host always waits tCONV-max.
        read = t + CONV_MAX
        put(read, "CNV", 0)
        put(read + ENABLE, "DOUT", (word >> 15) & 1)
        for k in range(16):
            rise = read + period // 2 + k * period
            fall = read + (k + 1) * period
            put(rise, "SCLK", 1)
            put(rise, "sample", (word >> (15-k)) & 1)
            put(fall, "SCLK", 0)
            if k < 15:
                put(fall + HOLD, "DOUT", "X")
                put(fall + DELAY, "DOUT", (word >> (14-k)) & 1)
            else:
                put(fall + HZ, "DOUT", "Z")
        release = read + 16 * period + HZ
        put(release, "frontend_release", True)
        slot = max(RATE_MIN, CONV_MAX + 16 * period + HZ + ACQ_MIN
                   if policy == "serial_frontend" else conv + ACQ_MIN)
        t += interval if interval is not None else slot
    put(t, "slot_end", True)
    events.sort(key=lambda e: e["t_ps"])
    return {"mode": "3wire_CS_DIN_high", "policy": policy,
            "supplies": {"AVDD_V": 3, "DVDD_V": 3, "VREF_V": 5,
                         "temperature_C": 25, "DOUT_load_pF": 20},
            "external_ready_asserted": True, "words": list(words),
            "events": events}


def validate(trace):
    def need(ok, name):
        if not ok:
            raise ValueError(name)
    need(trace["mode"] == "3wire_CS_DIN_high", "mode")
    need(trace["external_ready_asserted"] is True, "startup_external")
    p = trace["supplies"]
    need(p["AVDD_V"] == p["DVDD_V"] == 3 and p["VREF_V"] == 5
         and -40 <= p["temperature_C"] <= 85 and 0 <= p["DOUT_load_pF"] <= 20,
         "supply_profile")
    need(trace["policy"] in ("datasheet", "serial_frontend"), "policy")
    words = trace["words"]
    need(words and all(type(w) is int and 0 <= w <= 65535 for w in words), "word")
    cnv = 0
    sclk = 0
    dout = "Z"
    previous_t = -1
    last_start = None
    acq = 0
    release = 0
    high_at = None
    low_at = None
    rises = falls = count = 0
    start = done = selected = None
    ready = False
    sampled = []
    last_edge = -QUIET
    schedules = []
    seen_done = seen_acq = False
    slot_end = None
    for e in trace["events"]:
        need(set(e) == {"t_ps", "signal", "value"}, "schema")
        t, sig, v = e["t_ps"], e["signal"], e["value"]
        need(type(t) is int and t >= previous_t, "time_order")
        previous_t = t
        if sig == "ready":
            need(t == 0 and v is True and not ready, "ready")
            ready = True
        elif sig == "CNV":
            need(type(v) is int and v in (0, 1) and v != cnv, "CNV_edge")
            if v:
                need(ready and sclk == 0 and t-acq >= ACQ_MIN, "acquisition")
                need(last_start is None or t-last_start >= RATE_MIN, "rate")
                need(t-last_edge >= QUIET, "quiet")
                need(last_start is None or rises == falls == 16, "read_overlap")
                if trace["policy"] == "serial_frontend":
                    need(t-release >= ACQ_MIN, "serial_acquisition")
                if last_start is not None:
                    need(sampled == [(words[count-1] >> j) & 1
                                     for j in range(15, -1, -1)], "bit_order")
                need(count < len(words), "extra_conversion")
                count += 1
                start = last_start = t
                done = selected = None
                rises = falls = 0
                sampled = []
                seen_done = seen_acq = False
                schedules.append((t+HZ, "Z"))
            else:
                need(start is not None and t-start >= CONV_MAX, "read_during_conversion")
                need(t-start >= 10_000 and seen_done and seen_acq, "CNV_pulse")
                selected = t
                schedules.append((t+ENABLE, (words[count-1] >> 15) & 1))
            cnv = v
        elif sig == "conversion_done":
            need(start is not None and not seen_done and v is True
                 and 500_000 <= t-start <= CONV_MAX, "conversion_bound")
            done = t
            seen_done = True
        elif sig == "acquire_start":
            need(seen_done and t == done and not seen_acq and v is True, "acquire_start")
            acq = t
            seen_acq = True
        elif sig == "SCLK":
            need(type(v) is int and v in (0, 1) and v != sclk, "SCLK_edge")
            need(cnv == 0 and selected is not None and done is not None, "SCLK_conversion")
            if v:
                need(rises == falls and rises < 16, "clock_count")
                need(t >= selected+ENABLE and dout in (0, 1), "DOUT_setup")
                if falls:
                    need(t-low_at >= DELAY, "DOUT_next_setup")
                high_at = t
                rises += 1
            else:
                need(rises == falls+1, "clock_count")
                low_begin = selected if falls == 0 else low_at
                period = t-low_begin
                high_width = t-high_at
                need(period >= 15_000 and period*66_600_000 >= 10**12,
                     "SCLK_frequency")
                need(45*period <= 100*high_width <= 55*period, "SCLK_duty")
                low_at = t
                falls += 1
                if falls < 16:
                    schedules.extend([(t+HOLD, "X"),
                                      (t+DELAY, (words[count-1] >> (15-falls)) & 1)])
                else:
                    schedules.append((t+HZ, "Z"))
            sclk = v
            last_edge = t
        elif sig == "DOUT":
            need(schedules and (t, v) == schedules.pop(0), "DOUT_envelope")
            dout = v
        elif sig == "sample":
            need(sclk == 1 and t == high_at and len(sampled) == rises-1,
                 "sample_edge")
            need(v == dout and v in (0, 1), "sample_value")
            sampled.append(v)
        elif sig == "frontend_release":
            need(falls == 16 and dout == "Z" and t == low_at+HZ and v is True,
                 "frontend_release")
            release = t
        elif sig == "slot_end":
            need(slot_end is None and v is True, "slot_end")
            need(t-acq >= ACQ_MIN and t-last_start >= RATE_MIN
                 and t-last_edge >= QUIET, "final_slot")
            if trace["policy"] == "serial_frontend":
                need(t-release >= ACQ_MIN, "serial_acquisition")
            slot_end = t
        else:
            raise ValueError("unknown_event")
    need(slot_end == previous_t and count == len(words) and rises == falls == 16
         and not schedules and dout == "Z", "complete")
    need(sampled == [(words[-1] >> j) & 1 for j in range(15, -1, -1)], "bit_order")
    return {"accepted": True, "conversions": count, "events": len(trace["events"]),
            "elapsed_ps": slot_end, "hardware_passed": False}


def scenarios():
    base = generate([0xA53C])
    cases = [
        ("startup_single_max", base, True),
        ("continuous_8_100ksps", generate([0, 65535, 1, 32768, 0xA53C, 0x1234, 0x8001, 0x55AA]), True),
        ("serial_frontend_8", generate([0xA53C]*8, "serial_frontend"), True),
        ("conversion_min_host_waits_max", generate([0xA53C], conv=500_000), True),
    ]
    def changed(name, action):
        tr = deepcopy(base)
        action(tr)
        cases.append((name, tr, False))
    changed("startup_not_confirmed", lambda tr: tr.update(external_ready_asserted=False))
    changed("read_1ps_early", lambda tr: next(e for e in tr["events"] if e["signal"] == "CNV" and e["value"] == 0).update(t_ps=9_999_999))
    cases.append(("rate_1ps_early", generate([1, 2], interval=9_999_999), False))
    cases.append(("initial_acquisition_1ps_short", generate([1], start=1_199_999), False))
    cases.append(("SCLK_too_fast", generate([0xA53C], period=14_000), False))
    changed("wrong_bit_order", lambda tr: next(e for e in tr["events"] if e["signal"] == "sample").update(value=0))
    changed("wrong_supply_profile", lambda tr: tr["supplies"].update(DVDD_V=1.8))
    cases.append(("forbidden_serial_overlap", generate([1, 2], "serial_frontend", interval=10_000_000), False))
    return [{"name": n, "expected_accept": ok, "trace": tr} for n, tr, ok in cases]
