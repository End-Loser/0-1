"""Declared full-state doublet decay and multi-index impressed-current histories."""
import hashlib
import json
from pathlib import Path
import time

import numpy as np
from threadpoolctl import threadpool_limits

from copper_doublet import reference, discrete_sources
from copper_driven_midpoint import DrivenMidpoint
from finite_copper_cavity import CopperCavity

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "\u94dc\u8154\u5e26\u6e90\u56e0\u679c\u5386\u53f2\u7ed3\u679c"
LENGTHS = (.16, .10, .06)
SHAPE = (36, 23, 14)
PULSES = [
    dict(mode=0, start_s=.5e-9, end_s=2.5e-9, amplitude=1e9, phase_rad=0.),
    dict(mode=1, start_s=2e-9, end_s=4e-9, amplitude=1e9, phase_rad=.3),
    dict(mode=2, start_s=3.5e-9, end_s=5.5e-9, amplitude=1e9, phase_rad=1.1),
    dict(mode=0, start_s=6e-9, end_s=8e-9, amplitude=-.8e9, phase_rad=.7),
    dict(mode=1, start_s=7.5e-9, end_s=10e-9, amplitude=.9e9, phase_rad=2.),
    dict(mode=2, start_s=9.5e-9, end_s=12e-9, amplitude=-1e9, phase_rad=.2),
]
SOURCES = ("run_copper_driven_history.py", "copper_driven_midpoint.py",
           "copper_midpoint_cg.py", "copper_doublet.py",
           "finite_copper_cavity.py", "finite_pec_cavity.py")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def coefficients(t, frequencies, pulses):
    result = np.zeros(3)
    for pulse in pulses:
        start, end = pulse["start_s"], pulse["end_s"]
        if start < t < end:
            envelope = np.sin(np.pi * (t - start) / (end - start))**2
            c = pulse["mode"]
            result[c] += (pulse["amplitude"] * envelope *
                          np.cos(2 * np.pi * frequencies[c] * t + pulse["phase_rad"]))
    return result


def simulate(config, r, l, sigma):
    grid = CopperCavity(tuple(config["shape"]), tuple(config["lengths_m"]))
    ref = reference(grid.lengths, sigma)
    physical = ref["basis"] @ ref["loss_rotation"]
    physical /= np.linalg.norm(physical, axis=0)
    doublet, _, _ = discrete_sources(grid, physical)
    seed011 = grid.pec.lift(grid.pec.sample_mode((0, 1, 1)))
    seeds = np.column_stack((seed011, doublet))
    frequencies = np.array([299792458. / 2 * np.linalg.norm(
        np.array([0, 1, 1]) / grid.lengths), ref["frequency_Hz"], ref["frequency_Hz"]])
    dt = config["dt_s"]
    solver = DrivenMidpoint(grid, dt, r, l)
    e = np.zeros(grid.ne) if config["case"] == "mixed_history" else seeds[:, config["initial_seed"]].copy()
    b, state = np.zeros(grid.nf), np.zeros_like(solver.rr)
    steps = int(round(config["duration_s"] / dt))
    assert abs(steps * dt - config["duration_s"]) < 1e-20
    stride = int(round(config["sample_interval_s"] / dt))
    assert abs(stride * dt - config["sample_interval_s"]) < 1e-24
    snapshot_steps = set(map(int, np.linspace(0, steps - 1, config["snapshots"])))
    snapshots = {key: [] for key in (
        "step", "e_before", "b_before", "i_before", "e_after", "b_after",
        "i_after", "j_mid", "source_coefficients")}

    def energy():
        return .5 * (grid.m @ e**2 + grid.q @ b**2 + np.sum(solver.ll * state**2))

    initial = energy()
    samples = [[0., initial, 0., 0., *(seeds.T @ (grid.m * e))]]
    heat_steps, work_steps, residuals, iterations = [], [], [], []
    heat, work = 0., 0.
    started = time.perf_counter()
    for n in range(steps):
        coeff = coefficients((n + .5) * dt, frequencies, config["pulses"])
        source = grid.m * (seeds @ coeff)
        result = solver.step(e, b, state, source)
        en, bn, sn, j, it, residual = result
        em, im = (e + en) / 2, (state + sn) / 2
        dh = dt * np.sum(solver.rr * (j[:, None] - im)**2)
        dw = dt * (em @ source)
        heat += dh
        work += dw
        heat_steps.append(dh); work_steps.append(dw)
        residuals.append(residual); iterations.append(it)
        if n in snapshot_steps:
            for key, value in zip(snapshots, (n, e, b, state, en, bn, sn, j, coeff)):
                snapshots[key].append(np.array(value, copy=True))
        e, b, state = en, bn, sn
        if (n + 1) % stride == 0:
            samples.append([(n + 1) * dt, energy(), heat, work,
                            *(seeds.T @ (grid.m * e))])
    samples = np.asarray(samples)
    return dict(
        shape=grid.shape if hasattr(grid, "shape") else config["shape"],
        lengths_m=grid.lengths, dt_s=dt, duration_s=config["duration_s"],
        R_ohm=r, L_H=l, physical_loss_basis=physical, seeds=seeds,
        source_frequency_Hz=frequencies, samples=samples,
        per_step_heat_J=heat_steps, per_step_source_work_J=work_steps,
        scaled_residual=residuals, CG_iterations=iterations,
        stepping_seconds=time.perf_counter() - started,
        **{"snapshot_" + key: np.array(values) for key, values in snapshots.items()})


def main():
    assert not OUT.exists(), "Refuse to overwrite any existing batch"
    modelpath = ROOT / "\u539a\u94dc\u8fb9\u754c\u57fa\u51c6\u7ed3\u679c/passive_surface_models.json"
    model = next(m for m in json.loads(modelpath.read_text(encoding="utf-8"))["models"]
                 if m["terms"] == 64)
    configs = [dict(case=case, initial_seed=seed, shape=list(SHAPE), lengths_m=list(LENGTHS),
                    dt_s=dt, duration_s=16e-9, sample_interval_s=20e-12, snapshots=9,
                    pulses=PULSES if case == "mixed_history" else [])
               for case, seed in (("111_loss0", 1), ("111_loss1", 2), ("mixed_history", None))
               for dt in (5e-12, 2e-12)]
    OUT.mkdir()
    declared = dict(configurations=configs, thresholds=dict(
        Q_continuum=.01, frequency_continuum=.01, fit_window_Q=.01,
        paired_complete_state_energy_norm=.01, sampled_source_energy_closure=1e-7),
        scope="16 ns, 64-RL full-state; impressed distributed currents, not actual feed ports")
    (OUT / "configurations.json").write_text(json.dumps(declared, indent=2) + "\n", encoding="utf-8")
    summary = dict(status="running", cases=[], configuration_sha256=sha(OUT / "configurations.json"),
                   model_sha256=sha(modelpath), source_sha256={name: sha(ROOT / name) for name in SOURCES},
                   full_long_history_passed=False, actual_feed_passed=False,
                   manufactured_cavity_passed=False, full_joint_simulation_passed=False,
                   full_system_gates_closed=0)

    def save():
        (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    save()
    try:
        with threadpool_limits(limits=1):
            for config in configs:
                print("Starting", config["case"], config["dt_s"], flush=True)
                data = simulate(config, np.array(model["R_ohm"]), np.array(model["L_H"]),
                                model["sigma_S_per_m"])
                path = OUT / (f"{config['case']}_{config['dt_s'] * 1e12:g}ps.npz")
                np.savez_compressed(path, **data)
                summary["cases"].append(dict(config, file=path.name, sha256=sha(path),
                                             stepping_seconds=float(data["stepping_seconds"])))
                save()
                print("Saved", path.name, data["stepping_seconds"], flush=True)
        assert sha(modelpath) == summary["model_sha256"]
        assert all(sha(ROOT / name) == digest for name, digest in summary["source_sha256"].items())
        summary["status"] = "solver_complete_unverified"
        save()
    except BaseException as exc:
        summary["status"] = "failed_preserve_partial"
        summary["failure"] = repr(exc)
        save()
        raise


if __name__ == "__main__":
    main()
