"""Independent re-derivation of the PEC surface charge ledger.

Reads `PEC表面电荷账目结果/` written by `run_pec_surface_charge.py` and
recomputes, with its own array differences and its own mass matrix:

  * the all-node dual charge  q_all = +diff(m*e, prepend=0, append=0)
  * the six PEC wall face sums
  * the truncated closed-form wall sums from an independently fitted
    analytic amplitude
  * the eigen-equation residual of every stored edge vector

Neither `finite_pec_cavity.py` nor `run_pec_surface_charge.py` is imported.
"""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

C0 = 299792458.
MU = 4 * np.pi * 1e-7
EPS = 1 / (MU * C0**2)
WALL_KEYS = [(c, side) for c in range(3) for side in ("lo", "hi")]
MAIN_GRID = (36, 23, 14)
CROSS_GRID = (24, 15, 9)
EXPECTED = {MAIN_GRID: (31541, 38157, 13320),
            CROSS_GRID: (8346, 11190, 4000)}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def interior_divergence(e_flux, shape):
    ex, ey, ez = split(e_flux, layouts(shape)[0])
    return (np.diff(ex, axis=0)[:, 1:-1, 1:-1] +
            np.diff(ey, axis=1)[1:-1, :, 1:-1] +
            np.diff(ez, axis=2)[1:-1, 1:-1, :]).ravel()


def weights(shape, lengths):
    h = np.asarray(lengths, dtype=float) / np.asarray(shape, dtype=float)
    shape = tuple(map(int, shape))
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


def edge_charge(e, m, shape):
    """All-node charge from plain array differences, boundary nodes included."""
    es, _ = layouts(shape)
    flux = split(lift(e, shape) * lift(m, shape), es)
    total = 0.0
    for c in range(3):
        total = total + np.diff(flux[c], axis=c, prepend=0., append=0.)
    return total.ravel()


def wall_sums(q, shape):
    nodes = tuple(int(v) + 1 for v in shape)
    qq = q.reshape(nodes)
    out = {}
    for c, side in WALL_KEYS:
        window = [slice(1, -1)] * 3
        window[c] = 0 if side == "lo" else -1
        out[f"{c}{side}"] = float(qq[tuple(window)].sum())
    return out, qq


def analytic_basis(indices, c, shape, lengths):
    """Analytic ansatz of component c with unit amplitude, restricted to live edges."""
    shape = tuple(map(int, shape))
    lengths = np.asarray(lengths, dtype=float)
    h = lengths / np.asarray(shape, dtype=float)
    k = np.pi * np.asarray(indices, dtype=float) / lengths
    es, _ = layouts(shape)
    xyz = np.indices(es[c]).astype(float)
    xyz[c] += .5
    value = np.full(es[c], h[c])
    for a in range(3):
        phase = k[a] * h[a] * xyz[a]
        value *= np.cos(phase) if a == c else np.sin(phase)
    offsets = np.r_[0, np.cumsum([np.prod(s) for s in es])]
    full = np.zeros(int(offsets[-1]))
    full[offsets[c]:offsets[c + 1]] = value.ravel()
    return full[mask(shape)]


def fit_amplitudes(e, indices, m, shape, lengths):
    cols, live = [], []
    for c in range(3):
        basis = analytic_basis(indices, c, shape, lengths)
        if np.linalg.norm(basis) > 0:
            cols.append(basis)
            live.append(c)
    basis = np.array(cols)
    gram = basis @ (m[:, None] * basis.T)
    solution = np.linalg.lstsq(gram, basis @ (m * e), rcond=None)[0]
    coef = np.zeros(3)
    for i, c in enumerate(live):
        coef[c] = solution[i]
    residual = np.sqrt((e - solution @ basis) @ (m * (e - solution @ basis)) / (e @ (m * e)))
    return coef, float(residual)


def truncated_walls(indices, coef, shape, lengths):
    n = np.asarray(shape, dtype=int)
    h = np.asarray(lengths, dtype=float) / n
    k = np.pi * np.asarray(indices, dtype=float) / np.asarray(lengths, dtype=float)
    pred = {}
    for c in range(3):
        transverse = 1.0
        for a in range(3):
            if a != c:
                i = np.arange(1, n[a])
                transverse *= h[a] * np.sin(k[a] * h[a] * i).sum()
        pred[f"{c}lo"] = float(EPS * coef[c] * np.cos(k[c] * h[c] / 2) * transverse)
        pred[f"{c}hi"] = float(-EPS * coef[c] * np.cos(k[c] * (lengths[c] - h[c] / 2)) *
                               transverse)
    return pred


def recheck(existing, checks, recomputed_worst, summary_path, folder):
    """Compare a fresh recomputation with an already sealed verification record."""
    assert existing["independent_verification_pass"] is True
    assert existing["vectors_checked"] == len(checks)
    assert existing["summary_sha256"] == sha(summary_path)
    assert existing["archive_sha256"] == {name: sha(folder / name)
                                          for name in existing["archive_sha256"]}
    assert abs(existing["worst_wall_abs_to_charge_norm"] - recomputed_worst) <= 1e-15
    assert len(existing["checks"]) == len(checks)
    for stored, fresh in zip(existing["checks"], checks):
        for key, value in fresh.items():
            if isinstance(value, str):
                assert stored[key] == value, (key, stored[key], value)
            else:
                assert np.isclose(stored[key], value, rtol=1e-9, atol=1e-15), (
                    key, stored[key], value)
    print("recheck matches sealed record", existing["verifier_sha256"],
          "current verifier", sha(Path(__file__)))


def main(folder, recheck_only=False):
    summary_path = folder / "summary.json"
    out_path = folder / "verification.json"
    existing = None
    if out_path.exists():
        if not recheck_only:
            raise SystemExit(f"refusing to overwrite existing verification: {out_path}")
        existing = json.loads(out_path.read_text(encoding="utf-8"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    for name, digest in summary["source_sha256"].items():
        assert sha(Path(__file__).with_name(name)) == digest, name
    checks = []
    recomputed_worst = 0.0
    for case in summary["cases"]:
        shape = tuple(case["shape"])
        path = folder / case["file"]
        assert sha(path) == case["sha256"], case["case"]
        with np.load(path) as d:
            lengths = np.asarray(d["lengths_m"], dtype=float)
            modes = np.asarray(d["mode_indices"], dtype=int)
            values = np.asarray(d["eigenvalues_rad2_s2"], dtype=float)
            vectors = np.asarray(d["edge_vectors_V"], dtype=float)
            q_stored = np.asarray(d["q_all_C"], dtype=float)
            wall_stored = np.asarray(d["wall_measured_C"], dtype=float)
            pred_stored = np.asarray(d["wall_predicted_C"], dtype=float)
            keys = [str(k) for k in d["wall_keys"]]
        m, q = weights(shape, lengths)
        es, _ = layouts(shape)
        active = int(mask(shape).sum())
        full_edges = int(sum(np.prod(s) for s in es))
        nodes = int(np.prod([n + 1 for n in shape]))
        assert shape in EXPECTED, shape
        assert (active, full_edges, nodes) == EXPECTED[shape], (active, full_edges, nodes)
        assert case["active_edges"] == active and case["full_edges"] == full_edges
        assert case["dual_nodes"] == nodes
        assert keys == [f"{c}{side}" for c, side in WALL_KEYS]
        case_records = [r for r in summary["vectors"] if r["role"] == case["role"]]
        assert len(case_records) == vectors.shape[0]
        for j, record in enumerate(case_records):
            e = vectors[j]
            name = record["case"]
            assert record["mode_indices"] == [int(v) for v in modes[j]]
            q_norm = float(np.linalg.norm(q_stored[j]))
            q_ind = edge_charge(e, m, shape)
            wall_ind, qq = wall_sums(q_ind, shape)
            mass_norm = float(e @ (m * e))
            b = curl_array(lift(e, shape), shape)
            ke = transpose_array(q * b, shape)[mask(shape)]
            eigen_res = float(np.linalg.norm(ke - values[j] * m * e) / np.linalg.norm(values[j] * m * e))
            coef, fit_res = fit_amplitudes(e, modes[j], m, shape, lengths)
            pred_ind = truncated_walls(modes[j], coef, shape, lengths)
            array_vs_stored = float(np.linalg.norm(q_ind - q_stored[j])) / q_norm
            wall_vs_stored = max(abs(wall_ind[k] - wall_stored[j][i]) for i, k in enumerate(keys)) / q_norm
            pred_vs_stored = max(abs(pred_ind[k] - pred_stored[j][i]) for i, k in enumerate(keys)) / q_norm
            worst_abs = max(abs(pred_ind[k] - wall_ind[k]) for k in keys) / q_norm
            interior_ind = float(qq[1:-1, 1:-1, 1:-1].sum())
            interior_api = interior_divergence(lift(m * e, shape), shape)
            interior_match = float(np.linalg.norm(interior_api - qq[1:-1, 1:-1, 1:-1].ravel())) / q_norm
            index = np.indices(qq.shape)
            boundary = sum(((index[a] == 0) | (index[a] == qq.shape[a] - 1)).astype(int)
                           for a in range(3))
            partition = abs(float(qq[boundary == 1].sum()) + float(qq[boundary == 2].sum()) +
                            float(qq[boundary == 3].sum()) + interior_ind -
                            float(qq.sum())) / q_norm
            assert abs(mass_norm - 1.0) < 1e-12, mass_norm
            assert eigen_res < 1e-8, (name, eigen_res)
            assert array_vs_stored < 1e-12, (name, array_vs_stored)
            assert wall_vs_stored < 1e-13, (name, wall_vs_stored)
            assert pred_vs_stored < 1e-12, (name, pred_vs_stored)
            assert interior_match < 1e-12, (name, interior_match)
            assert abs(abs(qq.sum()) / q_norm - record["total_to_norm"]) < 1e-12
            assert abs(abs(interior_ind) / q_norm - record["interior_to_norm"]) < 1e-12
            assert abs(fit_res - record["analytic_fit_residual"]) < 1e-12
            assert worst_abs < 1e-10, (name, worst_abs)
            assert partition < 1e-12, (name, partition)
            recomputed_worst = max(recomputed_worst, worst_abs)
            checks.append({"case": name, "charge_norm_C": q_norm,
                           "array_charge_vs_stored": array_vs_stored,
                           "wall_sums_vs_stored": wall_vs_stored,
                           "closed_form_vs_stored": pred_vs_stored,
                           "worst_wall_abs_to_charge_norm": worst_abs,
                           "wall_relative_error_max": float(max(
                               abs(pred_ind[k] - wall_ind[k]) / max(abs(pred_ind[k]), 1e-6 * q_norm)
                               for k in keys)),
                           "total_to_norm": abs(float(qq.sum())) / q_norm,
                           "interior_to_norm": abs(interior_ind) / q_norm,
                           "interior_charge_identity": interior_match,
                           "partition_closure": partition,
                           "eigen_equation_residual": eigen_res,
                           "analytic_fit_residual": fit_res,
                           "unit_mass_norm": abs(mass_norm - 1.0)})
    print("worst wall abs/|q| stored", summary["worst_wall_abs_to_charge_norm"],
          "recomputed", recomputed_worst)
    assert abs(recomputed_worst / summary["worst_wall_abs_to_charge_norm"] - 1) < 1e-2
    assert recomputed_worst < 1e-10 and summary["all_wall_predictions_match"]
    assert summary["PEC_surface_charge_complete"] is True
    assert {tuple(c["shape"]) for c in summary["cases"]} == {MAIN_GRID, CROSS_GRID}
    result = {"independent_verification_pass": True,
              "vectors_checked": len(checks),
              "worst_wall_abs_to_charge_norm": float(recomputed_worst),
              "checks": checks,
              "summary_sha256": sha(summary_path),
              "archive_sha256": {c["file"]: sha(folder / c["file"]) for c in summary["cases"]},
              "verifier_sha256": sha(Path(__file__)),
              "PEC_surface_charge_independently_verified": True,
              "copper_boundary_complete": False, "copper_cavity_Q_complete": False,
              "full_joint_simulation_complete": False,
              "full_system_gates_closed": 0, "total_system_gates": 7,
              "goal_complete": False}
    if existing is not None:
        recheck(existing, checks, recomputed_worst, summary_path, folder)
        print(json.dumps({k: v for k, v in result.items() if k != "checks"}, indent=2))
        return
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "checks"}, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("folder", type=Path)
    p.add_argument("--recheck", action="store_true",
                   help="re-derive and compare with the sealed verification.json without overwriting")
    args = p.parse_args()
    main(args.folder, args.recheck)
