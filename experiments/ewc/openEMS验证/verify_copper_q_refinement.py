"""Full-array/energy/continuous-Q verification without importing refinement runner."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
from verify_finite_copper_cavity import geometry, quadrature_q, node_divergence
from verify_finite_pec_cavity import curl_array, transpose_array

ROOT = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pole_fit(f, amplitude):
    center, scale = np.mean(f), np.std(f)
    slope, intercept = np.polyfit((f - center) / scale, 1 / np.asarray(amplitude), 1)
    pole = center - scale * intercept / slope
    return pole, pole.real / (2 * pole.imag)


def main():
    out = ROOT / "铜Q细化结果"
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["full_frequency_solves"] == 26 and len(summary["cases"]) == 4
    for file, digest in summary["source_sha256"].items():
        assert sha(ROOT / file) == digest
    modelpath = ROOT / "厚铜边界基准结果" / "passive_surface_models.json"
    assert sha(modelpath) == summary["model_sha256"]
    model = next(m for m in json.loads(modelpath.read_text(encoding="utf-8"))["models"] if m["terms"] == 64)
    for control in summary["controls"]:
        assert sha(ROOT / "六面铜壁频域结果" / control["archive"]) == control["archive_sha256"]
        assert control["full_field_relative_difference"] < 1e-8
        assert control["source_power_relative_difference"] < 1e-7
    results = []
    for row in summary["cases"]:
        path = out / row["file"]
        assert sha(path) == row["sha256"]
        with np.load(path) as d:
            shape = tuple(map(int, d["shape"]))
            m, q, edges, scale, labels, area = geometry(shape, d["lengths_m"])
            r, l, drive = d["R_ohm"], d["L_H"], d["drive_A"]
            np.testing.assert_array_equal(r, model["R_ohm"])
            np.testing.assert_array_equal(l, model["L_H"])
            fc, qc = quadrature_q(d["mode"], d["lengths_m"], float(d["sigma_S_per_m"]))
            np.testing.assert_allclose(d["continuum_Q"], qc, rtol=1e-12)
            np.testing.assert_allclose(d["continuum_frequency_Hz"], fc, rtol=1e-12)
            errors, power_errors, charge_errors, amplitudes = [], [], [], []
            for i, f in enumerate(np.r_[d["frequency_Hz"], float(d["peak_frequency_Hz"])]):
                s = 2j * np.pi * f
                e = d["e_V"][i] if i < 5 else d["peak_e_V"]
                b = d["b_Wb"][i] if i < 5 else d["peak_b_Wb"]
                j = d["wall_j_A"][i] if i < 5 else d["peak_wall_j_A"]
                z = sum(r * s * l / (r + s * l))
                np.testing.assert_allclose(j, e[edges] / (z * scale), rtol=1e-12, atol=1e-30)
                total_j = np.zeros(len(m), complex)
                np.add.at(total_j, edges, j)
                qb = q * b
                ctqb = transpose_array(qb.real, shape) + 1j * transpose_array(qb.imag, shape)
                errors.append(np.linalg.norm(s * m * e - ctqb - drive + total_j) /
                              (np.linalg.norm(ctqb) + np.linalg.norm(total_j) + np.linalg.norm(drive)))
                assert np.linalg.norm(s * b + curl_array(e, shape)) / np.linalg.norm(s * b) < 1e-9
                pw, pin = .5 * np.real(np.vdot(e[edges], j)), .5 * np.real(np.vdot(e, drive))
                power_errors.append(abs(pin / pw - 1))
                np.testing.assert_allclose(pin, d["source_power_W"][i] if i < 5 else
                                           d["peak_source_power_W"], rtol=1e-10)
                left = s * node_divergence(m * e, shape)
                right = node_divergence(drive - total_j, shape)
                charge_errors.append(np.linalg.norm(left - right) / max(np.linalg.norm(left), np.linalg.norm(right)))
                ind = j[:, None] * r / (r + s * l)
                joule = .5 * np.sum(scale[:, None] * r * abs(j[:, None] - ind)**2)
                np.testing.assert_allclose(joule, pw, rtol=1e-12)
                if i < 5:
                    amplitudes.append(np.vdot(drive, e))
                else:
                    energy = .25 * (m @ abs(e)**2 + q @ abs(b)**2 +
                                     np.sum(scale[:, None] * l * abs(ind)**2))
                    qenergy = 2 * np.pi * f * energy / pw
                    np.testing.assert_allclose(d["peak_energy_J"], energy, rtol=1e-12)
                    np.testing.assert_allclose(d["peak_Q_energy"], qenergy, rtol=1e-9)
            np.testing.assert_allclose(d["amplitude"], amplitudes, rtol=1e-12)
            pole, qp = pole_fit(d["frequency_Hz"], amplitudes)
            _, q3 = pole_fit(d["frequency_Hz"][1:4], amplitudes[1:4])
            np.testing.assert_allclose(pole, d["fitted_pole_Hz"], rtol=1e-11)
            assert pole.imag > 0
            qerror, ferror = abs(qp / qc - 1), abs(pole.real / fc - 1)
            assert row["Q_1percent_pass"] == (qerror < .01)
            assert row["frequency_1percent_pass"] == (ferror < .01)
            window_error, energy_error = abs(q3 / qp - 1), abs(qenergy / qp - 1)
            assert max(errors) < 1e-8 and max(power_errors) < 1e-7 and max(charge_errors) < 1e-8
            assert window_error < .001 and energy_error < .001
            results.append({"case": row["case"], "Q_error": float(qerror), "frequency_error": float(ferror),
                            "full_e_equation_residual": max(errors), "power_closure": max(power_errors),
                            "all_node_charge_residual": max(charge_errors), "energy_vs_pole_Q": float(energy_error),
                            "five_vs_three_Q": float(window_error)})
    assert all(c["Q_1percent_pass"] and c["frequency_1percent_pass"] for c in summary["cases"][2:])
    assert all(summary["cases"][i]["Q_error"] > summary["cases"][i + 2]["Q_error"] for i in (0, 1))
    tests = subprocess.run([sys.executable, "-B", str(ROOT / "test_finite_copper_cavity.py")],
                           capture_output=True, text=True, check=True)
    record = {"independent_verification_pass": True, "full_field_frequency_points_checked": 24,
              "ordering_control_frequency_points": 2, "cases": results,
              "summary_sha256": sha(out / "summary.json"), "verifier_sha256": sha(Path(__file__)),
              "helper_sha256": {p: sha(ROOT / p) for p in
                                ["verify_finite_copper_cavity.py", "verify_finite_pec_cavity.py"]},
              "test_output": tests.stdout + tests.stderr, "manufactured_cavity_passed": False,
              "copper_long_transient_passed": False, "full_joint_simulation_passed": False}
    (out / "verification.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
