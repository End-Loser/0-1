"""Independent oblique Fresnel and discrete LC transfer audit."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import numpy as np

from verify_maxwell3d_boundary import curl_array, curl_transpose_array

MU = 4e-7 * np.pi
C0 = 299792458.
EPS = 1 / (MU * C0**2)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def discrete_reference(f, nx, ny, pol, r, l):
    omega = 2 * np.pi * f
    ky = 2 * np.pi / .2
    kx = np.sqrt((omega / C0)**2 - ky**2)
    air = omega * MU / kx if pol == "TE" else kx / (omega * EPS)
    dy, dx = .2 / ny, .12 / nx
    discrete_ky = 2 * np.sin(ky * dy / 2) / dy
    alpha = 1 - (discrete_ky * C0 / omega)**2
    s = 1j * omega
    wall_z = np.sum(1 / (1 / r + 1 / (s * l)))
    series = s * MU * dx * (alpha if pol == "TM" else 1)
    shunt = s * EPS * dx / 2 * (alpha if pol == "TE" else 1)
    c_half = np.array([[1, 0], [shunt, 1]], complex)
    l_series = np.array([[1, series], [0, 1]], complex)
    abcd = np.linalg.matrix_power(c_half @ l_series @ c_half, nx)
    zin = (abcd[0, 0] * wall_z + abcd[0, 1]) / (abcd[1, 0] * wall_z + abcd[1, 1])
    reflection = (zin - air) / (zin + air)
    wall_field = (1 + reflection) / (abcd[0, 0] + abcd[0, 1] / wall_z)
    density = .5 * wall_z.real * abs(wall_field / wall_z)**2
    return reflection, density


def continuum_reference(frequency, pol, sigma):
    omega = 2 * np.pi * frequency
    ky = 2 * np.pi / .2
    complex_eps = EPS - 1j * sigma / omega
    longitudinal_metal = np.sqrt(ky**2 - omega**2 * MU * complex_eps)
    longitudinal_air = np.sqrt(omega**2 * MU * EPS - ky**2)
    if pol == "TE":
        za = omega * MU / longitudinal_air
        zm = 1j * omega * MU / longitudinal_metal
    else:
        za = longitudinal_air / (omega * EPS)
        zm = longitudinal_metal / (1j * omega * complex_eps)
    gamma = (zm - za) / (zm + za) * np.exp(-2j * longitudinal_air * .12)
    density = .5 * zm.real * abs(2 / (za + zm))**2
    return gamma, density


def main(folder):
    summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    assert summary["case_count"] == len(summary["cases"]) == 20
    assert summary["not_openems"] and not summary["oblique_transient_boundary_complete"]
    assert sha(Path(__file__).with_name("run_oblique_boundary.py")) == summary["runner_sha256"]
    assert sha(Path(__file__).with_name("maxwell3d_boundary.py")) == summary["maxwell_module_sha256"]
    models_path = Path(summary["models_file"])
    assert sha(models_path) == summary["models_sha256"]
    model = next(x for x in json.loads(models_path.read_text(encoding="utf-8"))["models"] if x["terms"] == 64)
    r, l = np.array(model["R_ohm"]), np.array(model["L_H"])
    assert np.all(r > 0) and np.all(l > 0)
    rows = []
    for case in summary["cases"]:
        path = folder / case["data_file"]
        assert sha(path) == case["sha256"]
        with np.load(path, allow_pickle=False) as data:
            f = data["frequency_Hz"]
            assert len(f) == case["frequency_points"]
            gamma, absorption = continuum_reference(f, case["polarization"], model["sigma_S_per_m"])
            np.testing.assert_allclose(gamma, data["expected_reflection"], rtol=1e-12)
            np.testing.assert_allclose(absorption, data["expected_absorbed_density_W_per_m2"], rtol=1e-12)
            discrete = [discrete_reference(x, case["nx"], case["ny"], case["polarization"], r, l) for x in f]
            ref_discrete = np.array([x[0] for x in discrete])
            power_discrete = np.array([x[1] for x in discrete])
            ref_difference = float(np.max(abs(ref_discrete - data["reflection"])))
            absorbed_difference = float(np.max(abs(power_discrete / data["absorbed_density_W_per_m2"] - 1)))
            assert ref_difference < 1e-7 and absorbed_difference < 1e-7
            ref_error = float(np.max(abs(data["reflection"] - gamma)))
            power_error = float(np.max(abs(data["absorbed_density_W_per_m2"] / absorption - 1)))
            np.testing.assert_allclose(ref_error, case["reflection_absolute_error"], rtol=1e-7)
            np.testing.assert_allclose(power_error, case["absorbed_density_relative_error"], rtol=1e-7)
            assert (ref_error < .01) == case["reflection_1percent_pass"]
            assert (power_error < .01) == case["absorption_1percent_pass"]
            assert np.all(data["absorbed_density_W_per_m2"] > 0)
            assert np.max(data["full_equation_residual_relative"]) < 1e-7
            assert np.max(data["power_closure_relative"]) < 1e-7
            # Independently lift stored amplitudes, without importing Grid/projection.
            nx, ny, nz = case["nx"], case["ny"], 2
            dx, dy, dz = .12 / nx, .2 / ny, .015
            n, t = nx * ny * nz, (nx + 1) * ny * nz
            vx = np.full(nx + 1, dx)
            vx[[0, -1]] *= .5
            mass = np.r_[np.full(n, EPS * dy * dz / dx), np.repeat(EPS * vx * dz / dy, ny * nz),
                         np.repeat(EPS * vx * dy / dz, ny * nz)]
            q = np.r_[np.repeat(vx / (MU * dy * dz), ny * nz), np.full(n, dy / (MU * dx * dz)),
                      np.full(n, dz / (MU * dx * dy))]
            left = np.r_[np.arange(n, n + ny * nz), np.arange(n + t, n + t + ny * nz)]
            wall = left + nx * ny * nz
            lengths, widths = np.repeat([dy, dz], ny * nz), np.repeat([dz, dy], ny * nz)
            phase_y = np.exp(2j * np.pi * np.arange(ny) / ny)
            phase_y_edge = phase_y * np.exp(1j * np.pi / ny)
            shape = (nx, ny, nz)
            equation_error = power_error_independent = 0.
            for index in sorted(set((0, len(f) // 2, len(f) - 1))):
                amps = data["modal_edge_amplitudes_V"][index]
                fields = []
                for amplitude, phase in ((amps[:nx], phase_y), (amps[nx:2 * nx + 1], phase_y_edge),
                                         (amps[2 * nx + 1:], phase_y)):
                    fields.append(np.broadcast_to(amplitude[:, None, None] * phase[None, :, None] /
                                                  np.sqrt(ny * nz), (len(amplitude), ny, nz)).ravel())
                e = np.concatenate(fields)
                omega = 2 * np.pi * f[index]
                cos_angle = np.sqrt(1 - (2 * np.pi / .2 * C0 / omega)**2)
                source_g = 1 / (np.repeat([np.sqrt(MU / EPS) * cos_angle,
                                         np.sqrt(MU / EPS) / cos_angle], ny * nz) * lengths / widths)
                sw = 1j * omega
                zload = np.sum(1 / (1 / r + 1 / (sw * l)))
                wall_g = widths / (lengths * zload)
                profile = np.zeros(len(left), complex)
                start = 0 if case["polarization"] == "TM" else ny * nz
                chosen_phase = phase_y_edge if case["polarization"] == "TM" else phase_y
                profile[start:start + ny * nz] = lengths[start] * np.repeat(chosen_phase, nz)
                rhs = np.zeros(len(e), complex)
                rhs[left] = sw * 2 * profile * source_g
                residual = curl_transpose_array(q * curl_array(e, shape), shape) + sw * sw * mass * e
                residual[left] += sw * source_g * e[left]
                residual[wall] += sw * wall_g * e[wall]
                equation_error = max(equation_error, float(np.linalg.norm(residual - rhs) / np.linalg.norm(rhs)))
                observed = .5 * np.sum(wall_g.real * abs(e[wall])**2) / (.2 * .03)
                power_error_independent = max(power_error_independent, float(abs(
                    observed / data["absorbed_density_W_per_m2"][index] - 1)))
            assert equation_error < 1e-7 and power_error_independent < 1e-7
            if case["full_matrix_crosscheck"]:
                assert data["full_solve_difference_relative"].shape == (3,)
                assert np.max(data["full_solve_difference_relative"]) < 1e-7
            rows.append({
                "case": case["case"], "discrete_ABCD_reflection_difference": ref_difference,
                "discrete_ABCD_absorption_relative_difference": absorbed_difference,
                "reflection_error_vs_Fresnel": ref_error, "absorption_error_vs_Fresnel": power_error,
                "independent_lifted_3d_equation_residual": equation_error,
                "independent_lifted_absorption_relative_difference": power_error_independent,
            })
    lookup = {x["case"]: x for x in summary["cases"]}
    for pol in ("TE", "TM"):
        finest = lookup[f"nx240_ny64_{pol}_sweep"]
        assert finest["reflection_1percent_pass"] and finest["absorption_1percent_pass"]
        assert not lookup[f"nx120_ny8_{pol}_sweep"]["reflection_1percent_pass"]
        assert finest["reflection_absolute_error"] < lookup[f"nx120_ny64_{pol}_sweep"]["reflection_absolute_error"]
    tests_path = Path(__file__).with_name("test_oblique_boundary.py")
    tests = subprocess.run([sys.executable, "-B", str(tests_path)], capture_output=True, text=True, timeout=60)
    assert tests.returncode == 0, tests.stdout + tests.stderr
    result = {
        "independent_verification_pass": True, "case_count": len(rows), "checks": rows,
        "projected_frequency_solutions": sum(x["frequency_points"] for x in summary["cases"]),
        "full_matrix_frequency_solutions": sum(x["frequency_points"] for x in summary["cases"] if x["full_matrix_crosscheck"]),
        "independent_lifted_3d_frequency_checks": 3 * len(rows),
        "test_output": tests.stdout + tests.stderr,
        "summary_sha256": sha(folder / "summary.json"), "verifier_sha256": sha(Path(__file__)),
        "test_sha256": sha(tests_path), "all_physical_cases_pass": False,
        "independent_array_helper_sha256": sha(Path(__file__).with_name("verify_maxwell3d_boundary.py")),
        "oblique_transient_boundary_complete": False, "finite_boundary_complete": False,
        "closed_copper_cavity_complete": False, "full_joint_simulation_complete": False,
    }
    (folder / "verification.json").write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", type=Path)
    main(parser.parse_args().folder)
