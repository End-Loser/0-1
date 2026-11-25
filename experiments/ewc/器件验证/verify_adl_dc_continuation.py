"""Independent 24-case DC evidence audit across the original no-call boundary."""
import hashlib
import json
from pathlib import Path
import re
import sys

import numpy as np

from verify_adl_dc_holdout import evaluate, sha

ROOT = Path(__file__).resolve().parent
OLD = ROOT / "ADL共模接口留出"
OUT = ROOT / "ADL共模接口留出_未执行20项"
BASE = ROOT / "C输出64跳转与电流观测_D盘缓存"


def validate_boundary(configs, pending, stop, continuation):
    expected = {(cm, state) for cm in (3.2, 2.5, 3.8) for state in range(8)}
    if len(configs) != 24 or {(c["common_mode_V"], c["state"]) for c in configs} != expected:
        raise ValueError("All original 24 DC configurations required")
    ids = [c["id"] for c in configs]
    if len(set(ids)) != 24 or pending != configs[4:]:
        raise ValueError("Exact original never-called twenty required")
    prior_ids = [r["id"] for r in stop["completed_runs"]]
    if prior_ids != ids[:4] or stop["configurations_completed"] != 4:
        raise ValueError("Original archived four-case prefix required")
    if stop["next_configuration"] != pending[0] or stop["no_solver_call_for_next_case"] is not True:
        raise ValueError("Original no-call boundary proof required")
    if (continuation["original_completed_ids"] != prior_ids or
            continuation["only_never_called_ids"] != ids[4:] or
            continuation["original_cases_reexecuted"] is not False or
            continuation["library_or_bias_modified"] is not False):
        raise ValueError("Frozen continuation must not rerun or retune prior cases")


def check_deck(net, baseline, config):
    expected_lines = []
    replacements = {
        "VA": f"VA a 0 {0.3 * ((config['state'] >> 2) & 1)}",
        "VB": f"VB b 0 {0.3 * ((config['state'] >> 1) & 1)}",
        "VCI": f"VCI cin 0 {0.3 * (config['state'] & 1)}",
        "VSC": f"VSC scm 0 {config['common_mode_V']}"}
    for line in baseline.splitlines():
        token = line.split()[0] if line.split() else ""
        if token in replacements:
            line = replacements[token]
        elif token == "tran":
            line = "op"
        elif line.startswith("wrdata wave.txt "):
            line = line.replace("wrdata wave.txt ", "wrdata dc.txt ", 1)
        expected_lines.append(line)
    if net.splitlines() != expected_lines:
        raise ValueError("Only declared sources and the DC analysis/export change are allowed")
    lines = net.splitlines()
    for name, node, shift in (("VA", "a", 2), ("VB", "b", 1), ("VCI", "cin", 0)):
        expected = f"{name} {node} 0 {0.3 * ((config['state'] >> shift) & 1)}"
        if sum(s == expected for s in lines) != 1:
            raise ValueError("Declared independent DC inputs required")
    if (lines.count("op") != 1 or any(s.startswith("tran ") for s in lines) or
            lines.count(f"VSC scm 0 {config['common_mode_V']}") != 1 or
            "wrdata dc.txt " not in net or config["amplitude_gate_V"] != .001 or
            config["bias_retuned"] is not False):
        raise ValueError("Original DC analysis and precision gate required")


def classify(record, log, has_valid_vector):
    if record.get("returncode") != 0:
        return "timeout_or_nonzero_or_unknown_exit"
    if re.search(r"no such vector|simulation\(s\) aborted|doAnalyses:\s*OP:|DC solution failed",
                 log, re.IGNORECASE):
        return "solver_aborted"
    if not has_valid_vector:
        return "no_valid_DC_vector"
    if record["status"] != "dc_operating_point_observed_unverified":
        return "record_not_valid_DC"
    return "DC_observed"


def audit(partial=False):
    evidence = {}

    def bind(path, expected=None):
        digest = sha(path)
        if expected is not None and digest != expected:
            raise ValueError(f"Frozen evidence changed: {path}")
        evidence[str(path)] = digest
        return json.loads(path.read_bytes()) if path.suffix == ".json" else None

    original_provenance = bind(OLD / "provenance.json")
    provenance = bind(OUT / "provenance.json")
    continuation = bind(OUT / "continuation_source.json")
    configs = bind(OLD / "declared_configurations.json",
                   original_provenance["configuration_sha256"])
    pending = bind(OUT / "declared_configurations.json", provenance["configuration_sha256"])
    stop = bind(OLD / "resource_stop.json")
    validate_boundary(configs, pending, stop, continuation)
    for protected in (original_provenance["protected_sha256"], provenance["protected_sha256"],
                      continuation["frozen_prior_evidence_sha256"]):
        for path, digest in protected.items():
            bind(Path(path), digest)
    for name, digest in original_provenance["images_sha256"].items():
        bind(OLD / name, digest)
    baseline_path = BASE / "probe_equivalence_10ps" / "test.cir"
    bind(baseline_path)
    baseline = baseline_path.read_text(encoding="ascii")
    records, cases = [], []
    for index, config in enumerate(configs):
        folder = (OLD if index < 4 else OUT) / config["id"]
        if index >= 4 and (OLD / config["id"]).exists():
            raise ValueError("Unexpected duplicate original-case execution folder")
        path = folder / "run.json"
        if not path.exists():
            if not partial:
                raise ValueError("Missing declared stable DC run")
            cases.append(dict(id=config["id"], status=(
                "started_not_archived" if folder.exists() else "not_called")))
            continue
        record = bind(path)
        if any(record.get(k) != v for k, v in config.items()):
            raise ValueError("Run does not match original declaration")
        if index < 4 and record != stop["completed_runs"][index]:
            raise ValueError("Original failed run changed")
        if index >= 4:
            records.append(record)
        for name, digest in record["files_sha256"].items():
            bind(folder / name, digest)
        net = (folder / "test.cir").read_text(encoding="ascii")
        check_deck(net, baseline, config)
        log = (folder / "solver_stderr.log").read_text(encoding="utf-8", errors="replace")
        values = None
        if (folder / "dc.txt").exists():
            try:
                values = np.loadtxt(folder / "dc.txt", skiprows=1, ndmin=2)
            except ValueError:
                pass
        valid = (values is not None and values.shape == (1, len(provenance["columns"])) and
                 bool(np.isfinite(values).all()))
        status = classify(record, log, valid)
        result = dict(id=config["id"], status=status, returncode=record.get("returncode"),
                      reason=record.get("reason"), common_mode_V=config["common_mode_V"],
                      state=config["state"])
        if status == "DC_observed":
            data = dict(zip(provenance["columns"], values[0]))
            for node, shift in (("a", 2), ("b", 1), ("cin", 0)):
                if abs(data[node] - .3 * ((config["state"] >> shift) & 1)) >= 1e-9:
                    raise ValueError("DC input observation mismatches declared state")
            if abs(data["raw"] - .1 * config["state"].bit_count()) >= 1e-9:
                raise ValueError("DC raw observation mismatches independent inputs")
            result.update(evaluate(data, net, config))
        cases.append(result)
    if (OUT / "summary.json").exists():
        summary = bind(OUT / "summary.json")
        if (summary["runs"] != records or
                summary["provenance_sha256"] != evidence[str(OUT / "provenance.json")]):
            raise ValueError("Continuation summary does not match stable runs")
    elif not partial:
        raise ValueError("Missing completed continuation summary")
    for name in ("verify_adl_dc_holdout.py", "test_adl_dc_continuation.py",
                 "test_adl_dc_continuation_audit.py"):
        bind(ROOT / name)
    report = dict(partial=partial, declared_cases=24, cases=cases, evidence_sha256=evidence,
                  observed_DC_points=sum(c["status"] == "DC_observed" for c in cases),
                  archived_runs=sum(c["status"] not in ("not_called", "started_not_archived")
                                    for c in cases),
                  original_four_cases_reexecuted=False, actual_ADL_model=False,
                  real_rails=False, startup_passed=False, full_power_passed=False,
                  table_common_mode_conflict_unresolved=True, full_system_gates_closed=0,
                  goal_complete=False, verifier_sha256=sha(Path(__file__)))
    text = json.dumps(report, indent=2) + "\n"
    target = OUT / ("combined_verification_" + hashlib.sha256(text.encode()).hexdigest()[:12] + ".json")
    if target.exists():
        assert target.read_text(encoding="ascii") == text
    else:
        with target.open("x", encoding="ascii") as stream:
            stream.write(text)
    print(json.dumps(dict(report=str(target), observed_DC_points=report["observed_DC_points"],
                          archived_runs=report["archived_runs"], full_system_gates_closed=0), indent=2))
    return report


if __name__ == "__main__":
    audit("--partial" in sys.argv)
