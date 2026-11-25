"""Frequency-domain hypothetical homogeneous TEM material screening."""

import hashlib
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
EPS = 8.8541878128e-12
MU = 1.25663706212e-6
DK = 2.2
TAN = .0009
F0 = 3e9
LENGTH = .12
CTE = {"fixed": 0., "x": 31e-6, "y": 48e-6, "z": 237e-6}


def gamma(frequency, dk, loss, kind="tan_delta"):
    f = np.asarray(frequency, float)
    if (not np.all(np.isfinite(f)) or np.any(f <= 0) or not np.isfinite(dk) or dk <= 0 or
            not np.isfinite(loss) or loss < 0 or kind not in ("tan_delta", "conductivity")):
        raise ValueError("Finite positive f/Dk, nonnegative loss and valid model required.")
    omega = 2 * np.pi * f
    eps = EPS * dk * (1 - 1j * loss) if kind == "tan_delta" else EPS * dk - 1j * loss / omega
    return np.sqrt(-omega**2 * MU * eps)


def budget(dk, delta_temperature, axis):
    if (axis not in CTE or not np.isfinite(dk) or dk <= 0 or
            not np.isfinite(delta_temperature) or not 0 <= 23 + delta_temperature <= 100):
        raise ValueError("Budget uses explicit axis and temperature within CTE source range.")
    adjusted = dk * (1 - 125e-6 * delta_temperature)
    relative_phase = np.sqrt(adjusted / DK) * (1 + CTE[axis] * delta_temperature)
    phase0 = 2 * np.pi * F0 * np.sqrt(MU * EPS * DK) * LENGTH
    return {"dk_process_hypothesis": dk, "delta_temperature_C": delta_temperature,
            "length_axis_hypothesis": axis, "adjusted_dk": adjusted,
            "phase_relative_error": float(relative_phase - 1),
            "phase_error_degree": float((relative_phase - 1) * phase0 * 180 / np.pi),
            "filled_cavity_frequency_relative_error": float(1 / relative_phase - 1),
            "allocated_phase_0p2percent_pass": bool(abs(relative_phase - 1) < .002)}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    output = ROOT / "结果"
    output.mkdir(exist_ok=True)
    f = np.linspace(2.5e9, 3.5e9, 101)
    sigma = 2 * np.pi * F0 * EPS * DK * TAN
    gt, gs = gamma(f, DK, TAN), gamma(f, DK, sigma, "conductivity")
    path = output / "loss_models.npz"
    np.savez_compressed(path, frequency_Hz=f, gamma_tandelta_per_m=gt,
                        gamma_conductivity_per_m=gs, sigma_S_per_m=sigma,
                        assumed_dk=DK, assumed_tan_delta=TAN,
                        fitted_frequency_Hz=F0, path_length_m=LENGTH)
    cases = [budget(dk, dt, axis) for dk in (2.18, 2.2, 2.22)
             for dt in (-20, 0, 60) for axis in CTE]
    summary = {
        "scope": "hypothetical homogeneous fully-filled TEM screening, not actual PCB",
        "source_count": 3,
        "source_parameters": {
            "S01_printed_revision": "Revised 1603 080822 Publication #92-101, copyright 2022",
            "process_Dk": {"value": 2.2, "tolerance": .02, "frequency_Hz": 10e9,
                           "condition": "Z direction, approx 23 C, IPC-TM-650 2.5.5.5"},
            "design_Dk": {"value": 2.2, "frequency_min_Hz": 8e9, "frequency_max_Hz": 40e9},
            "tan_delta": {"value": .0009, "frequency_Hz": 10e9, "typical_not_limit": True},
            "tan_delta_1MHz": .0004, "TCDk_ppm_per_C": -125,
            "CTE_xyz_ppm_per_C": [31, 48, 237],
            "standard_0p062inch_thickness_mm": 1.575,
            "standard_0p062inch_tolerance_mm": .003 * 25.4},
        "assumed_3GHz_Dk": DK, "assumed_3GHz_tan_delta": TAN,
        "assumed_path_length_m": LENGTH, "assumed_center_Hz": F0,
        "loss_frequency_points": 101, "sensitivity_cases": cases,
        "sensitivity_case_count": 36,
        "phase_allocation_fraction_hypothesis": .002,
        "conductivity_fit_S_per_m": float(sigma),
        "constant_sigma_loss_relative_error_max": float(max(abs(gs.real / gt.real - 1))),
        "constant_sigma_band_loss_1percent_pass": bool(np.all(abs(gs.real / gt.real - 1) < .01)),
        "source_manifest_sha256": sha(ROOT / "source_manifest.json"),
        "loss_file_sha256": sha(path),
        "source_sha256": {p: sha(ROOT / p) for p in ["run_dielectric.py", "collect_sources.py"]},
        "material_3GHz_guarantee_passed": False, "causal_dispersion_model_passed": False,
        "actual_microstrip_passed": False, "thick_block_replacement_allowed": False,
        "roughness_model_passed": False, "full_joint_simulation_passed": False}
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("101 loss frequency points; 36 explicit sensitivity hypotheses; band-loss pass:",
          summary["constant_sigma_band_loss_1percent_pass"])


if __name__ == "__main__":
    main()
