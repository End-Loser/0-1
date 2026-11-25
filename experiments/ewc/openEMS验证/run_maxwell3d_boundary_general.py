"""N-30-2 companions: mirrored termination (-x) and transverse refinement.

The 3-D periodic module has no notion of axis names: its only open axis is the
first index.  Therefore "+/-y" and "+/-z" runs are coordinate relabelings of
the same operator and are not run here.  What is genuinely new:
  * termination on the low-index end instead of the high-index end (-x sign),
  * transverse cell count 2 -> 4 -> 8 at fixed transverse extent.
"""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from maxwell3d_boundary import Grid, simulate
from run_maxwell3d_boundary import analyze

LENGTHS = (.16, .10, .06)
SPACING_X = .12 / 120


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class MirroredGrid(Grid):
    """Same discrete grid with the driven end and the metal end swapped."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        left, wall = self.left.copy(), self.wall.copy()
        self.left, self.wall = wall, left


def main(args):
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite existing evidence: {args.output}")
    args.output.mkdir(parents=True)
    models = json.loads(args.models.read_text(encoding="utf-8"))["models"]
    model = next(m for m in models if m["terms"] == 64)
    r, l = np.asarray(model["R_ohm"]), np.asarray(model["L_H"])
    definitions = [("ny2_baseline", Grid, 2, .02),
                   ("ny4", Grid, 4, .02),
                   ("ny8", Grid, 8, .02),
                   ("mirror_termination", MirroredGrid, 2, .02)]
    rows = []
    for name, factory, ny, extent in definitions:
        grid = factory(120, ny, 3, SPACING_X, extent / ny, .015)
        data = simulate(grid, 1e-12, 8e-9, r, l, "y", False)
        data.pop("snapshots")
        metrics = data.pop("metrics")
        plane_metrics, spectra = analyze(data, 1e-12, model["sigma_S_per_m"])
        metrics.update(plane_metrics)
        path = args.output / (name + ".npz")
        np.savez_compressed(path, **data, **spectra)
        row = {"case": name, "grid_class": factory.__name__, "shape": list(grid.shape),
               "spacing_m": list(grid.spacing), "electric_edge_count": grid.ne,
               "magnetic_face_count": grid.nf, "wall_edges": len(grid.wall),
               "RL_states": len(grid.wall) * 64, "dt_ps": 1, "duration_ns": 8,
               "polarization": "y", "terms": 64, **metrics,
               "data_file": path.name, "data_sha256": sha(path)}
        rows.append(row)
        print(json.dumps(row), flush=True)
    summary = {
        "method": "independent 3-D integral Maxwell, periodic transverse, implicit midpoint",
        "purpose": "N-30-2: -x termination sign and transverse refinement at fixed extent",
        "cases": rows,
        "boundary_normal_into_metal": "+x",
        "axis_permutation_note": "the module's only open axis is its first index; "
                                 "+/-y and +/-z runs are coordinate relabelings, not new evidence",
        "arbitrary_oblique_normal_implemented": False,
        "all_normal_orientations_verified": False,
        "module_sha256": sha(Path(__file__).with_name("maxwell3d_boundary.py")),
        "runner_sha256": sha(Path(__file__)),
        "models_sha256": sha(args.models),
    }
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--models", type=Path, default=Path(__file__).parent /
                   "厚铜边界基准结果/passive_surface_models.json")
    main(p.parse_args())
