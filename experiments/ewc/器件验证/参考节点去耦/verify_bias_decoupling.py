"""Independent measurement and added-capacitor current/charge audit."""
import csv
import hashlib
import importlib.util
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LINKED = ROOT.parent / "\u771f\u5b9e\u53c2\u8003\u4f9b\u7535\u8054\u63a5"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_rows(run):
    with (ROOT / "results" / run["id"] / "wave.csv").open(encoding="ascii", newline="") as stream:
        reader = csv.reader(stream)
        assert len(next(reader)) == 33
        rows = [tuple(map(float, row)) for row in reader]
    assert len(rows) == run["rows"] and all(len(r) == 33 and all(math.isfinite(v) for v in r) for r in rows)
    assert all(a[0] < b[0] for a, b in zip(rows, rows[1:])) and rows[-1][0] >= 1e-6-1e-12
    return rows


def recalculate(run):
    rows = read_rows(run)
    oracle = load("independent_linked_oracle", LINKED / "verify_linked_reference.py")
    oracle.ROOT = ROOT
    oracle.read_rows = lambda record: [r[:29] for r in rows]
    result = oracle.recalculate(run)
    _, derivative = oracle.helpers()
    current_max = max(abs(r[c]) for r in rows for c in (29, 30, 31))
    assert abs(current_max-run["measurements"]["buffer_output_max_A"]) < 1e-12
    capacitance = run["bias_cap_F"]
    nodes = [(15, 29), (16, 30), (17, 31)]
    residuals, charge_errors = [], []
    for node, output in nodes:
        voltage = [r[node] for r in rows]
        loads = []
        for r in rows:
            if node == 15:
                current = r[32]
            else:
                src, amp = (2, 4) if node == 16 else (3, 5)
                divider = (r[src]/100+r[node]/100+r[amp]/950)/(2/100+1/950)
                current = (r[node]-divider)/100
            loads.append(current)
        max_kcl, integral, max_error = 0, 0, 0
        for i, r in enumerate(rows):
            if any(t+120e-9 <= r[0] <= t+170e-9 for t in (200e-9, 400e-9, 600e-9, 800e-9)):
                max_kcl = max(max_kcl, abs(r[output]-loads[i]-capacitance*derivative(rows, voltage, i)))
            if i:
                a = rows[i-1]
                integral += (r[0]-a[0])*(r[output]-loads[i]+a[output]-loads[i-1])/2
                expected = capacitance*(r[node]-rows[0][node])
                max_error = max(max_error, abs(expected-integral))
        residuals.append(max_kcl)
        charge_errors.append(max_error/(capacitance*max(map(abs, voltage))))
    result.update(buffer_output_max_A=current_max, bias_cap_kcl_A=residuals,
                  bias_cap_charge_relative_error=charge_errors,
                  capacitor_accounting_passed=max(residuals) < 1e-6 and max(charge_errors) < .01)
    return result


def audit():
    summary = json.loads((ROOT / "summary.json").read_text(encoding="utf-8"))
    for key in ("original_files_sha256", "previous_device_evidence_sha256"):
        for path, digest in summary[key].items():
            assert sha(Path(path)) == digest, path
    results = []
    for run in summary["runs"]:
        for path, digest in run["files"].items():
            assert sha(ROOT / path) == digest, path
        if "rows" in run:
            results.append(recalculate(run))
        else:
            assert run["status"] == "blocked"
            results.append(dict(id=run["id"], status="blocked", reason=run["reason"]))
    selected = [r for r in results if r["id"] in ("bias_1000pF", "bias_1000pF_5ps")]
    refinement = dict(passed=False, reason="two complete four-edge waveforms required")
    if len(selected) == 2 and all("events" in r for r in selected):
        changes = [abs(a["total_delay_s"]-b["total_delay_s"])
                   for a, b in zip(selected[0]["events"], selected[1]["events"])
                   if a["total_delay_s"] is not None and b["total_delay_s"] is not None]
        refinement = dict(delay_differences_s=changes, passed=len(changes) == 4 and max(changes) < 20e-12)
    output = dict(independent_evidence_verification_pass=True, runs=results, refinement=refinement,
                  full_G4_G5_passed=False, full_power_passed=False, startup_passed=False,
                  full_joint_passed=False, summary_sha256=sha(ROOT / "summary.json"),
                  verifier_sha256=sha(Path(__file__)))
    (ROOT / "verification.json").write_text(json.dumps(output, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(output, indent=2))
    return output


if __name__ == "__main__":
    audit()
