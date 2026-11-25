"""Height sweep of the k <= 2 wall-fed post up to the series-resonance crossing.

Section 24 (run_probe_height_sweep.py) swept the post height at fixed mesh but
stopped at 30 mm on the fine grid and 53.3 mm on the coarse grid, and neither
grid reached Im = 0. Section 24.4 therefore left C-3 open: continue the height
sweep at the usable port definition (k <= 2, i.e. one or two axial edges per
transverse direction) until Im crosses zero, or show that it cannot cross inside
an admissible height.

Admissible height is capped at nz - 2 cells on both grids, so the post tip keeps
exactly two cells of clearance to the top wall and is never shorted to it. The
coarse grid was previously allowed to reach 8 cells = nz - 1 (touching the top
wall); that point is not repeated here, and the 46.7 mm coarse point replaces it.

Everything else is unchanged from run_probe_cross_section.py: same cavity
(0.16 x 0.10 x 0.06 m), same 64-term copper surface model, same six-wall
frequency solve, same 5 frequencies spanning +-2 mode linewidths about the 111
pole, and the same wall delta-gap port at z = 0 at 1 A total drive.

Not openEMS; this is the in-repo FIT solver.
"""
import hashlib
import json
from pathlib import Path
import time

import numpy as np
from scipy.sparse import diags
from threadpoolctl import threadpool_limits

from finite_copper_cavity import CopperCavity
from run_probe_cross_section import LENGTHS, Z0, probe_pattern, solve_case

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "探针柱高共振扫描结果"
SOURCES = ROOT / "铜腔111双极化结果"
SHAPES = ((24, 15, 9), (36, 23, 14))
# cells are capped at nz - 2 so the tip keeps two cells of clearance to the top wall
HEIGHT_CELLS = {SHAPES[0]: tuple(range(2, 8)), SHAPES[1]: tuple(range(2, 13))}
# k = 2 is extended only on the fine grid, at the section-24 height (7 cells) and
# at the new upper end (12 cells); the coarse grid keeps k = 2 at its single point.
SIDE2_CELLS = {SHAPES[0]: (), SHAPES[1]: (7, 12)}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_case(grid, seed, side, cells, frequencies, resistance, inductance):
    gap, post, height, location, cells = probe_pattern(
        grid, seed, side, cells * grid.spacing[2])
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
    rows = []
    for f in frequencies:
        _, per_record, total = grid.admittance(f, resistance, inductance)
        row, _ = solve_case(grid, keep, position, wall_position, f,
                            drive, per_record, total)
        rows.append(row)
        print(side, cells, round(height * 1e3, 3), f,
              row["z_real_ohm"], row["z_imag_ohm"], row["abs_s11"], flush=True)
    del grid.m_keep, grid.stiffness_keep
    return dict(
        side=side, post_cells=cells, post_height_m=height,
        tip_clearance_cells=int(grid.shape[2] - cells),
        tip_clearance_m=float((grid.shape[2] - cells) * grid.spacing[2]),
        post_location_ij=[int(v) for v in location],
        positions=rows,
        mean_z_real_ohm=float(np.mean([r["z_real_ohm"] for r in rows])),
        mean_z_imag_ohm=float(np.mean([r["z_imag_ohm"] for r in rows])),
        best_abs_s11=float(min(r["abs_s11"] for r in rows)),
        max_power_balance_error=float(max(r["power_balance_error"] for r in rows)),
        status="complete")


def crossing(cases):
    """Linear Im = 0 crossing between adjacent heights, or None."""
    ordered = sorted(cases, key=lambda c: c["post_height_m"])
    for low, high in zip(ordered, ordered[1:]):
        if low["mean_z_imag_ohm"] == 0.:
            return dict(height_m=low["post_height_m"], bracketed=False)
        if low["mean_z_imag_ohm"] * high["mean_z_imag_ohm"] < 0.:
            fraction = (low["mean_z_imag_ohm"]
                        / (low["mean_z_imag_ohm"] - high["mean_z_imag_ohm"]))
            return dict(
                height_m=float(low["post_height_m"]
                               + fraction * (high["post_height_m"] - low["post_height_m"])),
                bracketed=True, lower_cells=low["post_cells"],
                upper_cells=high["post_cells"],
                lower_mean_z_imag_ohm=low["mean_z_imag_ohm"],
                upper_mean_z_imag_ohm=high["mean_z_imag_ohm"],
                linear_interpolation=True)
    only = ordered[-1]
    return dict(height_m=None, bracketed=False,
                highest_scanned_height_m=only["post_height_m"],
                highest_scanned_mean_z_imag_ohm=only["mean_z_imag_ohm"])


def main():
    assert not OUT.exists(), "New evidence only; do not overwrite a prior result"
    model_path = ROOT / "厚铜边界基准结果/passive_surface_models.json"
    model = next(row for row in json.loads(model_path.read_text(encoding="utf-8"))["models"]
                 if row["terms"] == 64)
    resistance, inductance = np.asarray(model["R_ohm"]), np.asarray(model["L_H"])
    OUT.mkdir()
    configurations = [dict(shape=list(shape), lengths_m=list(LENGTHS),
                           height_cells=list(HEIGHT_CELLS[shape]),
                           side2_height_cells=list(SIDE2_CELLS[shape]),
                           tip_clearance_cells=2, terms=64,
                           reference_impedance_ohm=Z0, scan_points=5,
                           frequency_rule="center - width/2 + linspace(-2, 2, 5) * width")
                      for shape in SHAPES]
    config_path = OUT / "configurations.json"
    config_path.write_text(json.dumps(configurations, indent=2) + "\n", encoding="utf-8")
    summary = dict(
        scope="k = 1 (and a k = 2 confirmation on the fine grid) post height sweep at "
              "fixed mesh, carried to Im = 0 or to the two-cell tip clearance limit, "
              "5 frequencies inside +-2 mode linewidths, full six-wall frequency solve",
        evidence_level="A (full six-wall frequency solve)",
        limitation="post is a bundle of k^2 thin wires with E_axial forced to zero; the "
                   "lateral post surface is not enforced; port is a wall delta gap at 1 A, "
                   "not a coaxial/SMA opening with an outer conductor and reference plane; "
                   "tip is kept two cells below the top wall, so the post is never shorted "
                   "to it, and no height beyond nz - 2 cells is claimed",
        configuration_sha256=sha(config_path), model_sha256=sha(model_path),
        source_sha256={name: sha(ROOT / name) for name in (
            "run_probe_height_resonance.py", "run_probe_cross_section.py",
            "finite_copper_cavity.py", "finite_pec_cavity.py")},
        cases=[], zero_crossing={}, status="running")

    def checkpoint():
        (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    checkpoint()
    try:
        with threadpool_limits(limits=1):
            started_all = time.perf_counter()
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
                for side, cells_table in ((1, HEIGHT_CELLS[shape]),
                                          (2, SIDE2_CELLS[shape])):
                    for cells in cells_table:
                        summary["cases"].append(run_case(
                            grid, seed, side, cells, frequencies,
                            resistance, inductance))
                        summary["cases"][-1].update(
                            shape=list(shape), center_frequency_Hz=center,
                            width_Hz=width, frequencies_Hz=[float(f) for f in frequencies])
                        checkpoint()
                for side in (1, 2):
                    subset = [c for c in summary["cases"]
                              if c["shape"] == list(shape) and c["side"] == side]
                    if subset:
                        summary["zero_crossing"]["x".join(map(str, shape))
                                                + "_k%d" % side] = crossing(subset)
                        checkpoint()
            summary["total_solve_seconds"] = time.perf_counter() - started_all
        summary["status"] = "complete"
        checkpoint()
        print(json.dumps(summary["zero_crossing"], indent=2), flush=True)
    except Exception as error:
        summary["status"] = "failed_preserve_partial"
        summary["failure"] = repr(error)
        checkpoint()
        raise


if __name__ == "__main__":
    main()
