"""Frequency-domain oblique incidence projected from the 3-D periodic grid.

Exact invariant Fourier subspace projection, not a general-geometry reduction.
Not openEMS, not an oblique transient absorber or a closed copper cavity.
"""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.sparse import coo_matrix, diags
from scipy.sparse.linalg import spsolve

from maxwell3d_boundary import Grid, MU, EPS, C0

FREQ = np.linspace(2.5e9, 3.5e9, 101)
WIDTH = .2
KY = 2 * np.pi / WIDTH
LENGTH = .12


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def references(frequency, ky, sigma, polarization):
    omega = 2 * np.pi * np.asarray(frequency)
    if polarization not in ("TE", "TM") or np.any(omega / C0 <= abs(ky)):
        raise ValueError("TE/TM and a propagating air mode required.")
    s = 1j * omega
    kx = np.sqrt((omega / C0)**2 - ky**2)
    gamma_metal = np.sqrt(s * MU * (sigma + s * EPS) + ky**2)
    if polarization == "TE":
        air = omega * MU / kx
        metal = s * MU / gamma_metal
    else:
        air = kx / (omega * EPS)
        metal = gamma_metal / (sigma + s * EPS)
    return air, metal, kx


def projection(grid, ky):
    nx, ny, nz = grid.shape
    dy = grid.spacing[1]
    rows, cols, values = [], [], []
    for component, count, offset in ((0, nx, 0), (1, nx + 1, nx), (2, nx + 1, 2 * nx + 1)):
        for i in range(count):
            for j in range(ny):
                for k in range(nz):
                    rows.append(grid.e(component, i, j, k))
                    cols.append(offset + i)
                    y = (j + (.5 if component == 1 else 0)) * dy
                    values.append(np.exp(1j * ky * y) / np.sqrt(ny * nz))
    return coo_matrix((values, (rows, cols)), shape=(grid.ne, 3 * nx + 2)).tocsc()


def solve_case(nx, ny, polarization, model, frequencies=FREQ, full_check=False):
    grid = Grid(nx, ny, 2, LENGTH / nx, WIDTH / ny, .015)
    p = projection(grid, KY)
    cp = grid.curl @ p
    stiffness = (cp.conj().T @ diags(grid.q) @ cp).tocsc()
    mass = (p.conj().T @ diags(grid.m) @ p).tocsc()
    source_projection = p[grid.left]
    wall_projection = p[grid.wall]
    pol_index = 1 if polarization == "TE" else 0
    comp = 2 if polarization == "TE" else 1
    amp_index = (2 * nx + 1 if comp == 2 else nx)
    wall_index = amp_index + nx
    sl = slice(pol_index * grid.plane, (pol_index + 1) * grid.plane)
    profile = np.zeros(len(grid.left), complex)
    for j in range(ny):
        for k in range(2):
            local = pol_index * grid.plane + j * 2 + k
            y = (j + (.5 if comp == 1 else 0)) * grid.spacing[1]
            profile[local] = grid.edge_length[local] * np.exp(1j * KY * y)
    resistance, inductance = np.array(model["R_ohm"]), np.array(model["L_H"])
    air, metal, kx = references(frequencies, KY, model["sigma_S_per_m"], polarization)
    reflections, absorptions, walls, residuals, balances, full_errors, amplitudes = [], [], [], [], [], [], []
    for index, f in enumerate(frequencies):
        s = 2j * np.pi * f
        wall_z = np.sum(1 / (1 / resistance + 1 / (s * inductance)))
        cos_angle = kx[index] / (2 * np.pi * f / C0)
        # y is TM and z is TE for incidence in the x-y plane.
        eta = np.sqrt(MU / EPS)
        source_r = np.repeat([eta * cos_angle, eta / cos_angle], grid.plane) * grid.surface_scale
        source_g = 1 / source_r
        wall_g = 1 / (wall_z * grid.surface_scale)
        boundary = (source_projection.conj().T @ diags(source_g) @ source_projection +
                    wall_projection.conj().T @ diags(wall_g) @ wall_projection)
        a = (stiffness + s * s * mass + s * boundary).tocsc()
        rhs = s * source_projection.conj().T @ (2 * profile * source_g)
        amplitudes_now = spsolve(a, rhs)
        e = p @ amplitudes_now
        left_e, wall_e = e[grid.left], e[grid.wall]
        norm = np.sqrt(grid.plane) * grid.edge_length[sl][0]
        reflection = amplitudes_now[amp_index] / norm - 1
        density = .5 * np.sum(wall_g.real * abs(wall_e)**2) / (WIDTH * .03)
        absorbed = .5 * np.sum(wall_g.real * abs(wall_e)**2)
        work = .5 * np.real(np.vdot(left_e, 2 * profile * source_g))
        source_loss = .5 * np.sum(source_g * abs(left_e)**2)
        balance = abs(work - source_loss - absorbed) / max(abs(work), source_loss, 1e-30)
        g = np.zeros(grid.ne, complex)
        g[grid.left], g[grid.wall] = source_g, wall_g
        full_rhs = np.zeros(grid.ne, complex)
        full_rhs[grid.left] = s * 2 * profile * source_g
        residual = (grid.curl.T @ (grid.q * (grid.curl @ e)) +
                    s * s * grid.m * e + s * g * e - full_rhs)
        residuals.append(float(np.linalg.norm(residual) / np.linalg.norm(full_rhs)))
        if full_check:
            full_a = (grid.curl.T @ diags(grid.q) @ grid.curl +
                      diags(s * s * grid.m + s * g)).tocsc()
            full_e = spsolve(full_a, full_rhs)
            full_errors.append(float(np.linalg.norm(full_e - e) / np.linalg.norm(full_e)))
        reflections.append(reflection)
        absorptions.append(density)
        walls.append(wall_z)
        balances.append(float(balance))
        amplitudes.append(amplitudes_now)
    expected_reflection = (metal - air) / (metal + air) * np.exp(-2j * kx * LENGTH)
    expected_density = .5 * metal.real * abs(2 / (air + metal))**2
    reflections, absorptions, walls = map(np.asarray, (reflections, absorptions, walls))
    ref_error = float(np.max(abs(reflections - expected_reflection)))
    absorbed_error = float(np.max(abs(absorptions / expected_density - 1)))
    return {
        "frequency_Hz": frequencies, "reflection": reflections,
        "absorbed_density_W_per_m2": absorptions, "local_wall_impedance_ohm": walls,
        "expected_reflection": expected_reflection, "expected_absorbed_density_W_per_m2": expected_density,
        "air_tangential_impedance_ohm": air, "exact_metal_tangential_impedance_ohm": metal,
        "modal_edge_amplitudes_V": np.asarray(amplitudes),
        "full_equation_residual_relative": np.asarray(residuals),
        "power_closure_relative": np.asarray(balances),
        "full_solve_difference_relative": np.asarray(full_errors),
        "metrics": {
            "reflection_absolute_error": ref_error,
            "absorbed_density_relative_error": absorbed_error,
            "local_wall_vs_exact_oblique_metal_error": float(np.max(abs(walls / metal - 1))),
            "max_full_equation_residual_relative": max(residuals),
            "max_power_closure_relative": max(balances),
            "reflection_1percent_pass": ref_error < .01,
            "absorption_1percent_pass": absorbed_error < .01,
            "full_solve_crosscheck_max_relative_difference": max(full_errors) if full_errors else None,
        },
    }


def main(args):
    args.output.mkdir(parents=True, exist_ok=True)
    model = next(m for m in json.loads(args.models.read_text(encoding="utf-8"))["models"] if m["terms"] == 64)
    cases = [(nx, ny, pol, False) for nx in (60, 120) for ny in (8, 16, 32, 64) for pol in ("TE", "TM")]
    cases += [(240, 64, pol, False) for pol in ("TE", "TM")]
    cases += [(12, 4, pol, True) for pol in ("TE", "TM")]
    rows = []
    for nx, ny, pol, full in cases:
        f = np.array([2.5e9, 3e9, 3.5e9]) if full else FREQ
        data = solve_case(nx, ny, pol, model, f, full)
        metrics = data.pop("metrics")
        name = f"nx{nx}_ny{ny}_{pol}_{'fullcheck' if full else 'sweep'}"
        path = args.output / (name + ".npz")
        np.savez_compressed(path, **data)
        row = {"case": name, "nx": nx, "ny": ny, "nz": 2, "polarization": pol,
               "frequency_points": len(f), "full_matrix_crosscheck": full, **metrics,
               "data_file": path.name, "sha256": sha(path)}
        rows.append(row)
        print(json.dumps(row), flush=True)
    result = {
        "method": "frequency-domain 3-D operator, exact periodic Fourier projection",
        "case_count": len(rows), "cases": rows, "ky_rad_per_m": KY, "length_m": LENGTH,
        "periodic_y_width_m": WIDTH, "periodic_z_width_m": .03, "terms": 64,
        "sigma_S_per_m": model["sigma_S_per_m"], "models_file": str(args.models.resolve()),
        "models_sha256": sha(args.models), "runner_sha256": sha(Path(__file__)),
        "maxwell_module_sha256": sha(Path(__file__).with_name("maxwell3d_boundary.py")),
        "not_openems": True, "oblique_transient_boundary_complete": False,
        "finite_boundary_complete": False, "closed_copper_cavity_complete": False,
        "full_joint_simulation_complete": False,
    }
    (args.output / "summary.json").write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--models", type=Path, default=Path(__file__).parent /
                        "\u539a\u94dc\u8fb9\u754c\u57fa\u51c6\u7ed3\u679c/passive_surface_models.json")
    main(parser.parse_args())
