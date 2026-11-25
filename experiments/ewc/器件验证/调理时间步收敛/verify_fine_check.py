"""Independent 5ps CSV audit and fixed 20ps comparison against 10ps."""
import json
from pathlib import Path

from run_refinement import ORIGINAL, ROOT, load, sha


def audit():
    destination = ROOT / "fine_check"
    record = json.loads((destination / "summary.json").read_text(encoding="utf-8"))
    for path, digest in record["prior_batch_sha256"].items():
        assert sha(Path(path)) == digest, path
    for path, digest in record["original_files_sha256"].items():
        assert sha(Path(path)) == digest, path
    run = record["run"]
    for path, digest in run["files"].items():
        assert sha(destination / path) == digest, path
    assert run["status"] in ("pass", "fail")
    oracle = load("fine_csv_oracle", ORIGINAL / "verify_offline.py")
    oracle.ROOT = destination
    result = oracle.recalculate(run)
    assert result["max_rc_kcl_residual_A"] < 1e-6
    prior = json.loads((ROOT / "verification.json").read_text(encoding="utf-8"))
    ten = prior["runs"][-1]
    delays = [abs(a["total_delay_s"]-b["total_delay_s"])
              for a, b in zip(ten["events"], result["events"])
              if a["total_delay_s"] is not None and b["total_delay_s"] is not None]
    amplitude = max(abs(a-b) for a, b in zip(ten["dc_errors_V"], result["dc_errors_V"]))
    cm = max(abs(a-b) for a, b in zip(ten["cm_errors_V"], result["cm_errors_V"]))
    passed = (len(delays) == 4 and max(delays) < 20e-12 and amplitude < .001 and cm < .005
              and result["independently_reproduced_status"] == "pass"
              and ten["independently_reproduced_status"] == "pass")
    output = dict(independent_verification_pass=True, run=result,
                  delay_differences_s=delays, dc_error_difference_V=amplitude,
                  cm_error_difference_V=cm, ten_vs_five_ps_passed=passed,
                  coarse_50_vs_25ps_failed=True,
                  whole_chain_passed=False, history_tolerance_noise_passed=False,
                  summary_sha256=sha(destination / "summary.json"),
                  oracle_sha256=sha(ORIGINAL / "verify_offline.py"),
                  verifier_sha256=sha(Path(__file__)))
    assert prior["comparisons"][1]["passed"] is False
    (destination / "verification.json").write_text(json.dumps(output, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(output, indent=2))
    return output


if __name__ == "__main__":
    audit()
