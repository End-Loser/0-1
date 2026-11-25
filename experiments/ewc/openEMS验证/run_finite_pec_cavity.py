"""Archive targeted finite-PEC eigenmodes and forced midpoint trajectories."""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from finite_pec_cavity import FinitePEC, C0, transient

MODES = [(1, 1, 0), (1, 0, 1), (0, 1, 1), (2, 1, 0), (1, 2, 0), (1, 1, 1)]
LENGTHS = (.16, .10, .06)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(output):
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    for shape in [(4, 3, 2), (8, 5, 3), (16, 10, 6), (24, 15, 9)]:
        grid = FinitePEC(shape, LENGTHS)
        values, fields = [], []
        for indices in MODES:
            value, e = grid.solve_mode(indices)
            values.append(value)
            fields.append(e)
        frequency = np.sqrt(values) / (2 * np.pi)
        reference = np.array([C0 / 2 * np.linalg.norm(np.array(mode) / LENGTHS)
                              for mode in MODES])
        error = abs(frequency / reference - 1)
        case = "pec_" + "x".join(map(str, shape))
        path = output / (case + ".npz")
        np.savez_compressed(path, shape=shape, lengths_m=LENGTHS, mode_indices=MODES,
                            eigenvalues_rad2_s2=values, edge_vectors_V=fields,
                            frequency_Hz=frequency, continuum_frequency_Hz=reference)
        row = {"case": case, "shape": list(shape), "active_edges": grid.ne,
               "faces": grid.nf, "mode_count": len(MODES),
               "frequency_relative_error": error.tolist(),
               "all_selected_modes_1percent_pass": bool(np.all(error < .01)),
               "file": path.name, "sha256": sha(path)}
        rows.append(row)
        print(case, "max frequency error", float(max(error)), flush=True)
    for dt in [5e-12, 20e-12]:
        grid = FinitePEC((8, 5, 3), LENGTHS)
        data = transient(grid, dt, round(8e-9 / dt))
        case = f"forced_pec_dt{dt / 1e-12:g}ps"
        path = output / (case + ".npz")
        np.savez_compressed(path, **data)
        rows.append({"case": case, "shape": list(grid.shape), "steps": len(data["current_A"]),
                     "file": path.name, "sha256": sha(path),
                     "transient_physical_accuracy_verified": False})
        print(case, "complete", flush=True)
    summary = {"method": "independent finite six-face PEC integral Maxwell baseline",
               "selected_eigenmode_solves": 24, "forced_transient_cases": 2,
               "cases": rows,
               "source_sha256": {n: sha(Path(__file__).with_name(n))
                                 for n in ["finite_pec_cavity.py", Path(__file__).name]},
               "copper_boundary_complete": False, "copper_cavity_Q_complete": False,
               "PEC_surface_charge_complete": False,
               "full_joint_simulation_complete": False}
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args().output)
