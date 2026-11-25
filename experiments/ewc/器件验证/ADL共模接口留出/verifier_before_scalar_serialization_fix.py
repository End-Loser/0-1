"""Independent fixed-chain operating-point checks; never certify an ADL model."""
import hashlib
import json
from pathlib import Path
import re
import sys

import numpy as np

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "ADL共模接口留出"
BASE = ROOT / "C输出64跳转与电流观测_D盘缓存"


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def resistor(net, name):
    found = re.findall(rf"(?m)^{name} \S+ \S+ ([0-9.eE+-]+)$", net)
    assert len(found) == 1
    return float(found[0])


def evaluate(d, net, config):
    count = config["state"].bit_count()
    carry, summation = count // 2, count % 2
    gain = .5 * 1000 / 1044
    errors = [abs(d["cp1"] - d["cn1"] - gain * (.1 * count - .15)),
              abs(d["cp2"] - d["cn2"] - gain * (.1 * count - .05 - .2 * carry))]
    logic = all(d[name] > 2.31 if bit else d[name] < .99
                for name, bit in (("carry", carry), ("sum", summation)))
    kcl = {
        "5V": d["i_5"] + d["i_f1"] + d["i_f2"],
        "3V3": d["i_3"] + d["i_c1"] + d["i_c2"] +
                sum(d[f"i_buf{i}"] for i in range(1, 6)),
        "C_feedback": d["i_c_feedback"] -
                (d["carry"] - d["im2"]) / resistor(net, "RCFEED"),
        "common_mode": d["i_bout1"] - d["i_fb1"] - d["i_cm1"] - d["i_cm2"],
        "reference": d["i_refout"] - sum(
            (d["vref"] - d[f"b{i}"]) / resistor(net, f"RDT{i}") for i in range(1, 6)),
    }
    load = {}
    power = {"upstream": -d["upstream"] * d["i_up"],
             "diagnostic_5V": -d["rail5"] * d["i_5"],
             "diagnostic_3V3": -d["rail3"] * d["i_3"]}
    for stage in (1, 2):
        for polarity, sign in (("p", 1), ("n", -1)):
            node = f"s{polarity}{stage}"
            current = d[f"i_s{polarity}{stage}"]
            source = config["common_mode_V"] + sign * d["raw"] / 2
            kcl[f"source_{polarity}{stage}"] = current - (
                source - d[node]) / resistor(net, f"RS{polarity.upper()}{stage}")
            load[f"{polarity}{stage}"] = dict(
                terminal_V=d[node], delivered_current_A=current,
                source_internal_voltage_V=source,
                terminal_power_W=d[node] * current,
                typical_12mA_comparison_only=abs(current) <= .012)
            power[f"synthetic_{polarity}{stage}"] = source * current
    cm_error = max(abs((d[f"cp{s}"] + d[f"cn{s}"]) / 2 - 1.65) for s in (1, 2))
    checks = dict(
        amplitude=max(errors) < .001, logic=bool(logic), output_common_mode=cm_error < .005,
        fda_inputs=all(-.2 <= d[f"{p}{s}"] <= 3.8 for s in (1, 2) for p in ("ip", "im")),
        comparator_inputs=all(-.2 <= d[f"{p}{s}"] <= 3.1
                              for s in (1, 2) for p in ("cp", "cn")),
        reference=abs(d["vref"] - 5) < .005,
        external_DC_kcl=max(abs(x) for x in kcl.values()) < 1e-9)
    return dict(
        amplitude_error_V=errors, common_mode_error_V=cm_error, checks=checks,
        local_fixed_chain_dc_passed=all(checks.values()),
        external_DC_kcl_A={k: abs(v) for k, v in kcl.items()},
        synthetic_source_terminal_load=load, primitive_source_power_W=power,
        C_feedback_internal_transfer_W=d["carry"] * d["i_c_feedback"],
        no_time_integral_or_energy_claim=True,
        typical_current_screen_passed=all(v["typical_12mA_comparison_only"]
                                         for v in load.values()),
        ADL_actual_loading_passed=False, ADL_model=False,
        real_RF_passed=False, startup_passed=False, full_power_passed=False)


def audit(partial=False):
    provenance_path = OUT / "provenance.json"
    provenance = json.loads(provenance_path.read_bytes())
    evidence = {str(provenance_path): sha(provenance_path)}
    for path, digest in provenance["protected_sha256"].items():
        assert sha(Path(path)) == digest
        evidence[path] = digest
    for name, digest in provenance["images_sha256"].items():
        assert sha(OUT / name) == digest
        evidence[str(OUT / name)] = digest
    declared = OUT / "declared_configurations.json"
    assert sha(declared) == provenance["configuration_sha256"]
    evidence[str(declared)] = sha(declared)
    configs = json.loads(declared.read_bytes())
    assert len(configs) == 24 and {(c["common_mode_V"], c["state"]) for c in configs} == {
        (cm, state) for cm in (3.2, 2.5, 3.8) for state in range(8)}
    original = (BASE / "probe_equivalence_10ps" / "test.cir").read_text(encoding="ascii")
    ignored = ("VA ", "VB ", "VCI ", "VSC ", "tran ", "op", "wrdata ")
    baseline = [s for s in original.splitlines() if not s.startswith(ignored)]
    cases, records = [], []
    for config in configs:
        path = OUT / config["id"] / "run.json"
        if not path.exists():
            assert partial, "Missing declared DC case"
            cases.append(dict(id=config["id"], status="not_archived"))
            continue
        record = json.loads(path.read_bytes())
        records.append(record)
        assert all(record[k] == v for k, v in config.items())
        assert config["amplitude_gate_V"] == .001 and not config["bias_retuned"]
        for name, digest in record["files_sha256"].items():
            assert sha(path.parent / name) == digest
            evidence[str(path.parent / name)] = digest
        evidence[str(path)] = sha(path)
        net = (path.parent / "test.cir").read_text(encoding="ascii")
        assert [s for s in net.splitlines() if not s.startswith(ignored)] == baseline
        assert "\nop\n" in net and "\ntran " not in net
        assert f"VSC scm 0 {config['common_mode_V']}" in net
        if record["status"] != "dc_operating_point_observed_unverified":
            cases.append(dict(id=config["id"], status="solver_failed", reason=record.get("reason")))
            continue
        assert record["returncode"] == 0
        values = np.loadtxt(path.parent / "dc.txt", skiprows=1, ndmin=2)
        assert values.shape == (1, len(provenance["columns"])) and np.isfinite(values).all()
        d = dict(zip(provenance["columns"], values[0]))
        for node, shift in (("a", 2), ("b", 1), ("cin", 0)):
            assert abs(d[node] - .3 * ((config["state"] >> shift) & 1)) < 1e-9
        assert abs(d["raw"] - .1 * config["state"].bit_count()) < 1e-9
        result = evaluate(d, net, config)
        cases.append(dict(id=config["id"], common_mode_V=config["common_mode_V"],
                          state=config["state"], status="DC_observed", **result))
    if not partial:
        summary_path = OUT / "summary.json"
        summary = json.loads(summary_path.read_bytes())
        assert summary["runs"] == records
        assert summary["provenance_sha256"] == sha(provenance_path)
        assert summary["no_transient_history"] and not summary["actual_ADL_model"]
        evidence[str(summary_path)] = sha(summary_path)
    result = dict(
        partial=partial, cases=cases, evidence_sha256=evidence,
        verifier_sha256=sha(Path(__file__)),
        table_common_mode_conflict_unresolved=True, actual_ADL_model=False,
        transient_histories_tested=0, full_system_gates_closed=0, goal_complete=False)
    text = json.dumps(result, indent=2) + "\n"
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    target = OUT / f"verification_{digest[:12]}.json"
    if target.exists():
        assert target.read_text(encoding="utf-8") == text
    else:
        with target.open("x", encoding="utf-8") as stream:
            stream.write(text)
    print(json.dumps(dict(report=str(target), cases=len(cases),
                         observed=sum(c["status"] == "DC_observed" for c in cases),
                         dc_local_passed=sum(c.get("local_fixed_chain_dc_passed", False)
                                             for c in cases),
                         full_system_gates_closed=0), indent=2))
    return result


if __name__ == "__main__":
    audit("--partial" in sys.argv)
