"""Finer full Maxwell Q scans; same physics, sparse ordering changed only."""

import hashlib
import json
from pathlib import Path
import time

import numpy as np
from scipy.sparse import diags
from scipy.sparse.linalg import splu

from finite_copper_cavity import CopperCavity, continuum_q, fit_pole

ROOT = Path(__file__).resolve().parent
LENGTHS = (.16, .10, .06)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def solve(grid, frequency, r, l, drive):
    z, record, total = grid.admittance(frequency, r, l)
    s = 2j * np.pi * frequency
    a = grid.stiffness + diags(s * s * grid.m + s * total)
    started = time.perf_counter()
    factor = splu(a.tocsc(), permc_spec="MMD_AT_PLUS_A")
    e = factor.solve(s * drive)
    elapsed = time.perf_counter() - started
    b = -(grid.curl @ e) / s
    j = record * e[grid.wall_edges]
    return e, b, j, .5 * np.real(np.vdot(e, drive)), z, elapsed


def main():
    out = ROOT / "铜Q细化结果"
    out.mkdir(exist_ok=True)
    modelpath = ROOT / "厚铜边界基准结果" / "passive_surface_models.json"
    model = next(m for m in json.loads(modelpath.read_text(encoding="utf-8"))["models"] if m["terms"] == 64)
    r, l = np.array(model["R_ohm"]), np.array(model["L_H"])
    controls = []
    grid = CopperCavity((24, 15, 9), LENGTHS)
    for mode in [(0, 1, 1), (1, 0, 1)]:
        path = ROOT / "六面铜壁频域结果" / ("copper_24x15x9_" + "".join(map(str, mode)) + "_m64.npz")
        with np.load(path) as d:
            e, b, j, power, _, duration = solve(grid, float(d["peak_frequency_Hz"]), r, l, d["drive_A"])
            error = np.linalg.norm(e - d["peak_e_V"]) / np.linalg.norm(d["peak_e_V"])
            assert error < 1e-8
            controls.append({"archive": path.name, "archive_sha256": sha(path),
                             "full_field_relative_difference": float(error),
                             "source_power_relative_difference": float(abs(power / d["peak_wall_power_W"] - 1)),
                             "solve_seconds": duration})
    rows = []
    for shape in [(32, 20, 12), (36, 23, 14)]:
        grid = CopperCavity(shape, LENGTHS)
        for mode in [(0, 1, 1), (1, 0, 1)]:
            seed = grid.pec.lift(grid.pec.sample_mode(mode))
            drive = grid.m * seed
            fp = np.sqrt(seed @ (grid.stiffness @ seed) / (grid.m @ abs(seed)**2)) / (2 * np.pi)
            fc, qc = continuum_q(np.array(mode), np.array(LENGTHS), model["sigma_S_per_m"])
            linewidth = fp / qc
            frequencies = fp - linewidth / 2 + np.linspace(-2, 2, 5) * linewidth
            es, bs, js, powers, times, amplitudes = [], [], [], [], [], []
            for f in frequencies:
                e, b, j, power, _, duration = solve(grid, f, r, l, drive)
                es.append(e); bs.append(b); js.append(j); powers.append(power); times.append(duration)
                amplitudes.append(np.vdot(drive, e))
            fit = fit_pole(frequencies, np.array(amplitudes))
            fit_center = fit_pole(frequencies[1:4], np.array(amplitudes)[1:4])
            e, b, j, power, _, duration = solve(grid, fit["frequency_Hz"], r, l, drive)
            ind = j[:, None] * r / (r + 2j * np.pi * fit["frequency_Hz"] * l)
            stored = .25 * (grid.m @ abs(e)**2 + grid.q @ abs(b)**2 +
                            np.sum(grid.wall_scale[:, None] * l * abs(ind)**2))
            qenergy = 2 * np.pi * fit["frequency_Hz"] * stored / power
            name = "q_" + "x".join(map(str, shape)) + "_" + "".join(map(str, mode))
            path = out / (name + ".npz")
            np.savez_compressed(path, shape=shape, lengths_m=LENGTHS, mode=mode,
                                sigma_S_per_m=model["sigma_S_per_m"], R_ohm=r, L_H=l, drive_A=drive,
                                frequency_Hz=frequencies, e_V=es, b_Wb=bs, wall_j_A=js,
                                source_power_W=powers, amplitude=amplitudes, fitted_pole_Hz=fit["pole_Hz"],
                                peak_frequency_Hz=fit["frequency_Hz"], peak_e_V=e, peak_b_Wb=b,
                                peak_wall_j_A=j, peak_source_power_W=power, peak_energy_J=stored,
                                peak_Q_energy=qenergy, continuum_frequency_Hz=fc, continuum_Q=qc)
            row = {"case": name, "shape": list(shape), "mode": list(mode), "frequency_solves": 6,
                   "full_edges": grid.ne, "frequency_Hz": fit["frequency_Hz"],
                   "Q_pole": fit["Q_from_local_pole"], "Q_energy": float(qenergy),
                   "Q_error": abs(fit["Q_from_local_pole"] / qc - 1),
                   "frequency_error": abs(fit["frequency_Hz"] / fc - 1),
                   "five_vs_three_point_Q_difference": abs(fit_center["Q_from_local_pole"] /
                                                           fit["Q_from_local_pole"] - 1),
                   "inverse_fit_residual": fit["inverse_response_fit_residual"],
                   "solve_seconds": sum(times) + duration, "file": path.name, "sha256": sha(path)}
            row["Q_1percent_pass"] = bool(row["Q_error"] < .01)
            row["frequency_1percent_pass"] = bool(row["frequency_error"] < .01)
            rows.append(row)
            print(name, "Q error", row["Q_error"], "frequency error", row["frequency_error"],
                  "solve seconds", row["solve_seconds"], flush=True)
    result = {"method": "unchanged full finite Maxwell; MMD_AT_PLUS_A factor ordering",
              "controls": controls, "cases": rows, "full_frequency_solves": 26,
              "source_sha256": {p: sha(ROOT / p) for p in
                                ["run_copper_q_refinement.py", "finite_copper_cavity.py", "finite_pec_cavity.py"]},
              "model_sha256": sha(modelpath), "copper_long_transient_passed": False,
              "manufactured_cavity_passed": False, "full_joint_simulation_passed": False}
    (out / "summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
