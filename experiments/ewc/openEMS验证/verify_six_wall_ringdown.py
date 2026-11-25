"""Independent causal midpoint snapshots, Joule heat and long-time decay checks."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
from verify_finite_copper_cavity import geometry, node_divergence
from verify_finite_pec_cavity import curl_array, transpose_array

ROOT = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    folder = ROOT / "六壁长时衰减结果"
    summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    archive = ROOT / "六面铜壁频域结果" / "copper_4x3x2_011_m64.npz"
    assert sha(archive) == summary["frequency_archive_sha256"]
    with np.load(archive) as d:
        continuous_pole = complex(d["fitted_pole_Hz"])
    modelpath = ROOT / "厚铜边界基准结果" / "passive_surface_models.json"
    assert sha(modelpath) == summary["model_sha256"]
    model = next(m for m in json.loads(modelpath.read_text(encoding="utf-8"))["models"] if m["terms"] == 64)
    for file, digest in summary["source_sha256"].items():
        assert sha(ROOT / file) == digest
    records = []
    for row in summary["cases"]:
        path = folder / row["file"]
        assert sha(path) == row["sha256"]
        with np.load(path) as d:
            shape, dt = tuple(map(int, d["shape"])), float(d["dt_s"])
            m, q, edges, scale, _, _ = geometry(shape, d["lengths_m"])
            np.testing.assert_array_equal(d["R_ohm"], model["R_ohm"])
            np.testing.assert_array_equal(d["L_H"], model["L_H"])
            rr, ll = scale[:, None] * d["R_ohm"], scale[:, None] * d["L_H"]
            electrical, magnetic, rl, boundary, charge, energy_step = [], [], [], [], [], []
            for index in range(41):
                e0, b0, i0 = (d["snapshot_" + key][index] for key in ["e_before", "b_before", "i_before"])
                e1, b1, i1 = (d["snapshot_" + key][index] for key in ["e_after", "b_after", "i_after"])
                em, bm, im = (e0 + e1) / 2, (b0 + b1) / 2, (i0 + i1) / 2
                j = d["snapshot_j_mid"][index]
                total_j = np.zeros(len(m))
                np.add.at(total_j, edges, j)
                cth = transpose_array(q * bm, shape)
                electric = m * (e1 - e0) / dt - cth + total_j
                electrical.append(np.linalg.norm(electric) / (np.linalg.norm(cth) + np.linalg.norm(total_j)))
                magnetic.append(np.linalg.norm((b1 - b0) / dt + curl_array(em, shape)) /
                                max(np.linalg.norm(curl_array(em, shape)), 1e-25))
                lr = ll * (i1 - i0) / dt
                resistive = rr * (j[:, None] - im)
                rl.append(np.linalg.norm(lr - resistive) /
                          max(np.linalg.norm(lr), np.linalg.norm(resistive), 1e-25))
                boundary.append(np.linalg.norm(em[edges] - resistive.sum(axis=1)) /
                                max(np.linalg.norm(em[edges]), 1e-25))
                drho = node_divergence(m * (e1 - e0), shape) / dt
                divergence_j = -node_divergence(total_j, shape)
                charge.append(np.linalg.norm(drho - divergence_j) /
                              max(np.linalg.norm(drho), np.linalg.norm(divergence_j), 1e-25))
                u0 = .5 * (m @ e0**2 + q @ b0**2 + np.sum(ll * i0**2))
                u1 = .5 * (m @ e1**2 + q @ b1**2 + np.sum(ll * i1**2))
                heat = dt * np.sum(rr * (j[:, None] - im)**2)
                energy_step.append(abs(u1 - u0 + heat) / max(u0, u1, 1e-25))
            t, u, heat = d["sample_time_s"], d["total_energy_J"], d["cumulative_wall_heat_J"]
            assert np.all(np.diff(t) > 0) and abs(t[-1] - 8e-6) < 1e-20
            assert np.all(u >= 0) and np.all(np.diff(heat) >= 0)
            closure = max(abs(u + heat - u[0])) / u[0]
            assert closure < 1e-7 and max(np.diff(u)) / u[0] < 1e-10
            assert max(electrical) < 1e-7 and max(magnetic) < 1e-7 and max(charge) < 1e-6
            # Extreme tiny/large poles produce subtractive roundoff in
            # individual RL differences; use a documented normwise gate.
            assert max(rl) < 1e-5 and max(boundary) < 1e-5 and max(energy_step) < 1e-9
            mapped = 2 / dt * np.arctan(2 * np.pi * continuous_pole * dt / 2)
            expected_q = mapped.real / (2 * mapped.imag)
            probe = d["modal_e_probe"]
            crossings = np.flatnonzero((probe[:-1] <= 0) & (probe[1:] > 0) & (t[:-1] >= 1e-6))
            crossing_times = t[crossings] - probe[crossings] * (
                t[crossings + 1] - t[crossings]) / (probe[crossings + 1] - probe[crossings])
            observed_frequency = (len(crossing_times) - 1) / (crossing_times[-1] - crossing_times[0])
            fits = []
            for end in (5e-6, 8e-6):
                selected = (t >= 1e-6) & (t <= end)
                tx, logu = t[selected], np.log(u[selected])
                slope = ((tx - tx.mean()) @ (logu - logu.mean())) / sum((tx - tx.mean())**2)
                frequency = observed_frequency
                decay_q = -2 * np.pi * frequency / slope
                fits.append(decay_q)
            assert abs(fits[1] / fits[0] - 1) < .01
            assert abs(fits[-1] / expected_q - 1) < .01
            frequency_expected = mapped.real / (2 * np.pi)
            assert abs(row["observed_frequency_Hz"] / frequency_expected - 1) < .001
            assert abs(observed_frequency / row["observed_frequency_Hz"] - 1) < .001
            records.append({"dt_s": dt, "Q_decay": fits[-1], "midpoint_expected_Q": expected_q,
                            "zero_crossing_frequency_Hz": float(observed_frequency),
                            "energy_closure": float(closure), "electric_residual": max(electrical),
                            "magnetic_residual": max(magnetic), "all_node_charge_residual": max(charge),
                            "RL_residual": max(rl), "wall_algebra_residual": max(boundary),
                            "snapshot_energy_closure": max(energy_step)})
    tests = subprocess.run([sys.executable, "-B", str(ROOT / "test_finite_copper_cavity.py")],
                           capture_output=True, text=True, check=True)
    result = {"independent_verification_pass": True, "snapshots_checked": 82,
              "records": records, "summary_sha256": sha(folder / "summary.json"),
              "verifier_sha256": sha(Path(__file__)), "test_output": tests.stdout + tests.stderr,
              "array_helper_sha256": {p: sha(ROOT / p) for p in
                                    ["verify_finite_copper_cavity.py", "verify_finite_pec_cavity.py"]},
              "fine_spatial_transient_accuracy_passed": False, "full_joint_simulation_passed": False}
    (folder / "verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
