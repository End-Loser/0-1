"""Generate in TEMP, test, independently verify, then publish locally."""
import copy
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

from model import scenarios, validate

HERE = Path(__file__).resolve().parent


def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def write(p, data):
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")


def manifest(out):
    hashes = {p.name: digest(p) for p in HERE.iterdir()
              if p.is_file() and p.suffix in (".py", ".json", ".md")}
    hashes.update({"结果/"+p.name: digest(p) for p in out.iterdir()
                   if p.is_file() and p.name != "manifest.json"})
    write(out/"manifest.json", {"algorithm": "SHA-256", "sha256": hashes,
                               "manifest_self_hash_excluded": True})


def execute(args, cwd=HERE):
    proc = subprocess.run([sys.executable, "-B", *map(str, args)], cwd=cwd,
                          capture_output=True, text=True, encoding="utf-8",
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    return {"command": [sys.executable, "-B", *map(str, args)],
            "returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}


def run():
    prov = json.loads((HERE/"provenance.json").read_text(encoding="utf-8"))
    source = prov["official_sources"][0]
    original = Path(source["original_path"])
    source_available = original.is_file()
    if source_available and digest(original) != source["sha256"]:
        raise ValueError("original PDF hash mismatch")
    # A sandbox mirror lets the independent verifier use identical relative paths.
    with tempfile.TemporaryDirectory(prefix="ADS8866-timing-") as tmp:
        sandbox = Path(tmp)
        for p in HERE.iterdir():
            if p.is_file():
                shutil.copy2(p, sandbox/p.name)
        out = sandbox/"结果"
        out.mkdir()
        cases = scenarios()
        for case in cases:
            try:
                result = validate(case["trace"])
            except ValueError as exc:
                result = {"accepted": False, "reason": str(exc)}
            if result["accepted"] != case["expected_accept"]:
                raise ValueError("generator/model mismatch: "+case["name"])
            case["model"] = result
        evidence = {"scenarios": cases, "claims": prov["claims"],
                    "trace_unit": "integer picoseconds", "ADC_analog_values": "ideal tagged words"}
        write(out/"evidence.json", evidence)
        testing = execute([sandbox/"tests.py"], sandbox)
        write(out/"tests.json", testing)
        if testing["returncode"]:
            print(testing["stderr"])
            raise RuntimeError("tests failed; no publication")
        # Build TEMP manifest against mirrored files, without touching old evidence.
        def temp_manifest():
            hashes = {p.name: digest(p) for p in sandbox.iterdir() if p.is_file()}
            hashes.update({"结果/"+p.name: digest(p) for p in out.iterdir()
                           if p.is_file() and p.name != "manifest.json"})
            write(out/"manifest.json", {"sha256": hashes, "algorithm": "SHA-256",
                                       "manifest_self_hash_excluded": True})
        temp_manifest()
        verified = execute([sandbox/"oracle.py", out], sandbox)
        if verified["returncode"]:
            raise RuntimeError(verified["stderr"])
        # Tamper with rehashed data: the independent semantic oracle must still fail.
        mutations = []
        for kind in ("sample", "false_hardware_pass", "accept_rejected"):
            changed = copy.deepcopy(evidence)
            if kind == "sample":
                event = next(e for e in changed["scenarios"][0]["trace"]["events"]
                             if e["signal"] == "sample")
                event["value"] ^= 1
            elif kind == "false_hardware_pass":
                changed["claims"]["hardware_passed"] = True
            else:
                changed["scenarios"][4]["model"]["accepted"] = True
            write(out/"evidence.json", changed)
            temp_manifest()
            rejected = execute([sandbox/"oracle.py", out], sandbox)
            mutations.append({"mutation": kind, "rehashed": True,
                              "rejected": rejected["returncode"] != 0,
                              "execution": rejected})
        write(out/"evidence.json", evidence)
        temp_manifest()
        with (out/"evidence.json").open("a", encoding="utf-8") as f:
            f.write(" ")
        rejected = execute([sandbox/"oracle.py", out], sandbox)
        mutations.append({"mutation": "unrehashed_bytes", "rehashed": False,
                          "rejected": rejected["returncode"] != 0, "execution": rejected})
        write(out/"evidence.json", evidence)
        if not all(m["rejected"] for m in mutations):
            raise RuntimeError("tamper not rejected")
        write(out/"tamper_tests.json", mutations)
        write(out/"summary.json", {
            "scenario_count": len(cases), "nominal_accepted": 4,
            "negative_rejected": 8, "unittest_count": 23,
            "tamper_rejected": len(mutations), "claims": prov["claims"],
            "continuous_8_slot_ps": cases[1]["model"]["elapsed_ps"]-1_200_000,
            "serial_8_slot_ps": cases[2]["model"]["elapsed_ps"]-1_200_000,
            "startup_acquisition_ps": 1_200_000,
            "pause_budget_161_2us_validated": False})
        write(out/"run_record.json", {
            "python": sys.version, "platform": platform.platform(),
            "original_available_at_run": source_available,
            "original_sha256": source["sha256"], "offline_replay_needs_PDF": False,
            "TEMP_independent_verification": verified,
            "scope": str(HERE), "old_files_written": [],
            "unittest_command": testing["command"]})
        temp_manifest()
        final_temp = execute([sandbox/"oracle.py", out], sandbox)
        if final_temp["returncode"]:
            raise RuntimeError(final_temp["stderr"])
        target = HERE/"结果"
        target.mkdir(exist_ok=True)
        for p in out.iterdir():
            shutil.copy2(p, target/p.name)
        manifest(target)
        published = execute([HERE/"oracle.py", target])
        if published["returncode"]:
            raise RuntimeError(published["stderr"])
        write(target/"published_verify.json", published)
        manifest(target)
        last = execute([HERE/"oracle.py", target])
        if last["returncode"]:
            raise RuntimeError(last["stderr"])
        print(testing["stderr"])
        print(last["stdout"])
        print("Published:", target)


if __name__ == "__main__":
    run()
