"""Independent array differences; does not import the finite-cavity solver."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import numpy as np

C0 = 299792458.
MU = 4 * np.pi * 1e-7
EPS = 1 / (MU * C0**2)


def layouts(shape):
    x, y, z = map(int, shape)
    return [(x, y + 1, z + 1), (x + 1, y, z + 1), (x + 1, y + 1, z)], [
        (x + 1, y, z), (x, y + 1, z), (x, y, z + 1)]


def split(vector, shapes):
    offsets = np.r_[0, np.cumsum([np.prod(s) for s in shapes])]
    return [vector[offsets[c]:offsets[c + 1]].reshape(shapes[c]) for c in range(3)]


def mask(shape):
    es, _ = layouts(shape)
    fields = []
    for c, s in enumerate(es):
        indices = np.indices(s)
        valid = np.ones(s, bool)
        for a in range(3):
            if a != c:
                valid &= (indices[a] > 0) & (indices[a] < shape[a])
        fields.append(valid.ravel())
    return np.concatenate(fields)


def lift(e, shape):
    valid = mask(shape)
    full = np.zeros(len(valid), dtype=np.asarray(e).dtype)
    full[valid] = e
    return full


def curl_array(e, shape):
    ex, ey, ez = split(e, layouts(shape)[0])
    return np.concatenate([(np.diff(ez, axis=1) - np.diff(ey, axis=2)).ravel(),
                           (np.diff(ex, axis=2) - np.diff(ez, axis=0)).ravel(),
                           (np.diff(ey, axis=0) - np.diff(ex, axis=1)).ravel()])


def transpose_array(b, shape):
    bx, by, bz = split(b, layouts(shape)[1])
    ex, ey, ez = [np.zeros(s) for s in layouts(shape)[0]]
    ey[:, :, :-1] += bx
    ey[:, :, 1:] -= bx
    ez[:, 1:, :] += bx
    ez[:, :-1, :] -= bx
    ez[:-1, :, :] += by
    ez[1:, :, :] -= by
    ex[:, :, 1:] += by
    ex[:, :, :-1] -= by
    ex[:, :-1, :] += bz
    ex[:, 1:, :] -= bz
    ey[1:, :, :] += bz
    ey[:-1, :, :] -= bz
    return np.concatenate([ex.ravel(), ey.ravel(), ez.ravel()])


def divergence_array(b, shape):
    fields = split(b, layouts(shape)[1])
    return sum(np.diff(fields[c], axis=c) for c in range(3)).ravel()


def interior_divergence(e_flux, shape):
    ex, ey, ez = split(e_flux, layouts(shape)[0])
    return (np.diff(ex, axis=0)[:, 1:-1, 1:-1] +
            np.diff(ey, axis=1)[1:-1, :, 1:-1] +
            np.diff(ez, axis=2)[1:-1, 1:-1, :]).ravel()


def gradient_array(phi):
    return np.concatenate([np.diff(phi, axis=c).ravel() for c in range(3)])


def weights(shape, lengths):
    h = np.asarray(lengths) / shape
    es, fs = layouts(shape)
    dual = [h[c] * np.r_[.5, np.ones(shape[c] - 1), .5] for c in range(3)]
    masses, reluctances = [], []
    for c in range(3):
        index = np.indices(es[c])
        mass = np.full(es[c], EPS / h[c])
        for a in range(3):
            if a != c:
                mass *= dual[a][index[a]]
        masses.append(mass.ravel())
        index = np.indices(fs[c])
        reluctances.append((dual[c][index[c]] / (MU * np.prod(np.delete(h, c)))).ravel())
    return np.concatenate(masses)[mask(shape)], np.concatenate(reluctances)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(folder):
    summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    assert summary["selected_eigenmode_solves"] == 24
    assert summary["forced_transient_cases"] == 2 and len(summary["cases"]) == 6
    for name, digest in summary["source_sha256"].items():
        assert sha(Path(__file__).with_name(name)) == digest, name
    checks = []
    for row in summary["cases"]:
        path = folder / row["file"]
        assert sha(path) == row["sha256"], row["case"]
        with np.load(path) as d:
            shape = tuple(map(int, d["shape"]))
            lengths = d["lengths_m"]
            m, q = weights(shape, lengths)
            if "mode_indices" in d:
                modes = d["mode_indices"]
                h = lengths / shape
                continuum = C0 / 2 * np.linalg.norm(modes / lengths, axis=1)
                discrete = C0 / np.pi * np.linalg.norm(
                    np.sin(np.pi * modes * h / (2 * lengths)) / h, axis=1)
                observed = np.sqrt(d["eigenvalues_rad2_s2"]) / (2 * np.pi)
                np.testing.assert_allclose(d["frequency_Hz"], observed, rtol=1e-13)
                np.testing.assert_allclose(d["continuum_frequency_Hz"], continuum, rtol=1e-13)
                eigen_residual, gauss_residual = [], []
                for value, e in zip(d["eigenvalues_rad2_s2"], d["edge_vectors_V"]):
                    b = curl_array(lift(e, shape), shape)
                    ke = transpose_array(q * b, shape)[mask(shape)]
                    eigen_residual.append(np.linalg.norm(ke - value * m * e) /
                                          np.linalg.norm(value * m * e))
                    gauss_residual.append(np.linalg.norm(interior_divergence(lift(m * e, shape), shape)) /
                                          np.linalg.norm(m * e))
                error = abs(observed / continuum - 1)
                np.testing.assert_allclose(error, row["frequency_relative_error"], atol=1e-13)
                assert row["all_selected_modes_1percent_pass"] == bool(np.all(error < .01))
                assert max(eigen_residual) < 1e-8
                assert max(gauss_residual) < 1e-8
                assert np.max(abs(observed / discrete - 1)) < 1e-8
                checks.append({"case": row["case"], "frequency_error_max": float(max(error)),
                               "independent_discrete_frequency_difference": float(max(abs(observed / discrete - 1))),
                               "eigen_equation_residual": float(max(eigen_residual)),
                               "interior_Gauss_residual": float(max(gauss_residual))})
            else:
                e, b, j, dt = d["e_V"], d["b_Wb"], d["current_A"], float(d["dt_s"])
                assert e.shape[0] == b.shape[0] == len(j) + 1
                assert np.all(e[0] == 0) and np.all(b[0] == 0)
                energy = .5 * (e**2 @ m + b**2 @ q)
                scale = max(float(max(energy)), 1e-40)
                e_res, b_res, charge_res, div_res, wall_flux = [], [], [], [], []
                source_work, rho_change = [], []
                for n in range(len(j)):
                    emid, bmid = (e[n] + e[n + 1]) / 2, (b[n] + b[n + 1]) / 2
                    ctqb = transpose_array(q * bmid, shape)[mask(shape)]
                    er = m * (e[n + 1] - e[n]) / dt - ctqb - j[n]
                    br = (b[n + 1] - b[n]) / dt + curl_array(lift(emid, shape), shape)
                    e_res.append(np.linalg.norm(er) / max(np.linalg.norm(ctqb) + np.linalg.norm(j[n]), 1e-40))
                    b_res.append(np.linalg.norm(br) / max(np.linalg.norm(curl_array(lift(emid, shape), shape)), 1e-40))
                    change = interior_divergence(lift(m * (e[n + 1] - e[n]), shape), shape)
                    expected = dt * interior_divergence(lift(j[n], shape), shape)
                    charge_res.append(np.linalg.norm(change - expected))
                    rho_change.append(np.linalg.norm(expected))
                    div_res.append(np.linalg.norm(divergence_array(b[n + 1], shape)))
                    faces = split(b[n + 1], layouts(shape)[1])
                    wall_flux.append(max(np.max(abs(np.take(faces[c], [0, -1], axis=c)))
                                         for c in range(3)))
                    source_work.append(float(j[n] @ emid))
                np.testing.assert_allclose(d["source_work_W"], source_work, atol=1e-30, rtol=1e-10)
                closure = np.max(abs(energy - energy[0] - dt * np.r_[0., np.cumsum(source_work)])) / scale
                charge_scale = max(np.linalg.norm(interior_divergence(lift(m * e[-1], shape), shape)),
                                   max(rho_change), 1e-40)
                charge_error = max(charge_res) / charge_scale
                flux_scale = max(float(np.max(np.linalg.norm(b, axis=1))), 1e-40)
                div_error = max(div_res) / flux_scale
                off = (np.arange(len(j)) + .5) * dt >= .4e-9
                assert np.all(j[off] == 0)
                off_growth = max(0., float(max(np.diff(energy)[off]))) / scale
                assert closure < 1e-9 and charge_error < 1e-9 and div_error < 1e-9
                assert max(e_res) < 1e-8 and max(b_res) < 1e-8 and max(wall_flux) == 0
                assert off_growth < 1e-10
                checks.append({"case": row["case"], "all_steps_checked": len(j),
                               "electric_equation_residual": float(max(e_res)),
                               "magnetic_equation_residual": float(max(b_res)),
                               "interior_charge_continuity_relative": float(charge_error),
                               "magnetic_divergence_relative": float(div_error),
                               "PEC_normal_wall_flux_max_Wb": float(max(wall_flux)),
                               "energy_closure_relative": float(closure),
                               "source_off_energy_growth_relative": float(off_growth)})
    assert not summary["cases"][0]["all_selected_modes_1percent_pass"]
    assert summary["cases"][3]["all_selected_modes_1percent_pass"]
    assert all(checks[n]["frequency_error_max"] > checks[n + 1]["frequency_error_max"]
               for n in range(3))
    tests = subprocess.run([sys.executable, "-B", str(Path(__file__).with_name("test_finite_pec_cavity.py"))],
                           capture_output=True, text=True, check=True)
    result = {"independent_verification_pass": True, "checks": checks,
              "test_output": tests.stdout + tests.stderr, "summary_sha256": sha(folder / "summary.json"),
              "verifier_sha256": sha(Path(__file__)),
              "tests_sha256": sha(Path(__file__).with_name("test_finite_pec_cavity.py")),
              "selected_modes_only": True, "surface_charge_verified": False,
              "copper_cavity_complete": False, "copper_Q_verified": False,
              "full_joint_simulation_complete": False}
    (folder / "verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("folder", type=Path)
    main(p.parse_args().folder)
