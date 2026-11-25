"""Independent typical-offset margin evaluation; original nominal gates stay fixed."""
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

from verify_c_saved_history64 import branch_accounting, load_case, sha

ROOT = Path(__file__).resolve().parent
BASE = ROOT / "C输出64跳转_显式观测保存"
OUT = ROOT / "ADL偏移典型留出_观测列修正"


def accounting_flags(accounting):
    return accounting["algebraic_passed"], accounting["capacitor_passed"]


def output_current_max(d):
    maximum = 0.
    for stage in (1, 2):
        for output, receiver, feedback in (("op", "cp", "im"), ("om", "cn", "ip")):
            voltage = d[f"{output}{stage}"] - d[f"{feedback}{stage}"]
            current = voltage / 1000 + (
                d[f"{output}{stage}"] - d[f"{receiver}{stage}"]) / 22
            current += 1e-12 * np.gradient(voltage, d["time"])
            maximum = max(maximum, float(np.max(abs(current))))
    return maximum


def window_metrics(d, mask, state):
    count = state.bit_count()
    carry, summation = count // 2, count % 2
    gain = .5 * 1000 / 1044
    target = [gain * (.1 * count - .15), gain * (.1 * count - .05 - .2 * carry)]
    errors = [float(np.max(abs(d[f"cp{s}"][mask] - d[f"cn{s}"][mask] - target[s - 1])))
              for s in (1, 2)]
    return dict(
        state=state, amplitude_error_V=errors, nominal_amplitude_passed=max(errors) < .001,
        logic_passed=all(bool(np.all(d[name][mask] > 2.31 if bit else d[name][mask] < .99))
                         for name, bit in (("carry", carry), ("sum", summation))))


def audit(partial=False):
    source_path = OUT / "running_source.json"
    source = json.loads(source_path.read_bytes())
    evidence = {str(source_path): sha(source_path)}
    for path, digest in source["source_sha256"].items():
        assert sha(Path(path)) == digest
        evidence[path] = digest
    declared_path = OUT / "declared_configurations.json"
    assert sha(declared_path) == source["configuration_sha256"]
    evidence[str(declared_path)] = sha(declared_path)
    configs = json.loads(declared_path.read_bytes())
    assert len(configs) == 4 and {tuple(c["offsets_V"]) for c in configs} == {
        (-.01, -.01), (-.01, .01), (.01, -.01), (.01, .01)}
    baseline = (BASE / "probe_equivalence_10ps" / "test.cir").read_text(encoding="ascii")
    cases, records = [], []
    for config in configs:
        assert config["states"] == list(range(8)) + [0]
        assert config["step_s"] == 10e-12 and abs(config["duration_s"] - 1.8e-6) < 1e-18
        assert config["amplitude_gate_V"] == .001 and not config["calibration_retuned"]
        path = OUT / config["id"] / "run.json"
        if not path.exists():
            assert partial
            cases.append(dict(id=config["id"], status="not_archived", local_passed=False))
            continue
        run = json.loads(path.read_bytes())
        records.append(run)
        assert all(run[k] == value for k, value in config.items())
        evidence[str(path)] = sha(path)
        for name, digest in run["files_sha256"].items():
            assert sha(path.parent / name) == digest
            evidence[str(path.parent / name)] = digest
        net = (path.parent / "test.cir").read_text(encoding="ascii")
        assert [s for s in net.splitlines() if not s.startswith(("BP", "BN"))] == [
            s for s in baseline.splitlines() if not s.startswith(("BP", "BN"))]
        for s, offset in enumerate(config["offsets_V"], 1):
            assert f"BP{s} spgen{s} 0 V=v(scm)+(v(raw)+({offset}))/2" in net
            assert f"BN{s} sngen{s} 0 V=v(scm)-(v(raw)+({offset}))/2" in net
        assert "carry" not in "".join(s for s in net.splitlines() if s.startswith(("BP", "BN")))
        vectors = load_case(run, OUT)
        if vectors is None:
            cases.append(dict(id=run["id"], status="no_vectors", local_passed=False,
                              reason=run.get("reason")))
            continue
        columns, a = vectors
        assert len(columns) == 83
        d = {name: a[:, i] for i, name in enumerate(columns)}
        t = d["time"]
        complete = bool(run["status"] == "solver_complete_unverified" and
                        run.get("returncode") == 0 and t[-1] >= run["duration_s"] - 1e-12)
        mask_all = np.zeros(len(t), dtype=bool)
        windows = []
        for index, state in enumerate(config["states"]):
            start = index * config["hold_s"]
            if t[-1] < start + 170e-9:
                break
            mask = (t >= start + 120e-9) & (t <= start + 170e-9)
            assert np.count_nonzero(mask) >= 100
            for name, bit in (("a", 2), ("b", 1), ("cin", 0)):
                assert np.max(abs(d[name][mask] - .3 * ((state >> bit) & 1))) < 1e-9
            mask_all |= mask
            windows.append(window_metrics(d, mask, state))
        accounting = branch_accounting(d, net, mask_all)
        algebraic_passed, capacitor_passed = accounting_flags(accounting)
        powers = dict(upstream=-d["upstream"] * d["i_up"],
                      diagnostic_5V=-d["rail5"] * d["i_5"],
                      diagnostic_3V3=-d["rail3"] * d["i_3"],
                      synthetic_common_mode=-3.2 * d["i_sc"])
        input_kcl = {}
        for name, resistor, current in (("a", "RA", "i_a"), ("b", "RB", "i_b"),
                                        ("cin", "RCI", "i_ci")):
            assert f"{resistor} {name} raw 300" in net
            input_kcl[name] = float(np.max(abs(
                d[current] + (d[name] - d["raw"]) / 300)))
            powers[f"input_{name}"] = -d[name] * d[current]
        for s, offset in enumerate(config["offsets_V"], 1):
            for name, sign in (("p", 1), ("n", -1)):
                powers[f"synthetic_{name}{s}"] = (
                    3.2 + sign * (d["raw"] + offset) / 2) * d[f"i_s{name}{s}"]
        work = {name: float(np.trapz(value, t)) for name, value in powers.items()}
        full_windows = complete and len(windows) == 9
        cm_error = float(max(np.max(abs((d[f"cp{s}"][mask_all] +
                                        d[f"cn{s}"][mask_all]) / 2 - 1.65))
                             for s in (1, 2))) if mask_all.any() else None
        checks = dict(
            complete_windows=full_windows,
            amplitude=bool(windows) and all(w["nominal_amplitude_passed"] for w in windows),
            logic=bool(windows) and all(w["logic_passed"] for w in windows),
            common_mode=cm_error is not None and cm_error < .005,
            external_kcl=algebraic_passed,
            input_source_kcl=max(input_kcl.values()) < 1e-9,
            capacitors=capacitor_passed,
            reference=np.max(abs(d["vref"] - 5)) < .005,
            fda_inputs=all(np.min(d[f"{p}{s}"]) >= -.2 and np.max(d[f"{p}{s}"]) <= 3.8
                           for s in (1, 2) for p in ("ip", "im")),
            fda_outputs=all(np.min(d[f"{p}{s}"]) >= .23 and np.max(d[f"{p}{s}"]) <= 4.77
                            for s in (1, 2) for p in ("op", "om")),
            comparator_inputs=all(np.min(d[f"{p}{s}"]) >= -.2 and np.max(d[f"{p}{s}"]) <= 3.1
                                  for s in (1, 2) for p in ("cp", "cn")),
            rails=np.max(abs(d["rail5"] - 5)) < .075 and np.max(abs(d["rail3"] - 3.3)) < .0495,
            fda_output_current=output_current_max(d) < .02)
        checks = {name: bool(value) for name, value in checks.items()}
        cases.append(dict(
            id=run["id"], offsets_V=config["offsets_V"], complete=complete, rows=len(t),
            windows=windows, nominal_amplitude_gate_V=.001,
            local_passed=all(checks.values()), checks=checks,
            common_mode_error_V=cm_error, branch_accounting=accounting,
            output_current_max_A=output_current_max(d),
            input_source_kcl_max_A=input_kcl,
            primitive_source_signed_work_J=work, startup_passed=False,
            RF_model=False, real_rails=False, full_power_passed=False))
    if not partial:
        path = OUT / "summary.json"
        assert json.loads(path.read_bytes())["runs"] == records
        evidence[str(path)] = sha(path)
    result = dict(
        partial=partial, cases=cases, evidence_sha256=evidence,
        verifier_sha256=sha(Path(__file__)), typical_900MHz_scope_only=True,
        actual_ADL_model=False, original_amplitude_gate_preserved=True,
        full_system_gates_closed=0, goal_complete=False)
    text = json.dumps(result, indent=2) + "\n"
    digest = hashlib.sha256(text.encode()).hexdigest()
    path = OUT / f"verification_{digest[:12]}.json"
    if path.exists():
        assert path.read_text(encoding="utf-8") == text
    else:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(text)
    print(json.dumps(dict(report=str(path), cases=[
        dict(id=c["id"], complete=c.get("complete", False), local_passed=c["local_passed"])
        for c in cases], full_system_gates_closed=0), indent=2))
    return result


if __name__ == "__main__":
    audit("--partial" in sys.argv)
