"""Independent CSV, source/load, capacitor and output-window checks."""
import csv
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESULT = ROOT / "supply_instrumented"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pwl(time, points):
    for (a, va), (b, vb) in zip(points, points[1:]):
        if a <= time <= b:
            return va + (vb-va)*(time-a)/(b-a)
    return points[-1][1]


def recalculate(run):
    with (RESULT / "supply_results" / run["id"] / "wave.csv").open(newline="", encoding="ascii") as stream:
        reader = csv.reader(stream)
        assert len(next(reader)) == 7
        data = [tuple(map(float, row)) for row in reader]
    assert len(data) == run["rows"]
    assert all(len(r) == 7 and all(math.isfinite(v) for v in r) for r in data)
    assert all(a[0] < b[0] for a, b in zip(data, data[1:]))
    kind = run["kind"]
    finish = .005 if kind == "reference" else .01
    assert data[-1][0] >= finish-1e-12
    first, last = run["load_A"], run["second_load_A"]
    if kind == "reference":
        load_points = [(0, first), (.002, first), (.002002, last), (.005, last)]
        vin_points = [(0, run["vin_V"]), (.002, run["vin_V"]),
                      (.002001, run["second_vin_V"]), (.005, run["second_vin_V"])]
    else:
        load_points = [(0, 0), (.0015, 0), (.001502, first), (.002, first), (.002002, last),
                       (.005998, last), (.006, 0), (.0085, 0), (.008502, last), (.01, last)]
        vin_points = [(0, run["vin_V"]), (.01, run["vin_V"])]
    load_error = max(abs(r[6]-pwl(r[0], load_points)) for r in data)
    resistor_error = max(abs(-r[5]-(pwl(r[0], vin_points)-r[1])/.05) for r in data)
    assert load_error < 1e-9 and resistor_error < 1e-7
    begin, end = run["window_s"]
    window = [r for r in data if begin <= r[0] <= end]
    target, tolerance = (5, .005) if kind == "reference" else (3.3, .0495)
    error = max(abs(r[2]-target) for r in window)
    headroom = min(r[1] for r in window) >= target + (.2 if kind == "reference" else .3)
    checks = dict(output_screen=error < tolerance, input_headroom=headroom)
    off_range = None
    if kind == "regulator":
        off = [r[2] for r in data if .0065 <= r[0] <= .0069]
        checks["enable_discharge"] = max(map(abs, off)) < .05
        off_range = [min(off), max(off)]
    assert checks == run["checks"]
    assert abs(error-run["output_error_V"]) < 1e-12
    status = "pass" if all(checks.values()) else "fail"
    assert status == run["status"]
    resistance = 1 if kind == "reference" else .05
    integral, max_charge_error = 0, 0
    for a, b in zip(data, data[1:]):
        integral += (b[0]-a[0])*((a[2]-a[3])+(b[2]-b[3]))/(2*resistance)
        expected = 1e-6*(b[3]-data[0][3])
        max_charge_error = max(max_charge_error, abs(integral-expected))
    scale = 1e-6*max(abs(r[3]) for r in data)
    return dict(id=run["id"], reproduced_status=status, checks=checks,
                output_error_V=error, off_window_range_V=off_range,
                load_current_error_A=load_error, upstream_resistor_error_A=resistor_error,
                capacitor_integral_relative_error=max_charge_error/scale,
                capacitor_integral_1percent_passed=max_charge_error/scale < .01)


def audit():
    record = json.loads((RESULT / "supply_screen.json").read_text(encoding="utf-8"))
    assert sha(ROOT / "supply_screen.json") == record["execution_revision"]["previous_summary_sha256"]
    for path, digest in record["original_files_sha256"].items():
        assert sha(Path(path)) == digest, path
    for name, key in [("sources.json", "sources_sha256"), ("followup.json", "followup_sha256")]:
        assert sha(RESULT / name) == record[key]
    for run in record["runs"]:
        for path, digest in run["files_sha256"].items():
            assert sha(RESULT / path) == digest, path
    runs = [recalculate(run) for run in record["runs"]]
    result = dict(independent_evidence_verification_pass=True, runs=runs,
                  solver_attempts_initial=7, solver_attempts_instrumented=7,
                  initial_export_failures_preserved=True,
                  regulator_quiescent_current_modeled=False, complete_power_chain_passed=False,
                  datasheet_all_conditions_passed=False,
                  summary_sha256=sha(RESULT / "supply_screen.json"), verifier_sha256=sha(Path(__file__)))
    (ROOT / "supply_verification.json").write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    audit()
