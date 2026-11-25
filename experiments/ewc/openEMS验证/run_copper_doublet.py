"""Full six-wall Maxwell solves for a 111 doublet and an independent mixed drive."""
import hashlib
import json
from pathlib import Path
import time

import numpy as np
from scipy.sparse import diags
from scipy.sparse.linalg import splu
from threadpoolctl import threadpool_limits

from copper_doublet import reference, discrete_sources, fit_matrix_poles
from finite_copper_cavity import CopperCavity

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "\u94dc\u8154111\u53cc\u6781\u5316\u7ed3\u679c"
LENGTHS = (.16, .10, .06)
SHAPES = ((24, 15, 9), (36, 23, 14))
MIXED = np.array([1., 1j]) / np.sqrt(2)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def solve_drives(grid, frequency, r, l, drives):
    """One unchanged full matrix factorization, three distinct current RHS."""
    _, per_record, total = grid.admittance(frequency, r, l)
    s = 2j * np.pi * frequency
    operator = (grid.stiffness + diags(s * s * grid.m + s * total)).tocsc()
    started = time.perf_counter()
    factor = splu(operator, permc_spec="MMD_AT_PLUS_A")
    e = factor.solve(s * np.asarray(drives, complex))
    b = -(grid.curl @ e) / s
    j = per_record[:, None] * e[grid.wall_edges]
    power = .5 * np.real(np.sum(e.conj() * drives, axis=0))
    return e, b, j, power, time.perf_counter() - started


def main():
    assert not OUT.exists(), "New evidence only; do not overwrite a prior solve"
    model_path = ROOT / "\u539a\u94dc\u8fb9\u754c\u57fa\u51c6\u7ed3\u679c/passive_surface_models.json"
    model = next(row for row in json.loads(model_path.read_text(encoding="utf-8"))["models"]
                 if row["terms"] == 64)
    r, l = np.asarray(model["R_ohm"]), np.asarray(model["L_H"])
    ref = reference(LENGTHS, model["sigma_S_per_m"])
    qref = np.sort(ref["Q"])
    assert 2.5e9 < ref["frequency_Hz"] < 3.5e9
    OUT.mkdir()
    configurations = [dict(shape=list(shape), lengths_m=list(LENGTHS), mode=[1, 1, 1],
                           independent_basis_drives=2, independently_solved_mixed_drive=True,
                           scan_frequencies=5, peak_frequencies=2, terms=64,
                           frequency_Q_limit=.01, three_five_Q_limit=.001)
                      for shape in SHAPES]
    config_path = OUT / "configurations.json"
    config_path.write_text(json.dumps(configurations, indent=2) + "\n", encoding="utf-8")
    summary = dict(
        scope="111 two polarizations and one complex mixed drive; full Maxwell frequency only",
        configuration_sha256=sha(config_path), model_sha256=sha(model_path),
        source_sha256={name: sha(ROOT / name) for name in (
            "run_copper_doublet.py", "copper_doublet.py", "finite_copper_cavity.py", "finite_pec_cavity.py")},
        status="running", cases=[], full_matrix_factorizations=0, full_current_rhs_solves=0,
        doublet_long_transient_passed=False, arbitrary_modes_passed=False,
        general_boundaries_passed=False, manufactured_cavity_passed=False,
        full_joint_simulation_passed=False, full_system_gates_closed=0)

    def checkpoint():
        (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    checkpoint()
    try:
        with threadpool_limits(limits=1):
            for shape in SHAPES:
                assert sha(model_path) == summary["model_sha256"]
                assert all(sha(ROOT / name) == digest for name, digest in summary["source_sha256"].items())
                grid = CopperCavity(shape, LENGTHS)
                seeds, basis_drives, physical_discrete = discrete_sources(grid, ref["basis"])
                drives = np.column_stack((basis_drives, basis_drives @ MIXED))
                fp = np.sqrt(seeds[:, 0] @ (grid.stiffness @ seeds[:, 0])) / (2 * np.pi)
                width = fp / min(qref)
                frequencies = fp - width / 2 + np.linspace(-2, 2, 5) * width
                assert np.all((frequencies > 2.5e9) & (frequencies < 3.5e9))
                fields, magnetic, currents, powers, durations, responses = [], [], [], [], [], []
                stem = "111_" + "x".join(map(str, shape))
                for f in frequencies:
                    e, b, j, power, seconds = solve_drives(grid, f, r, l, drives)
                    fields.append(e); magnetic.append(b); currents.append(j)
                    powers.append(power); durations.append(seconds)
                    responses.append(basis_drives.T @ e[:, :2])
                    summary["full_matrix_factorizations"] += 1
                    summary["full_current_rhs_solves"] += 3
                    print(shape, "111", f, "three full RHS", seconds, flush=True)
                scan = OUT / (stem + "_scan.npz")
                np.savez_compressed(
                    scan, shape=shape, mode=[1, 1, 1], lengths_m=LENGTHS,
                    sigma_S_per_m=model["sigma_S_per_m"], R_ohm=r, L_H=l,
                    reference_basis=ref["basis"], reference_mass=ref["mass"],
                    reference_surface=ref["surface"], reference_Q=qref,
                    reference_frequency_Hz=ref["frequency_Hz"],
                    seeds=seeds, discrete_polarizations=physical_discrete,
                    drives_A=drives, mixed_coefficients=MIXED, frequency_Hz=frequencies,
                    e_V=fields, b_Wb=magnetic, wall_j_A=currents,
                    source_power_W=powers, response=responses, solve_seconds=durations)
                row = dict(shape=list(shape), scan_file=scan.name, scan_sha256=sha(scan),
                           status="scan_saved_unverified")
                summary["cases"].append(row)
                checkpoint()
                fit = fit_matrix_poles(frequencies, responses)
                fit3 = fit_matrix_poles(frequencies[1:4], responses[1:4])
                peak_f = fit["poles_Hz"].real
                pe, pb, pj, pp, elapsed = [], [], [], [], []
                for f in peak_f:
                    e, b, j, power, seconds = solve_drives(grid, f, r, l, drives)
                    pe.append(e); pb.append(b); pj.append(j); pp.append(power); elapsed.append(seconds)
                    summary["full_matrix_factorizations"] += 1
                    summary["full_current_rhs_solves"] += 3
                peak = OUT / (stem + "_peaks.npz")
                np.savez_compressed(
                    peak, frequency_Hz=peak_f, e_V=pe, b_Wb=pb, wall_j_A=pj,
                    source_power_W=pp, fitted_poles_Hz=fit["poles_Hz"],
                    fitted_Q=fit["Q"], fitted_vectors=fit["eigenvectors"],
                    solve_seconds=elapsed)
                qe = abs(fit["Q"] / qref - 1)
                fe = abs(peak_f / ref["frequency_Hz"] - 1)
                windows = abs(fit3["Q"] / fit["Q"] - 1)
                row.update(
                    peak_file=peak.name, peak_sha256=sha(peak), status="solver_complete_unverified",
                    poles_Hz=[dict(real=float(p.real), imag=float(p.imag)) for p in fit["poles_Hz"]],
                    Q=fit["Q"].tolist(), reference_Q=qref.tolist(),
                    Q_relative_error=qe.tolist(), frequency_relative_error=fe.tolist(),
                    three_five_Q_difference=windows.tolist(),
                    inverse_matrix_fit_residual=fit["inverse_fit_residual"],
                    matrix_factorizations=7, independent_current_rhs_solves=21,
                    solve_seconds=float(sum(durations) + sum(elapsed)),
                    selected_doublet_frequency_Q_passed=bool(np.all(qe < .01) and np.all(fe < .01)
                                                            and np.all(windows < .001)))
                checkpoint()
                print(shape, "Q errors", qe, "frequency errors", fe, flush=True)
        summary["status"] = "solver_complete_unverified"
        checkpoint()
    except Exception as error:
        summary["status"] = "failed_preserve_partial"
        summary["failure"] = repr(error)
        checkpoint()
        raise


if __name__ == "__main__":
    main()
