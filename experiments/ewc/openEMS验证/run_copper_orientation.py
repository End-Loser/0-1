"""Third electric orientation: full copper-cavity frequency solves, not a BOM."""
import hashlib
import json
from pathlib import Path
import time

import numpy as np

from finite_copper_cavity import CopperCavity, continuum_q, fit_pole
from run_copper_q_refinement import solve

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "\u94dc\u8154\u7b2c\u4e09\u65b9\u5411\u7ed3\u679c"
LENGTHS = (.16, .10, .06)
MODE = (1, 2, 0)
SHAPES = ((24, 15, 9), (36, 23, 14))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    assert not OUT.exists(), "Use a new batch; never overwrite existing solver evidence"
    modelpath = ROOT / "\u539a\u94dc\u8fb9\u754c\u57fa\u51c6\u7ed3\u679c/passive_surface_models.json"
    model = next(row for row in json.loads(modelpath.read_text(encoding="utf-8"))["models"]
                 if row["terms"] == 64)
    r, l = np.array(model["R_ohm"]), np.array(model["L_H"])
    fc, qc = continuum_q(np.array(MODE), np.array(LENGTHS), model["sigma_S_per_m"])
    assert 2.5e9 < fc < 3.5e9
    OUT.mkdir()
    configurations = [dict(shape=list(shape), mode=list(MODE), terms=64,
                           frequency_Q_relative_limit=.01) for shape in SHAPES]
    (OUT / "configurations.json").write_text(
        json.dumps(configurations, indent=2) + "\n", encoding="utf-8")
    summary = dict(
        scope="full six-wall copper 120 mode, third electric orientation; frequency only",
        cases=[], frequency_solves=0, model_sha256=sha(modelpath),
        configuration_sha256=sha(OUT / "configurations.json"),
        source_sha256={name: sha(ROOT / name) for name in (
            "run_copper_orientation.py", "run_copper_q_refinement.py",
            "finite_copper_cavity.py", "finite_pec_cavity.py")},
        fine_grid_long_transient_passed=False, arbitrary_modes_passed=False,
        manufactured_cavity_passed=False, full_joint_simulation_passed=False,
    )
    for shape in SHAPES:
        started = time.perf_counter()
        grid = CopperCavity(shape, LENGTHS)
        seed = grid.pec.lift(grid.pec.sample_mode(MODE))
        drive = grid.m * seed
        fp = np.sqrt(seed @ (grid.stiffness @ seed) / (grid.m @ seed**2)) / (2 * np.pi)
        width = fp / qc
        frequencies = fp - width / 2 + np.linspace(-2, 2, 5) * width
        assert np.all((frequencies > 2.5e9) & (frequencies < 3.5e9))
        es, bs, js, powers, amplitudes = [], [], [], [], []
        for frequency in frequencies:
            e, b, j, power, _, seconds = solve(grid, frequency, r, l, drive)
            es.append(e); bs.append(b); js.append(j); powers.append(power)
            amplitudes.append(np.vdot(drive, e))
            print(shape, MODE, "frequency", frequency, "solve seconds", seconds, flush=True)
        fit = fit_pole(frequencies, np.array(amplitudes))
        fit3 = fit_pole(frequencies[1:4], np.array(amplitudes)[1:4])
        e, b, j, power, _, _ = solve(grid, fit["frequency_Hz"], r, l, drive)
        state = j[:, None] * r / (r + 2j * np.pi * fit["frequency_Hz"] * l)
        energy = .25 * (grid.m @ abs(e)**2 + grid.q @ abs(b)**2 +
                        np.sum(grid.wall_scale[:, None] * l * abs(state)**2))
        qenergy = 2 * np.pi * fit["frequency_Hz"] * energy / power
        path = OUT / ("copper_" + "x".join(map(str, shape)) + "_120.npz")
        np.savez_compressed(
            path, shape=shape, lengths_m=LENGTHS, mode=MODE, R_ohm=r, L_H=l,
            sigma_S_per_m=model["sigma_S_per_m"], drive_A=drive,
            frequency_Hz=frequencies, e_V=es, b_Wb=bs, wall_j_A=js,
            source_power_W=powers, amplitude=amplitudes, fitted_pole_Hz=fit["pole_Hz"],
            peak_frequency_Hz=fit["frequency_Hz"], peak_e_V=e, peak_b_Wb=b,
            peak_wall_j_A=j, peak_source_power_W=power,
            peak_energy_J=energy, peak_Q_energy=qenergy,
            continuum_frequency_Hz=fc, continuum_Q=qc)
        qerror = abs(fit["Q_from_local_pole"] / qc - 1)
        ferror = abs(fit["frequency_Hz"] / fc - 1)
        row = dict(
            shape=list(shape), mode=list(MODE), file=path.name, sha256=sha(path),
            frequency_solves=6, frequency_Hz=fit["frequency_Hz"],
            Q_pole=fit["Q_from_local_pole"], Q_energy=float(qenergy),
            Q_error=float(qerror), frequency_error=float(ferror),
            Q_1percent_pass=bool(qerror < .01),
            frequency_1percent_pass=bool(ferror < .01),
            five_vs_three_Q=float(abs(fit3["Q_from_local_pole"] /
                                       fit["Q_from_local_pole"] - 1)),
            inverse_fit_residual=fit["inverse_response_fit_residual"],
            elapsed_s=time.perf_counter() - started)
        summary["cases"].append(row)
        summary["frequency_solves"] += 6
        (OUT / "summary.json").write_text(
            json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print("Completed", shape, "Q error", qerror, "frequency error", ferror, flush=True)
    assert sha(modelpath) == summary["model_sha256"]


if __name__ == "__main__":
    main()
