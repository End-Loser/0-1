"""Independent full-field/geometry/Q checks, without importing copper solver."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
from verify_finite_pec_cavity import layouts, split, curl_array, transpose_array

C0 = 299792458.
MU = 4e-7 * np.pi
EPS = 1 / (MU * C0**2)


def geometry(shape, lengths):
    shape = np.asarray(shape, int)
    h = np.asarray(lengths) / shape
    es, fs = layouts(shape)
    dual = [h[c] * np.r_[.5, np.ones(shape[c] - 1), .5] for c in range(3)]
    mass, q = [], []
    for c in range(3):
        index = np.indices(es[c])
        m = np.full(es[c], EPS / h[c])
        for a in range(3):
            if a != c:
                m *= dual[a][index[a]]
        mass.append(m.ravel())
        index = np.indices(fs[c])
        q.append((dual[c][index[c]] / (MU * np.prod(np.delete(h, c)))).ravel())
    offsets = np.r_[0, np.cumsum([np.prod(s) for s in es])]
    edges, scales, labels, area_weights = [], [], [], []
    for normal in range(3):
        for side in (0, 1):
            for tangent in range(3):
                if tangent == normal:
                    continue
                transverse = 3 - normal - tangent
                index = np.indices(es[tangent])
                keep = index[normal] == side * shape[normal]
                local = np.flatnonzero(keep.ravel())
                width = dual[transverse][index[transverse].ravel()[local]]
                edges.extend(offsets[tangent] + local)
                scales.extend(h[tangent] / width)
                labels.extend([(normal, side, tangent)] * len(local))
                area_weights.extend(h[tangent] * width)
    return (np.concatenate(mass), np.concatenate(q), np.array(edges),
            np.array(scales), np.array(labels), np.array(area_weights))


def node_divergence(full_edge_flux, shape):
    fields = split(full_edge_flux, layouts(shape)[0])
    rho = np.zeros(tuple(np.asarray(shape) + 1), dtype=np.asarray(full_edge_flux).dtype)
    for c, field in enumerate(fields):
        lo, hi = [slice(None)] * 3, [slice(None)] * 3
        lo[c], hi[c] = slice(None, -1), slice(1, None)
        rho[tuple(lo)] += field
        rho[tuple(hi)] -= field
    return rho.ravel()


def quadrature_q(mode, lengths, sigma):
    """Separate Gaussian E-volume/H-surface integration of the PEC reference."""
    lengths = np.asarray(lengths)
    k = np.pi * mode / lengths
    omega = C0 * np.sqrt(sum(k**2))
    c = int(np.flatnonzero(np.asarray(mode) == 0)[0])
    a, b = [i for i in range(3) if i != c]
    nodes, weights = np.polynomial.legendre.leggauss(24)
    coords = [(nodes + 1) * lengths[i] / 2 for i in range(3)]
    ws = [weights * lengths[i] / 2 for i in range(3)]

    def fields(xyz):
        amplitude = np.sin(k[a] * xyz[a]) * np.sin(k[b] * xyz[b])
        grad = np.zeros((3,) + amplitude.shape)
        grad[a] = k[a] * np.cos(k[a] * xyz[a]) * np.sin(k[b] * xyz[b])
        grad[b] = k[b] * np.cos(k[b] * xyz[b]) * np.sin(k[a] * xyz[a])
        unit = np.eye(3)[c]
        hfield = np.cross(np.moveaxis(grad, 0, -1), unit) / (omega * MU)
        return amplitude, hfield

    xyz = np.meshgrid(*coords, indexing="ij")
    amplitude, _ = fields(xyz)
    volume_weights = np.einsum("i,j,k->ijk", *ws)
    e2 = np.sum(volume_weights * amplitude**2)
    h_surface = 0.
    for normal in range(3):
        other = [i for i in range(3) if i != normal]
        for side in (0, 1):
            face_shape = (24, 24)
            xyz = [np.full(face_shape, side * lengths[normal]) for _ in range(3)]
            xyz[other[0]], xyz[other[1]] = np.meshgrid(coords[other[0]], coords[other[1]], indexing="ij")
            _, hfield = fields(xyz)
            tangential2 = np.sum(hfield**2, axis=-1) - hfield[..., normal]**2
            h_surface += np.sum(np.outer(ws[other[0]], ws[other[1]]) * tangential2)
    rs = np.sqrt(omega * MU / (2 * sigma))
    return omega / (2 * np.pi), omega * EPS * e2 / (rs * h_surface)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(folder):
    summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    assert len(summary["cases"]) == 7 and summary["full_frequency_solves"] == 70
    model_path = Path(summary["model_path"])
    assert sha(model_path) == summary["model_sha256"]
    models = json.loads(model_path.read_text(encoding="utf-8"))["models"]
    for name, digest in summary["source_sha256"].items():
        assert sha(Path(__file__).with_name(name)) == digest
    checks = []
    for row in summary["cases"]:
        path = folder / row["file"]
        assert sha(path) == row["sha256"]
        with np.load(path) as d:
            shape, lengths = tuple(map(int, d["shape"])), d["lengths_m"]
            m, q, edges, scale, labels, areas = geometry(shape, lengths)
            r, l = d["R_ohm"], d["L_H"]
            model = next(x for x in models if x["terms"] == int(d["terms"]))
            np.testing.assert_array_equal(r, model["R_ohm"])
            np.testing.assert_array_equal(l, model["L_H"])
            assert np.all(r > 0) and np.all(l > 0)
            for normal in range(3):
                expected = np.prod(np.delete(lengths, normal))
                for side in (0, 1):
                    for tangent in range(3):
                        if tangent != normal:
                            select = np.all(labels == (normal, side, tangent), axis=1)
                            np.testing.assert_allclose(sum(areas[select]), expected, rtol=1e-13)
            np.testing.assert_equal(row["wall_records"], len(edges))
            fref, qref = quadrature_q(d["mode_indices"], lengths, float(d["sigma_S_per_m"]))
            np.testing.assert_allclose(d["continuum_Q"], qref, rtol=1e-12)
            np.testing.assert_allclose(d["continuum_frequency_Hz"], fref, rtol=1e-12)
            amplitudes, electric, magnetic, charge, power = [], [], [], [], []
            branch_difference = []
            drive = d["drive_A"]
            frequencies = np.r_[d["frequency_Hz"], float(d["peak_frequency_Hz"])]
            for n, f in enumerate(frequencies):
                s = 2j * np.pi * f
                e = d["e_V"][n] if n < 9 else d["peak_e_V"]
                bfield = d["b_Wb"][n] if n < 9 else d["peak_b_Wb"]
                j = d["wall_current_A"][n] if n < 9 else d["peak_wall_current_A"]
                z = sum(r * s * l / (r + s * l))
                observed_j = e[edges] / (z * scale)
                np.testing.assert_allclose(j, observed_j, rtol=1e-12, atol=1e-35)
                total_j = np.zeros(len(m), complex)
                np.add.at(total_j, edges, j)
                # The archived PEC helper is real-valued; use linearity to
                # preserve its source hash while checking complex fields.
                flux = q * bfield
                ctqb = transpose_array(flux.real, shape) + 1j * transpose_array(flux.imag, shape)
                er = s * m * e - ctqb - drive + total_j
                br = s * bfield + curl_array(e, shape)
                electric.append(np.linalg.norm(er) / (np.linalg.norm(ctqb) + np.linalg.norm(total_j) +
                                                       np.linalg.norm(drive)))
                magnetic.append(np.linalg.norm(br) / np.linalg.norm(s * bfield))
                rho = node_divergence(m * e, shape)
                expected_charge = node_divergence(drive - total_j, shape)
                charge.append(np.linalg.norm(s * rho - expected_charge) /
                              max(np.linalg.norm(expected_charge), np.linalg.norm(s * rho), 1e-35))
                pw = .5 * np.real(np.vdot(e[edges], j))
                pin = .5 * np.real(np.vdot(e, drive))
                power.append(abs(pin - pw) / pw)
                inductor = j[:, None] * r / (r + s * l)
                joule = .5 * np.sum(scale[:, None] * r * abs(j[:, None] - inductor)**2)
                branch_difference.append(abs(joule / pw - 1))
                if n < 9:
                    np.testing.assert_allclose(d["wall_impedance_ohm"][n], z, rtol=1e-13)
                    np.testing.assert_allclose(d["wall_power_W"][n], pw, rtol=1e-12)
                    np.testing.assert_allclose(d["source_power_W"][n], pin, rtol=1e-12)
                    amplitudes.append(np.vdot(drive, e))
                else:
                    np.testing.assert_allclose(d["peak_inductive_current_A"], inductor, rtol=1e-12, atol=1e-35)
                    em = .25 * (m @ abs(e)**2 + q @ abs(bfield)**2)
                    stored = .25 * np.sum(scale[:, None] * l * abs(inductor)**2)
                    np.testing.assert_allclose(d["em_energy_J"], em, rtol=1e-12)
                    np.testing.assert_allclose(d["boundary_energy_J"], stored, rtol=1e-12)
                    qenergy = 2 * np.pi * f * (em + stored) / pw
                    np.testing.assert_allclose(d["Q_energy"], qenergy, rtol=1e-12)
            np.testing.assert_allclose(d["modal_amplitude"], amplitudes, rtol=1e-12)
            f = d["frequency_Hz"]
            center, unit = float(np.mean(f)), float(np.std(f))
            slope, intercept = np.polyfit((f - center) / unit, 1 / np.asarray(amplitudes), 1)
            pole = center - unit * intercept / slope
            np.testing.assert_allclose(d["fitted_pole_Hz"], pole, rtol=1e-11)
            assert pole.imag > 0, "e^(j omega t): decaying pole has positive imaginary frequency."
            qfit = pole.real / (2 * pole.imag)
            qerror = abs(qfit / qref - 1)
            ferror = abs(pole.real / fref - 1)
            assert row["Q_1percent_pass"] == (qerror < .01)
            assert row["frequency_1percent_pass"] == (ferror < .01)
            np.testing.assert_allclose(row["Q_relative_error"], qerror, rtol=1e-8, atol=1e-11)
            qagreement = abs(qenergy / qfit - 1)
            # Independent same-grid PEC perturbation diagnostic. Each
            # surface trace takes only its tangent magnetic face component.
            seed = drive / m
            wp = 2 * np.pi * float(d["PEC_discrete_frequency_Hz"])
            bpec = -curl_array(seed, shape) / (1j * wp)
            offsets = np.r_[0, np.cumsum([np.prod(x) for x in layouts(shape)[1]])]
            trace_current = np.zeros(len(edges), complex)
            for component in range(3):
                flux = np.zeros(len(q), complex)
                sl = slice(offsets[component], offsets[component + 1])
                flux[sl] = (q * bpec)[sl]
                contribution = transpose_array(flux.real, shape) + 1j * transpose_array(flux.imag, shape)
                selected = (3 - labels[:, 0] - labels[:, 2]) == component
                trace_current[selected] = contribution[edges[selected]]
            zpec = sum(r * 1j * wp * l / (r + 1j * wp * l))
            pec_loss = .5 * zpec.real * np.sum(scale * abs(trace_current)**2)
            pec_energy = .25 * (m @ abs(seed)**2 + q @ abs(bpec)**2)
            discrete_q = wp * pec_energy / pec_loss
            same_grid_difference = abs(qfit / discrete_q - 1)
            assert max(electric) < 1e-8 and max(magnetic) < 1e-8
            assert max(charge) < 1e-8 and max(power) < 1e-7
            assert max(branch_difference) < 1e-10 and qagreement < .001
            checks.append({"case": row["case"], "frequency_points_checked": 10,
                           "electric_equation_residual": max(electric),
                           "magnetic_equation_residual": max(magnetic),
                           "all_node_charge_residual": max(charge),
                           "source_wall_power_closure": max(power),
                           "Foster_joule_vs_wall_power": max(branch_difference),
                           "pole_vs_energy_Q_difference": qagreement,
                           "same_grid_PEC_perturbation_Q": float(discrete_q),
                           "pole_vs_same_grid_perturbation_Q_difference": float(same_grid_difference),
                           "Q_relative_error": qerror, "frequency_relative_error": ferror})
    assert not summary["cases"][-1]["Q_1percent_pass"], "Keep 16-term copper failure."
    for index in (3, 5):
        assert summary["cases"][index]["frequency_1percent_pass"]
        assert not summary["cases"][index]["Q_1percent_pass"], "Preserve finest-grid Q failure."
    assert all(checks[n]["Q_relative_error"] > checks[n + 1]["Q_relative_error"]
               for n in range(3))
    tests = subprocess.run([sys.executable, "-B", str(Path(__file__).with_name("test_finite_copper_cavity.py"))],
                           capture_output=True, text=True, check=True)
    result = {"independent_verification_pass": True, "full_frequency_points_checked": 70,
              "checks": checks, "test_output": tests.stdout + tests.stderr,
              "summary_sha256": sha(folder / "summary.json"),
              "verifier_sha256": sha(Path(__file__)),
              "array_helper_sha256": sha(Path(__file__).with_name("verify_finite_pec_cavity.py")),
              "tests_sha256": sha(Path(__file__).with_name("test_finite_copper_cavity.py")),
              "long_time_decay_complete": False, "arbitrary_corner_accuracy_complete": False,
              "real_cavity_complete": False, "full_joint_simulation_complete": False}
    (folder / "verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("folder", type=Path)
    main(p.parse_args().folder)
