"""Run in TEMP and publish only complete numerical evidence in this directory."""

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
from runner import run, write_json
from verify import verify
import tests

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=HERE / "结果")
    args = parser.parse_args()
    out = args.out.resolve()
    if out == HERE or HERE not in out.parents:
        parser.error("final output must be a child directory of this calibration workspace")
    started = datetime.now(timezone.utc).isoformat()
    with tempfile.TemporaryDirectory(prefix="ewc-calibration-") as temp:
        root = Path(temp)
        summary = run(root)
        first = verify(root, HERE)
        os.environ["EWC_CAL_RESULTS"] = str(root)
        stream = io.StringIO()
        suite = unittest.defaultTestLoader.loadTestsFromModule(tests)
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
        (root / "tests.txt").write_text(stream.getvalue(), encoding="utf-8")
        test_summary = {
            "tests_run": result.testsRun, "failures": len(result.failures),
            "errors": len(result.errors), "skipped": len(result.skipped),
            "passed": result.wasSuccessful(),
            "numeric_control_mutations": 8, "hash_mutations": 1, "system_claim_mutations": 1,
        }
        write_json(root / "tests.json", test_summary)
        if not result.wasSuccessful() or not summary["expectations_match"]:
            print(stream.getvalue())
            print(json.dumps(test_summary))
            return 1
        summary["verification"] = first
        summary["tests"] = test_summary
        write_json(root / "summary.json", summary)
        second = verify(root, HERE)
        write_json(root / "verify.json", second)
        record = {
            "started_utc": started, "finished_utc": datetime.now(timezone.utc).isoformat(),
            "python": platform.python_version(), "platform": platform.platform(),
            "temporary_outputs": "OS TEMP, cleaned on completion",
            "final_output": str(out), "stdlib_only": True,
            "independent_checks": second["checks"], "tests": test_summary,
            "sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in sorted(root.iterdir()) if p.is_file()},
        }
        write_json(root / "run_record.json", record)
        out.mkdir(parents=True, exist_ok=True)
        for item in root.iterdir():
            shutil.copy2(item, out / item.name)
        # Check the published files, including code provenance, after copying.
        verify(out, HERE)
        print(json.dumps(dict(output=str(out), verification=second, tests=test_summary,
                              budget_cases=summary["budget_cases"],
                              failed_screen_cases=summary["retained_failed_screen_cases"]),
                         ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
