"""Independent fine-grid short-window state equations and decay measurements."""
import hashlib
import json
from pathlib import Path

import numpy as np

from verify_finite_copper_cavity import geometry, node_divergence, quadrature_q
from verify_finite_pec_cavity import curl_array, transpose_array

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "\u7ec6\u7f51\u683c\u94dc\u8154\u77ed\u7a97\u7ed3\u679c"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def decay(t, energy, probe):
    t, energy, probe = map(np.asarray, (t, energy, probe))
    assert np.all(np.isfinite(energy)) and np.all(energy > 0)
    crossings = np.flatnonzero((probe[:-1] <= 0) & (probe[1:] > 0) & (t[:-1] >= 2e-9))
    assert len(crossings) >= 10
    crossing_times = t[crossings] - probe[crossings] * (
        t[crossings + 1] - t[crossings]) / (probe[crossings + 1] - probe[crossings])
    frequency = (len(crossing_times) - 1) / (crossing_times[-1] - crossing_times[0])
    fits = []
    for end in (8e-9, 16e-9):
        selected = (t >= 4e-9) & (t <= end)
        tx, logu = t[selected], np.log(energy[selected])
        assert len(tx) >= 100
        centered = tx - tx.mean()
        slope = centered @ (logu - logu.mean()) / (centered @ centered)
        assert slope < 0
        residual = np.sqrt(np.mean((logu - logu.mean() - slope * centered)**2))
        fits.append(dict(end_s=end, Q_decay=float(-2 * np.pi * frequency / slope),
                         log_energy_RMS_residual=float(residual)))
    return float(frequency), fits


def audit():
    summary = json.loads((OUT / "summary.json").read_text(encoding="utf-8"))
    assert sha(OUT / "configurations.json") == summary["configuration_sha256"]
    configs = json.loads((OUT / "configurations.json").read_text(encoding="utf-8"))
    assert len(summary["cases"]) == len(configs) == 2
    for name, digest in summary["source_sha256"].items():
        assert sha(ROOT / name) == digest
    modelpath = ROOT / "\u539a\u94dc\u8fb9\u754c\u57fa\u51c6\u7ed3\u679c/passive_surface_models.json"
    assert sha(modelpath) == summary["model_sha256"]
    model = next(item for item in json.loads(modelpath.read_text(encoding="utf-8"))["models"]
                 if item["terms"] == 64)
    reference = ROOT / "\u94dcQ\u7ec6\u5316\u7ed3\u679c/q_36x23x14_011.npz"
    assert sha(reference) == summary["frequency_reference_sha256"]
    with np.load(reference) as data:
        pole = complex(data["fitted_pole_Hz"])
    records = []
    for config, row in zip(configs, summary["cases"]):
        assert all(row[key] == value for key, value in config.items())
        path = OUT / row["file"]
        assert sha(path) == row["sha256"]
        with np.load(path) as data:
            shape = tuple(map(int, data["shape"]))
            assert list(shape) == config["shape"] == [36, 23, 14]
            np.testing.assert_array_equal(data["mode"], [0, 1, 1])
            dt = float(data["dt_s"])
            assert dt == config["dt_s"] and float(data["duration_s"]) == 16e-9
            m, q, edges, scale, _, _ = geometry(shape, data["lengths_m"])
            np.testing.assert_array_equal(data["R_ohm"], model["R_ohm"])
            np.testing.assert_array_equal(data["L_H"], model["L_H"])
            rr = scale[:, None] * data["R_ohm"]
            ll = scale[:, None] * data["L_H"]
            electric, magnetic, rl, wall, charge, energy_step = [], [], [], [], [], []
            snapshots = data["snapshot_step"]
            assert len(snapshots) == 9
            assert snapshots[0] == 0 and snapshots[-1] == row["steps"] - 1
            for index in range(len(snapshots)):
                e0, b0, i0 = (data["snapshot_" + name][index]
                              for name in ("e_before", "b_before", "i_before"))
                e1, b1, i1 = (data["snapshot_" + name][index]
                              for name in ("e_after", "b_after", "i_after"))
                em, bm, im = (e0 + e1) / 2, (b0 + b1) / 2, (i0 + i1) / 2
                j = data["snapshot_j_mid"][index]
                total_j = np.zeros(len(m))
                np.add.at(total_j, edges, j)
                ctqb = transpose_array(q * bm, shape)
                er = m * (e1 - e0) / dt - ctqb + total_j
                electric.append(float(np.linalg.norm(er) /
                                      (np.linalg.norm(ctqb) + np.linalg.norm(total_j))))
                ce = curl_array(em, shape)
                magnetic.append(float(np.linalg.norm((b1 - b0) / dt + ce) /
                                      max(np.linalg.norm(ce), 1e-25)))
                lr, resistance = ll * (i1 - i0) / dt, rr * (j[:, None] - im)
                rl.append(float(np.linalg.norm(lr - resistance) /
                                max(np.linalg.norm(lr), np.linalg.norm(resistance), 1e-25)))
                wall.append(float(np.linalg.norm(em[edges] - resistance.sum(axis=1)) /
                                  max(np.linalg.norm(em[edges]), 1e-25)))
                drho = node_divergence(m * (e1 - e0), shape) / dt
                expected = -node_divergence(total_j, shape)
                charge.append(float(np.linalg.norm(drho - expected) /
                                    max(np.linalg.norm(drho), np.linalg.norm(expected), 1e-25)))
                u0 = .5 * (m @ e0**2 + q @ b0**2 + np.sum(ll * i0**2))
                u1 = .5 * (m @ e1**2 + q @ b1**2 + np.sum(ll * i1**2))
                heat = dt * np.sum(rr * (j[:, None] - im)**2)
                energy_step.append(float(abs(u1 - u0 + heat) / max(u0, u1, 1e-25)))
            t, u, heat = (data[name] for name in (
                "sample_time_s", "total_energy_J", "cumulative_wall_heat_J"))
            assert t[0] == 0 and abs(t[-1] - 16e-9) < 1e-20 and np.all(np.diff(t) > 0)
            assert abs(u[0] - .5) < 1e-12
            assert np.all(np.isfinite(u)) and np.all(np.isfinite(heat))
            assert np.all(np.diff(heat) >= 0)
            closure = float(np.max(abs(u + heat - u[0])) / u[0])
            growth = float(max(0., np.max(np.diff(u))) / u[0])
            np.testing.assert_allclose(row["energy_closure"], closure, atol=1e-14)
            assert closure < 1e-7 and growth < 1e-10
            assert max(electric) < 1e-7 and max(magnetic) < 1e-7
            assert max(charge) < 1e-6 and max(rl) < 1e-5 and max(wall) < 1e-5
            assert max(energy_step) < 1e-9
            frequency, fits = decay(t, u, data["modal_e_probe"])
            mapped = 2 / dt * np.arctan(2 * np.pi * pole * dt / 2)
            fref, qref = quadrature_q(np.array([0, 1, 1]), data["lengths_m"], model["sigma_S_per_m"])
            decay_q = fits[-1]["Q_decay"]
            qerror, ferror = abs(decay_q / qref - 1), abs(frequency / fref - 1)
            window_error = abs(fits[-1]["Q_decay"] / fits[0]["Q_decay"] - 1)
            records.append(dict(
                dt_s=dt, snapshots_checked=len(snapshots), frequency_Hz=frequency,
                decay_fits=fits, Q_vs_continuum_error=float(qerror),
                frequency_vs_continuum_error=float(ferror),
                Q_vs_midpoint_mapping_error=float(abs(decay_q / (mapped.real / (2 * mapped.imag)) - 1)),
                fit_window_Q_difference=float(window_error),
                short_window_frequency_Q_pass=bool(qerror < .01 and ferror < .01 and window_error < .01),
                energy_closure=closure, sampled_energy_growth=growth,
                electric_residual=max(electric), magnetic_residual=max(magnetic),
                charge_residual=max(charge), RL_residual=max(rl), wall_residual=max(wall),
                snapshot_energy_closure=max(energy_step)))
    for key in ("fine_grid_long_time_passed", "arbitrary_modes_passed",
                "manufactured_cavity_passed", "full_joint_simulation_passed"):
        assert summary[key] is False
    return dict(
        independent_short_window_equations_passed=True, cases=records,
        snapshots_checked=18, summary_sha256=sha(OUT / "summary.json"),
        verifier_sha256=sha(Path(__file__)),
        helper_sha256={name: sha(ROOT / name) for name in (
            "verify_finite_copper_cavity.py", "verify_finite_pec_cavity.py")},
        fine_grid_long_time_passed=False, arbitrary_modes_passed=False,
        manufactured_cavity_passed=False, full_joint_simulation_passed=False)


if __name__ == "__main__":
    result = audit()
    (OUT / "verification.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
