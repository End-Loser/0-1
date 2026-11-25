"""Independent element assembly and raw-data audit; no benchmark imports."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.linalg import spsolve

MU = 4e-7 * np.pi
EPS = 1 / (MU * 299792458.0**2)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reference(f, sigma, depth=None):
    omega = 2 * np.pi * f
    admittance = sigma + 1j * omega * EPS
    gamma = np.sqrt(1j * omega * MU * admittance)
    result = gamma / admittance
    return result if depth is None else result * np.tanh(gamma * depth)


def assemble(nodes, f, sigma):
    rows, cols, values = [], [], []
    q = 1j * 2 * np.pi * f * MU * (sigma + 1j * 2 * np.pi * f * EPS)
    for element, h in enumerate(np.diff(nodes)):
        local = np.array([[1, -1], [-1, 1]]) / h
        local = local + q * h / 6 * np.array([[2, 1], [1, 2]])
        for i in range(2):
            for j in range(2):
                rows.append(element + i)
                cols.append(element + j)
                values.append(local[i, j])
    full = coo_matrix((values, (rows, cols)), shape=(len(nodes), len(nodes))).tocsr()
    return full[:-1, :-1].tocsc()


def audit(folder):
    summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    script = Path(__file__).with_name("run_thick_copper_boundary.py")
    assert sha(script) == summary["script_sha256"], "Runner source changed since run"
    assert summary["not_openems_FDTD"]
    assert not summary["3d_boundary_coupling_complete"]
    assert not summary["transient_closed_loop_complete"]
    assert not summary["full_joint_simulation_complete"]
    checks, manifest = [], []
    max_residual = max_solution = max_power = 0.
    for case in summary["fem_cases"]:
        path = folder / (case["case"] + ".npz")
        with np.load(path, allow_pickle=False) as data:
            f = data["frequency_Hz"]
            nodes = data["depth_m"]
            field = data["E_V_per_m"]
            z = data["impedance_ohm"]
            sigma = case["sigma_S_per_m"]
            assert field.shape == (101, case["cells"] + 1)
            assert np.all(field[:, -1] == 0), "Back-face Dirichlet condition"
            assert np.array_equal(z, field[:, 0]), "H0=1 surface impedance"
            assert np.all(z.real > 0), "Nonpositive dissipated power"
            finite = reference(f, sigma, nodes[-1])
            half = reference(f, sigma)
            finite_error = float(np.max(abs(z / finite - 1)))
            half_error = float(np.max(abs(z / half - 1)))
            np.testing.assert_allclose(finite_error, case["finite_depth_complex_error"],
                                       rtol=1e-7, atol=1e-12)
            np.testing.assert_allclose(half_error, case["halfspace_complex_error"],
                                       rtol=1e-7, atol=1e-12)
            assert (finite_error < .01) == case["finite_depth_1percent_pass"]
            assert (half_error < .01) == case["halfspace_1percent_pass"]
            # Two-point Gaussian integration is exact for squared linear fields.
            integral = np.zeros(len(f))
            for xi in (-1 / np.sqrt(3), 1 / np.sqrt(3)):
                value = (1 - xi) / 2 * field[:, :-1] + (1 + xi) / 2 * field[:, 1:]
                integral += np.sum(abs(value)**2 * np.diff(nodes) / 2, axis=1)
            power = .5 * sigma * integral
            closure = float(np.max(abs(power / (.5 * z.real) - 1)))
            np.testing.assert_allclose(power, data["joule_power_W_per_m2"], rtol=1e-12)
            for index in (0, 50, 100):
                matrix = assemble(nodes, f[index], sigma)
                rhs = np.zeros(len(nodes) - 1, complex)
                rhs[0] = 1j * 2 * np.pi * f[index] * MU
                residual = np.linalg.norm(matrix @ field[index, :-1] - rhs) / np.linalg.norm(rhs)
                independent = spsolve(matrix, rhs)
                difference = np.linalg.norm(independent - field[index, :-1]) / np.linalg.norm(independent)
                max_residual = max(max_residual, float(residual))
                max_solution = max(max_solution, float(difference))
            max_power = max(max_power, closure)
            checks.append({"case": case["case"], "finite_error": finite_error,
                           "halfspace_error": half_error, "power_closure": closure})
    assert len(checks) == 18
    assert max_residual < 1e-9 and max_solution < 1e-9 and max_power < 1e-10
    models = json.loads((folder / "passive_surface_models.json").read_text(encoding="utf-8"))
    spice_errors = []
    for model, reported, raw in zip(models["models"], summary["foster_cases"], summary["ngspice_cases"]):
        terms = model["terms"]
        r, l, p = (np.array(model[key]) for key in ("R_ohm", "L_H", "pole_rad_per_s"))
        assert len(r) == terms and np.all(r > 0) and np.all(l > 0)
        np.testing.assert_allclose(r / l, p, rtol=1e-14)
        assert not model["physical_BOM"] and not model["native_openems_boundary_verified"]
        raw_folder = Path(raw["raw_folder"])
        for item in raw["raw_manifest"]:
            assert sha(raw_folder / item["name"]) == item["sha256"], item["name"]
        # Check that the executed netlist actually contains the saved parameters.
        lines = (raw_folder / "surface_impedance.cir").read_text(encoding="ascii").splitlines()
        for i in range(terms):
            for prefix, parameter in (("R", r[i]), ("L", l[i])):
                tokens = next(line.split() for line in lines if line.startswith(f"{prefix}{i+1} "))
                a = "port" if i == 0 else f"n{i}"
                b = "0" if i == terms - 1 else f"n{i+1}"
                assert tokens[1:3] == [a, b] and float(tokens[3]) == parameter
        with np.load(folder / f"foster_{terms}.npz", allow_pickle=False) as data:
            f = data["frequency_Hz"]
            omega = 2 * np.pi * f
            # Parallel RL impedances derived directly from admittances.
            z = np.sum(1 / (1 / r + 1 / (1j * omega[:, None] * l)), axis=1)
            np.testing.assert_allclose(z, data["impedance"], rtol=1e-12)
            ac = np.loadtxt(raw_folder / "impedance.dat", skiprows=1)
            assert ac.shape == (101, 3)
            np.testing.assert_allclose(ac[:, 0], f, rtol=1e-12)
            zs = ac[:, 1] + 1j * ac[:, 2]
            np.testing.assert_allclose(zs, data["ngspice_impedance"], rtol=1e-12)
            err = float(np.max(abs(zs / z - 1)))
            assert err < 1e-8
            spice_errors.append({"terms": terms, "relative_difference": err})
            expected = reference(f, model["sigma_S_per_m"])
            np.testing.assert_allclose(np.max(abs(z / expected - 1)),
                                       reported["max_relative_complex_error"], rtol=1e-9)
            np.testing.assert_allclose(np.max(abs(abs(z) / abs(expected) - 1)),
                                       reported["max_relative_amplitude_error"], rtol=1e-9)
            np.testing.assert_allclose(np.max(abs(np.angle(z / expected, deg=True))),
                                       reported["max_phase_error_deg"], rtol=1e-9)
            guard_f = data["guard_frequency_Hz"]
            guard_z = np.sum(1 / (1 / r + 1 / (2j * np.pi * guard_f[:, None] * l)), axis=1)
            np.testing.assert_allclose(guard_z, data["guard_impedance"], rtol=1e-12)
            np.testing.assert_allclose(np.max(abs(guard_z / reference(
                guard_f, model["sigma_S_per_m"]) - 1)),
                reported["guard_band_complex_error"], rtol=1e-9)
            for check in summary["bilinear_frequency_checks"]:
                if check["terms"] != terms:
                    continue
                dt = check["timestep_ps"] * 1e-12
                sd = 2j / dt * np.tan(omega * dt / 2)
                digital = np.sum(1 / (1 / r + 1 / (sd[:, None] * l)), axis=1)
                np.testing.assert_allclose(np.max(abs(digital / expected - 1)),
                                           check["max_relative_complex_error"], rtol=1e-9)
    assert len(spice_errors) == 3
    test = subprocess.run([sys.executable, "-B", str(Path(__file__).with_name(
        "test_thick_copper_boundary.py"))], capture_output=True, text=True, timeout=60)
    assert test.returncode == 0, test.stdout + test.stderr
    for path in sorted(folder.iterdir()):
        if path.is_file() and path.name != "verification.json":
            manifest.append({"name": path.name, "sha256": sha(path)})
    result = {
        "independent_verification_pass": True, "fem_case_count": len(checks),
        "independent_sparse_solves": 54, "max_relative_equation_residual": max_residual,
        "max_relative_independent_field_difference": max_solution,
        "max_relative_power_closure": max_power,
        "fem_checks": checks, "ngspice_checks": spice_errors,
        "test_output": test.stdout + test.stderr,
        "result_manifest": manifest, "verifier_sha256": sha(Path(__file__)),
        "test_sha256": sha(Path(__file__).with_name("test_thick_copper_boundary.py")),
        "physical_accuracy_of_all_cases_pass": False,
        "3d_boundary_coupling_complete": False, "transient_closed_loop_complete": False,
    }
    (folder / "verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", type=Path)
    audit(parser.parse_args().folder)
