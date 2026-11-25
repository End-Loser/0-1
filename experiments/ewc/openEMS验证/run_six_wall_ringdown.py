"""Causal six-wall midpoint ringdown; deliberately coarse spatial control."""

import hashlib
import json
from pathlib import Path
import time

import numpy as np
from scipy.sparse import diags
from scipy.sparse.linalg import splu

from finite_copper_cavity import CopperCavity

ROOT = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def simulate(dt, duration, r, l):
    grid = CopperCavity((4, 3, 2), (.16, .10, .06))
    seed = grid.pec.lift(grid.pec.sample_mode((0, 1, 1)))
    edges, scale = grid.wall_edges, grid.wall_scale[:, None]
    rr, ll = scale * r, scale * l
    bk = 1 / (1 + dt * r / l / 2)
    ak = 1 - bk
    rb = rr * bk
    effective = np.sum(rb, axis=1)
    conductance = np.zeros(grid.ne)
    np.add.at(conductance, edges, 1 / effective)
    factor = splu((diags(2 * grid.m / dt + conductance) +
                    dt / 2 * grid.stiffness).tocsc(), permc_spec="MMD_AT_PLUS_A")
    e, b, state = seed.copy(), np.zeros(grid.nf), np.zeros_like(rr)
    steps = int(round(duration / dt))
    sample = max(1, int(round(20e-12 / dt)))
    snapshot_steps = set(map(int, np.linspace(0, steps - 1, 41)))
    t, energy, loss, probe = [0.], [.5], [0.], [float(seed @ (grid.m * e))]
    snapshots = {k: [] for k in ["step", "e_before", "b_before", "i_before",
                                "e_after", "b_after", "i_after", "j_mid"]}
    cumulative = 0.
    max_growth = 0.
    initial = .5
    old_energy = initial
    started = time.perf_counter()
    for n in range(steps):
        memory = np.sum(rb * state, axis=1)
        rhs = 2 * grid.m / dt * e + grid.curl.T @ (grid.q * b)
        correction = np.zeros(grid.ne)
        np.add.at(correction, edges, memory / effective)
        emid = factor.solve(rhs - correction)
        jmid = (emid[edges] + memory) / effective
        imid = bk * state + ak * jmid[:, None]
        enew, bnew, inew = 2 * emid - e, b - dt * (grid.curl @ emid), 2 * imid - state
        cumulative += dt * np.sum(rr * (jmid[:, None] - imid)**2)
        if n in snapshot_steps:
            for key, value in zip(snapshots, [n, e, b, state, enew, bnew, inew, jmid]):
                snapshots[key].append(np.array(value, copy=True))
        e, b, state = enew, bnew, inew
        if (n + 1) % sample == 0 or n + 1 == steps:
            u = .5 * (grid.m @ e**2 + grid.q @ b**2 + np.sum(ll * state**2))
            max_growth = max(max_growth, u - old_energy)
            old_energy = u
            t.append((n + 1) * dt); energy.append(u); loss.append(cumulative)
            probe.append(float(seed @ (grid.m * e)))
    return {"shape": grid.shape, "lengths_m": grid.lengths, "dt_s": dt,
            "duration_s": duration, "R_ohm": r, "L_H": l, "sample_time_s": np.array(t),
            "total_energy_J": np.array(energy), "cumulative_wall_heat_J": np.array(loss),
            "modal_e_probe": np.array(probe), "max_sample_energy_growth_J": max_growth,
            "elapsed_s": time.perf_counter() - started,
            **{"snapshot_" + k: np.array(v) for k, v in snapshots.items()}}


def decay_metrics(data):
    t, u, probe = data["sample_time_s"], data["total_energy_J"], data["modal_e_probe"]
    sampled_dt = np.median(np.diff(t))
    spectrum = abs(np.fft.rfft(probe[1:] * np.hanning(len(probe) - 1)))
    f = np.fft.rfftfreq(len(probe) - 1, sampled_dt)
    candidate = np.flatnonzero((f > 2.5e9) & (f < 3e9))
    index = candidate[np.argmax(spectrum[candidate])]
    shift = .5 * (np.log(spectrum[index - 1]) - np.log(spectrum[index + 1])) / (
        np.log(spectrum[index - 1]) - 2 * np.log(spectrum[index]) + np.log(spectrum[index + 1]))
    observed_frequency = f[index] + shift * (f[1] - f[0])
    fits = []
    for end in (5e-6, 8e-6):
        keep = (t >= 1e-6) & (t <= end)
        slope, intercept = np.polyfit(t[keep], np.log(u[keep]), 1)
        residual = np.sqrt(np.mean((np.log(u[keep]) - slope * t[keep] - intercept)**2))
        fits.append({"end_s": end, "Q_energy_decay": float(-2 * np.pi * observed_frequency / slope),
                     "log_energy_RMS_residual": float(residual)})
    return {"observed_frequency_Hz": float(observed_frequency), "decay_fits": fits,
            "energy_closure": float(max(abs(u + data["cumulative_wall_heat_J"] - u[0])) / u[0]),
            "final_energy_fraction": float(u[-1] / u[0]),
            "max_sample_energy_growth_fraction": float(data["max_sample_energy_growth_J"] / u[0])}


def main():
    out = ROOT / "六壁长时衰减结果"
    out.mkdir(exist_ok=True)
    modelpath = ROOT / "厚铜边界基准结果" / "passive_surface_models.json"
    model = next(m for m in json.loads(modelpath.read_text(encoding="utf-8"))["models"] if m["terms"] == 64)
    archive = ROOT / "六面铜壁频域结果" / "copper_4x3x2_011_m64.npz"
    with np.load(archive) as d:
        pole = complex(d["fitted_pole_Hz"])
    rows = []
    for dt in (5e-12, 20e-12):
        data = simulate(dt, 8e-6, np.array(model["R_ohm"]), np.array(model["L_H"]))
        metrics = decay_metrics(data)
        mapped = 2 / dt * np.arctan(2 * np.pi * pole * dt / 2)
        expected_q = mapped.real / (2 * mapped.imag)
        row = {"dt_s": dt, **metrics, "continuous_grid_Q": pole.real / (2 * pole.imag),
               "midpoint_predicted_Q": float(expected_q),
               "relative_Q_vs_continuous_grid": abs(metrics["decay_fits"][-1]["Q_energy_decay"] /
                                                   (pole.real / (2 * pole.imag)) - 1),
               "relative_Q_vs_midpoint_mapping": abs(metrics["decay_fits"][-1]["Q_energy_decay"] / expected_q - 1),
               "elapsed_s": data["elapsed_s"], "steps": int(round(8e-6 / dt))}
        path = out / (f"ringdown_{dt * 1e12:g}ps.npz")
        np.savez_compressed(path, **data)
        row.update({"file": path.name, "sha256": sha(path)})
        rows.append(row)
        print("Ringdown", dt, metrics, "elapsed", data["elapsed_s"], flush=True)
    summary = {"scope": "coarse 4x3x2 causal six-wall 011 ringdown, not spatial accuracy",
               "cases": rows, "frequency_archive_sha256": sha(archive), "model_sha256": sha(modelpath),
               "source_sha256": {p: sha(ROOT / p) for p in ["run_six_wall_ringdown.py",
                                                          "finite_copper_cavity.py", "finite_pec_cavity.py"]},
               "fine_grid_transient_Q_passed": False, "arbitrary_modes_passed": False,
               "manufactured_cavity_passed": False, "full_joint_simulation_passed": False}
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
