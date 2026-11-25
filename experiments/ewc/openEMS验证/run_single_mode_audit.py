"""Measure the single-polarization (0,1,1) mode as an alternative channel.

The [1,1,1] design spends a full Maxwell budget on a two-fold polarization
doublet.  A mode with exactly one zero index has a single, fixed polarization
and no doublet.  This script measures its copper Q with the same six-wall
solver and compares it with the closed-form reference and with the [1,1,1]
doublet already on record.
"""

import hashlib
import json
import time
from pathlib import Path

import numpy as np
from scipy.sparse import diags
from scipy.sparse.linalg import splu
from threadpoolctl import threadpool_limits

from finite_copper_cavity import CopperCavity, continuum_q, fit_pole

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "单极化模校核结果"
MODEL_PATH = ROOT / "厚铜边界基准结果" / "passive_surface_models.json"
LENGTHS = np.array([.16, .10, .06])
CASES = (((24, 15, 9), (0, 1, 1)), ((36, 23, 14), (0, 1, 1)))
REFERENCE_Q_LIMIT = .01


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def solve_one(grid, seed, frequency, r, l):
    s = 2j * np.pi * frequency
    _, _, total = grid.admittance(frequency, r, l)
    operator = (grid.stiffness + diags(s * s * grid.m + s * total)).tocsc()
    started = time.perf_counter()
    factor = splu(operator, permc_spec="MMD_AT_PLUS_A")
    e = factor.solve(s * (grid.m * seed))
    return e, time.perf_counter() - started


def main():
    assert not OUT.exists(), "New evidence only; do not overwrite a prior solve"
    model = next(row for row in json.loads(MODEL_PATH.read_text(encoding="utf-8"))["models"]
                 if row["terms"] == 64)
    r, l = np.asarray(model["R_ohm"]), np.asarray(model["L_H"])
    OUT.mkdir()
    summary = dict(
        scope="single-polarization (0,1,1) copper Q, two full-Maxwell meshes",
        lengths_m=LENGTHS.tolist(), sigma_S_per_m=model["sigma_S_per_m"],
        terms=int(model["terms"]),
        source_sha256={name: sha(ROOT / name) for name in (
            "run_single_mode_audit.py", "finite_copper_cavity.py", "finite_pec_cavity.py")},
        status="running", cases=[])
    checkpoint = lambda: (OUT / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    checkpoint()

    with threadpool_limits(limits=1):
        for shape, mode in CASES:
            grid = CopperCavity(shape, LENGTHS)
            seed = grid.pec.lift(grid.pec.sample_mode(mode))
            pec_lambda = float(seed @ (grid.stiffness @ seed) / (grid.m @ seed**2))
            pec_frequency = float(np.sqrt(pec_lambda) / (2 * np.pi))
            reference_frequency, reference_q = continuum_q(np.array(mode), LENGTHS,
                                                           model["sigma_S_per_m"])
            width = pec_frequency / reference_q
            frequencies = pec_frequency - width / 2 + np.linspace(-2, 2, 5) * width
            amplitudes, seconds = [], []
            for f in frequencies:
                e, elapsed = solve_one(grid, seed, f, r, l)
                amplitudes.append(np.vdot(seed, grid.m * e))
                seconds.append(elapsed)
                print(shape, mode, f, elapsed, flush=True)
            fit = fit_pole(frequencies, amplitudes)
            fit3 = fit_pole(frequencies[1:4], amplitudes[1:4])
            error = abs(fit["Q_from_local_pole"] / reference_q - 1)
            summary["cases"].append(dict(
                shape=list(shape), mode=list(mode), dof=int(grid.ne),
                pec_frequency_Hz=pec_frequency,
                reference_frequency_Hz=float(reference_frequency),
                reference_Q=float(reference_q),
                fitted_pole_Hz=fit["frequency_Hz"], fitted_Q=fit["Q_from_local_pole"],
                Q_relative_error=float(error),
                three_five_Q_difference=float(abs(fit3["Q_from_local_pole"]
                                                   / fit["Q_from_local_pole"] - 1)),
                inverse_response_fit_residual=fit["inverse_response_fit_residual"],
                solve_seconds=float(sum(seconds)),
                passed=bool(error < REFERENCE_Q_LIMIT)))
            checkpoint()
            print(shape, "Q", fit["Q_from_local_pole"], "error", error, flush=True)
    summary["status"] = "solver_complete_unverified"
    checkpoint()
    print(json.dumps(summary["cases"], indent=2), flush=True)


if __name__ == "__main__":
    main()
