"""Mesh-resolved axial PEC post probe, six-wall frequency solve.

Not openEMS. The cavity and the copper surface model are unchanged from
finite_copper_cavity.py; the only new object is a thin PEC post whose axial
edges are removed from the unknown set, fed across one bottom delta-gap edge.
The post cross-section is still one edge (thin-wire): only the axial length is
mesh resolved. This is the first step of the G3 geometry gap, not a connector.
"""
import hashlib
import json
from pathlib import Path
import time

import numpy as np
from scipy.sparse import diags
from scipy.sparse.linalg import splu
from threadpoolctl import threadpool_limits

from finite_copper_cavity import CopperCavity

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "探针几何校核结果"
SOURCES = ROOT / "铜腔111双极化结果"
LENGTHS = (.16, .10, .06)
SHAPES = ((24, 15, 9), (36, 23, 14))
TARGET_HEIGHT_M = .03
Z0 = 50.


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def post_pattern(grid, seed, height):
    """Return (gap edge, post edges, physical height) for one axial post."""
    es, eoff = grid.pec.es, grid.pec.eoff
    low, high = int(eoff[2]), int(eoff[3])
    edge = low + int(np.argmax(np.abs(seed[low:high])))
    i, j, _ = np.unravel_index(edge - low, es[2])
    nz = grid.shape[2]
    cells = max(1, int(round(height / grid.spacing[2])))
    cells = min(cells, nz - 1)
    gap = int(eoff[2] + np.ravel_multi_index((i, j, 0), es[2]))
    post = np.array([eoff[2] + np.ravel_multi_index((i, j, k), es[2])
                     for k in range(1, cells + 1)], dtype=int)
    return gap, post, cells * grid.spacing[2], (i, j)


def solve_post(grid, keep, position, wall_position, frequency, resistance, inductance,
               drive, per_record, total):
    s = 2j * np.pi * frequency
    total_k = total[keep]
    operator = (grid.stiffness_keep + diags(s * s * grid.m_keep + s * total_k)).tocsc()
    started = time.perf_counter()
    factor = splu(operator, permc_spec="MMD_AT_PLUS_A")
    e = factor.solve(s * drive)
    seconds = time.perf_counter() - started
    z_in = np.vdot(e, drive)
    wall_power = .5 * float(np.sum(per_record.real * np.abs(e[wall_position]) ** 2))
    source_power = .5 * float(np.real(z_in))
    reflection = abs((z_in - Z0) / (z_in + Z0)) if abs(z_in + Z0) > 0 else np.inf
    return dict(frequency_Hz=float(frequency), z_real_ohm=float(z_in.real),
                z_imag_ohm=float(z_in.imag), abs_s11=float(reflection),
                radiation_efficiency=float(wall_power / source_power) if source_power > 0 else None,
                power_balance_error=float(abs(source_power - wall_power) / source_power)
                if source_power > 0 else None, solve_seconds=seconds)


def main():
    assert not OUT.exists(), "New evidence only; do not overwrite a prior result"
    model_path = ROOT / "厚铜边界基准结果/passive_surface_models.json"
    model = next(row for row in json.loads(model_path.read_text(encoding="utf-8"))["models"]
                 if row["terms"] == 64)
    resistance, inductance = np.asarray(model["R_ohm"]), np.asarray(model["L_H"])
    OUT.mkdir()
    configurations = [dict(shape=list(shape), lengths_m=list(LENGTHS),
                           post_target_height_m=TARGET_HEIGHT_M, terms=64,
                           reference_impedance_ohm=Z0, scan_points=5)
                      for shape in SHAPES]
    config_path = OUT / "configurations.json"
    config_path.write_text(json.dumps(configurations, indent=2) + "\n", encoding="utf-8")
    summary = dict(
        scope="axial thin-wire PEC post with one bottom delta-gap, full six-wall frequency solve",
        evidence_level="A (full six-wall frequency solve)",
        limitation="post cross-section not mesh resolved; no coaxial or SMA geometry; "
                   "gap is one edge; absolute S11 is a thin-wire model value only",
        configuration_sha256=sha(config_path), model_sha256=sha(model_path),
        source_sha256={name: sha(ROOT / name) for name in (
            "run_probe_geometry.py", "finite_copper_cavity.py", "finite_pec_cavity.py")},
        cases=[], status="running")

    def checkpoint():
        (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    checkpoint()
    try:
        with threadpool_limits(limits=1):
            for shape in SHAPES:
                grid = CopperCavity(shape, LENGTHS)
                stem = "111_" + "x".join(map(str, shape))
                peaks = np.load(SOURCES / (stem + "_peaks.npz"), allow_pickle=True)
                scan = np.load(SOURCES / (stem + "_scan.npz"), allow_pickle=True)
                seed = scan["seeds"][:, 0]
                poles, q_poles = peaks["fitted_poles_Hz"], peaks["fitted_Q"]
                gap, post, height, location = post_pattern(grid, seed, TARGET_HEIGHT_M)
                keep = np.setdiff1d(np.arange(grid.ne), post)
                position = -np.ones(grid.ne, dtype=int)
                position[keep] = np.arange(len(keep))
                assert position[gap] >= 0
                wall_keep = position[grid.wall_edges[grid.wall_edges < grid.ne]]
                drive = np.zeros(len(keep), complex)
                drive[position[gap]] = 1.
                grid.m_keep = grid.m[keep]
                grid.stiffness_keep = (grid.curl[:, keep].T @ diags(grid.q)
                                       @ grid.curl[:, keep]).tocsc()
                center = float(np.mean(poles.real))
                width = center / float(np.min(q_poles))
                frequencies = center - width / 2 + np.linspace(-2, 2, 5) * width
                rows = []
                for f in frequencies:
                    _, per_record, total = grid.admittance(f, resistance, inductance)
                    row = solve_post(grid, keep, position, wall_keep, f, resistance,
                                     inductance, drive, per_record, total)
                    rows.append(row)
                    print(shape, "post", height, f, row["z_real_ohm"], row["z_imag_ohm"],
                          row["abs_s11"], flush=True)
                del grid.m_keep, grid.stiffness_keep
                best = min(rows, key=lambda r: r["abs_s11"])
                summary["cases"].append(dict(
                    shape=list(shape), post_height_m=height, post_cells=int(len(post)),
                    post_location_ij=[int(v) for v in location], gap_edge_full_index=int(gap),
                    kept_edges=len(keep), removed_edges=len(post),
                    center_frequency_Hz=center, width_Hz=width,
                    positions=rows, best_abs_s11=best["abs_s11"],
                    best_z_real_ohm=best["z_real_ohm"], best_z_imag_ohm=best["z_imag_ohm"],
                    passive=bool(all(r["abs_s11"] <= 1. for r in rows)),
                    status="complete"))
                checkpoint()
        summary["status"] = "complete"
        checkpoint()
    except Exception as error:
        summary["status"] = "failed_preserve_partial"
        summary["failure"] = repr(error)
        checkpoint()
        raise


if __name__ == "__main__":
    main()
