"""Full fine-grid causal state stepping; short windows are not long-time acceptance."""
import hashlib
import json
from pathlib import Path
import time

import numpy as np
from scipy.sparse import diags
from scipy.sparse.linalg import splu

from finite_copper_cavity import CopperCavity

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "\u7ec6\u7f51\u683c\u94dc\u8154\u77ed\u7a97\u7ed3\u679c"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def simulate(shape, mode, dt, duration, r, l):
    grid = CopperCavity(shape, (.16, .10, .06))
    seed = grid.pec.lift(grid.pec.sample_mode(mode))
    e, b = seed.copy(), np.zeros(grid.nf)
    edges = grid.wall_edges
    rr, ll = grid.wall_scale[:, None] * r, grid.wall_scale[:, None] * l
    state = np.zeros_like(rr)
    bk = 1 / (1 + dt * r / l / 2)
    ak = 1 - bk
    rb = rr * bk
    effective = np.sum(rb, axis=1)
    conductance = np.zeros(grid.ne)
    np.add.at(conductance, edges, 1 / effective)
    started = time.perf_counter()
    factor = splu((diags(2 * grid.m / dt + conductance) +
                    dt / 2 * grid.stiffness).tocsc(), permc_spec="MMD_AT_PLUS_A")
    factor_seconds = time.perf_counter() - started
    steps = int(round(duration / dt))
    assert abs(steps * dt - duration) < 1e-20 and steps >= 2
    sample_stride = max(1, int(round(20e-12 / dt)))
    snapshots_at = set(map(int, np.linspace(0, steps - 1, 9)))
    snapshots = {name: [] for name in (
        "step", "e_before", "b_before", "i_before", "e_after",
        "b_after", "i_after", "j_mid")}
    initial = .5 * (grid.m @ e**2)
    times, energies, heats, probes = [0.], [initial], [0.], [float(seed @ (grid.m * e))]
    heat, max_growth, old_energy = 0., 0., initial
    loop_started = time.perf_counter()
    for n in range(steps):
        memory = np.sum(rb * state, axis=1)
        rhs = 2 * grid.m / dt * e + grid.curl.T @ (grid.q * b)
        correction = np.zeros(grid.ne)
        np.add.at(correction, edges, memory / effective)
        emid = factor.solve(rhs - correction)
        jmid = (emid[edges] + memory) / effective
        imid = bk * state + ak * jmid[:, None]
        enew = 2 * emid - e
        bnew = b - dt * (grid.curl @ emid)
        inew = 2 * imid - state
        heat += dt * np.sum(rr * (jmid[:, None] - imid)**2)
        if n in snapshots_at:
            for name, value in zip(snapshots, (n, e, b, state, enew, bnew, inew, jmid)):
                snapshots[name].append(np.array(value, copy=True))
        e, b, state = enew, bnew, inew
        if (n + 1) % sample_stride == 0 or n + 1 == steps:
            energy = .5 * (grid.m @ e**2 + grid.q @ b**2 + np.sum(ll * state**2))
            max_growth = max(max_growth, energy - old_energy)
            old_energy = energy
            times.append((n + 1) * dt); energies.append(energy); heats.append(heat)
            probes.append(float(seed @ (grid.m * e)))
        if (n + 1) % max(1, steps // 4) == 0:
            print("Transient", dt, n + 1, "of", steps, flush=True)
    return dict(
        shape=shape, mode=mode, lengths_m=grid.lengths, dt_s=dt, duration_s=duration,
        R_ohm=r, L_H=l, sample_time_s=np.array(times), total_energy_J=np.array(energies),
        cumulative_wall_heat_J=np.array(heats), modal_e_probe=np.array(probes),
        max_sample_energy_growth_J=max_growth, factor_seconds=factor_seconds,
        stepping_seconds=time.perf_counter() - loop_started,
        **{"snapshot_" + name: np.array(values) for name, values in snapshots.items()})


def main():
    assert not OUT.exists(), "New batches only; preserve existing raw transient evidence"
    modelpath = ROOT / "\u539a\u94dc\u8fb9\u754c\u57fa\u51c6\u7ed3\u679c/passive_surface_models.json"
    reference = ROOT / "\u94dcQ\u7ec6\u5316\u7ed3\u679c/q_36x23x14_011.npz"
    model = next(item for item in json.loads(modelpath.read_text(encoding="utf-8"))["models"]
                 if item["terms"] == 64)
    r, l = np.array(model["R_ohm"]), np.array(model["L_H"])
    configurations = [dict(shape=[36, 23, 14], mode=[0, 1, 1], dt_s=dt, duration_s=16e-9)
                      for dt in (5e-12, 2e-12)]
    OUT.mkdir()
    (OUT / "configurations.json").write_text(
        json.dumps(configurations, indent=2) + "\n", encoding="utf-8")
    summary = dict(
        scope="fine full E/H/RL short-window transient; not 8 us long-time acceptance",
        cases=[], model_sha256=sha(modelpath), frequency_reference_sha256=sha(reference),
        configuration_sha256=sha(OUT / "configurations.json"),
        source_sha256={name: sha(ROOT / name) for name in (
            "run_fine_copper_ringdown.py", "finite_copper_cavity.py", "finite_pec_cavity.py")},
        fine_grid_long_time_passed=False, arbitrary_modes_passed=False,
        manufactured_cavity_passed=False, full_joint_simulation_passed=False)
    for config in configurations:
        data = simulate(tuple(config["shape"]), tuple(config["mode"]), config["dt_s"],
                        config["duration_s"], r, l)
        path = OUT / (f"fine_011_{config['dt_s'] * 1e12:g}ps.npz")
        np.savez_compressed(path, **data)
        energy, heat = data["total_energy_J"], data["cumulative_wall_heat_J"]
        summary["cases"].append(dict(
            config, file=path.name, sha256=sha(path),
            steps=int(round(config["duration_s"] / config["dt_s"])),
            energy_closure=float(np.max(abs(energy + heat - energy[0])) / energy[0]),
            sampled_energy_growth_fraction=float(data["max_sample_energy_growth_J"] / energy[0]),
            final_energy_fraction=float(energy[-1] / energy[0]),
            factor_seconds=data["factor_seconds"], stepping_seconds=data["stepping_seconds"]))
        (OUT / "summary.json").write_text(
            json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print("Fine short window", summary["cases"][-1], flush=True)
    assert sha(modelpath) == summary["model_sha256"]
    assert sha(reference) == summary["frequency_reference_sha256"]


if __name__ == "__main__":
    main()
