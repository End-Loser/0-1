"""Independent 111 Gaussian reference and full-array equation checks."""
import hashlib
import json
from pathlib import Path

import numpy as np

from verify_finite_copper_cavity import geometry, node_divergence
from verify_finite_pec_cavity import curl_array, transpose_array, layouts, mask

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "\u94dc\u8154111\u53cc\u6781\u5316\u7ed3\u679c"
C0 = 299792458.
MU = 4e-7 * np.pi
EPS = 1 / (MU * C0**2)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def gaussian_reference(lengths, sigma, basis=None):
    lengths = np.asarray(lengths, float)
    assert lengths.shape == (3,) and np.all(np.isfinite(lengths)) and np.all(lengths > 0)
    assert np.isfinite(sigma) and sigma > 0
    k = np.pi / lengths
    omega = C0 * np.sqrt(sum(k**2))
    if basis is None:
        basis = np.linalg.svd(k[None, :], full_matrices=True)[2][1:].T
    basis = np.asarray(basis, float)
    assert basis.shape == (3, 2) and np.isfinite(basis).all() and np.linalg.matrix_rank(basis) == 2
    assert np.linalg.norm(k @ basis) < 1e-12 * np.linalg.norm(k) * np.linalg.norm(basis)
    points, weights = np.polynomial.legendre.leggauss(24)
    coordinates = [(points + 1) * length / 2 for length in lengths]
    ws = [weights * length / 2 for length in lengths]

    def fields(xyz):
        e, h = [np.zeros(xyz[0].shape + (3, 2)) for _ in range(2)]
        for c in range(3):
            electric = np.cos(k[c] * xyz[c])
            magnetic = np.sin(k[c] * xyz[c])
            for a in range(3):
                if a != c:
                    electric *= np.sin(k[a] * xyz[a])
                    magnetic *= np.cos(k[a] * xyz[a])
            e[..., c, :] = electric[..., None] * basis[c]
            a, b = (c + 1) % 3, (c + 2) % 3
            h[..., c, :] = magnetic[..., None] * (
                k[a] * basis[b] - k[b] * basis[a]) / (omega * MU)
        return e.reshape(-1, 3, 2), h.reshape(-1, 3, 2)

    e, h = fields(np.meshgrid(*coordinates, indexing="ij"))
    volume_w = np.einsum("i,j,k->ijk", *ws).ravel()
    mass = EPS * np.einsum("x,xcp,xcq->pq", volume_w, e, e)
    magnetic_mass = MU * np.einsum("x,xcp,xcq->pq", volume_w, h, h)
    np.testing.assert_allclose(mass, magnetic_mass, rtol=1e-12, atol=1e-29)
    surface = np.zeros((2, 2))
    for normal in range(3):
        other = [a for a in range(3) if a != normal]
        for side in (0, 1):
            xyz = [np.full((24, 24), side * lengths[normal]) for _ in range(3)]
            xyz[other[0]], xyz[other[1]] = np.meshgrid(
                coordinates[other[0]], coordinates[other[1]], indexing="ij")
            _, h = fields(xyz)
            h[:, normal, :] = 0.
            face_w = np.outer(ws[other[0]], ws[other[1]]).ravel()
            surface += np.einsum("x,xcp,xcq->pq", face_w, h, h)
    rs = np.sqrt(omega * MU / (2 * sigma))
    rates = np.linalg.eigvals(np.linalg.solve(mass, rs * surface))
    assert np.max(abs(rates.imag)) < 1e-8 and np.all(rates.real > 0)
    return dict(frequency_Hz=float(omega / (2 * np.pi)),
                Q=np.sort(omega / rates.real), mass=mass, surface=surface, basis=basis)


def matrix_poles(frequency, response):
    f, h = np.asarray(frequency), np.asarray(response)
    assert f.ndim == 1 and len(f) >= 3 and np.all(np.isfinite(f)) and np.all(np.diff(f) > 0)
    assert h.shape == (len(f), 2, 2) and np.all(np.isfinite(h))
    center, unit = np.mean(f), np.std(f)
    inverse = np.linalg.inv(h)
    slope, intercept = np.zeros((2, 2), complex), np.zeros((2, 2), complex)
    for i in range(2):
        for j in range(2):
            slope[i, j], intercept[i, j] = np.polyfit((f - center) / unit, inverse[:, i, j], 1)
    quadratic = np.linalg.det(slope)
    linear = (slope[0, 0] * intercept[1, 1] + slope[1, 1] * intercept[0, 0]
              - slope[0, 1] * intercept[1, 0] - slope[1, 0] * intercept[0, 1])
    poles = center + unit * np.roots([quadratic, linear, np.linalg.det(intercept)])
    assert np.all(np.isfinite(poles)) and np.all(poles.real > 0) and np.all(poles.imag > 0)
    q = poles.real / (2 * poles.imag)
    return poles[np.argsort(q)], np.sort(q)


def seed_basis(shape, lengths, physical_basis, m):
    spacing = np.asarray(lengths) / shape
    k = np.pi / lengths
    kd = 2 * np.sin(k * spacing / 2) / spacing
    seeds = []
    for vector in physical_basis.T:
        vector = vector - kd * np.dot(kd, vector) / np.dot(kd, kd)
        fields = []
        for c, layout in enumerate(layouts(shape)[0]):
            index = np.indices(layout, dtype=float)
            index[c] += .5
            field = np.full(layout, vector[c] * spacing[c])
            for a in range(3):
                phase = k[a] * spacing[a] * index[a]
                field *= np.cos(phase) if a == c else np.sin(phase)
            fields.append(field.ravel())
        seed = np.concatenate(fields)
        seed[~mask(shape)] = 0.
        for previous in seeds:
            seed -= previous * np.sum(m * previous * seed)
        seed /= np.sqrt(np.sum(m * seed**2))
        seeds.append(seed)
    return np.column_stack(seeds)


def check_fields(shape, frequency, drives, e, b, j, m, q, edges, scales, r, l):
    assert drives.shape == e.shape == (len(m), 3)
    assert b.shape == (len(q), 3) and j.shape == (len(edges), 3)
    assert all(np.all(np.isfinite(a)) for a in (drives, e, b, j))
    s = 2j * np.pi * frequency
    impedance = np.sum(r * s * l / (r + s * l))
    np.testing.assert_allclose(j, e[edges] / (scales[:, None] * impedance), rtol=1e-12, atol=1e-30)
    mixed = np.array([1., 1j]) / np.sqrt(2)
    superposition = []
    for field in (e, b, j):
        expected = field[:, :2] @ mixed
        superposition.append(float(np.linalg.norm(field[:, 2] - expected) / np.linalg.norm(expected)))
    assert max(superposition) < 1e-10
    rows = []
    for c in range(3):
        ec, bc, jc, drive = e[:, c], b[:, c], j[:, c], drives[:, c]
        total = np.bincount(edges, weights=jc.real, minlength=len(m)).astype(complex)
        total += 1j * np.bincount(edges, weights=jc.imag, minlength=len(m))
        qb = q * bc
        ct = transpose_array(qb.real, shape) + 1j * transpose_array(qb.imag, shape)
        er = np.linalg.norm(s * m * ec - ct - drive + total) / (
            np.linalg.norm(ct) + np.linalg.norm(drive) + np.linalg.norm(total))
        br = np.linalg.norm(s * bc + curl_array(ec, shape)) / np.linalg.norm(s * bc)
        left, right = s * node_divergence(m * ec, shape), node_divergence(drive - total, shape)
        cr = np.linalg.norm(left - right) / max(np.linalg.norm(left), np.linalg.norm(right), 1e-25)
        source = .5 * np.real(np.vdot(ec, drive))
        wall = .5 * np.real(np.vdot(ec[edges], jc))
        assert source > 0 and wall > 0
        power = abs(source / wall - 1)
        state = jc[:, None] * r / (r + s * l)
        heat = .5 * np.sum(scales[:, None] * r * abs(jc[:, None] - state)**2)
        np.testing.assert_allclose(wall, heat, rtol=1e-12, atol=0.)
        energy = .25 * (m @ abs(ec)**2 + q @ abs(bc)**2 +
                        np.sum(scales[:, None] * l * abs(state)**2))
        assert energy > 0 and er < 1e-8 and br < 1e-8 and cr < 1e-8 and power < 1e-7
        rows.append(dict(electric=float(er), magnetic=float(br), charge=float(cr),
                         power=float(power), energy_J=float(energy), source_power_W=float(source)))
    h = drives[:, :2].T @ e[:, :2]
    reciprocity = np.linalg.norm(h - h.T) / np.linalg.norm(h)
    assert reciprocity < 1e-10
    assert np.all(np.linalg.eigvalsh((h + h.conj().T) / 2) > 0)
    return dict(equations=rows, mixed_full_field_difference=max(superposition),
                reciprocity=float(reciprocity)), h


def modal_energy_q(frequency, e, b, j, coefficients, drives, m, q, scales, r, l):
    s = 2j * np.pi * frequency
    ec, bc, jc = e[:, :2] @ coefficients, b[:, :2] @ coefficients, j[:, :2] @ coefficients
    drive = drives[:, :2] @ coefficients
    state = jc[:, None] * r / (r + s * l)
    wall = .5 * np.sum(scales[:, None] * r * abs(jc[:, None] - state)**2)
    energy = .25 * (m @ abs(ec)**2 + q @ abs(bc)**2 +
                    np.sum(scales[:, None] * l * abs(state)**2))
    source = .5 * np.real(np.vdot(ec, drive))
    assert wall > 0 and energy > 0 and abs(source / wall - 1) < 1e-7
    return float(2 * np.pi * frequency * energy / wall)


def audit(folder=OUT):
    summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "solver_complete_unverified", "Doublet solves are not complete"
    configs = json.loads((folder / "configurations.json").read_text(encoding="utf-8"))
    assert sha(folder / "configurations.json") == summary["configuration_sha256"]
    assert len(configs) == len(summary["cases"]) == 2
    assert summary["full_matrix_factorizations"] == 14 and summary["full_current_rhs_solves"] == 42
    expected_sources = {"run_copper_doublet.py", "copper_doublet.py", "finite_copper_cavity.py", "finite_pec_cavity.py"}
    assert set(summary["source_sha256"]) == expected_sources
    assert all(sha(ROOT / name) == digest for name, digest in summary["source_sha256"].items())
    model_path = ROOT / "\u539a\u94dc\u8fb9\u754c\u57fa\u51c6\u7ed3\u679c/passive_surface_models.json"
    assert sha(model_path) == summary["model_sha256"]
    model = next(row for row in json.loads(model_path.read_text(encoding="utf-8"))["models"] if row["terms"] == 64)
    results = []
    for expected_shape, config, row in zip(((24, 15, 9), (36, 23, 14)), configs, summary["cases"]):
        assert config["shape"] == row["shape"] == list(expected_shape)
        assert config["mode"] == [1, 1, 1] and config["lengths_m"] == [.16, .10, .06]
        assert config["independent_basis_drives"] == 2 and config["independently_solved_mixed_drive"] is True
        assert config["scan_frequencies"] == 5 and config["peak_frequencies"] == 2 and config["terms"] == 64
        assert config["frequency_Q_limit"] == .01 and config["three_five_Q_limit"] == .001
        assert row["status"] == "solver_complete_unverified"
        assert row["matrix_factorizations"] == 7 and row["independent_current_rhs_solves"] == 21
        scan_path, peak_path = folder / row["scan_file"], folder / row["peak_file"]
        assert sha(scan_path) == row["scan_sha256"] and sha(peak_path) == row["peak_sha256"]
        field_checks, responses, energy_q = [], [], []
        with np.load(scan_path) as scan, np.load(peak_path) as peak:
            shape, lengths = tuple(map(int, scan["shape"])), scan["lengths_m"]
            assert shape == expected_shape
            np.testing.assert_array_equal(scan["mode"], [1, 1, 1])
            np.testing.assert_array_equal(lengths, config["lengths_m"])
            m, q, edges, scales, labels, _ = geometry(shape, lengths)
            r, l = scan["R_ohm"], scan["L_H"]
            np.testing.assert_array_equal(r, model["R_ohm"])
            np.testing.assert_array_equal(l, model["L_H"])
            assert len(r) == 64 and np.all(r > 0) and np.all(l > 0)
            assert float(scan["sigma_S_per_m"]) == model["sigma_S_per_m"]
            k = np.pi / lengths
            p1 = np.array([0., k[2], -k[1]])
            p1 /= np.linalg.norm(p1)
            p2 = np.cross(k, p1)
            p2 /= np.linalg.norm(p2)
            physical = np.column_stack((p1, p2))
            np.testing.assert_allclose(scan["reference_basis"], physical, rtol=1e-13, atol=1e-15)
            ref = gaussian_reference(lengths, model["sigma_S_per_m"], physical)
            for name in ("mass", "surface", "Q", "frequency_Hz"):
                np.testing.assert_allclose(scan["reference_" + name], ref[name], rtol=1e-12, atol=1e-29)
            independent_ref = gaussian_reference(lengths, model["sigma_S_per_m"])
            np.testing.assert_allclose(ref["Q"], independent_ref["Q"], rtol=1e-12)
            seeds = seed_basis(shape, lengths, physical, m)
            np.testing.assert_allclose(scan["seeds"], seeds, rtol=1e-12, atol=1e-10)
            np.testing.assert_allclose(seeds.T @ (m[:, None] * seeds), np.eye(2), atol=1e-12)
            mixed = np.array([1., 1j]) / np.sqrt(2)
            np.testing.assert_array_equal(scan["mixed_coefficients"], mixed)
            drives = np.column_stack((m[:, None] * seeds, (m[:, None] * seeds) @ mixed))
            np.testing.assert_allclose(scan["drives_A"], drives, rtol=1e-13, atol=1e-30)
            frequencies = scan["frequency_Hz"]
            assert len(frequencies) == 5 and np.all(np.diff(frequencies) > 0)
            for data, expected_count in ((scan, 5), (peak, 2)):
                assert len(data["frequency_Hz"]) == expected_count
                for index, frequency in enumerate(data["frequency_Hz"]):
                    assert 2.5e9 < frequency < 3.5e9
                    e, b, j = data["e_V"][index], data["b_Wb"][index], data["wall_j_A"][index]
                    checks, h = check_fields(shape, frequency, drives, e, b, j, m, q, edges, scales, r, l)
                    np.testing.assert_allclose(data["source_power_W"][index],
                                               [c["source_power_W"] for c in checks["equations"]], rtol=1e-10, atol=0.)
                    field_checks.append(checks)
                    if data is scan:
                        responses.append(h)
                    else:
                        energy_q.append(modal_energy_q(
                            frequency, e, b, j, peak["fitted_vectors"][:, index],
                            drives, m, q, scales, r, l))
            np.testing.assert_allclose(scan["response"], responses, rtol=1e-12, atol=1e-30)
            poles, qfit = matrix_poles(frequencies, responses)
            _, q3 = matrix_poles(frequencies[1:4], np.asarray(responses)[1:4])
            np.testing.assert_allclose(peak["fitted_poles_Hz"], poles, rtol=1e-11, atol=1e-5)
            np.testing.assert_allclose(peak["fitted_Q"], qfit, rtol=1e-8)
            np.testing.assert_allclose(peak["frequency_Hz"], poles.real, rtol=1e-12)
            rotation = np.array([[np.cos(.7), -np.sin(.7)], [np.sin(.7), np.cos(.7)]])
            rotated_poles, _ = matrix_poles(frequencies, [rotation.T @ h @ rotation for h in responses])
            np.testing.assert_allclose(rotated_poles, poles, rtol=1e-12, atol=1e-4)
            inverse = np.linalg.inv(responses)
            unit = np.ptp(frequencies)
            x = (frequencies - np.mean(frequencies)) / unit
            coefficients = np.array([np.polyfit(x, inverse[:, i, j], 1)
                                     for i in range(2) for j in range(2)])
            slope, intercept = coefficients[:, 0].reshape(2, 2), coefficients[:, 1].reshape(2, 2)
            fitted_inverse = x[:, None, None] * slope + intercept
            matrix_residual = np.linalg.norm(fitted_inverse - inverse) / np.linalg.norm(inverse)
            np.testing.assert_allclose(row["inverse_matrix_fit_residual"], matrix_residual, rtol=1e-7)
            assert matrix_residual < .001
            null_errors = []
            for index, pole in enumerate(poles):
                vector = peak["fitted_vectors"][:, index]
                np.testing.assert_allclose(np.linalg.norm(vector), 1., rtol=1e-12)
                coordinate = (pole - np.mean(frequencies)) / unit
                null_error = np.linalg.norm((coordinate * slope + intercept) @ vector) / (
                    abs(coordinate) * np.linalg.norm(slope) + np.linalg.norm(intercept))
                assert null_error < 1e-7
                null_errors.append(float(null_error))
            qe, fe, window = abs(qfit / ref["Q"] - 1), abs(poles.real / ref["frequency_Hz"] - 1), abs(q3 / qfit - 1)
            ep = abs(np.array(energy_q) / qfit - 1)
            for key, observed in (("Q", qfit), ("reference_Q", ref["Q"]), ("Q_relative_error", qe),
                                  ("frequency_relative_error", fe), ("three_five_Q_difference", window)):
                np.testing.assert_allclose(row[key], observed, rtol=1e-7, atol=1e-10)
            selected = bool(np.all(qe < .01) and np.all(fe < .01) and np.all(window < .001))
            assert row["selected_doublet_frequency_Q_passed"] == selected
            results.append(dict(
                shape=list(shape), Q=qfit.tolist(), reference_Q=ref["Q"].tolist(),
                Q_relative_error=qe.tolist(), frequency_relative_error=fe.tolist(),
                three_five_Q_difference=window.tolist(), modal_energy_Q=energy_q,
                inverse_matrix_fit_residual=float(matrix_residual), max_pole_null_residual=max(null_errors),
                modal_energy_vs_pole_Q_difference=ep.tolist(),
                selected_doublet_frequency_Q_passed=selected,
                modal_energy_pole_consistency_passed=bool(np.all(ep < .001)),
                full_fields_checked=21, max_electric_residual=max(c["electric"] for f in field_checks for c in f["equations"]),
                max_magnetic_residual=max(c["magnetic"] for f in field_checks for c in f["equations"]),
                max_charge_residual=max(c["charge"] for f in field_checks for c in f["equations"]),
                max_power_closure=max(c["power"] for f in field_checks for c in f["equations"]),
                max_reciprocity=max(f["reciprocity"] for f in field_checks),
                max_mixed_full_field_difference=max(f["mixed_full_field_difference"] for f in field_checks)))
    for key in ("doublet_long_transient_passed", "arbitrary_modes_passed", "general_boundaries_passed",
                "manufactured_cavity_passed", "full_joint_simulation_passed"):
        assert summary[key] is False
    assert summary["full_system_gates_closed"] == 0
    return dict(
        independent_full_equations_passed=True, full_fields_checked=42, cases=results,
        finest_doublet_frequency_model_passed=bool(
            results[-1]["selected_doublet_frequency_Q_passed"] and results[-1]["modal_energy_pole_consistency_passed"]),
        doublet_long_transient_passed=False, arbitrary_modes_passed=False, general_boundaries_passed=False,
        manufactured_cavity_passed=False, full_joint_simulation_passed=False, full_system_gates_closed=0,
        summary_sha256=sha(folder / "summary.json"), verifier_sha256=sha(Path(__file__)),
        helper_sha256={name: sha(ROOT / name) for name in (
            "verify_finite_copper_cavity.py", "verify_finite_pec_cavity.py")})


if __name__ == "__main__":
    result = audit()
    target = OUT / ("verification_" + result["verifier_sha256"][:12] + ".json")
    text = json.dumps(result, indent=2) + "\n"
    if target.exists():
        assert target.read_text(encoding="utf-8") == text, "Existing verification differs"
    else:
        with target.open("x", encoding="utf-8") as stream:
            stream.write(text)
    print(json.dumps(result, indent=2))
