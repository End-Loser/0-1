"""Independent node KCL includes the measured buffer feedback-input branch."""
import csv
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
LINKED = ROOT.parent / "\u771f\u5b9e\u53c2\u8003\u4f9b\u7535\u8054\u63a5"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_oracle():
    spec = importlib.util.spec_from_file_location("linked_oracle", LINKED / "verify_linked_reference.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = ROOT
    return module


def read_rows(run):
    with (ROOT / "results" / run["id"] / "wave.csv").open(encoding="ascii", newline="") as stream:
        reader = csv.reader(stream)
        assert len(next(reader)) == 36
        rows = [tuple(map(float, row)) for row in reader]
    assert len(rows) == run["rows"]
    assert all(len(r) == 36 and all(math.isfinite(v) for v in r) for r in rows)
    assert all(a[0] < b[0] for a, b in zip(rows, rows[1:]))
    assert rows[-1][0] >= 1e-6-1e-12
    return rows


def recalculate(run):
    rows = read_rows(run)
    oracle = load_oracle()
    oracle.read_rows = lambda record: [r[:29] for r in rows]
    result = oracle.recalculate(run)
    _, derivative = oracle.helpers()
    cap = run["bias_cap_F"]
    kcl, charge, omitted = [], [], []
    for node, output, feedback in ((15, 29, 33), (16, 30, 34), (17, 31, 35)):
        voltage = [r[node] for r in rows]
        loads = []
        for r in rows:
            if node == 15:
                load = r[32]
            else:
                src, amp = (2, 4) if node == 16 else (3, 5)
                divider = (r[src]/100+r[node]/100+r[amp]/950)/(2/100+1/950)
                load = (r[node]-divider)/100
            loads.append(load)
        integral, max_charge, max_kcl, max_omitted = 0., 0., 0., 0.
        for i, r in enumerate(rows):
            if any(t+120e-9 <= r[0] <= t+170e-9 for t in (200e-9, 400e-9, 600e-9, 800e-9)):
                residual = r[output]-loads[i]-cap*derivative(rows, voltage, i)
                max_omitted = max(max_omitted, abs(residual))
                max_kcl = max(max_kcl, abs(residual-r[feedback]))
            if i:
                a = rows[i-1]
                integral += (r[0]-a[0]) * (
                    r[output]-loads[i]-r[feedback]+a[output]-loads[i-1]-a[feedback])/2
                max_charge = max(max_charge, abs(cap*(r[node]-rows[0][node])-integral))
        kcl.append(max_kcl)
        omitted.append(max_omitted)
        charge.append(max_charge/(cap*max(map(abs, voltage))))
    outmax = max(abs(r[c]) for r in rows for c in (29, 30, 31))
    fbmax = max(abs(r[c]) for r in rows for c in (33, 34, 35))
    assert abs(outmax-run["measurements"]["buffer_output_max_A"]) < 1e-12
    assert abs(fbmax-run["measurements"]["feedback_input_max_A"]) < 1e-12
    windows = [r for r in rows if any(
        t+120e-9 <= r[0] <= t+170e-9 for t in (200e-9, 400e-9, 600e-9, 800e-9))]
    # Diagnostic only: the acceptance threshold remains the frozen nominal value.
    reference_diagnostic = dict(
        acceptance_threshold_unchanged=True, scope="not_an_acceptance_check",
        reference_threshold_deviation_V=max(
            abs(r[17]-r[16]-run["threshold_V"]) for r in windows),
        measured_reference_tracking_error_V=max(
            abs(r[8]-r[9]-(500/1044)*(r[1]-(r[17]-r[16]))) for r in windows))
    result.update(bias_cap_kcl_A=kcl, bias_cap_charge_relative_error=charge,
                  omitted_feedback_kcl_A=omitted, feedback_input_max_A=fbmax,
                  buffer_output_max_A=outmax,
                  reference_diagnostic=reference_diagnostic,
                  capacitor_accounting_passed=max(kcl) < 1e-6 and max(charge) < .01)
    return result


def audit():
    summary = json.loads((ROOT / "summary.json").read_text(encoding="utf-8"))
    for key in ("original_files_sha256", "previous_evidence_sha256"):
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
            log = (ROOT / "results" / run["id"] / "solver.log").read_text(encoding="utf-8")
            stopped = re.search(r"Timestep too small; time = ([0-9.eE+-]+)", log)
            reason = run["reason"] or (
                "Incomplete transient: timestep too small" if stopped else
                "No validated waveform; see original solver log")
            blocked = dict(id=run["id"], status="blocked", reason=reason,
                           original_reason=run["reason"])
            if stopped:
                blocked["solver_stopped_at_s"] = float(stopped.group(1))
            partial = Path(summary["cache"]) / run["id"] / "wave.txt"
            if partial.exists():
                last, count = "", 0
                with partial.open(encoding="ascii") as stream:
                    next(stream)
                    for line in stream:
                        if line.strip():
                            last, count = line, count+1
                blocked["partial_waveform"] = dict(
                    path=str(partial), sha256=sha(partial), rows=count,
                    last_time_s=float(last.split()[0]), accepted=False)
            results.append(blocked)
    selected = {r["id"]: r for r in results}
    a, b = (selected.get(key, {}) for key in ("bias_1000pF", "bias_1000pF_5ps"))
    changes = [abs(x["total_delay_s"]-y["total_delay_s"])
               for x, y in zip(a.get("events", []), b.get("events", []))
               if x["total_delay_s"] is not None and y["total_delay_s"] is not None]
    output = dict(independent_evidence_verification_pass=True, runs=results,
                  refinement=dict(delay_differences_s=changes,
                                  passed=len(changes) == 4 and max(changes) < 20e-12),
                  full_G4_G5_passed=False, full_power_passed=False, startup_passed=False,
                  full_joint_passed=False, summary_sha256=sha(ROOT / "summary.json"),
                  verifier_sha256=sha(Path(__file__)))
    (ROOT / "verification.json").write_text(json.dumps(output, indent=2)+"\n", encoding="utf-8")
    for run in results:
        print(run["id"], run.get("capacitor_accounting_passed", run["status"]),
              run.get("bias_cap_kcl_A"), run.get("bias_cap_charge_relative_error"))
    return output


if __name__ == "__main__":
    audit()
