"""Independent full archived state comparison and array midpoint equations."""
import hashlib
import json
from pathlib import Path

import numpy as np

from verify_finite_copper_cavity import geometry, node_divergence, quadrature_q
from verify_finite_pec_cavity import curl_array, transpose_array
from verify_fine_copper_ringdown import decay

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "\u94dc\u8154CG\u5b8c\u6574\u72b6\u6001\u5bf9\u7167"
BASE = ROOT / "\u7ec6\u7f51\u683c\u94dc\u8154\u77ed\u7a97\u7ed3\u679c"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit():
    summary = json.loads((OUT / "summary.json").read_text(encoding="utf-8"))
    assert sha(BASE / "summary.json") == summary["LU_summary_sha256"]
    assert sha(OUT / "configurations.json") == summary["configuration_sha256"]
    configs = json.loads((OUT / "configurations.json").read_text(encoding="utf-8"))
    original = json.loads((BASE / "summary.json").read_text(encoding="utf-8"))
    assert len(summary["cases"]) == len(configs) == len(original["cases"]) == 2
    for name, digest in summary["source_sha256"].items():
        assert sha(ROOT / name) == digest
    modelpath = ROOT / "\u539a\u94dc\u8fb9\u754c\u57fa\u51c6\u7ed3\u679c/passive_surface_models.json"
    assert sha(modelpath) == summary["model_sha256"]
    model = next(row for row in json.loads(modelpath.read_text(encoding="utf-8"))["models"]
                 if row["terms"] == 64)
    records = []
    for config, row, lu_row in zip(configs, summary["cases"], original["cases"]):
        assert all(row[key] == value for key, value in config.items())
        assert row["cg_rtol"] == 1e-13 and row["maxiter"] == 80
        assert row["LU_file"] == lu_row["file"] and row["LU_sha256"] == lu_row["sha256"]
        path, lu_path = OUT / row["file"], BASE / row["LU_file"]
        assert sha(path) == row["sha256"] and sha(lu_path) == row["LU_sha256"]
        with np.load(path) as data, np.load(lu_path) as lu:
            for key in ("shape", "mode", "lengths_m", "dt_s", "duration_s", "R_ohm",
                        "L_H", "sample_time_s", "snapshot_step"):
                np.testing.assert_array_equal(data[key], lu[key])
            np.testing.assert_array_equal(data["R_ohm"], model["R_ohm"])
            np.testing.assert_array_equal(data["L_H"], model["L_H"])
            shape = tuple(map(int, data["shape"]))
            dt = float(data["dt_s"])
            assert row["steps"] == int(round(float(data["duration_s"]) / dt))
            assert len(data["scaled_residual"]) == len(data["cg_iterations"]) == row["steps"]
            assert np.all(np.isfinite(data["scaled_residual"]))
            assert np.all(data["scaled_residual"] <= 5e-13)
            assert np.all((data["cg_iterations"] >= 0) & (data["cg_iterations"] <= 80))
            m, q, edges, scale, _, _ = geometry(shape, data["lengths_m"])
            rr = scale[:, None] * data["R_ohm"]
            ll = scale[:, None] * data["L_H"]
            state_errors, wall_differences, electric, magnetic, charges, rl_errors = [], [], [], [], [], []
            for index in range(9):
                for suffix in ("before", "after"):
                    e, b, state = (data["snapshot_" + prefix + "_" + suffix][index]
                                   for prefix in ("e", "b", "i"))
                    er, br, ir = (lu["snapshot_" + prefix + "_" + suffix][index]
                                  for prefix in ("e", "b", "i"))
                    difference = m @ (e - er)**2 + q @ (b - br)**2 + np.sum(ll * (state - ir)**2)
                    reference = m @ er**2 + q @ br**2 + np.sum(ll * ir**2)
                    state_errors.append(float(np.sqrt(difference / reference)))
                e0, b0, i0 = (data["snapshot_" + name][index]
                              for name in ("e_before", "b_before", "i_before"))
                e1, b1, i1 = (data["snapshot_" + name][index]
                              for name in ("e_after", "b_after", "i_after"))
                em, bm, im = (e0 + e1) / 2, (b0 + b1) / 2, (i0 + i1) / 2
                j = data["snapshot_j_mid"][index]
                jr = lu["snapshot_j_mid"][index]
                wall_differences.append(float(np.linalg.norm(j - jr) / max(np.linalg.norm(jr), 1e-25)))
                total_j = np.zeros(len(m))
                np.add.at(total_j, edges, j)
                ctqb = transpose_array(q * bm, shape)
                electric.append(float(np.linalg.norm(m * (e1 - e0) / dt - ctqb + total_j) /
                                      (np.linalg.norm(ctqb) + np.linalg.norm(total_j))))
                ce = curl_array(em, shape)
                magnetic.append(float(np.linalg.norm((b1 - b0) / dt + ce) /
                                      max(np.linalg.norm(ce), 1e-25)))
                drho = node_divergence(m * (e1 - e0), shape) / dt
                expected = -node_divergence(total_j, shape)
                charges.append(float(np.linalg.norm(drho - expected) /
                                     max(np.linalg.norm(drho), np.linalg.norm(expected), 1e-25)))
                left = ll * (i1 - i0) / dt
                right = rr * (j[:, None] - im)
                rl_errors.append(float(np.linalg.norm(left - right) /
                                       max(np.linalg.norm(left), np.linalg.norm(right), 1e-25)))
            u, heat = data["total_energy_J"], data["cumulative_wall_heat_J"]
            assert np.all(np.isfinite(u)) and np.all(np.isfinite(heat)) and np.all(u > 0)
            assert np.all(np.diff(heat) >= 0)
            closure = float(np.max(abs(u + heat - u[0])) / u[0])
            energy_difference = float(np.max(abs(u - lu["total_energy_J"])) / u[0])
            heat_difference = float(np.max(abs(heat - lu["cumulative_wall_heat_J"])) / u[0])
            probe_difference = float(np.max(abs(data["modal_e_probe"] - lu["modal_e_probe"])))
            assert max(state_errors) < 1e-9 and max(wall_differences) < 1e-8
            assert closure < 1e-7 and energy_difference < 1e-9 and heat_difference < 1e-9
            assert probe_difference < 1e-9
            assert max(electric) < 1e-7 and max(magnetic) < 1e-7
            assert max(charges) < 1e-6 and max(rl_errors) < 1e-5
            assert float(np.max(np.diff(u))) / u[0] < 1e-10
            frequency, fits = decay(data["sample_time_s"], u, data["modal_e_probe"])
            fref, qref = quadrature_q(data["mode"], data["lengths_m"], model["sigma_S_per_m"])
            qerror = abs(fits[-1]["Q_decay"] / qref - 1)
            ferror = abs(frequency / fref - 1)
            records.append(dict(
                dt_s=dt, steps=row["steps"], full_state_frame_comparisons=18,
                max_full_state_energy_norm_difference=max(state_errors),
                max_wall_current_relative_difference=max(wall_differences),
                energy_difference=energy_difference, heat_difference=heat_difference,
                probe_difference=probe_difference, energy_closure=closure,
                electric_residual=max(electric), magnetic_residual=max(magnetic),
                charge_residual=max(charges), RL_residual=max(rl_errors),
                short_window_Q_error=float(qerror), short_window_frequency_error=float(ferror),
                short_window_frequency_Q_pass=bool(qerror < .01 and ferror < .01),
                speedup=float(row["LU_stepping_seconds"] / row["stepping_seconds"]),
                CG_stepping_seconds=row["stepping_seconds"]))
    assert not records[0]["short_window_frequency_Q_pass"]
    assert records[1]["short_window_frequency_Q_pass"]
    for key in ("full_long_time_passed", "manufactured_cavity_passed", "full_joint_simulation_passed"):
        assert summary[key] is False
    return dict(
        independent_full_state_comparison_passed=True, cases=records,
        full_state_frame_comparisons=36, summary_sha256=sha(OUT / "summary.json"),
        verifier_sha256=sha(Path(__file__)),
        helper_sha256={name: sha(ROOT / name) for name in (
            "verify_finite_copper_cavity.py", "verify_finite_pec_cavity.py",
            "verify_fine_copper_ringdown.py")},
        full_long_time_passed=False, manufactured_cavity_passed=False,
        full_joint_simulation_passed=False)


if __name__ == "__main__":
    result = audit()
    (OUT / "verification.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
