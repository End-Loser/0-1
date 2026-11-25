"""Rule-6 compliant 36x23x14 six-face copper frequency scans (Q convergence check).

Separate runner: the historical run_finite_copper_cavity.py summary sha256 is
bound in the ledger section 30.2, so it must not be modified or re-run.
This runner writes to a brand-new directory guarded by `assert not exists`.
"""

import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np

from finite_copper_cavity import CopperCavity, continuum_q, fit_pole

LENGTHS = (.16, .10, .06)
SHAPE = (36, 23, 14)
MODES = ((0, 1, 1), (1, 0, 1))
TERMS = 64


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(args):
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite existing evidence: {args.output}")
    args.output.mkdir(parents=True)
    t_start = time.time()
    models = json.loads(args.models.read_text(encoding="utf-8"))["models"]
    model = next(m for m in models if m["terms"] == TERMS)
    r, l = np.asarray(model["R_ohm"]), np.asarray(model["L_H"])
    rows = []
    for mode in MODES:
        t_case = time.time()
        g = CopperCavity(SHAPE, LENGTHS)
        seed = g.pec.lift(g.pec.sample_mode(mode))
        drive = g.m * seed
        pec_lambda = float(seed @ (g.stiffness @ seed) / (g.m @ (seed**2)))
        pec_frequency = np.sqrt(pec_lambda) / (2 * np.pi)
        reference_frequency, reference_q = continuum_q(
            np.array(mode), np.array(LENGTHS), model["sigma_S_per_m"])
        linewidth = pec_frequency / reference_q
        frequency = pec_frequency - linewidth / 2 + np.linspace(-2, 2, 9) * linewidth
        fields, fluxes, currents, powers, source_powers, impedances, amplitudes = (
            [], [], [], [], [], [], [])
        for f in frequency:
            data = g.solve(f, r, l, drive)
            fields.append(data["e_V"])
            fluxes.append(data["b_Wb"])
            currents.append(data["wall_current_A"])
            powers.append(data["wall_power_W"])
            source_powers.append(data["source_power_W"])
            impedances.append(data["wall_impedance_ohm"])
            amplitudes.append(np.vdot(seed, g.m * data["e_V"]))
        pole = fit_pole(frequency, np.array(amplitudes))
        peak = g.solve(pole["frequency_Hz"], r, l, drive)
        s = 2j * np.pi * pole["frequency_Hz"]
        j = peak["wall_current_A"]
        r_edge = g.wall_scale[:, None] * r
        l_edge = g.wall_scale[:, None] * l
        inductive_current = j[:, None] * r_edge / (r_edge + s * l_edge)
        em_energy = .25 * (g.m @ abs(peak["e_V"])**2 + g.q @ abs(peak["b_Wb"])**2)
        boundary_energy = .25 * np.sum(l_edge * abs(inductive_current)**2)
        q_energy = (2 * np.pi * pole["frequency_Hz"] * (em_energy + boundary_energy)
                    / peak["wall_power_W"])
        case = ("copper_" + "x".join(map(str, SHAPE)) + "_"
                + "".join(map(str, mode)) + f"_m{TERMS}")
        path = args.output / (case + ".npz")
        np.savez_compressed(
            path, shape=SHAPE, lengths_m=LENGTHS, mode_indices=mode, terms=TERMS,
            sigma_S_per_m=model["sigma_S_per_m"], R_ohm=r, L_H=l, drive_A=drive,
            frequency_Hz=frequency, e_V=fields, b_Wb=fluxes, wall_current_A=currents,
            wall_power_W=powers, source_power_W=source_powers,
            wall_impedance_ohm=impedances, modal_amplitude=amplitudes,
            fitted_pole_Hz=pole["pole_Hz"], peak_frequency_Hz=pole["frequency_Hz"],
            peak_e_V=peak["e_V"], peak_b_Wb=peak["b_Wb"],
            peak_wall_current_A=j, peak_wall_power_W=peak["wall_power_W"],
            peak_inductive_current_A=inductive_current,
            em_energy_J=em_energy, boundary_energy_J=boundary_energy, Q_energy=q_energy,
            continuum_frequency_Hz=reference_frequency, continuum_Q=reference_q,
            PEC_discrete_frequency_Hz=pec_frequency)
        frequency_error = abs(pole["frequency_Hz"] / reference_frequency - 1)
        q_error = abs(pole["Q_from_local_pole"] / reference_q - 1)
        row = {"case": case, "shape": list(SHAPE), "mode": list(mode), "terms": TERMS,
               "frequency_solves": 10, "full_edge_count": g.ne,
               "wall_records": len(g.wall_edges), "solve_seconds": time.time() - t_case,
               **{k: v for k, v in pole.items() if k != "pole_Hz"},
               "continuum_Q": reference_q, "Q_energy": float(q_energy),
               "Q_relative_error": q_error, "frequency_relative_error": frequency_error,
               "Q_1percent_pass": bool(q_error < .01),
               "frequency_1percent_pass": bool(frequency_error < .01),
               "file": path.name, "sha256": sha(path)}
        rows.append(row)
        print(case, "Q error", q_error, "frequency error", frequency_error, flush=True)
    summary = {
        "method": "full finite integral Maxwell with six copper surface admittances, frequency domain",
        "purpose": "rule-6 minimum grid (36x23x14) recheck of Q convergence for gates G1",
        "cases": rows, "full_frequency_solves": 10 * len(rows),
        "model_sha256": sha(args.models), "model_path": str(args.models.resolve()),
        "source_sha256": {p: sha(Path(__file__).with_name(p)) for p in
                          ["finite_copper_cavity.py", "finite_pec_cavity.py", Path(__file__).name]},
        "supersedes_nothing": True,
        "prior_coarse_grid": {"directory": "六面铜壁频域结果",
                              "best_Q_relative_error": 0.020462,
                              "best_shape": [24, 15, 9]},
        "total_solve_seconds": time.time() - t_start,
        "finite_copper_frequency_subtask_complete": True,
        "copper_transient_boundary_complete": False, "long_decay_Q_complete": False,
        "arbitrary_corner_accuracy_complete": False, "real_cavity_complete": False,
        "full_joint_simulation_complete": False}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--models", type=Path,
                   default=Path(__file__).parent / "厚铜边界基准结果" / "passive_surface_models.json")
    main(p.parse_args())
