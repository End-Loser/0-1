"""3-D periodic plane tests; not an openEMS extension or copper cavity."""

import argparse
import hashlib
import json
from pathlib import Path
import tempfile

import numpy as np

from maxwell3d_boundary import Grid, simulate, MU, EPS, C0, ETA


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def analyze(data, dt, sigma):
    f = np.linspace(2.5e9, 3.5e9, 101)
    t = data["time_mid_s"]
    phase = dt * np.exp(-2j * np.pi * f[:, None] * t)
    drive = phase @ data["drive_V_per_m"]
    reflection = (phase @ data["left_mean_E_V_per_m"]) / drive - 1
    impedance = (phase @ data["wall_mean_E_V_per_m"]) / (phase @ data["wall_mean_K_A_per_m"])
    s = 2j * np.pi * f
    target = np.sqrt(s * MU / (sigma + s * EPS))
    expected = (target - ETA) / (target + ETA) * np.exp(-2 * s * .12 / C0)
    row = {
        "reflection_absolute_error": float(np.max(abs(reflection - expected))),
        "wall_complex_impedance_error": float(np.max(abs(impedance / target - 1))),
        "wall_phase_error_deg": float(np.max(abs(np.angle(impedance / target, deg=True)))),
    }
    row["plane_reflection_1percent_pass"] = row["reflection_absolute_error"] < .01
    row["wall_impedance_1percent_pass"] = row["wall_complex_impedance_error"] < .01
    return row, {"frequency_Hz": f, "reflection": reflection, "wall_impedance_ohm": impedance,
                 "target_wall_impedance_ohm": target}


def main(args):
    args.output.mkdir(parents=True, exist_ok=True)
    args.raw_dir.mkdir(parents=True, exist_ok=True)
    models = json.loads(args.models.read_text(encoding="utf-8"))["models"]
    definitions = [(nx, 1, pol, 64, False) for nx in (60, 120) for pol in ("y", "z")]
    definitions += [(120, 5, "y", 64, False), (120, 1, "y", 16, False),
                    (60, 5, "both", 64, True)]
    rows = []
    for nx, dt_ps, pol, terms, nonuniform in definitions:
        model = next(m for m in models if m["terms"] == terms)
        grid = Grid(nx, 2, 3, .12 / nx, .01, .015)
        case = f"nx{nx}_dt{dt_ps}ps_{pol}_m{terms}_{'nonuniform' if nonuniform else 'plane'}"
        data = simulate(grid, dt_ps * 1e-12, 8e-9,
                        model["R_ohm"], model["L_H"], pol, nonuniform)
        raw = args.raw_dir / (case + ".npz")
        np.savez_compressed(raw, **data.pop("snapshots"))
        metrics = data.pop("metrics")
        if nonuniform:
            spectra = {}
            metrics["nonuniform_physical_accuracy_verified"] = False
        else:
            plane_metrics, spectra = analyze(data, dt_ps * 1e-12, model["sigma_S_per_m"])
            metrics.update(plane_metrics)
        path = args.output / (case + ".npz")
        np.savez_compressed(path, **data, **spectra)
        row = {
            "case": case, "shape": list(grid.shape), "spacing_m": list(grid.spacing),
            "electric_edge_count": grid.ne, "magnetic_face_count": grid.nf,
            "wall_edges": len(grid.wall), "RL_states": len(grid.wall) * terms,
            "dt_ps": dt_ps, "duration_ns": 8, "polarization": pol, "terms": terms,
            "nonuniform": nonuniform, "sigma_S_per_m": model["sigma_S_per_m"], **metrics,
            "data_file": path.name, "data_sha256": sha(path),
            "snapshots_file": str(raw), "snapshots_sha256": sha(raw),
        }
        rows.append(row)
        print(json.dumps(row), flush=True)
    summary = {
        "method": "independent 3-D integral Maxwell, periodic y/z, implicit midpoint",
        "not_openems_FDTD": True, "not_Yee_leapfrog": True, "case_count": len(rows), "cases": rows,
        "boundary_normal_into_metal": "+x", "boundary_tangential_components": ["y", "z"],
        "periodic_extent_m": [.02, .045], "incident_field_peak_V_per_m": 1,
        "module_sha256": sha(Path(__file__).with_name("maxwell3d_boundary.py")),
        "runner_sha256": sha(Path(__file__)), "models_file": str(args.models.resolve()),
        "models_sha256": sha(args.models), "3d_periodic_plane_boundary_implemented": True,
        "openems_boundary_extension_complete": False, "closed_copper_cavity_complete": False,
        "all_normal_orientations_verified": False, "actual_connector_complete": False,
        "full_joint_simulation_complete": False,
    }
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--raw-dir", type=Path, default=Path(tempfile.gettempdir()) / "ewc-openems/maxwell3d")
    parser.add_argument("--models", type=Path, default=Path(__file__).parent /
                        "\u539a\u94dc\u8fb9\u754c\u57fa\u51c6\u7ed3\u679c/passive_surface_models.json")
    main(parser.parse_args())
