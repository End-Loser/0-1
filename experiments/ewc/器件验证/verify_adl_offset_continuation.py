"""Bind continuation evidence without inventing the missing first exit code."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys

import verify_adl_offset_review as original


def audit(partial=False):
    capture = io.StringIO()
    with contextlib.redirect_stdout(capture):
        result = original.audit(partial)
    base_report = Path(json.loads(capture.getvalue())["report"])
    continuation_path = original.OUT / "continuation_source.json"
    continuation = json.loads(continuation_path.read_bytes())
    assert continuation["original_source_sha256"] == original.sha(
        original.OUT / "running_source.json")
    assert not continuation["first_case_reexecuted"]
    assert continuation["first_solver_returncode"] is None
    for path, digest in continuation["source_sha256"].items():
        assert original.sha(Path(path)) == digest
        result["evidence_sha256"][path] = digest
    result["evidence_sha256"][str(continuation_path)] = original.sha(continuation_path)
    result["evidence_sha256"][str(base_report)] = original.sha(base_report)
    for path in (original.OUT / "verification_source_contract_failure.json",
                 original.OUT / "verifier_continuation_before_capacitor_contract_fix.py",
                 original.OUT / "review_before_output_current_column_fix.py",
                 original.ROOT / "test_adl_offset_accounting_contract.py"):
        result["evidence_sha256"][str(path)] = original.sha(path)
    time_coverage = {}
    for index, case in enumerate(result["cases"]):
        path = original.OUT / case["id"] / "run.json"
        if not path.exists():
            assert partial
            continue
        record = json.loads(path.read_bytes())
        if index == 0:
            assert record["returncode"] is None
            assert record["status"] == "full_waveform_exitcode_unrecorded"
            assert not case["complete"]
        else:
            exit_path = path.parent / "solver_exit.json"
            exit_record = json.loads(exit_path.read_bytes())
            assert exit_record["returncode"] == record["returncode"]
            result["evidence_sha256"][str(exit_path)] = original.sha(exit_path)
        time_coverage[case["id"]] = bool(
            record.get("observed_last_time_s", 0) >= record["duration_s"] - 1e-12 and
            len(case.get("windows", [])) == 9)
    result.update(
        continuation_verifier_sha256=original.sha(Path(__file__)),
        first_solver_exitcode_unrecorded=True,
        observed_complete_time_windows=time_coverage,
        time_coverage_is_not_solver_exitcode_proof=True)
    text = json.dumps(result, indent=2) + "\n"
    digest = hashlib.sha256(text.encode()).hexdigest()
    path = original.OUT / f"continued_verification_{digest[:12]}.json"
    if path.exists():
        assert path.read_text(encoding="utf-8") == text
    else:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(text)
    print(json.dumps(dict(report=str(path), observed_time_coverage=time_coverage,
                         first_solver_exitcode_unrecorded=True,
                         full_system_gates_closed=0), indent=2))
    return result


if __name__ == "__main__":
    audit("--partial" in sys.argv)
