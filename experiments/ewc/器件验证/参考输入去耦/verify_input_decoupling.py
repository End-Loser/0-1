"""Independently account for bias divider, positive input and added input capacitance."""
import csv
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
PRIOR = ROOT.parent / "\u53c2\u8003\u53cd\u9988\u7535\u6d41\u590d\u6838"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_oracle():
    spec = importlib.util.spec_from_file_location("feedback_oracle", PRIOR / "verify_feedback_audit.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = ROOT
    return module


def read_rows(run):
    with (ROOT / "results" / run["id"] / "wave.csv").open(encoding="ascii", newline="") as stream:
        reader = csv.reader(stream)
        assert len(next(reader)) == 42
        rows = [tuple(map(float, row)) for row in reader]
    assert len(rows) == run["rows"] and all(
        len(r) == 42 and all(math.isfinite(v) for v in r) for r in rows)
    assert all(a[0] < b[0] for a, b in zip(rows, rows[1:]))
    assert rows[-1][0] >= 1e-6-1e-12
    return rows


def resistor_values(net, i):
    top = re.findall(rf"(?m)^RDT{i} vref b{i} ([0-9.eE+-]+)$", net)
    bottom = re.findall(rf"(?m)^RDB{i} b{i} 0 ([0-9.eE+-]+)$", net)
    assert len(top) == len(bottom) == 1
    return float(top[0]), float(bottom[0])


def recalculate(run):
    rows = read_rows(run)
    oracle = load_oracle()
    oracle.read_rows = lambda record: [r[:36] for r in rows]
    result = oracle.recalculate(run)
    _, derivative = oracle.load_oracle().helpers()
    net = (ROOT / "results" / run["id"] / "test.cir").read_text(encoding="ascii")
    cap = run["input_cap_F"]
    kcl, charges, setpoints, tracking, rc = [], [], [], [], []
    windows = [i for i, r in enumerate(rows) if any(
        e+120e-9 <= r[0] <= e+170e-9 for e in (200e-9, 400e-9, 600e-9, 800e-9))]
    for i, (node, current, out) in enumerate(((36, 39, 15), (37, 40, 16), (38, 41, 17)), 1):
        top, bottom = resistor_values(net, i)
        values = [r[node] for r in rows]
        branch = [(r[18]-r[node])/top-r[node]/bottom-r[current] for r in rows]
        kcl.append(max(abs(branch[j]-cap*derivative(rows, values, j)) for j in windows))
        integral, max_error = 0., 0.
        for j in range(1, len(rows)):
            integral += (rows[j][0]-rows[j-1][0])*(branch[j]+branch[j-1])/2
            max_error = max(max_error, abs(cap*(values[j]-values[0])-integral))
        charges.append(max_error/(cap*max(map(abs, values))) if cap else None)
        setpoints.append(max(abs(rows[j][node]-rows[j][18]*bottom/(top+bottom)) for j in windows))
        tracking.append(max(abs(rows[j][out]-rows[j][node]) for j in windows))
        rc.append(cap/(1/top+1/bottom))
    posmax = max(abs(r[c]) for r in rows for c in (39, 40, 41))
    assert abs(posmax-run["measurements"]["positive_input_max_A"]) < 1e-12
    passed = max(kcl) < 1e-6 and (not cap or max(charges) < .01)
    result.update(input_cap_kcl_A=kcl, input_cap_charge_relative_error=charges,
                  input_cap_accounting_passed=passed, positive_input_max_A=posmax,
                  input_setpoint_error_V=setpoints, buffer_tracking_error_V=tracking,
                  input_divider_RC_s=rc, initial_operating_point_not_startup=True,
                  initial_input_cap_energy_J=.5*cap*sum(rows[0][c]**2 for c in (36, 37, 38)),
                  input_cap_energy_change_J=.5*cap*sum(
                      rows[-1][c]**2-rows[0][c]**2 for c in (36, 37, 38)),
                  input_charge_normalization="C times maximum absolute node voltage; not ripple-relative")
    return result


def audit():
    summary = json.loads((ROOT / "summary.json").read_text(encoding="utf-8"))
    for key in ("original_files_sha256", "previous_evidence_sha256"):
        for path, digest in summary[key].items():
            assert sha(Path(path)) == digest, path
    assert sha(ROOT / "declared_configurations.json") == summary["declared_configurations_sha256"]
    declared = json.loads((ROOT / "declared_configurations.json").read_text(encoding="utf-8"))
    results = []
    for config, run in zip(declared, summary["runs"]):
        assert all(config[key] == run[key] for key in config)
        for path, digest in run["files"].items():
            assert sha(ROOT / path) == digest, path
        if "rows" in run:
            results.append(recalculate(run))
        else:
            assert run["status"] == "blocked"
            blocked = dict(id=run["id"], status="blocked", reason=run["reason"])
            if "partial_waveform" in run:
                partial = run["partial_waveform"]
                assert sha(Path(partial["path"])) == partial["sha256"]
                assert not partial["accepted"]
                blocked["partial_waveform"] = partial
                blocked["last_time_s"] = run.get("observed_last_time_s")
            results.append(blocked)
    assert len(results) == len(declared) == 9
    by_id = {r["id"]: r for r in results}
    refinement = []
    for pf in (1000, 10000):
        a, b = (by_id[f"input_{pf}pF{suffix}"] for suffix in ("", "_5ps"))
        differences = [abs(x["total_delay_s"]-y["total_delay_s"])
                       for x, y in zip(a.get("events", []), b.get("events", []))
                       if x["total_delay_s"] is not None and y["total_delay_s"] is not None]
        refinement.append(dict(input_cap_pF=pf, delay_differences_s=differences,
                               passed=len(differences) == 4 and max(differences) < 20e-12,
                               local_screens_passed=a["status"] == b["status"] == "pass"))
    output = dict(independent_evidence_verification_pass=True, runs=results, refinement=refinement,
                  full_G4_G5_passed=False, full_power_passed=False, startup_passed=False,
                  full_joint_passed=False, summary_sha256=sha(ROOT / "summary.json"),
                  verifier_sha256=sha(Path(__file__)))
    (ROOT / "verification.json").write_text(json.dumps(output, indent=2)+"\n", encoding="utf-8")
    for run in results:
        print(run["id"], run["status"], run.get("input_cap_accounting_passed"),
              run.get("input_setpoint_error_V"), run.get("buffer_tracking_error_V"))
    print("Refinement", refinement)
    return output


if __name__ == "__main__":
    audit()
