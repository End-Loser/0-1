"""Independent fine-waveform equations and explicitly bounded step comparisons."""
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PRIOR = ROOT.parent / "\u53c2\u8003\u8f93\u5165\u53bb\u8026"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def oracle():
    spec = importlib.util.spec_from_file_location("input_oracle", PRIOR / "verify_input_decoupling.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = ROOT
    return module


def comparison(first, second):
    differences = [abs(a["total_delay_s"]-b["total_delay_s"])
                   for a, b in zip(first.get("events", []), second.get("events", []))
                   if a["total_delay_s"] is not None and b["total_delay_s"] is not None]
    return dict(first=first["id"], second=second["id"], delay_differences_s=differences,
                passed=len(differences) == 4 and max(differences) < 20e-12,
                local_screens_passed=first["status"] == second["status"] == "pass")


def audit():
    summary = json.loads((ROOT / "summary.json").read_text(encoding="utf-8"))
    for key in ("original_files_sha256", "previous_evidence_sha256"):
        for path, digest in summary[key].items():
            assert sha(Path(path)) == digest, path
    assert sha(ROOT / "declared_configurations.json") == summary["declared_configurations_sha256"]
    declared = json.loads((ROOT / "declared_configurations.json").read_text(encoding="utf-8"))
    module = oracle()
    results = []
    for config, run in zip(declared, summary["runs"]):
        assert all(run[k] == v for k, v in config.items())
        for path, digest in run["files"].items():
            assert sha(ROOT / path) == digest
        if "rows" in run:
            results.append(module.recalculate(run))
        else:
            assert run["status"] == "blocked"
            result = dict(id=run["id"], status="blocked", reason=run["reason"],
                          last_time_s=run.get("observed_last_time_s"))
            if "partial_waveform" in run:
                p = run["partial_waveform"]
                assert sha(Path(p["path"])) == p["sha256"] and not p["accepted"]
                result["partial_waveform"] = p
            results.append(result)
    assert len(results) == len(declared) == 4
    old = json.loads((PRIOR / "verification.json").read_text(encoding="utf-8"))
    by_id = {r["id"]: r for r in old["runs"]+results}
    pairs = [("input_100pF", "input_100pF_5ps"),
             ("input_100pF_5ps", "input_100pF_2ps"),
             ("input_1000pF", "input_1000pF_2ps"),
             ("input_1000pF_output100pF", "input_1000pF_output100pF_5ps")]
    comparisons = [comparison(by_id[a], by_id[b]) for a, b in pairs]
    triple_pass = all(c["passed"] and c["local_screens_passed"] for c in comparisons[:2])
    output = dict(independent_evidence_verification_pass=True, runs=results,
                  comparisons=comparisons, input_100pF_three_step_local_passed=triple_pass,
                  earlier_1nF_and_10nF_5ps_failures_preserved=True,
                  full_G4_G5_passed=False, full_power_passed=False, startup_passed=False,
                  full_joint_passed=False, summary_sha256=sha(ROOT / "summary.json"),
                  verifier_sha256=sha(Path(__file__)))
    (ROOT / "verification.json").write_text(json.dumps(output, indent=2)+"\n", encoding="utf-8")
    for run in results:
        print(run["id"], run["status"], run.get("input_cap_accounting_passed"),
              run.get("dc_errors_V"), run.get("last_time_s"))
    print("Comparisons:", comparisons)
    return output


if __name__ == "__main__":
    audit()
