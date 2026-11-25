"""Full-state 16 ns CG/LU comparison before any fine-grid long-time use."""
import hashlib
import json
from pathlib import Path
import time

import numpy as np
from threadpoolctl import threadpool_limits

from copper_midpoint_cg import MidpointCG
from finite_copper_cavity import CopperCavity

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "\u94dc\u8154CG\u5b8c\u6574\u72b6\u6001\u5bf9\u7167"
BASE = ROOT / "\u7ec6\u7f51\u683c\u94dc\u8154\u77ed\u7a97\u7ed3\u679c"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def simulate(shape, mode, dt, duration, r, l):
    grid = CopperCavity(shape, (.16, .10, .06))
    solver = MidpointCG(grid, dt, r, l)
    seed = grid.pec.lift(grid.pec.sample_mode(mode))
    e, b, state = seed.copy(), np.zeros(grid.nf), np.zeros_like(solver.rr)
    steps = int(round(duration / dt))
    stride = int(round(20e-12 / dt))
    snapshot_steps = set(map(int, np.linspace(0, steps - 1, 9)))
    snapshots = {name: [] for name in (
        "step", "e_before", "b_before", "i_before", "e_after", "b_after", "i_after", "j_mid")}
    times, energies, heats, probes = [0.], [.5 * (grid.m @ e**2)], [0.], [1.]
    iterations, residuals = [], []
    heat, max_growth, previous = 0., 0., energies[0]
    started = time.perf_counter()
    for n in range(steps):
        enew, bnew, inew, jmid, count, residual = solver.step(e, b, state)
        imid = (state + inew) / 2
        heat += dt * np.sum(solver.rr * (jmid[:, None] - imid)**2)
        iterations.append(count); residuals.append(residual)
        if n in snapshot_steps:
            for name, value in zip(snapshots, (n, e, b, state, enew, bnew, inew, jmid)):
                snapshots[name].append(np.array(value, copy=True))
        e, b, state = enew, bnew, inew
        if (n + 1) % stride == 0 or n + 1 == steps:
            energy = .5 * (grid.m @ e**2 + grid.q @ b**2 + np.sum(solver.ll * state**2))
            max_growth = max(max_growth, energy - previous)
            previous = energy
            times.append((n + 1) * dt); energies.append(energy); heats.append(heat)
            probes.append(float(seed @ (grid.m * e)))
        if (n + 1) % max(1, steps // 4) == 0:
            print("CG", dt, n + 1, "of", steps, flush=True)
    return dict(
        shape=shape, mode=mode, lengths_m=grid.lengths, dt_s=dt, duration_s=duration,
        R_ohm=r, L_H=l, sample_time_s=np.array(times), total_energy_J=np.array(energies),
        cumulative_wall_heat_J=np.array(heats), modal_e_probe=np.array(probes),
        max_sample_energy_growth_J=max_growth, stepping_seconds=time.perf_counter() - started,
        scaled_residual=np.array(residuals), cg_iterations=np.array(iterations),
        jacobi_scaled_row_radius=solver.max_scaled_row_radius,
        **{"snapshot_" + name: np.array(values) for name, values in snapshots.items()})


def main():
    assert not OUT.exists(), "Do not overwrite any previous solver comparison"
    baseline = json.loads((BASE / "summary.json").read_text(encoding="utf-8"))
    assert len(baseline["cases"]) == 2
    for name, digest in baseline["source_sha256"].items():
        assert sha(ROOT / name) == digest
    modelpath = ROOT / "\u539a\u94dc\u8fb9\u754c\u57fa\u51c6\u7ed3\u679c/passive_surface_models.json"
    assert sha(modelpath) == baseline["model_sha256"]
    model = next(item for item in json.loads(modelpath.read_text(encoding="utf-8"))["models"]
                 if item["terms"] == 64)
    OUT.mkdir()
    configurations = [dict(shape=row["shape"], mode=row["mode"], dt_s=row["dt_s"],
                           duration_s=row["duration_s"], cg_rtol=1e-13, maxiter=80)
                      for row in baseline["cases"]]
    (OUT / "configurations.json").write_text(
        json.dumps(configurations, indent=2) + "\n", encoding="utf-8")
    summary = dict(
        scope="full same-physics E/H/64-RL state comparison, CG versus original LU",
        cases=[], model_sha256=sha(modelpath), LU_summary_sha256=sha(BASE / "summary.json"),
        configuration_sha256=sha(OUT / "configurations.json"),
        source_sha256={name: sha(ROOT / name) for name in (
            "copper_midpoint_cg.py", "run_copper_cg_validation.py",
            "finite_copper_cavity.py", "finite_pec_cavity.py")},
        full_long_time_passed=False, manufactured_cavity_passed=False,
        full_joint_simulation_passed=False)
    with threadpool_limits(limits=1):
        for config, original in zip(configurations, baseline["cases"]):
            archive = BASE / original["file"]
            assert sha(archive) == original["sha256"]
            data = simulate(tuple(config["shape"]), tuple(config["mode"]),
                            config["dt_s"], config["duration_s"],
                            np.array(model["R_ohm"]), np.array(model["L_H"]))
            path = OUT / original["file"]
            np.savez_compressed(path, **data)
            summary["cases"].append(dict(
                config, file=path.name, sha256=sha(path), LU_file=original["file"],
                LU_sha256=sha(archive), steps=len(data["cg_iterations"]),
                max_scaled_residual=float(max(data["scaled_residual"])),
                max_CG_iterations=int(max(data["cg_iterations"])),
                mean_CG_iterations=float(np.mean(data["cg_iterations"])),
                stepping_seconds=data["stepping_seconds"],
                LU_stepping_seconds=original["stepping_seconds"]))
            (OUT / "summary.json").write_text(
                json.dumps(summary, indent=2) + "\n", encoding="utf-8")
            print(summary["cases"][-1], flush=True)


if __name__ == "__main__":
    main()
