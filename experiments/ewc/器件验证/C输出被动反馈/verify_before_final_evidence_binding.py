"""Independent carry-pin, reference KCL, outcome and source-work inspection."""
import hashlib
import json
from pathlib import Path
import re

import numpy as np

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "C输出被动反馈"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resistor(net, name, start, end):
    matches = re.findall(
        rf"(?m)^{name} {start} {end} ([0-9.eE+-]+)$", net)
    assert len(matches) == 1
    return float(matches[0])


def inspect_case(run):
    folder = OUT / run["id"]
    for name, digest in run["files_sha256"].items():
        assert sha(folder / name) == digest, name
    assert json.loads((folder / "run.json").read_bytes()) == run
    net = (folder / "test.cir").read_text(encoding="ascii")
    assert "VCFEED carry cfeed 0\nRCFEED cfeed im2 32750" in net
    assert net.count("XC1 ") == net.count("XC2 ") == 1
    assert " EWC_CMP" in net and " EWC_REF" in net and " EWC_BUFFER" in net
    behavior = [line for line in net.splitlines() if line.startswith("B")]
    assert len(behavior) == 4
    assert all("v(raw)" in line and "v(scm)" in line for line in behavior)
    assert all("carry" not in line and "sum" not in line for line in behavior)
    assert ".ic " not in net.lower() and "uic" not in net.lower()
    if run["real_rails"]:
        assert "XR5 upstream en r5 EWC_RAIL PARAMS: LEVEL=5" in net
        assert "V5 rail5 0 5" not in net and "V3 rail3 0 3.3" not in net
    else:
        assert "V5 rail5 0 5" in net and "V3 rail3 0 3.3" in net
    if not (folder / "full_vectors.npz").exists():
        return dict(id=run["id"], status="missing_waveform",
                    reason=run.get("reason"), local_passed=False)
    with np.load(folder / "full_vectors.npz", allow_pickle=False) as archive:
        data = archive["data"]
        columns = archive["columns"].tolist()
    assert len(columns) == len(set(columns)) == data.shape[1]
    assert columns[0] == "time" and np.isfinite(data).all()
    assert len(data) == run["rows"]
    t = data[:, 0]
    assert np.all(np.diff(t) > 0) and t[0] == 0
    assert t[-1] == run["observed_last_time_s"]
    assert sha(Path(run["raw_wave"]["path"])) == run["raw_wave"]["sha256"]
    d = {name: data[:, i] for i, name in enumerate(columns)}
    complete = bool(t[-1] >= run["duration_s"] - 1e-12 and run["returncode"] == 0)
    if not complete:
        assert run["status"] == "failed_preserve_partial"
        return dict(id=run["id"], status="incomplete",
                    last_time_s=float(t[-1]), local_passed=False)
    assert run["status"] == "solver_complete_unverified"
    raw_current = sum((d[name] - d["raw"]) / 300 for name in ("a", "b", "cin"))
    raw_kcl = float(np.max(np.abs(raw_current)))
    input_source_kcl = [
        float(np.max(np.abs(d["i_" + current] + (d[node] - d["raw"]) / 300)))
        for node, current in (("a", "a"), ("b", "b"), ("cin", "ci"))]
    rfeed = resistor(net, "RCFEED", "cfeed", "im2")
    feedback_error = float(np.max(np.abs(
        d["i_c_feedback"] - (d["carry"] - d["im2"]) / rfeed)))
    assert raw_kcl < 1e-9 and max(input_source_kcl) < 1e-9
    assert feedback_error < 1e-9
    window_mask = np.zeros(len(t), dtype=bool)
    windows = []
    gain = .5 * 1000 / 1044
    for index, state in enumerate(run["states"]):
        start = index * run["hold_s"]
        mask = (t >= start + 120e-9) & (t <= start + 170e-9)
        assert np.count_nonzero(mask) >= 100
        window_mask |= mask
        bits = [(state >> shift) & 1 for shift in (2, 1, 0)]
        assert all(np.max(np.abs(d[name][mask] - .3 * bit)) < 1e-9
                   for name, bit in zip(("a", "b", "cin"), bits))
        count = sum(bits)
        carry, summation = count // 2, count % 2
        # Truth is used only here, after solving. Neither netlist nor runtime uses it.
        target_c = gain * (.1 * count - .15)
        target_s = gain * (.1 * count - .05 - .2 * carry)
        error_c = float(np.max(np.abs(d["cp1"][mask] - d["cn1"][mask] - target_c)))
        error_s = float(np.max(np.abs(d["cp2"][mask] - d["cn2"][mask] - target_s)))
        levels = []
        for node, expected in (("carry", carry), ("sum", summation)):
            levels.append(bool(np.min(d[node][mask]) > .7 * 3.3 if expected
                               else np.max(d[node][mask]) < .3 * 3.3))
        windows.append(dict(
            index=index, input_state=state, expected_carry=carry,
            expected_sum=summation, start_s=start + 120e-9, end_s=start + 170e-9,
            carry_amplitude_error_V=error_c, sum_amplitude_error_V=error_s,
            carry_range_V=[float(d["carry"][mask].min()), float(d["carry"][mask].max())],
            sum_range_V=[float(d["sum"][mask].min()), float(d["sum"][mask].max())],
            logic_passed=all(levels), amplitude_passed=error_c < .001 and error_s < .001))
    input_kcl, input_charge = [], []
    bias_kcl, bias_charge = [], []
    for i in range(1, 6):
        top = resistor(net, f"RDT{i}", "vref", f"b{i}")
        bottom = resistor(net, f"RDB{i}", f"b{i}", "0")
        voltage = d[f"b{i}"]
        current = (d["vref"] - voltage) / top - voltage / bottom - d[f"i_bin{i}"]
        derivative = np.gradient(voltage, t)
        input_kcl.append(float(np.max(np.abs(current[window_mask] -
                                             1e-9 * derivative[window_mask]))))
        integral = np.r_[0., np.cumsum(np.diff(t) * (current[:-1] + current[1:]) / 2)]
        input_charge.append(float(np.max(np.abs(
            1e-9 * (voltage - voltage[0]) - integral)) /
            (1e-9 * max(np.max(np.abs(voltage)), 1e-20))))
        if i == 1:
            # Shared FDA common-mode pin currents were not individually probed.
            continue
        stage, pin = (1 if i < 4 else 2), ("dp" if i in (2, 4) else "dn")
        bias = d[f"bias{i}"]
        external = (bias - d[f"{pin}{stage}"]) / 100
        net_current = d[f"i_bout{i}"] - d[f"i_fb{i}"] - external
        derivative = np.gradient(bias, t)
        bias_kcl.append(float(np.max(np.abs(
            net_current[window_mask] - 100e-12 * derivative[window_mask]))))
        integral = np.r_[0., np.cumsum(
            np.diff(t) * (net_current[:-1] + net_current[1:]) / 2)]
        bias_charge.append(float(np.max(np.abs(
            100e-12 * (bias - bias[0]) - integral)) /
            (100e-12 * max(np.max(np.abs(bias)), 1e-20))))
    pin_ok = all(np.min(d[f"{pin}{stage}"]) >= -.2 and
                 np.max(d[f"{pin}{stage}"]) <= 3.8
                 for stage in (1, 2) for pin in ("ip", "im"))
    comparator_ok = all(np.min(d[f"{pin}{stage}"]) >= -.2 and
                        np.max(d[f"{pin}{stage}"]) <= 3.1
                        for stage in (1, 2) for pin in ("cp", "cn"))
    output_ok = all(np.min(d[f"{pin}{stage}"]) >= .23 and
                    np.max(d[f"{pin}{stage}"]) <= 4.77
                    for stage in (1, 2) for pin in ("op", "om"))
    output_current_max = 0.
    for stage in (1, 2):
        for out, load, feedback in (("op", "cp", "im"), ("om", "cn", "ip")):
            voltage = d[f"{out}{stage}"]
            feedback_voltage = voltage - d[f"{feedback}{stage}"]
            current = (feedback_voltage / 1000 +
                       (voltage - d[f"{load}{stage}"]) / 22 +
                       1e-12 * np.gradient(feedback_voltage, t))
            output_current_max = max(output_current_max, float(np.max(np.abs(current))))
    rail_ok = bool(np.max(np.abs(d["rail5"] - 5)) < .075 and
                   np.max(np.abs(d["rail3"] - 3.3)) < .0495)
    reference_ok = bool(np.max(np.abs(d["vref"] - 5)) < .005)
    cm_error = max(float(np.max(np.abs(
        (d[f"cp{stage}"][window_mask] + d[f"cn{stage}"][window_mask]) / 2 - 1.65)))
        for stage in (1, 2))
    capacitor_ok = (max(input_kcl + bias_kcl) < 1e-6 and
                    max(input_charge + bias_charge) < .01)
    checks = dict(eight_states_present=set(run["states"]) == set(range(8)),
                  actual_C_resistor_current=feedback_error < 1e-9,
                  logic=all(w["logic_passed"] for w in windows),
                  amplitude=all(w["amplitude_passed"] for w in windows),
                  common_mode=cm_error < .005, input_pin_range=bool(pin_ok),
                  fda_output_range=bool(output_ok),
                  output_continuous_current=output_current_max < .020,
                  comparator_pin_range=bool(comparator_ok), rails=rail_ok,
                  reference=reference_ok, observed_capacitor_accounting=capacitor_ok)
    source_work = {}
    powers = {"upstream": -d["upstream"] * d["i_up"]}
    for node, current in (("a", "a"), ("b", "b"), ("cin", "ci")):
        powers["input_" + node] = -d[node] * d["i_" + current]
    if not run["real_rails"]:
        powers["diagnostic_5V"] = -d["rail5"] * d["i_5"]
        powers["diagnostic_3V3"] = -d["rail3"] * d["i_3"]
    for stage in (1, 2):
        powers[f"synthetic_split_p{stage}"] = (
            (3.2 + d["raw"] / 2) * d[f"i_sp{stage}"])
        powers[f"synthetic_split_n{stage}"] = (
            (3.2 - d["raw"] / 2) * d[f"i_sn{stage}"])
    for name, power in powers.items():
        source_work[name] = dict(net_J=float(np.trapz(power, t)),
                                 delivered_J=float(np.trapz(np.maximum(power, 0), t)),
                                 absorbed_J=float(np.trapz(np.maximum(-power, 0), t)))
    transferred_power = d["carry"] * d["i_c_feedback"]
    feedback_heat = (d["carry"] - d["im2"]) * d["i_c_feedback"]
    assert np.min(feedback_heat) > -1e-12
    return dict(
        id=run["id"], status="local_pass" if all(checks.values()) else "local_fail",
        checks=checks, local_passed=all(checks.values()), windows=windows,
        raw_summing_kcl_A=raw_kcl, input_source_kcl_A=input_source_kcl,
        C_feedback_current_error_A=feedback_error,
        C_feedback_current_range_A=[float(d["i_c_feedback"].min()),
                                    float(d["i_c_feedback"].max())],
        common_mode_error_V=cm_error, input_cap_kcl_A=input_kcl,
        fda_output_current_max_A=output_current_max,
        input_cap_charge_relative_error=input_charge, bias_cap_kcl_A=bias_kcl,
        bias_cap_charge_relative_error=bias_charge,
        independent_primitive_source_work=source_work,
        feedback_port_transferred_net_J=float(np.trapz(transferred_power, t)),
        feedback_resistor_heat_J=float(np.trapz(feedback_heat, t)),
        feedback_transfer_is_not_extra_external_supply=True,
        unclosed_accounting=["Shared FDA common-mode pin current decomposition",
                            "REF output capacitor ESR node and full internal energy",
                            "Real LO/RF, control, register and calibration supply",
                            "TPS7A20 quiescent ground current and temperature"],
        initial_operating_point_not_cold_start=True,
        nominal_linear_margin_V=gain * .05, real_rails=run["real_rails"],
        last_time_s=float(t[-1]), G4_closed=False, G5_closed=False, G6_closed=False)


def audit():
    summary = json.loads((OUT / "summary.json").read_bytes())
    assert sha(ROOT / "run_c_output_feedback.py") == summary["runner_sha256"]
    running = json.loads((OUT / "running_source.json").read_bytes())
    assert running["runner_sha256"] == summary["runner_sha256"]
    assert running["original_files_sha256"] == summary["original_files_sha256"]
    assert sha(ROOT / "参考输入去耦" / "summary.json") == running["previous_summary_sha256"]
    assert all(sha(Path(p)) == digest
               for p, digest in summary["original_files_sha256"].items())
    assert sha(OUT / "declared_configurations.json") == summary["configuration_sha256"]
    configs = json.loads((OUT / "declared_configurations.json").read_bytes())
    assert len(configs) == len(summary["runs"]) == 3
    assert [c["step_s"] for c in configs] == [10e-12, 5e-12, 10e-12]
    assert [c["real_rails"] for c in configs] == [False, False, True]
    for config, run in zip(configs, summary["runs"]):
        assert all(run[k] == value for k, value in config.items())
        assert config["states"] == [0, 1, 2, 3, 4, 5, 6, 7, 0]
        assert config["duration_s"] == 9 * 200e-9
        assert config["feedback_resistance_ohm"] == 32750
    cases = [inspect_case(run) for run in summary["runs"]]
    pair = dict(complete=False, passed=False)
    if all(cases[i]["status"] in ("local_pass", "local_fail") for i in (0, 1)):
        differences = []
        for run in summary["runs"][:2]:
            with np.load(OUT / run["id"] / "full_vectors.npz", allow_pickle=False) as ar:
                cols = ar["columns"].tolist()
                a = ar["data"]
                differences.append((a[:, 0], a[:, [cols.index(x) for x in (
                    "cp1", "cn1", "cp2", "cn2", "carry", "sum")]]))
        times = differences[0][0]
        x = np.column_stack([np.interp(times, differences[0][0], differences[0][1][:, i])
                             for i in range(6)])
        y = np.column_stack([np.interp(times, differences[1][0], differences[1][1][:, i])
                             for i in range(6)])
        edge_records = []
        for segment in range(1, 9):
            start, end = segment * 200e-9, (segment + 1) * 200e-9
            for signal in (4, 5):
                crossings = []
                for tt, vectors in differences:
                    yy = vectors[:, signal] - 1.65
                    selected = np.flatnonzero(
                        ((yy[:-1] < 0) & (yy[1:] >= 0) |
                         (yy[:-1] > 0) & (yy[1:] <= 0)) &
                        (tt[1:] >= start) & (tt[1:] <= end))
                    crossings.append([
                        float(tt[i] - yy[i] * (tt[i + 1] - tt[i]) / (yy[i + 1] - yy[i]))
                        for i in selected])
                matched = len(crossings[0]) == len(crossings[1])
                changes = [abs(a - b) for a, b in zip(*crossings)] if matched else []
                edge_records.append(dict(
                    segment=segment, signal="carry" if signal == 4 else "sum",
                    coarse_crossings_s=crossings[0], fine_crossings_s=crossings[1],
                    counts_match=matched, edge_differences_s=changes,
                    passed=matched and all(change < 20e-12 for change in changes)))
        pair = dict(complete=True, diagnostic_max_voltage_difference_V=float(np.max(np.abs(x - y))),
                    interpolation_scope="All coarse recorded times, no phase or time alignment",
                    edge_records=edge_records,
                    passed=all(e["passed"] for e in edge_records),
                    edge_convergence_validated=all(e["passed"] for e in edge_records))
    evidence = {str(p.relative_to(ROOT)): sha(p)
                for p in OUT.rglob("*") if p.is_file() and
                not p.name.startswith("verification_")}
    result = dict(
        scope="Actual C passive baseband preliminary evidence; not seven gates",
        cases=cases, diagnostic_time_pair=pair, evidence_sha256=evidence,
        verifier_sha256=sha(Path(__file__)), summary_sha256=sha(OUT / "summary.json"),
        full_64_transitions_present=False, actual_RF_feedback=False,
        full_power_passed=False, cold_start_passed=False,
        full_system_gates_closed=0, goal_complete=False)
    text = json.dumps(result, indent=2) + "\n"
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    target = OUT / f"verification_{digest[:12]}.json"
    if target.exists():
        assert target.read_text(encoding="utf-8") == text
    else:
        with target.open("x", encoding="utf-8") as stream:
            stream.write(text)
    print(json.dumps(dict(
        cases=[dict(id=c["id"], status=c["status"], checks=c.get("checks"),
                    max_sum_error_V=max((w["sum_amplitude_error_V"]
                                         for w in c.get("windows", [])), default=None))
               for c in cases],
        diagnostic_time_pair=pair, report=str(target),
        full_system_gates_closed=0, goal_complete=False), indent=2))
    return result


if __name__ == "__main__":
    audit()
