"""Post-height sweep of the single-edge (k = 1) wall-fed post, both grids.

Not openEMS. Section 23 compared 4 cells on 24x15x9 (26.67 mm) with 7 cells on
36x23x14 (30 mm) and read the 4.85% agreement as convergence. Section 24's
matched_cells case only matched the cell count, and dz differs between the
grids (6.667 vs 4.286 mm), so that control still mixed height with mesh.

This run sweeps the post height on each grid independently at fixed mesh, so
Re(h) and Im(h) are measured directly. It answers two questions:

  * is Re at 1 ohm a height effect or a mesh artefact?
  * do the two grids agree at a matched physical height (interpolated)?
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
from run_probe_cross_section import LENGTHS, Z0, probe_pattern

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "探针柱高扫描结果"
SOURCES = ROOT / "铜腔111双极化结果"
SHAPES = ((24, 15, 9), (36, 23, 14))
HEIGHT_CELLS = {SHAPES[0]: (2, 3, 4, 6, 8), SHAPES[1]: (2, 3, 4, 5, 6, 7)}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    assert not OUT.exists(), "New evidence only; do not overwrite a prior result"
    model_path = ROOT / "厚铜边界基准结果/passive_surface_models.json"
    model = next(row for row in json.loads(model_path.read_text(encoding="utf-8"))["models"]
                 if row["terms"] == 64)
    resistance, inductance = np.asarray(model["R_ohm"]), np.asarray(model["L_H"])
    OUT.mkdir()
    configurations = [dict(shape=list(shape), lengths_m=list(LENGTHS), side=1,
                           height_cells=list(HEIGHT_CELLS[shape]), terms=64,
                           reference_impedance_ohm=Z0, scan_points=5)
                      for shape in SHAPES]
    config_path = OUT / "configurations.json"
    config_path.write_text(json.dumps(configurations, indent=2) + "\n", encoding="utf-8")
    summary = dict(
        scope="k = 1 post height sweep at fixed mesh on both grids, 5 frequencies "
              "inside +-2 mode linewidths, full six-wall frequency solve",
        evidence_level="A (full six-wall frequency solve)",
        limitation="single-edge post (E_axial forced to zero); no coaxial/SMA geometry; "
                   "port defined as the mean wall-gap voltage at 1 A drive",
        configuration_sha256=sha(config_path), model_sha256=sha(model_path),
        source_sha256={name: sha(ROOT / name) for name in (
            "run_probe_height_sweep.py", "run_probe_cross_section.py",
            "finite_copper_cavity.py", "finite_pec_cavity.py")},
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
                for cells in HEIGHT_CELLS[shape]:
                    height = cells * grid.spacing[2]
                    gap, post, height, location, cells = probe_pattern(
                        grid, seed, 1, height)
                    keep = np.setdiff1d(np.arange(grid.ne), post)
                    position = -np.ones(grid.ne, dtype=int)
                    position[keep] = np.arange(len(keep))
                    assert np.all(position[gap] >= 0)
                    wall_position = position[grid.wall_edges[grid.wall_edges < grid.ne]]
                    drive = np.zeros(len(keep), complex)
                    drive[position[gap]] = 1.
                    grid.m_keep = grid.m[keep]
                    grid.stiffness_keep = (grid.curl[:, keep].T @ diags(grid.q)
                                           @ grid.curl[:, keep]).tocsc()
                    rows = []
                    for f in frequencies:
                        _, per_record, total = grid.admittance(f, resistance, inductance)
                        s = 2j * np.pi * f
                        operator = (grid.stiffness_keep
                                    + diags(s * s * grid.m_keep + s * total[keep])).tocsc()
                        started = time.perf_counter()
                        e = splu(operator, permc_spec="MMD_AT_PLUS_A").solve(s * drive)
                        seconds = time.perf_counter() - started
                        z = np.vdot(e, drive)
                        wave_power = .5 * float(np.sum(per_record.real
                                                       * np.abs(e[wall_position]) ** 2))
                        source_power = .5 * float(z.real)
                        rows.append(dict(frequency_Hz=float(f), z_real_ohm=float(z.real),
                                         z_imag_ohm=float(z.imag),
                                         abs_s11=float(abs((z - Z0) / (z + Z0))),
                                         power_balance_error=float(
                                             abs(source_power - wave_power) / source_power),
                                         solve_seconds=seconds))
                        print(shape, "cells", cells, round(height * 1e3, 3), f,
                              rows[-1]["z_real_ohm"], rows[-1]["z_imag_ohm"],
                              rows[-1]["abs_s11"], flush=True)
                    del grid.m_keep, grid.stiffness_keep
                    summary["cases"].append(dict(
                        shape=list(shape), post_cells=cells, post_height_m=height,
                        post_location_ij=[int(v) for v in location],
                        center_frequency_Hz=center, width_Hz=width, positions=rows,
                        mean_z_real_ohm=float(np.mean([r["z_real_ohm"] for r in rows])),
                        mean_z_imag_ohm=float(np.mean([r["z_imag_ohm"] for r in rows])),
                        best_abs_s11=float(min(r["abs_s11"] for r in rows)),
                        max_power_balance_error=float(
                            max(r["power_balance_error"] for r in rows)),
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
