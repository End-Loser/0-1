"""Run in OS TEMP; publish generated evidence only inside this batch."""

import argparse
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import unittest

import tests
from runner import generate, save
from verify import verify


def external_verify(out, code):
    command = [sys.executable, "-B", str(code / "verify.py"),
               "--results", str(out), "--code-dir", str(code)]
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    return command, json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent / "结果")
    args = parser.parse_args()
    code = Path(__file__).resolve().parent
    out = args.out.resolve()
    if out.parent != code:
        raise ValueError("published results must be a direct child of this batch")
    if out.exists() and not out.is_dir():
        raise ValueError("result destination is not a directory")
    with tempfile.TemporaryDirectory(prefix="ewc-handshake-") as temp:
        work = Path(temp) / "results"
        summary = generate(work)
        verify(work, code)
        tests.RESULTS = work
        stream = io.StringIO()
        suite = unittest.defaultTestLoader.loadTestsFromModule(tests)
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
        (work / "tests.txt").write_text(stream.getvalue(), encoding="utf-8")
        test_report = dict(tests=result.testsRun, failures=len(result.failures),
                           errors=len(result.errors), skipped=len(result.skipped),
                           passed=result.wasSuccessful())
        save(work / "tests.json", test_report)
        if not result.wasSuccessful():
            print(stream.getvalue())
            raise SystemExit(1)
        command1, verified = external_verify(work, code)
        save(work / "verify.json", verified)
        out.mkdir(exist_ok=True)
        for source in work.iterdir():
            shutil.copy2(source, out / source.name)
        command2, published_verified = external_verify(out, code)
        save(out / "published_verify.json", published_verified)
        record = dict(
            python=sys.version, platform=platform.platform(),
            intermediate_directory="OS TEMP; automatically cleaned",
            subprocess_command_count=2, subprocess_commands=[command1, command2],
            artifacts_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in out.iterdir() if p.is_file() and p.name != "run_record.json"},
            hardware_simulator_commands=0, new_download_commands=0,
            tests=test_report, verification=published_verified,
        )
        save(out / "run_record.json", record)
        print(json.dumps(dict(summary=summary, tests=test_report, verify=published_verified,
                              subprocess_commands=2, output=str(out)), ensure_ascii=False))


if __name__ == "__main__":
    main()
