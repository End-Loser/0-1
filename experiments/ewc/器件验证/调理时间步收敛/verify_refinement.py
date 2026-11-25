"""Reuse the independent CSV/KCL oracle, never the simulation measurements code."""
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ORIGINAL = ROOT.parent / "\u771f\u5b9e\u57fa\u5e26\u8c03\u7406"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit():
    summary = json.loads((ROOT / "summary.json").read_text(encoding="utf-8"))
    for path, digest in summary["original_evidence_sha256"].items():
        assert sha(Path(path)) == digest, path
    for path, digest in summary["original_files_sha256"].items():
        assert sha(Path(path)) == digest, path
    for run in summary["runs"]:
        for path, digest in run["files"].items():
            assert sha(ROOT / path) == digest, path
        assert run["status"] in ("pass", "fail") and "rows" in run
    spec = importlib.util.spec_from_file_location("csv_oracle", ORIGINAL / "verify_offline.py")
    oracle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(oracle)
    oracle.ROOT = ROOT
    audited = [oracle.recalculate(r) for r in summary["runs"]]
    assert all(r["max_rc_kcl_residual_A"] < 1e-6 for r in audited), "RC KCL"
    old = json.loads((ORIGINAL / "summary.json").read_text(encoding="utf-8"))
    control = next(r for r in old["runs"] if r["id"] == "d05_fda_100pF")["measurements"]
    pairs = [(control, summary["runs"][0]["measurements"])]
    pairs += [(a["measurements"], b["measurements"])
              for a, b in zip(summary["runs"], summary["runs"][1:])]
    differences = []
    for a, b in pairs:
        delays = [abs(x["total_delay_s"] - y["total_delay_s"])
                  for x, y in zip(a["events"], b["events"])
                  if x["total_delay_s"] is not None and y["total_delay_s"] is not None]
        amplitude = max(abs(x-y) for x, y in zip(a["dc_errors_V"], b["dc_errors_V"]))
        cm = max(abs(x-y) for x, y in zip(a["cm_errors_V"], b["cm_errors_V"]))
        differences.append(dict(delay_differences_s=delays, dc_error_difference_V=amplitude,
                                cm_error_difference_V=cm,
                                passed=len(delays) == 4 and max(delays) < 20e-12
                                and amplitude < .001 and cm < .005))
    result = dict(independent_evidence_verification_pass=True, runs=audited,
                  comparisons=differences,
                  local_timestep_screen_passed=all(d["passed"] for d in differences)
                  and all(r["independently_reproduced_status"] == "pass" for r in audited),
                  whole_chain_passed=False, tolerance_history_noise_passed=False,
                  summary_sha256=sha(ROOT / "summary.json"),
                  oracle_sha256=sha(ORIGINAL / "verify_offline.py"),
                  verifier_sha256=sha(Path(__file__)))
    (ROOT / "verification.json").write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    audit()
