"""Independent frame-based oracle. No imports from model or runner."""
import hashlib
import json
from fractions import Fraction
from pathlib import Path
import sys


def check(tr):
    def require(condition, message):
        if not condition:
            raise ValueError(message)
    require(tr["mode"] == "3wire_CS_DIN_high", "mode")
    require(tr["policy"] in ["datasheet", "serial_frontend"], "policy")
    require(tr["external_ready_asserted"] is True, "external_ready")
    p = tr["supplies"]
    require(p["AVDD_V"] == 3 and p["DVDD_V"] == 3 and p["VREF_V"] == 5
            and -40 <= p["temperature_C"] <= 85 and 0 <= p["DOUT_load_pF"] <= 20,
            "characterization_profile")
    ev = tr["events"]
    require(all(set(e) == {"t_ps", "signal", "value"} and type(e["t_ps"]) is int
                and e["t_ps"] >= 0 for e in ev), "schema")
    require([e["t_ps"] for e in ev] == sorted(e["t_ps"] for e in ev), "time")
    starts = [e["t_ps"] for e in ev if e["signal"] == "CNV" and e["value"] == 1]
    words = tr["words"]
    require(words and len(starts) == len(words), "conversion_count")
    require(all(type(w) is int and 0 <= w < 65536 for w in words), "word")
    expected = [(0, "ready", True)]
    def add(t, s, v):
        expected.append((t, s, v))
    acquisition = 0
    released = 0
    for i, (st, word) in enumerate(zip(starts, words)):
        end = starts[i+1] if i+1 < len(starts) else ev[-1]["t_ps"]
        require(st-acquisition >= 1_200_000, "acquisition")
        require(i == 0 or st-starts[i-1] >= 10_000_000, "100ksps")
        if tr["policy"] == "serial_frontend":
            require(st-released >= 1_200_000, "serial_frontend")
        frame = [e for e in ev if st <= e["t_ps"] < end]
        def times(signal, value=None):
            return [e["t_ps"] for e in frame
                    if e["signal"] == signal and (value is None or e["value"] == value)]
        done = times("conversion_done")
        lows = times("CNV", 0)
        require(len(done) == len(lows) == 1, "one_conversion_one_select")
        completion, select = done[0], lows[0]
        require(500_000 <= completion-st <= 8_800_000, "conversion_min_max")
        require(select-st >= 8_800_000 and select-st >= 10_000, "CNV_hold_read")
        acquisition = completion
        up, down = times("SCLK", 1), times("SCLK", 0)
        require(len(up) == len(down) == 16, "16_clocks")
        require(up[0]-select >= 12_300, "MSB_enable")
        for k in range(16):
            low_begin = select if k == 0 else down[k-1]
            period = down[k]-low_begin
            high = down[k]-up[k]
            low = up[k]-low_begin
            require(period >= 15_000 and Fraction(10**12, period) <= 66_600_000,
                    "SCLK_max_frequency")
            require(45*period <= 100*high <= 55*period
                    and 45*period <= 100*low <= 55*period, "SCLK_duty")
            if k:
                require(up[k]-down[k-1] >= 13_400, "next_bit_valid")
        require(end-completion >= 1_200_000 and end-st >= 10_000_000, "slot_end")
        require(end-down[-1] >= 20_000, "quiet")
        released = down[-1]+13_200
        if tr["policy"] == "serial_frontend":
            require(end-released >= 1_200_000, "serial_final")
        add(st, "CNV", 1)
        add(st+13_200, "DOUT", "Z")
        add(completion, "conversion_done", True)
        add(completion, "acquire_start", True)
        add(select, "CNV", 0)
        add(select+12_300, "DOUT", (word >> 15) & 1)
        # Bits computed by binary string, separately from model shift rules.
        bits = list(map(int, format(word, "016b")))
        for k in range(16):
            add(up[k], "SCLK", 1)
            add(up[k], "sample", bits[k])
            add(down[k], "SCLK", 0)
            if k < 15:
                add(down[k]+3_000, "DOUT", "X")
                add(down[k]+13_400, "DOUT", bits[k+1])
            else:
                add(down[k]+13_200, "DOUT", "Z")
        add(released, "frontend_release", True)
    add(ev[-1]["t_ps"], "slot_end", True)
    actual = [(e["t_ps"], e["signal"], e["value"]) for e in ev]
    # Stable tie order preserves sample-after-rising-edge and done-before-acquire.
    require(actual == sorted(expected, key=lambda e: e[0]), "event_or_bit_mismatch")
    return {"accepted": True, "conversions": len(words), "events": len(ev),
            "elapsed_ps": ev[-1]["t_ps"], "hardware_passed": False}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify(directory):
    directory = Path(directory)
    manifest = json.loads((directory/"manifest.json").read_text(encoding="utf-8"))
    required = {"model.py", "oracle.py", "tests.py", "run_all.py", "provenance.json",
                "结果/evidence.json", "结果/tests.json"}
    if not required.issubset(manifest["sha256"]):
        raise ValueError("missing_required_hash")
    for name, digest in manifest["sha256"].items():
        target = directory.parent/name
        if ".." in Path(name).parts or not target.is_file() or sha(target) != digest:
            raise ValueError("hash: "+name)
    data = json.loads((directory/"evidence.json").read_text(encoding="utf-8"))
    claim_keys = {"analog_precision_passed", "real_ADC_SPICE_passed", "RTL_passed",
                  "hardware_passed", "system_1pct_passed", "procurement"}
    names = ["startup_single_max", "continuous_8_100ksps", "serial_frontend_8",
             "conversion_min_host_waits_max", "startup_not_confirmed",
             "read_1ps_early", "rate_1ps_early", "initial_acquisition_1ps_short",
             "SCLK_too_fast", "wrong_bit_order", "wrong_supply_profile",
             "forbidden_serial_overlap"]
    if ([c["name"] for c in data["scenarios"]] != names
            or set(data["claims"]) != claim_keys
            or any(v is not False for v in data["claims"].values())
            or [c["expected_accept"] for c in data["scenarios"]] != [True]*4+[False]*8):
        raise ValueError("coverage_or_false_claim")
    testing = json.loads((directory/"tests.json").read_text(encoding="utf-8"))
    if testing["returncode"] != 0 or "Ran 23 tests" not in testing["stderr"]:
        raise ValueError("test_execution")
    summary_path = directory/"summary.json"
    if summary_path.is_file():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        expected = {
            "scenario_count": 12, "nominal_accepted": 4, "negative_rejected": 8,
            "unittest_count": 23, "tamper_rejected": 4, "claims": data["claims"],
            "continuous_8_slot_ps": 80_000_000, "serial_8_slot_ps": 86_505_600,
            "startup_acquisition_ps": 1_200_000, "pause_budget_161_2us_validated": False}
        if summary != expected:
            raise ValueError("summary")
        tamper = json.loads((directory/"tamper_tests.json").read_text(encoding="utf-8"))
        if len(tamper) != 4 or not all(x["rejected"] for x in tamper):
            raise ValueError("tamper")
    count = 0
    for case in data["scenarios"]:
        try:
            outcome = check(case["trace"])
            accepted = True
        except ValueError as exc:
            outcome = {"accepted": False, "reason": str(exc)}
            accepted = False
        if accepted != case["expected_accept"] or accepted != case["model"]["accepted"]:
            raise ValueError("scenario: "+case["name"])
        if accepted and outcome != case["model"]:
            raise ValueError("result: "+case["name"])
        count += len(case["trace"]["events"])
    return {"verified": True, "scenarios": 12, "trace_events_checked": count,
            "hardware_passed": False}


if __name__ == "__main__":
    print(json.dumps(verify(sys.argv[1] if len(sys.argv) > 1
                            else Path(__file__).parent/"结果"), ensure_ascii=False))
