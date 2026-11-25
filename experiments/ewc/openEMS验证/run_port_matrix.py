"""Nine-port open-circuit impedance matrix on the same FIT cavity.

Not openEMS. Cavity, 64-term copper surface model, 111-mode reference and the
k x k axial edge-bundle port definition are unchanged from
run_probe_cross_section.py, so every number here is produced by the same
operator as sections 23/24/26.

Nine nominal ports are placed on the bottom wall in an evenly spaced 3 x 3
lattice (7 bookkeeping "input" ports, 2 bookkeeping "receive" ports). The
lattice is a study arrangement only: the project has no final port layout yet,
and the role assignment carries no topological meaning.

For each frequency one LU factorisation is built and reused for nine
right-hand sides, one per driven port (1 A total over that port's k^2 gap
edges, all other ports open-circuited). Entry Z[i][j] is the mean gap voltage
at port i per 1 A at port j, evaluated with the same np.vdot(e, drive)
convention as every earlier section, so the diagonal is directly comparable in
sign and magnitude to those results apart from the eight extra posts.
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
OUT = ROOT / "多端口响应矩阵结果"
SOURCES = ROOT / "铜腔111双极化结果"
LENGTHS = (.16, .10, .06)
Z0 = 50.
INPUT_PORTS = 7          # bookkeeping only: 7 RF inputs in the baseline topology
RECEIVE_PORTS = 2        # bookkeeping only: 2 receive ports in the baseline topology

# (shape, side, post cells, lateral index list, longitudinal index list)
# i runs along 160 mm, j along 100 mm; both lists are evenly spaced in cells.
PLANS = (
    ((24, 15, 9), 1, 4, (6, 12, 18), (4, 8, 12)),
    ((36, 23, 14), 1, 7, (9, 18, 27), (6, 12, 18)),
    ((36, 23, 14), 2, 7, (9, 18, 27), (6, 12, 18)),
)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def port_pattern(grid, i0, j0, side, cells):
    """Gap edges at z=0 and the removed post edges above them."""
    es, eoff = grid.pec.es, grid.pec.eoff
    nx, ny, nz = grid.shape
    if not (1 <= i0 <= nx - side and 1 <= j0 <= ny - side):
        raise ValueError("Port footprint leaves the interior")
    if not (1 <= cells <= nz - 2):
        raise ValueError("Post must stay at least two cells below the top wall")
    gap = np.array([eoff[2] + np.ravel_multi_index((i0 + di, j0 + dj, 0), es[2])
                    for di in range(side) for dj in range(side)], dtype=int)
    post = np.array([eoff[2] + np.ravel_multi_index((i0 + di, j0 + dj, k), es[2])
                     for di in range(side) for dj in range(side)
                     for k in range(1, cells + 1)], dtype=int)
    return gap, post


def main():
    assert not OUT.exists(), "New evidence only; do not overwrite a prior result"
    model_path = ROOT / "厚铜边界基准结果/passive_surface_models.json"
    model = next(row for row in json.loads(model_path.read_text(encoding="utf-8"))["models"]
                 if row["terms"] == 64)
    resistance, inductance = np.asarray(model["R_ohm"]), np.asarray(model["L_H"])
    OUT.mkdir()
    summary = dict(
        scope="9 x 9 open-circuit port impedance matrix, one LU reused for nine "
              "1 A right-hand sides per frequency, full six-wall frequency solve",
        evidence_level="A (full six-wall frequency solve)",
        limitation="ports are k x k axial edge bundles (E_axial forced to zero); the "
                   "lateral post surface is not enforced; each port is a wall delta "
                   "gap, not a coaxial/SMA opening with an outer conductor and "
                   "reference plane; the 3 x 3 lattice and the 7/2 role split are a "
                   "nominal study arrangement, not a design; the diagonal differs from "
                   "sections 23/24/26 because all nine posts are present at once",
        reference_impedance_ohm=Z0,
        port_roles=dict(
            inputs=[f"P{n + 1}" for n in range(INPUT_PORTS)],
            receives=[f"R{n + 1}" for n in range(RECEIVE_PORTS)],
            mapping="index 0..6 = P1..P7, index 7..8 = R1, R2 (row-major over the lattice)"),
        configuration_sha256=None, model_sha256=sha(model_path),
        source_sha256={name: sha(ROOT / name) for name in (
            "run_port_matrix.py", "run_probe_cross_section.py",
            "finite_copper_cavity.py", "finite_pec_cavity.py")},
        cases=[], status="running")

    def checkpoint():
        (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n",
                                          encoding="utf-8")

    configurations = []
    for shape, side, cells, rows, cols in PLANS:
        spacing = np.asarray(LENGTHS) / np.asarray(shape, dtype=float)
        ports = []
        index = 0
        for i0 in rows:
            for j0 in cols:
                role = f"P{index + 1}" if index < INPUT_PORTS else f"R{index - INPUT_PORTS + 1}"
                ports.append(dict(index=index, role=role, i=int(i0), j=int(j0),
                                  x_mm=float(i0 * spacing[0] * 1e3),
                                  y_mm=float(j0 * spacing[1] * 1e3)))
                index += 1
        assert len(ports) == INPUT_PORTS + RECEIVE_PORTS
        configurations.append(dict(
            shape=list(shape), lengths_m=list(LENGTHS), side=side,
            post_cells=cells, post_height_m=float(cells * spacing[2]),
            port_footprint_mm=[float(side * spacing[0] * 1e3),
                               float(side * spacing[1] * 1e3)],
            terms=64, reference_impedance_ohm=Z0, scan_points=5,
            ports=ports))
    config_path = OUT / "configurations.json"
    config_path.write_text(json.dumps(configurations, indent=2) + "\n", encoding="utf-8")
    summary["configuration_sha256"] = sha(config_path)
    checkpoint()

    try:
        with threadpool_limits(limits=1):
            for (shape, side, cells, rows, cols), config in zip(PLANS, configurations):
                grid = CopperCavity(shape, LENGTHS)
                stem = "111_" + "x".join(map(str, shape))
                peaks = np.load(SOURCES / (stem + "_peaks.npz"), allow_pickle=True)
                scan = np.load(SOURCES / (stem + "_scan.npz"), allow_pickle=True)
                poles, q_poles = peaks["fitted_poles_Hz"], peaks["fitted_Q"]
                center = float(np.mean(poles.real))
                width = center / float(np.min(q_poles))
                frequencies = center - width / 2 + np.linspace(-2, 2, 5) * width

                gaps, posts = [], []
                for port in config["ports"]:
                    gap, post = port_pattern(grid, port["i"], port["j"], side, cells)
                    gaps.append(gap)
                    posts.append(post)
                post_union = np.unique(np.concatenate(posts))
                keep = np.setdiff1d(np.arange(grid.ne), post_union)
                position = -np.ones(grid.ne, dtype=int)
                position[keep] = np.arange(len(keep))
                assert all(np.all(position[gap] >= 0) for gap in gaps)
                wall_position = position[grid.wall_edges[grid.wall_edges < grid.ne]]
                assert np.all(wall_position >= 0)
                grid.m_keep = grid.m[keep]
                grid.stiffness_keep = (grid.curl[:, keep].T @ diags(grid.q)
                                       @ grid.curl[:, keep]).tocsc()

                drives = []
                for gap in gaps:
                    drive = np.zeros(len(keep), complex)
                    drive[position[gap]] = 1. / len(gap)
                    drives.append(drive)
                n_port = len(drives)
                rows_out = []
                for f in frequencies:
                    s = 2j * np.pi * f
                    _, per_record, total = grid.admittance(f, resistance, inductance)
                    operator = (grid.stiffness_keep
                                + diags(s * s * grid.m_keep + s * total[keep])).tocsc()
                    started = time.perf_counter()
                    factor = splu(operator, permc_spec="MMD_AT_PLUS_A")
                    factor_seconds = time.perf_counter() - started
                    matrix = np.zeros((n_port, n_port), complex)
                    powers = []
                    for j_index, drive in enumerate(drives):
                        e = factor.solve(s * drive)
                        for i_index in range(n_port):
                            matrix[i_index, j_index] = np.vdot(e, drives[i_index])
                        wall_power = .5 * float(
                            np.sum(per_record.real * np.abs(e[wall_position]) ** 2))
                        source_power = .5 * float(np.real(matrix[j_index, j_index]))
                        powers.append(dict(
                            driven_port=config["ports"][j_index]["role"],
                            source_power_W=source_power,
                            wall_power_W=wall_power,
                            efficiency=float(wall_power / source_power)
                            if source_power > 0 else None))
                    asymmetry = float(np.max(np.abs(matrix - matrix.T)))
                    real_part = matrix.real
                    eigenvalues = np.linalg.eigvalsh((real_part + real_part.T) / 2)
                    diagonal = [dict(port=config["ports"][n]["role"],
                                     z_real_ohm=float(matrix[n, n].real),
                                     z_imag_ohm=float(matrix[n, n].imag),
                                     abs_s11=float(abs((matrix[n, n] - Z0)
                                                       / (matrix[n, n] + Z0))))
                                for n in range(n_port)]
                    transfers = [dict(input=config["ports"][j_index]["role"],
                                      receive=config["ports"][i_index]["role"],
                                      abs_z_ohm=float(abs(matrix[i_index, j_index])),
                                      ratio_to_self=float(abs(matrix[i_index, j_index])
                                                          / abs(matrix[i_index, i_index]))
                                      if abs(matrix[i_index, i_index]) > 0 else None)
                                 for j_index in range(INPUT_PORTS)
                                 for i_index in range(INPUT_PORTS, n_port)]
                    rows_out.append(dict(
                        frequency_Hz=float(f),
                        z_matrix=[[[float(v.real), float(v.imag)] for v in row]
                                  for row in matrix],
                        diagonal=diagonal,
                        transfers_input_to_receive=transfers,
                        max_reciprocity_error_ohm=asymmetry,
                        min_real_symmetric_eigenvalue=float(eigenvalues[0]),
                        max_real_symmetric_eigenvalue=float(eigenvalues[-1]),
                        per_drive_power=powers,
                        factor_seconds=factor_seconds))
                    print(shape, "k", side, "cells", cells, f,
                          "max_reZ", max(d["z_real_ohm"] for d in diagonal),
                          "min_imag", min(d["z_imag_ohm"] for d in diagonal),
                          "asym", asymmetry, "min_eig", eigenvalues[0],
                          flush=True)
                    summary["cases"].append(dict(
                        shape=list(shape), side=side, post_cells=cells,
                        post_height_m=config["post_height_m"],
                        ports=config["ports"],
                        center_frequency_Hz=center, width_Hz=width,
                        **rows_out[-1], status="complete"))
                    checkpoint()
                del grid.m_keep, grid.stiffness_keep
        summary["status"] = "complete"
        checkpoint()
    except Exception as error:
        summary["status"] = "failed_preserve_partial"
        summary["failure"] = repr(error)
        checkpoint()
        raise


if __name__ == "__main__":
    main()
