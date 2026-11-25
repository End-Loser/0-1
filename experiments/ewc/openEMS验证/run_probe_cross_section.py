"""Mesh-resolved probe cross-section: k x k wire bundle with a wall-plane port.

Not openEMS. Cavity, copper surface model and solve chain are unchanged from
run_probe_geometry.py. The single thin-wire post of section 23 is enlarged to a
k x k footprint of axial edges (E_axial = 0), fed by k x k axial delta-gap
edges on the bottom wall, so the port cross-section is resolved by k cells in
each transverse direction instead of one edge.

The port is defined at the wall plane z = 0: total drive current is 1 A split
equally over the k^2 gap edges, hence Z_in = vdot(e, drive) is the mean gap
voltage per 1 A and is directly comparable with section 23.

Added case: the fine grid at a post height of exactly MATCHED_CELLS cells, so
the coarse/fine difference is a pure mesh difference (section 23 had 4 cells
vs 7 cells, which confounded resolution with height).
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
OUT = ROOT / "探针横截面校核结果"
SOURCES = ROOT / "铜腔111双极化结果"
LENGTHS = (.16, .10, .06)
SHAPES = ((24, 15, 9), (36, 23, 14))
TARGET_HEIGHT_M = .03
MATCHED_CELLS = 4
Z0 = 50.
SIDES = (1, 2, 3)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def probe_pattern(grid, seed, side, height):
    """Return (gap edges, post edges, height) for a side x side wire bundle."""
    es, eoff = grid.pec.es, grid.pec.eoff
    low, high = int(eoff[2]), int(eoff[3])
    edge = low + int(np.argmax(np.abs(seed[low:high])))
    i, j, _ = np.unravel_index(edge - low, es[2])
    nx, ny, nz = grid.shape
    i0 = min(max(i - (side - 1) // 2, 1), nx - side)
    j0 = min(max(j - (side - 1) // 2, 1), ny - side)
    cells = max(1, int(round(height / grid.spacing[2])))
    cells = min(cells, nz - 1)
    gap = np.array([eoff[2] + np.ravel_multi_index((i0 + di, j0 + dj, 0), es[2])
                    for di in range(side) for dj in range(side)], dtype=int)
    post = np.array([eoff[2] + np.ravel_multi_index((i0 + di, j0 + dj, k), es[2])
                     for di in range(side) for dj in range(side)
                     for k in range(1, cells + 1)], dtype=int)
    return gap, post, cells * grid.spacing[2], (i0, j0), cells


def solve_case(grid, keep, position, wall_position, frequency, drive,
               per_record, total):
    s = 2j * np.pi * frequency
    operator = (grid.stiffness_keep + diags(s * s * grid.m_keep + s * total[keep])).tocsc()
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
                if source_power > 0 else None, solve_seconds=seconds), e


def main():
    assert not OUT.exists(), "New evidence only; do not overwrite a prior result"
    model_path = ROOT / "厚铜边界基准结果/passive_surface_models.json"
    model = next(row for row in json.loads(model_path.read_text(encoding="utf-8"))["models"]
                 if row["terms"] == 64)
    resistance, inductance = np.asarray(model["R_ohm"]), np.asarray(model["L_H"])
    OUT.mkdir()
    configurations = [dict(shape=list(shape), lengths_m=list(LENGTHS),
                           sides=list(SIDES), post_target_height_m=TARGET_HEIGHT_M,
                           matched_cells=MATCHED_CELLS, terms=64,
                           reference_impedance_ohm=Z0, scan_points=5)
                      for shape in SHAPES]
    config_path = OUT / "configurations.json"
    config_path.write_text(json.dumps(configurations, indent=2) + "\n", encoding="utf-8")
    summary = dict(
        scope="k x k axial edge bundle probe, k^2 wall delta-gap feed, "
              "port plane at z=0, full six-wall frequency solve",
        evidence_level="A (full six-wall frequency solve)",
        limitation="post is a bundle of k^2 thin wires with E_axial forced to zero; "
                   "the lateral post surface is not enforced; port is still a wall "
                   "delta gap, not a coaxial/SMA opening with an outer conductor; "
                   "no de-embedding to a connector plane",
        configuration_sha256=sha(config_path), model_sha256=sha(model_path),
        source_sha256={name: sha(ROOT / name) for name in (
            "run_probe_cross_section.py", "finite_copper_cavity.py",
            "finite_pec_cavity.py")},
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
                center = float(np.mean(poles.real))
                width = center / float(np.min(q_poles))
                frequencies = center - width / 2 + np.linspace(-2, 2, 5) * width
                plans = [dict(side=k, height=TARGET_HEIGHT_M, tag="target")
                         for k in SIDES]
                if shape == SHAPES[1]:
                    plans += [dict(side=k, height=MATCHED_CELLS * grid.spacing[2],
                                   tag="matched_cells") for k in SIDES[:2]]
                for plan in plans:
                    gap, post, height, location, cells = probe_pattern(
                        grid, seed, plan["side"], plan["height"])
                    keep = np.setdiff1d(np.arange(grid.ne), post)
                    position = -np.ones(grid.ne, dtype=int)
                    position[keep] = np.arange(len(keep))
                    assert np.all(position[gap] >= 0)
                    wall_position = position[grid.wall_edges[grid.wall_edges < grid.ne]]
                    assert np.all(wall_position >= 0)
                    drive = np.zeros(len(keep), complex)
                    drive[position[gap]] = 1. / len(gap)
                    grid.m_keep = grid.m[keep]
                    grid.stiffness_keep = (grid.curl[:, keep].T @ diags(grid.q)
                                           @ grid.curl[:, keep]).tocsc()
                    rows, voltages = [], []
                    for f in frequencies:
                        _, per_record, total = grid.admittance(f, resistance, inductance)
                        row, e = solve_case(grid, keep, position, wall_position, f,
                                            drive, per_record, total)
                        rows.append(row)
                        voltages.append(np.abs(e[position[gap]]))
                        print(shape, "k", plan["side"], "cells", cells, plan["tag"], f,
                              row["z_real_ohm"], row["z_imag_ohm"], row["abs_s11"],
                              flush=True)
                    del grid.m_keep, grid.stiffness_keep
                    level = np.asarray(voltages)
                    uniformity = float(level.std() / level.mean()) if level.mean() > 0 else None
                    best = min(rows, key=lambda r: r["abs_s11"])
                    summary["cases"].append(dict(
                        shape=list(shape), side=plan["side"], tag=plan["tag"],
                        post_height_m=height, post_cells=cells,
                        post_location_ij=[int(v) for v in location],
                        gap_edges=int(len(gap)), removed_edges=int(len(post)),
                        kept_edges=int(len(keep)),
                        port_footprint_mm=[float(plan["side"] * grid.spacing[0] * 1e3),
                                           float(plan["side"] * grid.spacing[1] * 1e3)],
                        gap_voltage_uniformity=uniformity,
                        center_frequency_Hz=center, width_Hz=width, positions=rows,
                        best_abs_s11=best["abs_s11"],
                        best_z_real_ohm=best["z_real_ohm"],
                        best_z_imag_ohm=best["z_imag_ohm"],
                        best_series_inductance_nH=float(
                            1e9 * best["z_imag_ohm"] / (2 * np.pi * best["frequency_Hz"])),
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
