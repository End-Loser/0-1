"""Independent fixture equations and factorial contrasts, not a full power gate."""
import hashlib
import json
from pathlib import Path
import re

import numpy as np
from scipy.integrate import cumulative_trapezoid

ROOT = Path(__file__).resolve().parent
HEADERS = ["time", "vin", "out", "cap", "en", "i_up", "i_load", "vyy", "vzz", "vxx",
           "vout_int", "en_ok", "vin_pre", "load_dis", "i_enable", "ctl"]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def control(t):
    return np.interp(t, [0, .0015, .001502, .005998, .006, .0085, .008502, .01],
                     [0, 0, 1, 1, 0, 0, 1, 1])


def recalculate(run):
    folder = ROOT / "results" / run["id"]
    if run["status"] == "blocked":
        partial = run.get("partial_waveform")
        details = None
        if partial:
            path = Path(partial["path"])
            assert sha(path) == partial["sha256"] and not partial["accepted"]
            rows = np.loadtxt(path, skiprows=1)
            assert rows.ndim == 2 and rows.shape[1] == len(HEADERS)
            assert rows[-1, 0] < run["finish_s"] - 1e-12
            details = dict(rows=len(rows), last_time_s=float(rows[-1, 0]),
                           accepted=False, path=str(path), sha256=partial["sha256"])
        log = (folder / "solver.log").read_text(encoding="utf-8")
        reason = ("Incomplete transient: timestep too small"
                  if "Timestep too small" in log else run["reason"])
        return dict(id=run["id"], status="blocked", waveform_accepted=False,
                    reason=reason, original_reason=run["reason"], partial_waveform=details), None
    path = folder / "wave.csv"
    with path.open(encoding="ascii") as stream:
        assert stream.readline().strip().split(",") == HEADERS == run["columns"]
    rows = np.loadtxt(path, skiprows=1, delimiter=",")
    assert rows.shape == (run["rows"], len(HEADERS))
    assert np.isfinite(rows).all() and np.all(np.diff(rows[:, 0]) > 0)
    assert abs(rows[-1, 0] - .01) < 1e-12
    v = dict(zip(HEADERS, rows.T))
    t = v["time"]
    window = (t >= .0097) & (t <= .0099)
    assert window.sum() > 100
    error = float(abs(v["out"][window] - 3.3).max())
    assert abs(error - run["output_error_V"]) < 1e-12
    status = "pass" if error < .0495 else "fail"
    assert status == run["status"]
    net = (folder / "test.cir").read_text(encoding="ascii")
    for line in ("RS upstream vin .05", "RESR out cap .05", "COUT cap 0 1u", "CIN vin 0 1u",
                 f".options method={run['method']} maxord=2"):
        assert line in net
    assert ".ic" not in net.lower() and "uic" not in net.lower()
    assert len(re.findall(r"^BLOAD ", net, re.M)) == 1
    expected_load = control(t) * (v["out"] / 330 if run["load"] == "resistive" else .01)
    load_error = float(abs(v["i_load"] - expected_load).max())
    control_error = float(abs(v["ctl"] - control(t)).max())
    source_error = float(abs((5 - v["vin"]) / .05 + v["i_up"]).max())
    if run["enable"] == "dc":
        expected_enable = np.full(len(t), 3.3)
    else:
        expected_enable = np.interp(t, [0, .0001, .000101, .006, .006001, .007, .007001, .01],
                                    [0, 0, 3.3, 3.3, 0, 0, 3.3, 3.3])
    enable_error = float(abs(v["en"] - expected_enable).max())
    algebraic_passed = max(load_error, control_error, enable_error) < 1e-9 and source_error < 1e-6
    cap_current = (v["out"] - v["cap"]) / .05
    integral = cumulative_trapezoid(cap_current, t, initial=0)
    charge_error = integral - 1e-6 * (v["cap"] - v["cap"][0])
    scale = max(1e-6 * float(abs(v["cap"]).max()), 1e-30)
    charge_relative = float(abs(charge_error).max()) / scale
    residual = float(abs(cap_current[window] -
                         1e-6 * np.gradient(v["cap"], t, edge_order=2)[window]).max())
    off = (t >= .0065) & (t <= .0069)
    log = (folder / "solver.log").read_text(encoding="utf-8")
    conductance = re.search(r"^\s*conductance\s+(\S+)\s*$", log, re.M | re.I)
    return dict(id=run["id"], status=status, waveform_accepted=True, output_error_V=error,
                terminal_output_range_V=[float(v["out"][window].min()), float(v["out"][window].max())],
                output_min_V=float(v["out"].min()), output_max_V=float(v["out"].max()),
                negative_output_samples=int((v["out"] < 0).sum()),
                nonnegative_output_all_samples=bool((v["out"] >= 0).all()),
                ideal_current_fixture_not_startup_safety=run["load"] == "current",
                algebraic_errors=dict(load_A=load_error, upstream_resistor_A=source_error,
                                      load_control_V=control_error, enable_V=enable_error),
                algebraic_passed=algebraic_passed,
                algebraic_limits=dict(load_A=1e-9, upstream_resistor_A=1e-6,
                                      load_control_V=1e-9, enable_V=1e-9),
                output_capacitor=dict(charge_relative_error=charge_relative, window_kcl_A=residual,
                                      accounting_passed=charge_relative < .01 and residual < 1e-6,
                                      initial_energy_J=float(.5e-6 * v["cap"][0] ** 2),
                                      final_energy_J=float(.5e-6 * v["cap"][-1] ** 2),
                                      energy_change_J=float(.5e-6 * (v["cap"][-1] ** 2 - v["cap"][0] ** 2))),
                internal_output_terminal_range_V=[float(v["vout_int"][window].min()), float(v["vout_int"][window].max())],
                discharge_control_terminal_range_V=[float(v["load_dis"][window].min()), float(v["load_dis"][window].max())],
                reported_final_switch_conductance_S=float(conductance[1]) if conductance else None,
                enable_off_max_abs_output_V=float(abs(v["out"][off]).max()) if run["enable"] == "cycle" else None,
                source_energies_J=dict(upstream=float(np.trapz(-5 * v["i_up"], t)),
                                       enable=float(np.trapz(-v["en"] * v["i_enable"], t))),
                output_load_absorbed_energy_J=float(np.trapz(v["out"] * v["i_load"], t)),
                energy_scope="individual_channels_no_full_system_sum; internal_quiescent_current_not_modeled",
                internal_output_current_independently_measured=False), rows


def audit():
    summary = json.loads((ROOT / "summary.json").read_text(encoding="utf-8"))
    declared = json.loads((ROOT / "declared_configurations.json").read_text(encoding="utf-8"))
    expected_ids = {f"{load}_{enable}_{method}" for load in ("resistive", "current")
                    for enable in ("dc", "cycle") for method in ("gear", "trap")}
    assert {r["id"] for r in summary["runs"]} == expected_ids and len(declared) == 8
    assert sha(ROOT / "run_load_history.py") == summary["runner_sha256"]
    assert sha(ROOT / "declared_configurations.json") == summary["declared_configurations_sha256"]
    for group in ("original_files_sha256", "previous_evidence_sha256"):
        for path, digest in summary[group].items():
            assert sha(Path(path)) == digest, path
    records, waves = [], {}
    for config, run in zip(declared, summary["runs"]):
        assert all(run[k] == value for k, value in config.items())
        for path, digest in run["files_sha256"].items():
            assert sha(ROOT / path) == digest, path
        record, rows = recalculate(run)
        records.append(record)
        if rows is not None:
            waves[run["id"]] = rows
    contrasts = []
    for load in ("resistive", "current"):
        for enable in ("dc", "cycle"):
            first, second = f"{load}_{enable}_gear", f"{load}_{enable}_trap"
            if first not in waves or second not in waves:
                contrasts.append(dict(first=first, second=second, complete=False))
                continue
            a, b = waves[first], waves[second]
            mask = (a[:, 0] >= .0097) & (a[:, 0] <= .0099)
            delta = abs(a[mask, 2] - np.interp(a[mask, 0], b[:, 0], b[:, 2]))
            contrasts.append(dict(first=first, second=second, complete=True,
                                  terminal_output_method_difference_V=float(delta.max()),
                                  scope="diagnostic_only_not_ns_conditioner_convergence"))
    output = dict(runs=records, method_contrasts=contrasts, evidence_integrity_passed=True,
                  complete_waveforms=len(waves), terminal_output_pass_count=sum(r["status"] == "pass" for r in records),
                  startup_safety_passed=False, full_power_passed=False, full_joint_passed=False,
                  supported_pspice_verified=False, root_cause_proven=False,
                  charge_normalization="C_times_max_absolute_cap_voltage",
                  summary_sha256=sha(ROOT / "summary.json"), verifier_sha256=sha(Path(__file__)))
    (ROOT / "verification.json").write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    for record in records:
        print(record["id"], record["status"], record.get("output_min_V"),
              record.get("reported_final_switch_conductance_S"))
    return output


if __name__ == "__main__":
    audit()
