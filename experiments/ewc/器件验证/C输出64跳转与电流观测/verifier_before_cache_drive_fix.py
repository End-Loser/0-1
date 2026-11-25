"""Independent continuous-history coverage and observed external branch accounting."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys

import numpy as np

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "C输出64跳转与电流观测"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resistance(net, name):
    value = re.findall(rf"(?m)^{name} \S+ \S+ ([0-9.eE+-]+)$", net)
    assert len(value) == 1
    return float(value[0])


def load_case(run):
    folder = OUT / run["id"]
    assert json.loads((folder / "run.json").read_bytes()) == run
    assert all(sha(folder / p) == digest for p, digest in run["files_sha256"].items())
    if "raw_wave" in run:
        assert sha(Path(run["raw_wave"]["path"])) == run["raw_wave"]["sha256"]
    arrays, columns, previous = [], None, None
    for index, chunk in enumerate(run.get("chunks", [])):
        assert chunk["file"] == f"vectors_{index:05d}.npz"
        assert sha(folder / chunk["file"]) == chunk["sha256"]
        with np.load(folder / chunk["file"], allow_pickle=False) as ar:
            names, data = ar["columns"].tolist(), ar["data"]
        assert names[0] == "time" and len(set(names)) == len(names) == data.shape[1]
        assert columns is None or columns == names
        columns = names
        assert np.isfinite(data).all() and len(data) == chunk["rows"]
        assert data[0, 0] == chunk["first_time_s"] and data[-1, 0] == chunk["last_time_s"]
        assert previous is None or data[0, 0] > previous
        assert np.all(np.diff(data[:, 0]) > 0)
        previous = data[-1, 0]
        arrays.append(data)
    if not arrays:
        return None
    data = np.concatenate(arrays)
    assert len(data) == run["rows"] and data[0, 0] == 0
    assert data[-1, 0] == run["observed_last_time_s"]
    return columns, data


def branch_accounting(d, net, mask):
    t = d["time"]
    feedback = d["i_c_feedback"] - (d["carry"] - d["im2"]) / resistance(net, "RCFEED")
    raw = sum((d[n] - d["raw"]) / resistance(net, r)
              for n, r in (("a", "RA"), ("b", "RB"), ("cin", "RCI")))
    input_sources = [d["i_" + c] + (d[n] - d["raw"]) / resistance(net, r)
                     for n, c, r in (("a", "a", "RA"), ("b", "b", "RB"),
                                     ("cin", "ci", "RCI"))]
    bias_cap = d["i_bout1"] - d["i_fb1"] - d["i_cm1"] - d["i_cm2"]
    ref_load = sum((d["vref"] - d[f"b{i}"]) / resistance(net, f"RDT{i}")
                   for i in range(1, 6))
    ref_cap = (d["vref"] - d["cref"]) / resistance(net, "RREFESR")
    ref_kcl = d["i_refout"] - ref_load - ref_cap
    rail5 = d["i_5"] - d["i_f1"] - d["i_f2"]
    rail3 = d["i_3"] - d["i_c1"] - d["i_c2"] - sum(d[f"i_buf{i}"] for i in range(1, 6))
    def max_abs(v):
        return float(np.max(np.abs(v)))
    def cap_check(voltage, current, cap):
        integral = np.r_[0., np.cumsum(np.diff(t) * (current[1:] + current[:-1]) / 2)]
        charge = max_abs(cap * (voltage - voltage[0]) - integral)
        kcl = current - cap * np.gradient(voltage, t)
        return dict(
            window_kcl_A=max_abs(kcl[mask]) if mask.any() else None,
            charge_relative_error=charge / (cap * max(max_abs(voltage), 1e-20)),
            initial_energy_J=.5 * cap * float(voltage[0]**2),
            final_energy_J=.5 * cap * float(voltage[-1]**2))
    caps = {"shared_common_mode": cap_check(d["cm"], bias_cap, 100e-12),
            "reference_output": cap_check(d["cref"], ref_cap, 1e-6)}
    for i in range(1, 6):
        current = ((d["vref"] - d[f"b{i}"]) / resistance(net, f"RDT{i}") -
                   d[f"b{i}"] / resistance(net, f"RDB{i}") - d[f"i_bin{i}"])
        caps[f"input_bias_{i}"] = cap_check(d[f"b{i}"], current, 1e-9)
        if i == 1:
            continue
        stage, pin = (1 if i < 4 else 2), ("dp" if i in (2, 4) else "dn")
        current = (d[f"i_bout{i}"] - d[f"i_fb{i}"] -
                   (d[f"bias{i}"] - d[f"{pin}{stage}"]) / 100)
        caps[f"output_bias_{i}"] = cap_check(d[f"bias{i}"], current, 100e-12)
    errors = dict(C_feedback=max_abs(feedback), passive_raw=max_abs(raw),
                  source_inputs=max(map(max_abs, input_sources)),
                  reference_output=max_abs(ref_kcl), diagnostic_5V=max_abs(rail5),
                  diagnostic_3V3=max_abs(rail3))
    return dict(
        algebraic_kcl_A=errors, capacitor_checks=caps,
        algebraic_passed=all(v < 1e-9 for v in errors.values()),
        capacitor_passed=bool(mask.any() and all(
            v["window_kcl_A"] < 1e-6 and v["charge_relative_error"] < .01
            for v in caps.values())))


def inspect(run):
    folder = OUT / run["id"]
    net = (folder / "test.cir").read_text(encoding="ascii")
    assert "RCFEED cfeed im2 32750" in net
    for stage in (1, 2):
        assert f"VCM{stage} cm cm{stage} 0" in net
        assert f"om{stage} cm{stage} vs{stage} 0 EWC_FDA" in net
    assert "VROUT refout vref 0" in net
    assert "trimref refout EWC_REF" in net
    behavior = [line for line in net.splitlines() if line.startswith("B")]
    assert len(behavior) == 4 and all(
        "v(raw)" in line and "v(scm)" in line and
        "carry" not in line and "sum" not in line for line in behavior)
    assert ".ic " not in net.lower() and "uic" not in net.lower()
    vectors = load_case(run)
    if vectors is None:
        return dict(id=run["id"], status="no_valid_vectors", complete=False,
                    coverage64=False, local_passed=False, reason=run.get("reason"))
    columns, data = vectors
    d = {name: data[:, i] for i, name in enumerate(columns)}
    t = d["time"]
    complete = bool(run["status"] == "solver_complete_unverified" and
                    run.get("returncode") == 0 and t[-1] >= run["duration_s"] - 1e-12)
    windows, observed_pairs = [], []
    mask_all = np.zeros(len(t), dtype=bool)
    gain = .5 * 1000 / 1044
    for index, state in enumerate(run["states"]):
        start = index * run["hold_s"]
        if t[-1] < start + 170e-9:
            break
        mask = (t >= start + 120e-9) & (t <= start + 170e-9)
        assert np.count_nonzero(mask) >= 100
        mask_all |= mask
        bits = [(state >> shift) & 1 for shift in (2, 1, 0)]
        assert all(np.max(np.abs(d[name][mask] - .3 * bit)) < 1e-9
                   for name, bit in zip(("a", "b", "cin"), bits))
        number = sum(bits)
        expected_c, expected_s = number // 2, number % 2
        errors = [
            float(np.max(np.abs(d["cp1"][mask] - d["cn1"][mask] - gain * (.1 * number - .15)))),
            float(np.max(np.abs(d["cp2"][mask] - d["cn2"][mask] -
                                gain * (.1 * number - .05 - .2 * expected_c))))]
        level_ok = all(bool(d[name][mask].min() > 2.31 if bit
                            else d[name][mask].max() < .99)
                       for name, bit in (("carry", expected_c), ("sum", expected_s)))
        windows.append(dict(index=index, state=state, error_V=errors,
                            logic_passed=level_ok, amplitude_passed=max(errors) < .001))
        if index:
            observed_pairs.append((run["states"][index - 1], state))
    required = Counter({(a, b): 1 for a in range(8) for b in range(8)})
    coverage = complete and Counter(observed_pairs) == required
    branches = branch_accounting(d, net, mask_all)
    cm_error = max(float(np.max(np.abs(
        (d[f"cp{s}"][mask_all] + d[f"cn{s}"][mask_all]) / 2 - 1.65)))
        for s in (1, 2)) if mask_all.any() else None
    output_current = 0.
    for stage in (1, 2):
        for a, b, c in (("op", "cp", "im"), ("om", "cn", "ip")):
            dv = d[f"{a}{stage}"] - d[f"{c}{stage}"]
            current = dv / 1000 + (d[f"{a}{stage}"] - d[f"{b}{stage}"]) / 22
            current += 1e-12 * np.gradient(dv, t)
            output_current = max(output_current, float(np.max(np.abs(current))))
    checks = dict(
        full_declared_duration=complete, logic=bool(windows) and all(w["logic_passed"] for w in windows),
        amplitude=bool(windows) and all(w["amplitude_passed"] for w in windows),
        common_mode=cm_error is not None and cm_error < .005,
        FDA_inputs=all(d[f"{p}{s}"].min() >= -.2 and d[f"{p}{s}"].max() <= 3.8
                       for s in (1, 2) for p in ("ip", "im")),
        FDA_outputs=all(d[f"{p}{s}"].min() >= .23 and d[f"{p}{s}"].max() <= 4.77
                        for s in (1, 2) for p in ("op", "om")),
        comparator_inputs=all(d[f"{p}{s}"].min() >= -.2 and d[f"{p}{s}"].max() <= 3.1
                              for s in (1, 2) for p in ("cp", "cn")),
        output_current=output_current < .020,
        rails=bool(np.max(np.abs(d["rail5"] - 5)) < .075 and
                   np.max(np.abs(d["rail3"] - 3.3)) < .0495),
        reference=bool(np.max(np.abs(d["vref"] - 5)) < .005),
        algebraic_accounting=branches["algebraic_passed"],
        observed_capacitor_accounting=branches["capacitor_passed"])
    powers = {"upstream": -d["upstream"] * d["i_up"],
              "diagnostic_5V": -d["rail5"] * d["i_5"],
              "diagnostic_3V3": -d["rail3"] * d["i_3"]}
    for name, current in (("a", "a"), ("b", "b"), ("cin", "ci")):
        powers["input_" + name] = -d[name] * d["i_" + current]
    for stage in (1, 2):
        powers[f"synthetic_p{stage}"] = (3.2 + d["raw"] / 2) * d[f"i_sp{stage}"]
        powers[f"synthetic_n{stage}"] = (3.2 - d["raw"] / 2) * d[f"i_sn{stage}"]
    work = {name: dict(net_J=float(np.trapz(p, t)),
                       delivered_J=float(np.trapz(np.maximum(p, 0), t)),
                       absorbed_J=float(np.trapz(np.maximum(-p, 0), t)))
            for name, p in powers.items()}
    return dict(
        id=run["id"], status="local_pass" if all(checks.values()) else "local_fail_or_incomplete",
        checks=checks, local_passed=all(checks.values()), complete=complete,
        coverage64=bool(coverage), observed_pairs=[list(x) for x in observed_pairs],
        windows=windows, common_mode_error_V=cm_error, output_current_max_A=output_current,
        branch_accounting=branches, primitive_source_work=work,
        reference_output_terminal_transfer_J=float(np.trapz(d["vref"] * d["i_refout"], t)),
        C_feedback_terminal_transfer_J=float(np.trapz(d["carry"] * d["i_c_feedback"], t)),
        feedback_resistor_heat_J=float(np.trapz((d["carry"] - d["im2"])**2 / 32750, t)),
        internal_transfers_not_extra_external_supplies=True,
        remaining_energy_gaps=["Macro internal stored energy and dissipative partition",
                               "Cold start and initial-energy funding",
                               "Actual RF, LO, calibration, clock, register and management"],
        full_power_passed=False, actual_RF_feedback=False, G6_closed=False,
        observed_last_time_s=float(t[-1]))


def audit(partial=False):
    declared_path = OUT / "declared_configurations.json"
    declared = json.loads(declared_path.read_bytes())
    source = json.loads((OUT / "running_source.json").read_bytes())
    assert sha(declared_path) == source["configuration_sha256"]
    assert all(sha(Path(p)) == digest for p, digest in source["source_sha256"].items())
    assert len(declared) == 3 and [c["step_s"] for c in declared] == [10e-12, 10e-12, 5e-12]
    for c in declared:
        assert not c["reset_between_transitions"] and not c["real_rails"]
        assert c["amplitude_gate_V"] == .001
        assert c["edge_time_difference_gate_s"] == 20e-12
    for c in declared[1:]:
        assert len(c["states"]) == 65 and c["duration_s"] == 65 * 200e-9
        assert Counter(zip(c["states"], c["states"][1:])) == Counter(
            {(a, b): 1 for a in range(8) for b in range(8)})
    cases, runs = [], []
    evidence = {str(declared_path): sha(declared_path),
                str(OUT / "running_source.json"): sha(OUT / "running_source.json")}
    for config in declared:
        path = OUT / config["id"] / "run.json"
        if not path.exists():
            assert partial, "Declared solver case not archived"
            cases.append(dict(id=config["id"], status="not_archived",
                              complete=False, coverage64=False, local_passed=False))
            continue
        run = json.loads(path.read_bytes())
        assert all(run[k] == value for k, value in config.items())
        cases.append(inspect(run))
        runs.append(run)
        evidence[str(path)] = sha(path)
        for name, digest in run["files_sha256"].items():
            evidence[str(path.parent / name)] = digest
    if not partial:
        summary = json.loads((OUT / "summary.json").read_bytes())
        assert summary["runs"] == runs and summary["source_sha256"] == source["source_sha256"]
        evidence[str(OUT / "summary.json")] = sha(OUT / "summary.json")
    result = dict(
        partial_mode=partial, cases=cases, evidence_sha256=evidence,
        verifier_sha256=sha(Path(__file__)), all_cases_archived=len(runs) == 3,
        specified_64_transition_windows_passed=(not partial and
            all(c["complete"] and c["local_passed"] and c["coverage64"] for c in cases[1:])),
        # Numerical edge pairing and probe-equivalence are separate unmet gates.
        time_pair_passed=False, probe_equivalence_passed=False,
        actual_RF_feedback=False, real_rails_passed=False, full_power_passed=False,
        full_system_gates_closed=0, goal_complete=False)
    text = json.dumps(result, indent=2) + "\n"
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    target = OUT / f"verification_{digest[:12]}.json"
    if target.exists():
        assert target.read_text(encoding="utf-8") == text
    else:
        with target.open("x", encoding="utf-8") as stream:
            stream.write(text)
    print(json.dumps(dict(report=str(target),
                         cases=[dict(id=c["id"], status=c["status"], coverage64=c["coverage64"])
                                for c in cases], full_system_gates_closed=0), indent=2))
    return result


if __name__ == "__main__":
    audit("--partial" in sys.argv[1:])
