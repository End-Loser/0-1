"""PEC surface charge ledger for selected eigenmodes of the finite six-face PEC cavity.

The archived field of `finite_pec_cavity.py` only exposes interior dual-node
charge (`FinitePEC.charge`).  This runner reconstructs the *all-node* charge

    q_all = -full_gradient.T @ lift(m * e)

and splits it into six PEC wall faces, twelve wall-edge interiors, eight
corners and the volume interior.  Every wall entry is compared with an
independent closed-form truncated midpoint sum of the analytic mode ansatz.
"""

import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np
from scipy.sparse import diags
from scipy.sparse.linalg import eigsh

from finite_pec_cavity import EPS, FinitePEC

MODES = [(1, 1, 0), (1, 0, 1), (0, 1, 1), (2, 1, 0), (1, 2, 0), (1, 1, 1)]
LENGTHS = (.16, .10, .06)
GRIDS = [("main_quantitative", (36, 23, 14)), ("cross_check", (24, 15, 9))]
WALL_KEYS = [(c, side) for c in range(3) for side in ("lo", "hi")]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def edge_shapes(shape):
    x, y, z = map(int, shape)
    return [(x, y + 1, z + 1), (x + 1, y, z + 1), (x + 1, y + 1, z)]


def independent_mass(shape, lengths):
    """Recompute the active-edge mass without touching FinitePEC internals."""
    shape = tuple(map(int, shape))
    h = np.asarray(lengths, dtype=float) / np.asarray(shape, dtype=float)
    es = edge_shapes(shape)
    dual = [h[c] * np.r_[.5, np.ones(shape[c] - 1), .5] for c in range(3)]
    out = []
    for c in range(3):
        index = np.indices(es[c])
        value = np.full(es[c], EPS / h[c])
        for a in range(3):
            if a != c:
                value *= dual[a][index[a]]
        out.append(value)
    return out


def analytic_component(g, indices, c):
    """Full-edge vector of the analytic ansatz with unit amplitude on component c."""
    k = np.pi * np.asarray(indices, dtype=float) / np.asarray(g.lengths)
    es = g.es[c]
    xyz = np.indices(es).astype(float)
    xyz[c] += .5
    value = np.full(es, g.spacing[c])
    for a in range(3):
        phase = k[a] * g.spacing[a] * xyz[a]
        value *= np.cos(phase) if a == c else np.sin(phase)
    full = np.zeros(g.ne_full)
    full[g.eoff[c]:g.eoff[c + 1]] = value.ravel()
    return full[g.active]


def fit_amplitudes(g, e, indices):
    """Weighted least squares on the live analytic components.

    A component c whose ansatz contains sin(k_a h_a i) with k_a == 0 is
    identically zero, so only the surviving components enter the Gram matrix.
    """
    cols, live = [], []
    for c in range(3):
        basis = analytic_component(g, indices, c)
        if np.linalg.norm(basis) > 0:
            cols.append(basis)
            live.append(c)
    basis = np.array(cols)
    gram = basis @ (g.m[:, None] * basis.T)
    rhs = basis @ (g.m * e)
    solution = np.linalg.lstsq(gram, rhs, rcond=None)[0]
    coef = np.zeros(3)
    for i, c in enumerate(live):
        coef[c] = solution[i]
    residual = np.sqrt((e - solution @ basis) @ (g.m * (e - solution @ basis)) /
                       (e @ (g.m * e)))
    return coef, float(residual), live


def full_charge(g, e):
    """All-node dual charge, including the six PEC wall faces."""
    return -g.full_gradient.T @ g.lift(g.m * e)


def independent_charge(g, e, mass_arrays):
    offsets = np.r_[0, np.cumsum([a.size for a in mass_arrays])]
    full = g.lift(e)
    q = 0.0
    for c, mass in enumerate(mass_arrays):
        flux = (mass.ravel() * full[offsets[c]:offsets[c + 1]]).reshape(mass.shape)
        q = q + np.diff(flux, axis=c, prepend=0, append=0)
    return q.ravel()


def charge_ledger(q, shape):
    nodes = tuple(np.asarray(shape) + 1)
    qq = q.reshape(nodes)
    index = np.indices(nodes)
    boundary = sum(((index[a] == 0) | (index[a] == nodes[a] - 1)).astype(int)
                   for a in range(3))
    walls = {}
    for c, side in WALL_KEYS:
        window = [slice(1, -1)] * 3
        window[c] = 0 if side == "lo" else -1
        walls[(c, side)] = float(qq[tuple(window)].sum())
    ledger = {"walls": walls,
              "wall_total": float(qq[boundary == 1].sum()),
              "edge_total": float(qq[boundary == 2].sum()),
              "corner_total": float(qq[boundary == 3].sum()),
              "interior": float(qq[boundary == 0].sum()),
              "total": float(qq.sum())}
    ledger["wall_sum"] = float(sum(walls[k] for k in WALL_KEYS))
    return ledger


def wall_prediction(indices, coef, shape, lengths):
    """Independent closed form: truncated midpoint sums of the analytic field."""
    k = np.pi * np.asarray(indices, dtype=float) / np.asarray(lengths, dtype=float)
    n = np.asarray(shape, dtype=int)
    h = np.asarray(lengths, dtype=float) / n
    pred = {}
    for c in range(3):
        transverse = 1.0
        for a in range(3):
            if a != c:
                i = np.arange(1, n[a])
                transverse *= h[a] * np.sin(k[a] * h[a] * i).sum()
        pred[(c, "lo")] = float(EPS * coef[c] * np.cos(k[c] * h[c] / 2) * transverse)
        pred[(c, "hi")] = float(-EPS * coef[c] * np.cos(k[c] * (lengths[c] - h[c] / 2)) *
                                transverse)
    return pred


def solve_vectors(g, indices):
    seed = g.sample_mode(indices)
    lam = float(seed @ (g.stiffness @ seed))
    pair = bool(np.all(np.asarray(indices) > 0))
    values, vectors = eigsh(g.stiffness, k=2 if pair else 1, M=diags(g.m),
                            sigma=lam * (1 + 1e-5), v0=seed, tol=1e-10)
    order = np.argsort(values)
    values, vectors = values[order], vectors[:, order]
    vectors = vectors / np.sqrt(np.einsum("ij,ij->j", vectors, g.m[:, None] * vectors))
    return values, vectors


def main(output):
    if output.exists():
        raise SystemExit(f"refusing to overwrite existing archive: {output}")
    output.mkdir(parents=True)
    started = time.perf_counter()
    cases, records = [], []
    payload = {}
    for role, shape in GRIDS:
        grid = FinitePEC(shape, LENGTHS)
        mass_arrays = independent_mass(shape, LENGTHS)
        vectors, modes, eigenvalues = [], [], []
        case_rows = []
        for indices in MODES:
            t_mode = time.perf_counter()
            values, fields = solve_vectors(grid, indices)
            for j in range(fields.shape[1]):
                e = fields[:, j]
                q_attr = full_charge(grid, e)
                q_ind = independent_charge(grid, e, mass_arrays)
                q_norm = float(np.linalg.norm(q_attr))
                ledger = charge_ledger(q_attr, shape)
                coef, fit_res, live = fit_amplitudes(grid, e, indices)
                pred = wall_prediction(indices, coef, shape, LENGTHS)
                wall_error = {key: abs(pred[key] - ledger["walls"][key]) / max(abs(pred[key]), 1e-6 * q_norm)
                              for key in WALL_KEYS}
                worst_wall = max(wall_error.values())
                worst_abs_to_q = max(abs(pred[key] - ledger["walls"][key]) for key in WALL_KEYS) / q_norm
                q_interior = q_attr.reshape(tuple(np.asarray(shape) + 1))[1:-1, 1:-1, 1:-1].ravel()
                row = {
                    "case": f"{role}_{'x'.join(map(str, shape))}_mode{''.join(map(str, indices))}_v{j}",
                    "role": role, "shape": list(shape), "mode_indices": list(indices),
                    "vector_index": j,
                    "frequency_Hz": float(np.sqrt(values[j]) / (2 * np.pi)),
                    "eigenvalue_rad2_s2": float(values[j]),
                    "degenerate_peers": int(fields.shape[1]),
                    "active_edges": grid.ne, "full_edges": grid.ne_full,
                    "dual_nodes": int(np.prod(np.asarray(shape) + 1)),
                    "live_components": live,
                    "analytic_fit_residual": fit_res,
                    "charge_norm_C": q_norm,
                    "total_to_norm": abs(ledger["total"]) / q_norm,
                    "interior_to_norm": abs(ledger["interior"]) / q_norm,
                    "edge_to_norm": abs(ledger["edge_total"]) / q_norm,
                    "corner_to_norm": abs(ledger["corner_total"]) / q_norm,
                    "wall_to_interior_ratio": abs(ledger["wall_sum"]) / max(abs(ledger["interior"]), 1e-300),
                    "ledger_closure": abs(ledger["wall_sum"] + ledger["edge_total"] +
                                          ledger["corner_total"] + ledger["interior"] -
                                          ledger["total"]) / q_norm,
                    "wall_partition_residual": abs(ledger["wall_sum"] - ledger["wall_total"]) / q_norm,
                    "attribute_vs_array_divergence": float(np.linalg.norm(q_attr - q_ind)) / q_norm,
                    "charge_api_vs_interior": float(np.linalg.norm(grid.charge(e) - q_interior)) / q_norm,
                    "wall_measured_C": {f"{c}{side}": ledger["walls"][(c, side)]
                                        for c, side in WALL_KEYS},
                    "wall_predicted_C": {f"{c}{side}": pred[(c, side)]
                                         for c, side in WALL_KEYS},
                    "wall_relative_error": {f"{c}{side}": wall_error[(c, side)]
                                            for c, side in WALL_KEYS},
                    "worst_wall_relative_error": float(worst_wall),
                    "worst_wall_abs_to_charge_norm": float(worst_abs_to_q),
                    "solve_seconds": time.perf_counter() - t_mode,
                }
                case_rows.append(row)
                records.append(row)
                vectors.append(e)
                modes.append(indices)
                eigenvalues.append(values[j])
                print(row["case"], f"f={row['frequency_Hz'] / 1e6:.4f} MHz",
                      f"live={live}", f"fit={fit_res:.2e}",
                      f"worst_wall_abs/q={worst_abs_to_q:.2e}", flush=True)
        values_arr = np.array(eigenvalues)
        modes_arr = np.array(modes)
        vectors_arr = np.array(vectors)
        q_all = np.array([full_charge(grid, e) for e in vectors])
        walls_measured = np.array([[charge_ledger(q, shape)["walls"][k] for k in WALL_KEYS]
                                   for q in q_all])
        walls_predicted = np.array([[wall_prediction(m, fit_amplitudes(grid, e, m)[0], shape,
                                                     LENGTHS)[k] for k in WALL_KEYS]
                                    for m, e in zip(modes, vectors)])
        np.savez_compressed(output / f"charge_{'x'.join(map(str, shape))}.npz",
                            shape=shape, lengths_m=LENGTHS, mode_indices=modes_arr,
                            eigenvalues_rad2_s2=values_arr,
                            edge_vectors_V=vectors_arr,
                            q_all_C=q_all,
                            wall_measured_C=walls_measured,
                            wall_predicted_C=walls_predicted,
                            wall_keys=np.array([f"{c}{side}" for c, side in WALL_KEYS]))
        cases.append({"case": f"charge_{'x'.join(map(str, shape))}", "role": role,
                      "shape": list(shape), "active_edges": grid.ne,
                      "full_edges": grid.ne_full,
                      "dual_nodes": int(np.prod(np.asarray(shape) + 1)),
                      "mode_vectors": len(case_rows),
                      "file": f"charge_{'x'.join(map(str, shape))}.npz",
                      "sha256": sha(output / f"charge_{'x'.join(map(str, shape))}.npz"),
                      "solve_seconds": float(sum(r["solve_seconds"] for r in case_rows))})
    worst = max(r["worst_wall_abs_to_charge_norm"] for r in records)
    summary = {
        "method": "full_node dual charge from the finite six-face PEC FIT operator",
        "charge_definition": "q_all = -full_gradient.T @ lift(m*e); equals the all-node "
                             "discrete divergence of m*e",
        "wall_definition": "PEC wall face interior: one node coordinate on a mesh boundary, "
                           "the other two strictly interior",
        "reference": "closed-form truncated midpoint sum of the analytic sin/cos mode ansatz",
        "lengths_m": list(LENGTHS), "modes": MODES,
        "cases": cases, "vectors": records,
        "main_quantitative_grid": list(GRIDS[0][1]),
        "cross_check_grid": list(GRIDS[1][1]),
        "worst_wall_abs_to_charge_norm": float(worst),
        "all_wall_predictions_match": bool(worst < 1e-10),
        "total_solve_seconds": time.perf_counter() - started,
        "source_sha256": {n: sha(Path(__file__).with_name(n))
                          for n in ["finite_pec_cavity.py", Path(__file__).name]},
        "PEC_surface_charge_complete": bool(worst < 1e-10),
        "PEC_surface_charge_independently_verified": False,
        "copper_boundary_complete": False, "copper_cavity_Q_complete": False,
        "full_joint_simulation_complete": False,
        "full_system_gates_closed": 0, "total_system_gates": 7, "goal_complete": False,
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    config = {name: value for name, value in summary.items()
              if name in ("method", "charge_definition", "wall_definition", "reference",
                          "lengths_m", "modes", "main_quantitative_grid", "cross_check_grid")}
    config["grids"] = [{"role": role, "shape": list(shape)} for role, shape in GRIDS]
    config["wall_keys"] = [f"{c}{side}" for c, side in WALL_KEYS]
    (output / "configurations.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    print("summary: worst wall abs/|q|", worst, "complete", summary["PEC_surface_charge_complete"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args().output)
