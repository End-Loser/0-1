"""Independent accounting via original CSV equations; old failures stay intact."""
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
PRIOR = ROOT.parent / "\u4f9b\u7535\u5206\u5c42\u8054\u63a5"
CASE_IDS = ("dual_enable_cycle", "dual_ramp", "dual_reference_ramp",
            "buffer_real_rails", "buffer_real_ramp")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit():
    summary = json.loads((ROOT / "summary.json").read_text(encoding="utf-8"))
    configs = json.loads((ROOT / "declared_configurations.json").read_text(encoding="utf-8"))
    assert len(configs) == len(summary["runs"]) == 10
    assert sha(ROOT / "declared_configurations.json") == summary["declared_configurations_sha256"]
    assert sha(ROOT / "run_capacitor_refinement.py") == summary["runner_sha256"]
    for group in ("original_files_sha256", "previous_evidence_sha256"):
        assert all(sha(Path(p)) == h for p, h in summary[group].items())
    spec = importlib.util.spec_from_file_location("independent_supply", PRIOR / "verify_supply_layers.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    helper.ROOT = ROOT
    baseline = json.loads((PRIOR / "summary.json").read_text(encoding="utf-8"))
    old_checks = json.loads((PRIOR / "verification.json").read_text(encoding="utf-8"))
    records = []
    for config, run in zip(configs, summary["runs"]):
        assert all(run[key] == value for key, value in config.items())
        assert config["baseline_id"] in CASE_IDS and config["step_s"] in (50e-9, 20e-9)
        old = next(r for r in baseline["runs"] if r["id"] == config["baseline_id"])
        for key in ("reference", "buffers", "ideal_rails", "ramp", "enable_cycle", "load_step", "finish_s"):
            assert config[key] == old[key]
        assert old["step_s"] == 100e-9
        for path, digest in run["files"].items():
            assert sha(ROOT / path) == digest
        net = (ROOT / "results" / run["id"] / "test.cir").read_text(encoding="ascii")
        original_net = (PRIOR / "results" / old["id"] / "test.cir").read_text(encoding="ascii")
        fine = f"tran {run['step_s']} {run['finish_s']} 0 {run['step_s']}"
        coarse = f"tran {old['step_s']} {old['finish_s']} 0 {old['step_s']}"
        assert net.count(fine) == 1 and net.replace(fine, coarse) == original_net
        if run["status"] == "blocked":
            records.append(dict(id=run["id"], baseline_id=config["baseline_id"], step_s=config["step_s"],
                                waveform_complete=False, capacitor_accounting_passed=False,
                                interface_passed=False, reason=run["reason"]))
        else:
            assert run["returncode"] == 0
            records.append(dict(helper.recalculate(run), baseline_id=config["baseline_id"],
                                step_s=config["step_s"]))
    comparisons = []
    for case in CASE_IDS:
        old = next(r for r in old_checks["runs"] if r["id"] == case)
        assert not old["capacitor_accounting_passed"] and not old["interface_passed"]
        fine = [r for r in records if r["baseline_id"] == case]
        assert sorted(r["step_s"] for r in fine) == [20e-9, 50e-9]
        errors = {}
        for capacitor, data in old["capacitors"].items():
            errors[capacitor] = [data["charge_relative_error"]] + [
                r["capacitors"][capacitor]["charge_relative_error"] if r["waveform_complete"] else None
                for r in sorted(fine, key=lambda r: -r["step_s"])]
        comparisons.append(dict(baseline_id=case, time_steps_s=[100e-9, 50e-9, 20e-9],
                                charge_relative_errors=errors,
                                both_fine_accounting_passed=all(r["capacitor_accounting_passed"] for r in fine),
                                original_100ns_failure_preserved=True))
    for key in ("vendor_library_edits", "full_G4_G5_passed", "full_power_passed",
                "startup_safety_passed", "full_joint_passed"):
        assert summary[key] is False
    result = dict(
        scope="finer_step_capacitor_accounting_not_supply_or_system_acceptance", runs=records,
        comparisons=comparisons, independent_evidence_passed=True,
        complete_waveforms=sum(r["waveform_complete"] for r in records),
        accounting_pass_count=sum(r["capacitor_accounting_passed"] for r in records),
        interface_pass_count=sum(r["interface_passed"] for r in records),
        summary_sha256=sha(ROOT / "summary.json"), verifier_sha256=sha(Path(__file__)),
        independent_helper_sha256=sha(PRIOR / "verify_supply_layers.py"),
        full_G4_G5_passed=False, full_power_passed=False, startup_safety_passed=False, full_joint_passed=False)
    (ROOT / "verification.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    for r in records:
        print(r["id"], "complete", r["waveform_complete"], "accounting",
              r["capacitor_accounting_passed"], "interface", r["interface_passed"])
    return result


if __name__ == "__main__":
    audit()
