"""Independent physical gates for the unchanged circuit's trap comparison."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys

import numpy as np

from verify_c_saved_history64 import branch_accounting, compare_signals, load_case, sha

ROOT = Path(__file__).resolve().parent
PRIOR = ROOT / "C输出64跳转_显式观测保存"
OUT = ROOT / "C输出64跳转_二阶梯形积分对照"


def check_deck(candidate, original):
    expected = original.replace(".options method=gear maxord=2",
                                ".options method=trap maxord=2", 1)
    if original.splitlines().count(".options method=gear maxord=2") != 1 or candidate != expected:
        raise ValueError("Only second-order integration method may change")


def inspect(run, columns):
    folder = OUT / run["id"]
    net = (folder / "test.cir").read_text(encoding="ascii")
    check_deck(net, (PRIOR / run["id"] / "test.cir").read_text(encoding="ascii"))
    exit_record = json.loads((folder / "solver_exit.json").read_bytes())
    assert exit_record["returncode"] == run["returncode"]
    vectors = load_case(run, OUT)
    if vectors is None:
        return dict(id=run["id"], complete=False, local_passed=False, coverage64=False)
    names, data = vectors
    assert names == columns and len(names) == 83
    d = dict(zip(names, data.T))
    t = d["time"]
    log = (folder / "solver_stderr.log").read_text(encoding="utf-8", errors="replace")
    complete = bool(run["status"] == "solver_complete_unverified" and
                    run["returncode"] == 0 and t[-1] >= run["duration_s"] - 1e-12 and
                    not re.search(r"simulation\(s\) aborted|doAnalyses:.*Timestep too small", log))
    gain, windows = .5 * 1000 / 1044, []
    mask_all = np.zeros(len(t), dtype=bool)
    for i, state in enumerate(run["states"]):
        if t[-1] < i * 200e-9 + 170e-9:
            break
        mask = (t >= i * 200e-9 + 120e-9) & (t <= i * 200e-9 + 170e-9)
        assert mask.sum() >= 100
        mask_all |= mask
        for name, shift in (("a", 2), ("b", 1), ("cin", 0)):
            assert np.max(abs(d[name][mask] - .3 * ((state >> shift) & 1))) < 1e-9
        count = state.bit_count()
        carry, summation = count // 2, count % 2
        errors = [float(np.max(abs(d["cp1"][mask] - d["cn1"][mask] -
                                   gain * (.1 * count - .15)))),
                  float(np.max(abs(d["cp2"][mask] - d["cn2"][mask] -
                                   gain * (.1 * count - .05 - .2 * carry))))]
        logic = all(bool(d[name][mask].min() > 2.31 if bit else d[name][mask].max() < .99)
                    for name, bit in (("carry", carry), ("sum", summation)))
        windows.append(dict(index=i, state=state, error_V=errors, logic_passed=logic))
    accounting = branch_accounting(d, net, mask_all)
    cm = max(float(np.max(abs((d[f"cp{s}"][mask_all] + d[f"cn{s}"][mask_all]) / 2 - 1.65)))
             for s in (1, 2)) if mask_all.any() else float("inf")
    current = 0.
    for stage in (1, 2):
        for output, receiver, feedback in (("op", "cp", "im"), ("om", "cn", "ip")):
            delta = d[f"{output}{stage}"] - d[f"{feedback}{stage}"]
            v = delta / 1000 + (d[f"{output}{stage}"] - d[f"{receiver}{stage}"]) / 22
            v += 1e-12 * np.gradient(delta, t)
            current = max(current, float(np.max(abs(v))))
    checks = dict(
        complete=complete, all_windows=len(windows) == len(run["states"]),
        amplitude=bool(windows) and all(max(w["error_V"]) < .001 for w in windows),
        logic=bool(windows) and all(w["logic_passed"] for w in windows),
        common_mode=cm < .005, FDA_output_current=current < .020,
        FDA_inputs=all(d[f"{p}{s}"].min() >= -.2 and d[f"{p}{s}"].max() <= 3.8
                       for p in ("ip", "im") for s in (1, 2)),
        FDA_outputs=all(d[f"{p}{s}"].min() >= .23 and d[f"{p}{s}"].max() <= 4.77
                        for p in ("op", "om") for s in (1, 2)),
        comparator_inputs=all(d[f"{p}{s}"].min() >= -.2 and d[f"{p}{s}"].max() <= 3.1
                              for p in ("cp", "cn") for s in (1, 2)),
        rails=bool(np.max(abs(d["rail5"] - 5)) < .075 and
                   np.max(abs(d["rail3"] - 3.3)) < .0495),
        reference=bool(np.max(abs(d["vref"] - 5)) < .005),
        algebraic_KCL=accounting["algebraic_passed"],
        capacitors=accounting["capacitor_passed"])
    pairs = Counter(zip(run["states"], run["states"][1:]))
    coverage = complete and len(windows) == 65 and pairs == Counter(
        {(a, b): 1 for a in range(8) for b in range(8)})
    powers = {"upstream": -d["upstream"] * d["i_up"],
              "diagnostic_5V": -d["rail5"] * d["i_5"],
              "diagnostic_3V3": -d["rail3"] * d["i_3"],
              "source_common_mode": -3.2 * d["i_sc"]}
    for node, label in (("a", "a"), ("b", "b"), ("cin", "ci")):
        powers["input_" + node] = -d[node] * d["i_" + label]
    for stage in (1, 2):
        powers[f"synthetic_p{stage}"] = (3.2 + d["raw"] / 2) * d[f"i_sp{stage}"]
        powers[f"synthetic_n{stage}"] = (3.2 - d["raw"] / 2) * d[f"i_sn{stage}"]
    work = {name: dict(net_J=float(np.trapz(p, t)),
                       delivered_J=float(np.trapz(np.maximum(p, 0), t)),
                       absorbed_J=float(np.trapz(np.maximum(-p, 0), t)))
            for name, p in powers.items()}
    return dict(id=run["id"], complete=complete, local_passed=all(checks.values()),
                coverage64=coverage, checks=checks, windows=windows,
                common_mode_error_V=cm if mask_all.any() else None,
                output_current_max_A=current, branch_accounting=accounting,
                primitive_source_work=work, full_power_passed=False,
                C_feedback_terminal_transfer_J=float(np.trapz(d["carry"] * d["i_c_feedback"], t)),
                feedback_resistor_heat_J=float(np.trapz((d["carry"] - d["im2"])**2 / 32750, t)),
                internal_transfers_not_extra_external_supplies=True,
                observed_last_time_s=float(t[-1]))


def audit(partial=False):
    source = json.loads((OUT / "provenance.json").read_bytes())
    evidence = dict(source["protected_sha256"])
    assert all(sha(Path(p)) == digest for p, digest in evidence.items())
    configs = json.loads((OUT / "declared_configurations.json").read_bytes())
    assert sha(OUT / "declared_configurations.json") == source["declaration_sha256"]
    original_configs = json.loads((PRIOR / "declared_configurations.json").read_bytes())
    extra = ("integration_method", "integration_max_order", "original_failure_replaced",
             "tolerances_relaxed")
    assert [{k: v for k, v in c.items() if k not in extra} for c in configs] == original_configs
    for c in configs:
        assert (c["integration_method"], c["integration_max_order"]) == ("trap", 2)
        assert c["original_failure_replaced"] is False and c["tolerances_relaxed"] is False
    cases, runs = [], []
    for config in configs:
        path = OUT / config["id"] / "run.json"
        if not path.exists():
            assert partial
            cases.append(dict(id=config["id"], complete=False, local_passed=False, coverage64=False))
            continue
        run = json.loads(path.read_bytes())
        assert all(run.get(k) == v for k, v in config.items())
        cases.append(inspect(run, source["columns"]))
        runs.append(run)
        evidence[str(path)] = sha(path)
        for name, digest in run["files_sha256"].items():
            evidence[str(path.parent / name)] = digest
    for name in ("provenance.json", "declared_configurations.json", "execution_source.json",
                 "probe_admission.json", "probe_verifier_stdout.log", "probe_verifier_stderr.log"):
        path = OUT / name
        if path.exists():
            evidence[str(path)] = sha(path)
    if not partial:
        summary_path = OUT / "summary.json"
        summary = json.loads(summary_path.read_bytes())
        assert summary["runs"] == runs and summary["provenance_sha256"] == sha(OUT / "provenance.json")
        evidence[str(summary_path)] = sha(summary_path)
    by_id = {r["id"]: r for r in runs}
    probe, pair, cross_method = [dict(complete=False, passed=False) for _ in range(3)]
    if cases[0]["complete"]:
        reference = json.loads((PRIOR / configs[0]["id"] / "run.json").read_bytes())
        probe = compare_signals(load_case(reference, PRIOR), load_case(by_id[reference["id"]], OUT),
                                200e-9, configs[0]["states"])
    if cases[1]["complete"]:
        reference = json.loads((PRIOR / configs[1]["id"] / "run.json").read_bytes())
        cross_method = compare_signals(load_case(reference, PRIOR),
                                       load_case(by_id[reference["id"]], OUT), 200e-9, configs[1]["states"])
    if cases[1]["complete"] and cases[2]["complete"]:
        pair = compare_signals(load_case(by_id[configs[1]["id"]], OUT),
                               load_case(by_id[configs[2]["id"]], OUT), 200e-9, configs[1]["states"])
    result = dict(partial=partial, cases=cases, evidence_sha256=evidence,
                  verifier_sha256=sha(Path(__file__)), probe_comparison=probe,
                  probe_admitted=cases[0]["local_passed"] and probe["passed"],
                  full_time_pair=pair, full_cross_method_comparison=cross_method,
                  candidate_local_complete=(all(c["local_passed"] for c in cases) and
                                            all(c["coverage64"] for c in cases[1:]) and
                                            probe["passed"] and pair["passed"] and cross_method["passed"]),
                  original_gear_failure_replaced=False, actual_RF_model=False,
                  real_rails=False, cold_start_passed=False, full_power_passed=False,
                  full_system_gates_closed=0, goal_complete=False)
    text = json.dumps(result, indent=2) + "\n"
    target = OUT / ("verification_" + hashlib.sha256(text.encode()).hexdigest()[:12] + ".json")
    if not target.exists():
        with target.open("x", encoding="ascii") as stream:
            stream.write(text)
    else:
        assert target.read_text(encoding="ascii") == text
    print(json.dumps(dict(report=str(target), probe_admitted=result["probe_admitted"],
                          candidate_local_complete=result["candidate_local_complete"],
                          full_system_gates_closed=0), indent=2))
    return result


if __name__ == "__main__":
    audit("--partial" in sys.argv)
