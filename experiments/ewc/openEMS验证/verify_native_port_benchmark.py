"""Independent post-processing audit of saved native-solver evidence.

Uses a rectangular DFT with explicit endpoint correction (runner uses
trapezoids) and derives the shunt
reference from parallel admittance. It is not an independent field solver.
"""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


def dft(t, v, frequency):
    dt = np.mean(np.diff(t))
    if not np.allclose(np.diff(t), dt, rtol=1e-8, atol=1e-20):
        raise AssertionError("Nonuniform probe sampling.")
    spectrum = 2 * dt * np.sum(
        v[:, None] * np.exp(-2j * np.pi * t[:, None] * frequency), axis=0)
    # Finite pulse windows need not end at exactly zero. Correct the two
    # half-weight endpoints explicitly, rather than relaxing audit tolerance.
    endpoints = dt * (
        v[0] * np.exp(-2j * np.pi * t[0] * frequency) +
        v[-1] * np.exp(-2j * np.pi * t[-1] * frequency))
    return spectrum - endpoints


def verify(root):
    batches, count = [], 0
    for summary_path in sorted(root.glob("*/summary.json")):
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        for item in summary["raw_manifest"]:
            raw = Path(item["path"])
            assert hashlib.sha256(raw.read_bytes()).hexdigest() == item["sha256"]
        max_error = 0
        for case in summary["cases"]:
            archive = np.load(summary_path.parent / (case["case"] + ".npz"))
            f = archive["frequency_Hz"]
            folder = Path(case["raw_folder"])
            spectra = []
            width = summary["geometry_mm"]["width"] * 1e-3
            for number in (1, 2):
                u = np.loadtxt(folder / f"port_ut{number}", comments="%")
                ha = np.loadtxt(folder / f"port_it{number}a", comments="%")
                hb = np.loadtxt(folder / f"port_it{number}b", comments="%")
                voltage = dft(u[:, 0], u[:, 1], f)
                current = width * .5 * (
                    dft(ha[:, 0], ha[:, 2], f) + dft(hb[:, 0], hb[:, 2], f))
                spectra.append((voltage, current))
            v1, i1 = spectra[0]
            v2, i2 = spectra[1]
            inc = (v1 + 50 * i1) / 2
            r = (v1 - 50 * i1) / (2 * inc)
            t = (v2 + 50 * i2) / (2 * inc)
            err = max(float(np.max(abs(r - archive["s11"]))),
                      float(np.max(abs(t - archive["s21"]))))
            max_error = max(err, max_error)
            assert err < 1e-6, f"{case['case']}: DFT difference {err:g}"
            resistance = case["load_ohm"]
            impedance = 1 / (1 / 50 + 1 / resistance)
            reflection = (impedance - 50) / (impedance + 50)
            beta = 2 * np.pi * f / 299792458
            m1, m2 = summary["measurement_planes_mm"]
            expected_r = reflection * np.exp(
                -2j * beta * (summary["shunt_position_mm"] - m1) * 1e-3)
            expected_t = (1 + reflection) * np.exp(-1j * beta * (m2 - m1) * 1e-3)
            assert np.max(abs(expected_r - archive["expected_s11"])) < 1e-14
            assert np.max(abs(expected_t - archive["expected_s21"])) < 1e-14
            assert np.all(np.isfinite(archive["load_z"]))
            count += 1
        batches.append({
            "batch": summary_path.parent.name,
            "case_count": len(summary["cases"]),
            "independent_DFT_max_complex_difference": max_error,
            "transmission_gate": summary["finest_transmission_and_power_1percent_pass"],
            "full_port_gate": summary["finest_analytic_1percent_pass"],
        })
    assert batches
    time_changes = []
    base = root / "主矩阵"
    extended = root / "时间复核"
    if base.is_dir() and extended.is_dir():
        for load in (25, 50, 100):
            a = np.load(base / f"dx0.5_ny80_load{load}_t8.npz")
            b = np.load(extended / f"dx0.5_ny80_load{load}_t16.npz")
            time_changes.append({
                "load_ohm": load,
                "reflection_max_change": float(np.max(abs(a["s11"] - b["s11"]))),
                "transmission_max_change": float(np.max(abs(a["s21"] - b["s21"]))),
            })
    result = {
        "evidence_audit_pass": True, "verified_case_count": count,
        "batches": batches, "time_window_checks": time_changes,
        "scope": "hashes, independent DFT and analytic circuit reference only",
        "DFT_method": "rectangular summation with explicit half-endpoint correction",
        "not_claimed": "independent FDTD, copper loss, full physical port or chip validation",
    }
    (root / "verification.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    verify(parser.parse_args().root)
