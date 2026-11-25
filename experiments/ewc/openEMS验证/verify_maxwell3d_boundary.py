"""Independent array-difference Maxwell equations; no solver-module imports."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import numpy as np

MU = 4e-7 * np.pi
C0 = 299792458.
EPS = 1 / (MU * C0**2)
ETA = np.sqrt(MU / EPS)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def split_e(e, shape):
    nx, ny, nz = shape
    n = nx * ny * nz
    t = (nx + 1) * ny * nz
    return (e[:n].reshape(nx, ny, nz), e[n:n + t].reshape(nx + 1, ny, nz),
            e[n + t:].reshape(nx + 1, ny, nz))


def split_b(b, shape):
    nx, ny, nz = shape
    n = nx * ny * nz
    t = (nx + 1) * ny * nz
    return (b[:t].reshape(nx + 1, ny, nz), b[t:t + n].reshape(nx, ny, nz),
            b[t + n:].reshape(nx, ny, nz))


def curl_array(e, shape):
    ex, ey, ez = split_e(e, shape)
    bx = np.roll(ez, -1, axis=1) - ez - np.roll(ey, -1, axis=2) + ey
    by = np.roll(ex, -1, axis=2) - ex - ez[1:] + ez[:-1]
    bz = ey[1:] - ey[:-1] - np.roll(ex, -1, axis=1) + ex
    return np.r_[bx.ravel(), by.ravel(), bz.ravel()]


def curl_transpose_array(b, shape):
    bx, by, bz = split_b(b, shape)
    ex = np.roll(by, 1, axis=2) - by - np.roll(bz, 1, axis=1) + bz
    ey = bx - np.roll(bx, 1, axis=2)
    ey[1:] += bz
    ey[:-1] -= bz
    ez = np.roll(bx, 1, axis=1) - bx
    ez[:-1] += by
    ez[1:] -= by
    return np.r_[ex.ravel(), ey.ravel(), ez.ravel()]


def divergence_array(b, shape):
    bx, by, bz = split_b(b, shape)
    return (bx[1:] - bx[:-1] + np.roll(by, -1, axis=1) - by +
            np.roll(bz, -1, axis=2) - bz).ravel()


def gradient_array(phi):
    return np.r_[(phi[1:] - phi[:-1]).ravel(),
                 (np.roll(phi, -1, axis=1) - phi).ravel(),
                 (np.roll(phi, -1, axis=2) - phi).ravel()]


def spectral(t, value, f):
    return np.array([np.sum(value * np.exp(-2j * np.pi * x * t)) *
                     (t[1] - t[0]) for x in f])


def main(folder):
    s = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    module = Path(__file__).with_name("maxwell3d_boundary.py")
    assert sha(module) == s["module_sha256"]
    assert sha(Path(__file__).with_name("run_maxwell3d_boundary.py")) == s["runner_sha256"]
    assert sha(Path(s["models_file"])) == s["models_sha256"]
    assert s["not_openems_FDTD"] and not s["closed_copper_cavity_complete"]
    assert s["case_count"] == len(s["cases"]) == 7
    checks = []
    for row in s["cases"]:
        path, rawpath = folder / row["data_file"], Path(row["snapshots_file"])
        assert sha(path) == row["data_sha256"] and sha(rawpath) == row["snapshots_sha256"]
        nx, ny, nz = row["shape"]
        dx, dy, dz = row["spacing_m"]
        n, t = nx * ny * nz, (nx + 1) * ny * nz
        vx = np.full(nx + 1, dx)
        vx[[0, -1]] *= .5
        mass = np.r_[np.full(n, EPS * dy * dz / dx), np.repeat(EPS * vx * dz / dy, ny * nz),
                     np.repeat(EPS * vx * dy / dz, ny * nz)]
        q = np.r_[np.repeat(vx / (MU * dy * dz), ny * nz), np.full(n, dy / (MU * dx * dz)),
                  np.full(n, dz / (MU * dx * dy))]
        left = np.r_[np.arange(n, n + ny * nz), np.arange(n + t, n + t + ny * nz)]
        wall = left + nx * ny * nz
        edge_length = np.repeat([dy, dz], ny * nz)
        dual_width = np.repeat([dz, dy], ny * nz)
        with np.load(path, allow_pickle=False) as data, np.load(rawpath, allow_pickle=False) as raw:
            dt = row["dt_ps"] * 1e-12
            r, l = data["R_edge_ohm"], data["L_edge_H"]
            # Geometry mapping must recover the exact same surface material on both orientations.
            models = json.loads(Path(s["models_file"]).read_text(encoding="utf-8"))["models"]
            model = next(m for m in models if m["terms"] == row["terms"])
            np.testing.assert_allclose(r / (edge_length / dual_width)[:, None],
                                       np.broadcast_to(model["R_ohm"], r.shape), rtol=1e-14)
            np.testing.assert_allclose(l / (edge_length / dual_width)[:, None],
                                       np.broadcast_to(model["L_H"], l.shape), rtol=1e-14)
            source_r = ETA * edge_length / dual_width
            np.testing.assert_allclose(source_r, data["source_R_edge_ohm"], rtol=1e-14)
            energy = data["energy_J"]
            work = data["source_work_W"]
            losses = data["source_resistor_loss_W"] + data["copper_resistor_loss_W"]
            scale = max(np.max(energy), abs(dt * np.sum(work)), 1e-30)
            closure = float(np.max(abs(energy[1:] - energy[0] - dt * np.cumsum(work - losses))) / scale)
            assert closure < 1e-8
            off = data["time_mid_s"] >= 2.4e-9
            assert np.all(data["drive_V_per_m"][off] == 0)
            assert np.max(np.diff(energy)[off]) / scale < 1e-10
            electric_residual = magnetic_residual = state_residual = divergence = 0.
            for index, step in enumerate(raw["snapshot_step"]):
                eo, en = raw["e_old"][index], raw["e_new"][index]
                bo, bn = raw["b_old"][index], raw["b_new"][index]
                io, inn = raw["i_old"][index], raw["i_new"][index]
                em, bm, im = (eo + en) / 2, (bo + bn) / 2, (io + inn) / 2
                j = data["wall_edge_current_A"][step]
                current = curl_transpose_array(q * bm, row["shape"])
                drive_i = 2 * data["source_profile_m"] * data["drive_V_per_m"][step] / source_r
                current[left] += drive_i - em[left] / source_r
                current[wall] -= j
                de = mass * (en - eo) / dt
                db = (bn - bo) / dt
                electric_residual = max(electric_residual, np.max(abs(de - current)) /
                                        max(np.max(abs(de)), np.max(abs(current)), 1e-30))
                expected_db = -curl_array(em, row["shape"])
                magnetic_residual = max(magnetic_residual, np.max(abs(db - expected_db)) /
                                        max(np.max(abs(db)), np.max(abs(expected_db)), 1e-30))
                branch = l * (inn - io) / dt - r * (j[:, None] - im)
                state_residual = max(state_residual, np.max(abs(branch)) /
                                     max(np.max(abs(em[wall])), 1e-30))
                np.testing.assert_allclose(em[wall], np.sum(r * (j[:, None] - im), axis=1),
                                           rtol=1e-6, atol=1e-16)
                power = np.sum(r * (j[:, None] - im)**2)
                np.testing.assert_allclose(power, data["copper_resistor_loss_W"][step], rtol=1e-7, atol=1e-35)
                for e, b, state, which in ((eo, bo, io, step), (en, bn, inn, step + 1)):
                    independent_energy = .5 * (mass @ e**2 + q @ b**2 + np.sum(l * state**2))
                    np.testing.assert_allclose(independent_energy, energy[which], rtol=1e-11, atol=1e-35)
                    divergence = max(divergence, np.max(abs(divergence_array(b, row["shape"]))))
            assert electric_residual < 1e-5 and magnetic_residual < 1e-5 and state_residual < 1e-5
            check = {"case": row["case"], "snapshot_steps": len(raw["snapshot_step"]),
                     "energy_closure_relative": closure, "electric_equation_relative_residual": float(electric_residual),
                     "magnetic_equation_relative_residual": float(magnetic_residual),
                     "RL_equation_relative_residual": float(state_residual),
                     "snapshot_divergence_B_Wb": float(divergence)}
            if not row["nonuniform"]:
                f = data["frequency_Hz"]
                drive = spectral(data["time_mid_s"], data["drive_V_per_m"], f)
                refl = spectral(data["time_mid_s"], data["left_mean_E_V_per_m"], f) / drive - 1
                z = spectral(data["time_mid_s"], data["wall_mean_E_V_per_m"], f) / spectral(
                    data["time_mid_s"], data["wall_mean_K_A_per_m"], f)
                target = np.sqrt(2j * np.pi * f * MU / (row["sigma_S_per_m"] + 2j * np.pi * f * EPS))
                expected = (target - ETA) / (target + ETA) * np.exp(-4j * np.pi * f * .12 / C0)
                ref_error = float(np.max(abs(refl - expected)))
                z_error = float(np.max(abs(z / target - 1)))
                np.testing.assert_allclose(ref_error, row["reflection_absolute_error"], rtol=1e-7)
                np.testing.assert_allclose(z_error, row["wall_complex_impedance_error"], rtol=1e-7)
                assert (ref_error < .01) == row["plane_reflection_1percent_pass"]
                assert (z_error < .01) == row["wall_impedance_1percent_pass"]
                # Independent prior 1-D solver is a second implementation of the normal mode.
                dx_mm = int(round(dx * 1e3))
                prior = folder.parent / "\u5e73\u9762\u8fb9\u754c\u77ac\u6001\u7ed3\u679c" / (
                    f"copper_dx{dx_mm}mm_dt{row['dt_ps']}ps_m{row['terms']}_t8ns.npz")
                if prior.exists():
                    with np.load(prior, allow_pickle=False) as old:
                        difference = float(np.max(abs(refl - old["left_reflection"])))
                        loss_ratio = float(dt * np.sum(data["copper_resistor_loss_W"]) /
                                           (dt * np.sum(old["copper_power_W"]) * ny * dy * nz * dz))
                    assert difference < 1e-6 and abs(loss_ratio - 1) < 1e-6
                    check.update(prior_1d_file=str(prior), prior_1d_sha256=sha(prior),
                                 reflection_difference_vs_1d=difference, copper_loss_3d_to_1d_area_ratio=loss_ratio)
            else:
                assert np.all(data["component_peak_integral_units"] > 1e-20), "All six components must be excited"
                epeak = np.max(abs(raw["e_new"]), axis=0)
                bpeak = np.max(abs(raw["b_new"]), axis=0)
                peaks = [np.max(x) for x in (*split_e(epeak, row["shape"]), *split_b(bpeak, row["shape"]))]
                assert np.all(np.array(peaks) > 1e-20), "Snapshots must independently show all six components"
                assert not row["nonuniform_physical_accuracy_verified"]
                check["all_six_components_excited"] = True
                check["snapshot_six_component_peaks_integral_units"] = peaks
            checks.append(check)
    testpath = Path(__file__).with_name("test_maxwell3d_boundary.py")
    tests = subprocess.run([sys.executable, "-B", str(testpath)], capture_output=True, text=True, timeout=60)
    assert tests.returncode == 0, tests.stdout + tests.stderr
    result = {
        "independent_verification_pass": True, "case_count": len(checks), "checks": checks,
        "test_output": tests.stdout + tests.stderr, "verifier_sha256": sha(Path(__file__)),
        "test_sha256": sha(testpath), "summary_sha256": sha(folder / "summary.json"),
        "all_physical_cases_pass": False, "nonuniform_3d_accuracy_complete": False,
        "openems_extension_complete": False, "closed_copper_cavity_complete": False,
        "full_joint_simulation_complete": False,
    }
    (folder / "verification.json").write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", type=Path)
    main(parser.parse_args().folder)
