"""Separate fourth-order candidate evidence; includes unresolved-fast-state failures."""
import json
import time
from pathlib import Path

import numpy as np
from scipy.linalg import expm
from threadpoolctl import threadpool_limits

from copper_gauss_legendre import GaussLegendre
from finite_copper_cavity import CopperCavity
from run_copper_driven_history import ROOT, LENGTHS, SHAPE, PULSES, sha
from copper_doublet import reference, discrete_sources
from run_copper_driven_history import coefficients
from test_copper_gauss_legendre import uneliminated

OUT = ROOT / "\u94dc\u8154\u56db\u9636\u5b8c\u6574\u72b6\u6001\u5bf9\u7167\u7ed3\u679c"
FILES = ("run_copper_gauss_validation.py", "copper_gauss_legendre.py",
         "test_copper_gauss_legendre.py", "run_copper_driven_history.py",
         "copper_doublet.py", "finite_copper_cavity.py", "finite_pec_cavity.py")


def small_preflight(duration, steps):
    grid = CopperCavity((3, 2, 2), LENGTHS)
    r, l = np.array([.01, .1]), np.array([1e-10, 1e-11])
    rng = np.random.default_rng(423)
    state = (rng.normal(size=grid.ne), rng.normal(size=grid.nf) * 1e-9,
             rng.normal(size=(len(grid.wall_edges), 2)) * .01)
    mass, flux, jmap = uneliminated(grid, r, l)
    n = len(mass)
    generator = (flux[:n, :n] + flux[:n, n:] @ jmap) / mass[:, None]
    energy_weight = np.concatenate((grid.m, grid.q, (grid.wall_scale[:, None] * l).ravel()))
    weight = np.sqrt(energy_weight)
    initial = np.concatenate([a.ravel() for a in state])
    exact = expm(duration * (weight[:, None] * generator / weight[None, :])) @ (weight * initial)
    solver = GaussLegendre(grid, duration / steps, r, l)
    snapshots = {key: [] for key in (
        "e_before", "b_before", "i_before", "e_after", "b_after", "i_after",
        "stage_e", "stage_b", "stage_i", "stage_j")}
    residuals, iterations = [], []
    for _ in range(steps):
        result = solver.step(*state, np.zeros((2, grid.ne)))
        for key, value in zip(snapshots, (*state, *result[:7])):
            snapshots[key].append(value.copy())
        state = result[:3]
        iterations.append(result[7]); residuals.append(result[8])
    final = np.concatenate([a.ravel() for a in state])
    error = np.linalg.norm(weight * final - exact) / np.linalg.norm(exact)
    return dict(shape=[3, 2, 2], lengths_m=LENGTHS, duration_s=duration, dt_s=duration / steps,
                R_ohm=r, L_H=l, energy_weight=energy_weight, initial_full_state=initial,
                scaled_exact_state=exact, final_full_state=final,
                complete_state_energy_norm_error=error, GMRES_iterations=iterations,
                scaled_residual=residuals, **{key: np.array(values) for key, values in snapshots.items()})


def physical_case(config, model):
    grid = CopperCavity(SHAPE, LENGTHS)
    r, l = np.array(model["R_ohm"]), np.array(model["L_H"])
    ref = reference(grid.lengths, model["sigma_S_per_m"])
    physical = ref["basis"] @ ref["loss_rotation"]
    physical /= np.linalg.norm(physical, axis=0)
    doublet, _, _ = discrete_sources(grid, physical)
    seeds = np.column_stack((grid.pec.lift(grid.pec.sample_mode((0, 1, 1))), doublet))
    freqs = np.array([299792458. / 2 * np.linalg.norm(np.array([0, 1, 1]) / grid.lengths),
                      ref["frequency_Hz"], ref["frequency_Hz"]])
    dt, steps = config["dt_s"], round(config["duration_s"] / config["dt_s"])
    stride = round(config["sample_interval_s"] / dt)
    assert steps * dt == config["duration_s"] or abs(steps * dt - config["duration_s"]) < 1e-23
    assert abs(stride * dt - config["sample_interval_s"]) < 1e-24
    solver = GaussLegendre(grid, dt, r, l)
    e = np.zeros(grid.ne) if config["initial_seed"] is None else seeds[:, config["initial_seed"]].copy()
    b, state = np.zeros(grid.nf), np.zeros_like(solver.rr)
    snapshots = {key: [] for key in (
        "step", "e_before", "b_before", "i_before", "e_after", "b_after", "i_after",
        "stage_e", "stage_b", "stage_i", "stage_j", "source_coefficients", "source_currents")}
    chosen = set(np.linspace(0, steps - 1, 9).astype(int))

    def energy():
        return .5 * (grid.m @ e**2 + grid.q @ b**2 + np.sum(solver.ll * state**2))

    samples = [[0., energy(), 0., 0., *(seeds.T @ (grid.m * e))]]
    heat_steps, work_steps, residuals, iterations = [], [], [], []
    heat, work, started = 0., 0., time.perf_counter()
    for step in range(steps):
        coeff = np.array([coefficients((step + c) * dt, freqs, config["pulses"]) for c in solver.nodes])
        sources = (coeff @ seeds.T) * grid.m
        result = solver.step(e, b, state, sources)
        en, bn, sn, es, bs, is_, js, it, residual = result
        dh = dt / 2 * np.sum(solver.rr[None, :, :] * (js[:, :, None] - is_)**2)
        dw = dt / 2 * np.sum(es * sources)
        heat_steps.append(dh); work_steps.append(dw)
        iterations.append(it); residuals.append(residual)
        heat += dh; work += dw
        if step in chosen:
            for key, value in zip(snapshots, (step, e, b, state, en, bn, sn, es, bs, is_, js, coeff, sources)):
                snapshots[key].append(np.array(value, copy=True))
        e, b, state = en, bn, sn
        if (step + 1) % stride == 0:
            samples.append([(step + 1) * dt, energy(), heat, work, *(seeds.T @ (grid.m * e))])
    return dict(shape=SHAPE, lengths_m=LENGTHS, dt_s=dt, duration_s=config["duration_s"],
                R_ohm=r, L_H=l, seeds=seeds, physical_loss_basis=physical, source_frequency_Hz=freqs,
                samples=samples, per_step_heat_J=heat_steps, per_step_source_work_J=work_steps,
                GMRES_iterations=iterations, scaled_residual=residuals,
                stepping_seconds=time.perf_counter() - started,
                **{"snapshot_" + key: np.array(value) for key, value in snapshots.items()})


def main():
    assert not OUT.exists(), "Refuse to overwrite candidate or prior failed evidence"
    modelpath = ROOT / "\u539a\u94dc\u8fb9\u754c\u57fa\u51c6\u7ed3\u679c/passive_surface_models.json"
    model = next(m for m in json.loads(modelpath.read_text(encoding="utf-8"))["models"] if m["terms"] == 64)
    configs = [dict(case=case, initial_seed=seed, dt_s=dt, duration_s=16e-9,
                    sample_interval_s=20e-12, pulses=PULSES if case == "mixed_history" else [])
               for case, seed in (("111_loss0", 1), ("111_loss1", 2), ("mixed_history", None))
               for dt in (20e-12, 10e-12, 5e-12)]
    preflight = [dict(duration_s=t, steps=n) for t, counts in
                 ((80e-12, (8, 16, 32)), (1e-14, (2, 4, 8))) for n in counts]
    OUT.mkdir()
    declaration = dict(shape=list(SHAPE), lengths_m=list(LENGTHS), configurations=configs,
                       preflight=preflight, thresholds=dict(Q=.01, frequency=.01, fit_window_Q=.01,
                       paired_complete_state_energy_norm=.01, source_energy_closure=1e-7),
                       scope="Fourth-order full-state candidate; retains unresolved fast initial-state failure")
    (OUT / "configurations.json").write_text(json.dumps(declaration, indent=2) + "\n", encoding="utf-8")
    summary = dict(status="running", cases=[], preflight=[], configuration_sha256=sha(OUT / "configurations.json"),
                   source_sha256={name: sha(ROOT / name) for name in FILES}, model_sha256=sha(modelpath),
                   general_initial_state_approved=False, full_long_history_passed=False, actual_feed_passed=False,
                   full_joint_simulation_passed=False, full_system_gates_closed=0)

    def save():
        (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    save()
    try:
        with threadpool_limits(limits=1):
            for index, config in enumerate(preflight):
                data = small_preflight(config["duration_s"], config["steps"])
                path = OUT / f"preflight_{index}.npz"
                np.savez_compressed(path, **data)
                summary["preflight"].append(dict(config, file=path.name, sha256=sha(path),
                    reported_full_state_error=float(data["complete_state_energy_norm_error"])))
                save()
            for config in configs:
                print("Starting full Gauss candidate", config["case"], config["dt_s"], flush=True)
                data = physical_case(config, model)
                path = OUT / f"{config['case']}_{config['dt_s'] * 1e12:g}ps.npz"
                np.savez_compressed(path, **data)
                summary["cases"].append(dict(config, file=path.name, sha256=sha(path),
                                             stepping_seconds=float(data["stepping_seconds"])))
                save()
                print("Saved Gauss", path.name, data["stepping_seconds"], flush=True)
        assert sha(modelpath) == summary["model_sha256"]
        assert all(sha(ROOT / name) == digest for name, digest in summary["source_sha256"].items())
        summary["status"] = "solver_complete_unverified"
        save()
    except BaseException as exc:
        summary["status"], summary["failure"] = "failed_preserve_partial", repr(exc)
        save()
        raise


if __name__ == "__main__":
    main()
