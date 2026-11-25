"""Independent closed-form loss/phase checks; never imports the runner."""

import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

import numpy as np

ROOT = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def components(f, dk, loss_tangent):
    base = 2 * math.pi * f * math.sqrt(1.25663706212e-6 * 8.8541878128e-12 * dk)
    factor = math.sqrt(1 + loss_tangent**2)
    beta = base * math.sqrt((factor + 1) / 2)
    alpha = base * loss_tangent / math.sqrt(2 * (factor + 1))
    return alpha + 1j * beta


def verify():
    folder = ROOT / "结果"
    summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    manifest = json.loads((ROOT / "source_manifest.json").read_text(encoding="utf-8"))
    assert sha(ROOT / "source_manifest.json") == summary["source_manifest_sha256"]
    assert summary["source_count"] == manifest["source_count"] == 3
    assert manifest["originals_redistributed"] is False
    for source in manifest["sources"]:
        snapshot = ROOT / source["snapshot_path"]
        assert sha(snapshot) == source["snapshot_sha256"]
        assert source["url"].startswith("https://www.rogerscorp.com/")
    source_text = (ROOT / manifest["sources"][0]["snapshot_path"]).read_text(encoding="utf-8")
    for token in ["2.20", "0.0009", "10 GHz", "8 GHz - 40 GHz", "080822",
                  "Typical values should not be used for specification limits"]:
        assert token in source_text, token
    p = summary["source_parameters"]
    assert p["process_Dk"]["frequency_Hz"] == 10e9
    assert p["process_Dk"]["value"] == 2.2 and p["process_Dk"]["tolerance"] == .02
    assert p["design_Dk"]["frequency_min_Hz"] == 8e9
    assert p["tan_delta"]["frequency_Hz"] == 10e9 and p["tan_delta"]["typical_not_limit"]
    for flag in ["material_3GHz_guarantee_passed", "causal_dispersion_model_passed",
                 "actual_microstrip_passed", "thick_block_replacement_allowed",
                 "roughness_model_passed", "full_joint_simulation_passed"]:
        assert summary[flag] is False
    for file, digest in summary["source_sha256"].items():
        assert sha(ROOT / file) == digest
    path = folder / "loss_models.npz"
    assert sha(path) == summary["loss_file_sha256"]
    with np.load(path) as d:
        f = d["frequency_Hz"]
        assert len(f) == 101 and f[0] == 2.5e9 and f[-1] == 3.5e9
        expected_sigma = 2 * math.pi * 3e9 * 8.8541878128e-12 * 2.2 * .0009
        np.testing.assert_allclose(d["sigma_S_per_m"], expected_sigma, rtol=1e-13)
        tand = np.array([components(x, 2.2, .0009) for x in f])
        cond = np.array([components(x, 2.2, expected_sigma /
                                    (2 * math.pi * x * 8.8541878128e-12 * 2.2)) for x in f])
        np.testing.assert_allclose(d["gamma_tandelta_per_m"].real, tand.real, rtol=1e-12)
        np.testing.assert_allclose(d["gamma_tandelta_per_m"].imag, tand.imag, rtol=1e-12)
        np.testing.assert_allclose(d["gamma_conductivity_per_m"], cond, rtol=1e-12)
        error = abs(cond.real / tand.real - 1)
        assert max(error) > .19 and summary["constant_sigma_band_loss_1percent_pass"] is False
        np.testing.assert_allclose(summary["constant_sigma_loss_relative_error_max"], max(error), rtol=1e-12)
        np.testing.assert_allclose(tand[50], cond[50], rtol=1e-13)
    cases = summary["sensitivity_cases"]
    assert len(cases) == summary["sensitivity_case_count"] == 36
    cte = {"fixed": 0, "x": 31e-6, "y": 48e-6, "z": 237e-6}
    expected_keys = {(dk, dt, axis) for dk in (2.18, 2.2, 2.22)
                     for dt in (-20, 0, 60) for axis in cte}
    observed_keys = set()
    phase0 = 2 * math.pi * 3e9 * math.sqrt(1.25663706212e-6 * 8.8541878128e-12 * 2.2) * .12
    for row in cases:
        dk, dt, axis = row["dk_process_hypothesis"], row["delta_temperature_C"], row["length_axis_hypothesis"]
        observed_keys.add((dk, dt, axis))
        relative = math.sqrt(dk / 2.2) * math.sqrt(1 - 125e-6 * dt) * (1 + cte[axis] * dt)
        np.testing.assert_allclose(row["phase_relative_error"], relative - 1, atol=1e-14)
        np.testing.assert_allclose(row["phase_error_degree"], (relative - 1) * phase0 * 180 / math.pi,
                                   atol=1e-11)
        np.testing.assert_allclose(row["filled_cavity_frequency_relative_error"], 1 / relative - 1,
                                   atol=1e-14)
        assert row["allocated_phase_0p2percent_pass"] == (abs(relative - 1) < .002)
    assert observed_keys == expected_keys
    assert any(not x["allocated_phase_0p2percent_pass"] for x in cases)
    tests = subprocess.run([sys.executable, "-B", str(ROOT / "test_dielectric.py")],
                           capture_output=True, text=True, check=True)
    result = {"independent_verification_pass": True, "source_snapshots_checked": 3,
              "frequency_points_checked": 101, "sensitivity_cases_checked": 36,
              "constant_sigma_alpha_error_max": float(max(error)),
              "constant_sigma_alpha_error_low_end": float(cond[0].real / tand[0].real - 1),
              "constant_sigma_alpha_error_high_end": float(cond[-1].real / tand[-1].real - 1),
              "test_output": tests.stdout + tests.stderr,
              "summary_sha256": sha(folder / "summary.json"),
              "verifier_sha256": sha(Path(__file__)),
              "tests_sha256": sha(ROOT / "test_dielectric.py"),
              "material_3GHz_guarantee_passed": False, "full_joint_simulation_passed": False}
    (folder / "verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    verify()
