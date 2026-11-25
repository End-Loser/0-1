"""Independent array equations and Gaussian reference for the 120 copper mode."""
import hashlib
import json
from pathlib import Path

import numpy as np

from verify_finite_copper_cavity import geometry, node_divergence, quadrature_q
from verify_finite_pec_cavity import curl_array, transpose_array, split, layouts

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "\u94dc\u8154\u7b2c\u4e09\u65b9\u5411\u7ed3\u679c"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pole_fit(frequency, amplitude):
    frequency, amplitude = np.asarray(frequency), np.asarray(amplitude)
    center, unit = np.mean(frequency), np.std(frequency)
    assert unit > 0 and np.all(np.isfinite(amplitude)) and np.all(abs(amplitude) > 0)
    slope, intercept = np.polyfit((frequency - center) / unit, 1 / amplitude, 1)
    pole = center - unit * intercept / slope
    assert pole.imag > 0
    return pole, float(pole.real / (2 * pole.imag))


def audit(folder=OUT):
    summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    config_path = folder / "configurations.json"
    assert sha(config_path) == summary["configuration_sha256"]
    configs = json.loads(config_path.read_text(encoding="utf-8"))
    assert len(summary["cases"]) == len(configs) == 2
    assert summary["frequency_solves"] == 12
    for name, digest in summary["source_sha256"].items():
        assert sha(ROOT / name) == digest, name
    modelpath = ROOT / "\u539a\u94dc\u8fb9\u754c\u57fa\u51c6\u7ed3\u679c/passive_surface_models.json"
    assert sha(modelpath) == summary["model_sha256"]
    model = next(item for item in json.loads(modelpath.read_text(encoding="utf-8"))["models"]
                 if item["terms"] == 64)
    results = []
    for config, row in zip(configs, summary["cases"]):
        assert config["shape"] == row["shape"] and config["mode"] == row["mode"] == [1, 2, 0]
        assert config["terms"] == 64 and config["frequency_Q_relative_limit"] == .01
        path = folder / row["file"]
        assert sha(path) == row["sha256"]
        errors, magnetic, charges, powers = [], [], [], []
        with np.load(path) as data:
            shape = tuple(map(int, data["shape"]))
            assert list(shape) == row["shape"]
            np.testing.assert_array_equal(data["mode"], row["mode"])
            m, q, edges, scales, labels, _ = geometry(shape, data["lengths_m"])
            r, l, drive = data["R_ohm"], data["L_H"], data["drive_A"]
            np.testing.assert_array_equal(r, model["R_ohm"])
            np.testing.assert_array_equal(l, model["L_H"])
            assert float(data["sigma_S_per_m"]) == model["sigma_S_per_m"]
            fref, qref = quadrature_q(data["mode"], data["lengths_m"], model["sigma_S_per_m"])
            np.testing.assert_allclose(data["continuum_frequency_Hz"], fref, rtol=1e-12)
            np.testing.assert_allclose(data["continuum_Q"], qref, rtol=1e-12)
            # This source orientation is absent from both prior 011/101 scans.
            components = split(drive, layouts(shape)[0])
            assert np.all(components[0] == 0) and np.all(components[1] == 0)
            assert np.linalg.norm(components[2]) > 0
            frequencies = np.r_[data["frequency_Hz"], float(data["peak_frequency_Hz"])]
            assert len(frequencies) == 6 and np.all((frequencies > 2.5e9) & (frequencies < 3.5e9))
            amplitudes = []
            for index, frequency in enumerate(frequencies):
                s = 2j * np.pi * frequency
                e = data["e_V"][index] if index < 5 else data["peak_e_V"]
                b = data["b_Wb"][index] if index < 5 else data["peak_b_Wb"]
                j = data["wall_j_A"][index] if index < 5 else data["peak_wall_j_A"]
                assert np.all(np.isfinite(e)) and np.all(np.isfinite(b)) and np.all(np.isfinite(j))
                z = np.sum(r * s * l / (r + s * l))
                np.testing.assert_allclose(j, e[edges] / (scales * z), rtol=1e-12, atol=1e-30)
                total_j = np.zeros(len(m), complex)
                np.add.at(total_j, edges, j)
                qb = q * b
                ctqb = transpose_array(qb.real, shape) + 1j * transpose_array(qb.imag, shape)
                errors.append(float(np.linalg.norm(s * m * e - ctqb - drive + total_j) /
                                    (np.linalg.norm(ctqb) + np.linalg.norm(drive) +
                                     np.linalg.norm(total_j))))
                magnetic.append(float(np.linalg.norm(s * b + curl_array(e, shape)) /
                                      np.linalg.norm(s * b)))
                left = s * node_divergence(m * e, shape)
                right = node_divergence(drive - total_j, shape)
                charges.append(float(np.linalg.norm(left - right) /
                                     max(np.linalg.norm(left), np.linalg.norm(right))))
                wall = .5 * np.real(np.vdot(e[edges], j))
                source = .5 * np.real(np.vdot(e, drive))
                assert wall > 0 and source > 0
                powers.append(float(abs(source / wall - 1)))
                np.testing.assert_allclose(source, data["source_power_W"][index] if index < 5
                                           else data["peak_source_power_W"], rtol=1e-10)
                states = j[:, None] * r / (r + s * l)
                heat = .5 * np.sum(scales[:, None] * r * abs(j[:, None] - states)**2)
                np.testing.assert_allclose(wall, heat, rtol=1e-12)
                if index < 5:
                    amplitudes.append(np.vdot(drive, e))
                else:
                    energy = .25 * (m @ abs(e)**2 + q @ abs(b)**2 +
                                     np.sum(scales[:, None] * l * abs(states)**2))
                    qenergy = 2 * np.pi * frequency * energy / wall
                    np.testing.assert_allclose(data["peak_energy_J"], energy, rtol=1e-12)
                    np.testing.assert_allclose(data["peak_Q_energy"], qenergy, rtol=1e-9)
            np.testing.assert_allclose(data["amplitude"], amplitudes, rtol=1e-12)
            pole, qfit = pole_fit(data["frequency_Hz"], amplitudes)
            _, q3 = pole_fit(data["frequency_Hz"][1:4], amplitudes[1:4])
            np.testing.assert_allclose(pole, data["fitted_pole_Hz"], rtol=1e-11)
            qerror, ferror = abs(qfit / qref - 1), abs(pole.real / fref - 1)
            np.testing.assert_allclose(row["Q_error"], qerror, atol=1e-11, rtol=1e-8)
            np.testing.assert_allclose(row["frequency_error"], ferror, atol=1e-12)
            assert row["Q_1percent_pass"] == (qerror < .01)
            assert row["frequency_1percent_pass"] == (ferror < .01)
            assert max(errors) < 1e-8 and max(magnetic) < 1e-8
            assert max(charges) < 1e-8 and max(powers) < 1e-7
            assert abs(qenergy / qfit - 1) < .001 and abs(q3 / qfit - 1) < .001
            results.append(dict(
                shape=list(shape), mode=[1, 2, 0], Q_error=float(qerror),
                frequency_error=float(ferror), electric_residual=max(errors),
                magnetic_residual=max(magnetic), charge_residual=max(charges),
                power_closure=max(powers), energy_vs_pole_Q=float(abs(qenergy / qfit - 1)),
                five_vs_three_Q=float(abs(q3 / qfit - 1))))
    for key in ("fine_grid_long_transient_passed", "arbitrary_modes_passed",
                "manufactured_cavity_passed", "full_joint_simulation_passed"):
        assert summary[key] is False
    return dict(
        independent_equations_passed=True, field_frequency_points_checked=12,
        cases=results, summary_sha256=sha(folder / "summary.json"),
        verifier_sha256=sha(Path(__file__)),
        helper_sha256={name: sha(ROOT / name) for name in (
            "verify_finite_copper_cavity.py", "verify_finite_pec_cavity.py")},
        fine_grid_long_transient_passed=False, arbitrary_modes_passed=False,
        manufactured_cavity_passed=False, full_joint_simulation_passed=False)


if __name__ == "__main__":
    result = audit()
    (OUT / "verification.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
