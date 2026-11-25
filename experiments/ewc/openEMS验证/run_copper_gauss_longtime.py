"""Full-state 8 us doublet/source-off histories; immutable resumable chunks."""
import hashlib
import json
from pathlib import Path
import shutil
import time

import numpy as np
from threadpoolctl import threadpool_limits

from copper_doublet import discrete_sources, reference
from copper_gauss_legendre import GaussLegendre
from finite_copper_cavity import CopperCavity
from run_copper_driven_history import coefficients, PULSES

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "\u94dc\u8154\u56db\u9636\u53cc\u6781\u5316\u4e0e\u591a\u7d22\u5f158us\u7ed3\u679c"
FILES = ("run_copper_gauss_longtime.py", "copper_gauss_legendre.py",
         "copper_doublet.py", "run_copper_driven_history.py",
         "finite_copper_cavity.py", "finite_pec_cavity.py")
SHAPE, LENGTHS = [36, 23, 14], [.16, .10, .06]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def configurations():
    return [dict(id=f"{name}_{dt * 1e12:g}ps", case=name, initial_seed=seed,
                 shape=SHAPE, lengths_m=LENGTHS, dt_s=dt, duration_s=8e-6,
                 chunk_duration_s=100e-9, sample_interval_s=20e-12,
                 pulses=PULSES if seed is None else [])
            for name, seed in (("111_loss0", 1), ("111_loss1", 2), ("mixed_history", None))
            for dt in (2.5e-12, 1.25e-12)]


def energy(grid, ll, state):
    e, b, i = state
    return float(.5 * (grid.m @ e**2 + grid.q @ b**2 + np.sum(ll * i**2)))


def advance(solver, seeds, freqs, state, heat, work, start, end, stride, pulses):
    if not 0 <= start < end or stride < 1 or int(stride) != stride:
        raise ValueError("Forward complete chunk and integer stride required")
    grid, dt = solver.grid, solver.dt
    initial = tuple(x.copy() for x in state)
    heat_start, work_start = heat, work
    samples = [[start * dt, energy(grid, solver.ll, state), heat, work,
                *(seeds.T @ (grid.m * state[0]))]]
    snapshots = {key: [] for key in ("step", "e_before", "b_before", "i_before",
        "e_after", "b_after", "i_after", "stage_e", "stage_b", "stage_i",
        "stage_j", "source_coefficients", "source_currents")}
    heats, works, residuals, counts = [], [], [], []
    zero = np.zeros((2, grid.ne))
    pulse_end = max((p["end_s"] for p in pulses), default=0.)
    chosen = {start, end - 1}
    chosen.update(round(t / dt) - 1 for t in np.arange(2, 18, 2) * 1e-9
                  if start <= round(t / dt) - 1 < end)
    started = time.perf_counter()
    for n in range(start, end):
        if n * dt >= pulse_end:
            coeff, sources = np.zeros((2, seeds.shape[1])), zero
        else:
            coeff = np.array([coefficients((n + c) * dt, freqs, pulses) for c in solver.nodes])
            sources = (coeff @ seeds.T) * grid.m
        result = solver.step(*state, sources)
        after, stages = result[:3], result[3:7]
        es, _, is_, js = stages
        dh = float(dt / 2 * np.sum(solver.rr[None] * (js[:, :, None] - is_)**2))
        dw = float(dt / 2 * np.sum(es * sources))
        heats.append(dh); works.append(dw)
        residuals.append(result[8]); counts.append(result[7])
        heat += dh; work += dw
        if n in chosen:
            for key, value in zip(snapshots, (n, *state, *after, *stages, coeff, sources)):
                snapshots[key].append(np.array(value, copy=True))
        state = after
        if (n + 1) % stride == 0 or n == end - 1:
            samples.append([(n + 1) * dt, energy(grid, solver.ll, state), heat, work,
                            *(seeds.T @ (grid.m * state[0]))])
    return dict(start_step=start, end_step=end, shape=grid.shape, lengths_m=grid.lengths,
                dt_s=dt, sample_stride=stride,
                seeds=seeds, source_frequency_Hz=freqs, e_start=initial[0],
                b_start=initial[1], i_start=initial[2],
                e_after=state[0], b_after=state[1], i_after=state[2],
                heat_start_J=heat_start, work_start_J=work_start,
                samples=np.array(samples), per_step_heat_J=np.array(heats),
                per_step_source_work_J=np.array(works), GMRES_iterations=np.array(counts),
                scaled_residual=np.array(residuals), stepping_seconds=time.perf_counter() - started,
                **{"snapshot_" + key: np.array(value) for key, value in snapshots.items()})


def sources(grid, model):
    ref = reference(grid.lengths, model["sigma_S_per_m"])
    basis = ref["basis"] @ ref["loss_rotation"]
    basis /= np.linalg.norm(basis, axis=0)
    doublet, _, _ = discrete_sources(grid, basis)
    seeds = np.column_stack((grid.pec.lift(grid.pec.sample_mode((0, 1, 1))), doublet))
    freqs = [299792458. / 2 * np.linalg.norm(np.array([0, 1, 1]) / grid.lengths),
             ref["frequency_Hz"], ref["frequency_Hz"]]
    return seeds, np.array(freqs), basis


def main():
    model_path = ROOT / "\u539a\u94dc\u8fb9\u754c\u57fa\u51c6\u7ed3\u679c/passive_surface_models.json"
    approval_path = ROOT / "\u94dc\u8154\u56db\u9636\u5b8c\u6574\u72b6\u6001\u5bf9\u7167\u7ed3\u679c/verification_27617c5451c5.json"
    approval = json.loads(approval_path.read_bytes())
    assert approval["independent_discrete_checks_passed"]
    assert approval["all_nine_physical_cases_present"] and approval["all_six_short_pairs_passed"]
    assert approval["general_initial_state_approved"] is False
    for name, digest in approval["summary_snapshot"]["source_sha256"].items():
        assert sha(ROOT / name) == digest
    for row in approval["summary_snapshot"]["cases"]:
        assert sha(approval_path.parent / row["file"]) == row["sha256"]
    model = next(x for x in json.loads(model_path.read_bytes())["models"] if x["terms"] == 64)
    configs = configurations()
    declaration = dict(configurations=configs, terms=64, cg_rtol=1e-13, maxiter=80,
                       thresholds=dict(frequency=.01, Q=.01, complete_state_pair=.01,
                                       energy_closure=1e-7),
                       scope="Specified complete 111/source-off 8 us histories; not general state or real feed")
    version = {name: sha(ROOT / name) for name in FILES}
    if OUT.exists():
        manifest = json.loads((OUT / "progress.json").read_bytes())
        assert json.loads((OUT / "configurations.json").read_bytes()) == declaration
        assert manifest["source_sha256"] == version
        assert manifest["model_sha256"] == sha(model_path)
        assert manifest["approval_sha256"] == sha(approval_path)
        assert manifest["configuration_sha256"] == sha(OUT / "configurations.json")
        for case in manifest["cases"]:
            for row in case["chunks"]:
                assert sha(OUT / case["id"] / row["file"]) == row["sha256"]
        if manifest["status"] == "solver_complete_unverified":
            print("Already stepped; independent long-window acceptance still required", flush=True)
            return
    else:
        OUT.mkdir()
        (OUT / "configurations.json").write_text(json.dumps(declaration, indent=2) + "\n", encoding="ascii")
        manifest = dict(status="running", source_sha256=version, model_sha256=sha(model_path),
                        approval_sha256=sha(approval_path), configuration_sha256=sha(OUT / "configurations.json"),
                        cases=[], full_long_history_passed=False, general_initial_state_approved=False,
                        actual_feed_passed=False, full_joint_simulation_passed=False, full_system_gates_closed=0)

    def save():
        (OUT / "progress.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="ascii")

    save()
    with threadpool_limits(limits=1):
        try:
            for config in configs:
                case = next((x for x in manifest["cases"] if x["id"] == config["id"]), None)
                if case is None:
                    (OUT / config["id"]).mkdir()
                    case = dict(id=config["id"], status="running", chunks=[], completed_steps=0)
                    manifest["cases"].append(case)
                    save()
                steps = round(config["duration_s"] / config["dt_s"])
                if case["completed_steps"] == steps:
                    continue
                grid = CopperCavity(tuple(SHAPE), tuple(LENGTHS))
                solver = GaussLegendre(grid, config["dt_s"], model["R_ohm"], model["L_H"])
                seeds, freqs, basis = sources(grid, model)
                if case["chunks"]:
                    with np.load(OUT / case["id"] / case["chunks"][-1]["file"]) as d:
                        state = tuple(d[key] for key in ("e_after", "b_after", "i_after"))
                        heat, work = map(float, d["samples"][-1, 2:4])
                else:
                    initial = np.zeros(grid.ne) if config["initial_seed"] is None else seeds[:, config["initial_seed"]].copy()
                    state = initial, np.zeros(grid.nf), np.zeros_like(solver.rr)
                    heat = work = 0.
                start = case["completed_steps"]
                chunk_steps = round(config["chunk_duration_s"] / config["dt_s"])
                stride = round(config["sample_interval_s"] / config["dt_s"])
                while start < steps:
                    assert all(sha(ROOT / name) == value for name, value in version.items())
                    assert sha(model_path) == manifest["model_sha256"]
                    assert sha(approval_path) == manifest["approval_sha256"]
                    if shutil.disk_usage(OUT).free < 8 * 1024**3:
                        raise RuntimeError("Less than 8 GiB free; preserve originals and request storage")
                    end = min(start + chunk_steps, steps)
                    target = OUT / case["id"] / f"steps_{start:07d}_{end:07d}.npz"
                    assert not target.exists(), "Unlisted original chunk; audit before resume"
                    data = advance(solver, seeds, freqs, state, heat, work,
                                   start, end, stride, config["pulses"])
                    data["physical_loss_basis"] = basis
                    data["R_ohm"], data["L_H"] = np.array(model["R_ohm"]), np.array(model["L_H"])
                    np.savez_compressed(target, **data)
                    case["chunks"].append(dict(file=target.name, sha256=sha(target),
                        start_step=start, end_step=end, stepping_seconds=data["stepping_seconds"]))
                    case["completed_steps"] = end
                    save()
                    state = tuple(data[key] for key in ("e_after", "b_after", "i_after"))
                    heat, work = map(float, data["samples"][-1, 2:4])
                    start = end
                    print(config["id"], start, "/", steps, "actual_s", start * config["dt_s"], flush=True)
                case["status"] = "solver_complete_unverified"
                save()
        except Exception as error:
            manifest.update(status="failed_preserve_partial", failure_type=type(error).__name__,
                            failure_message=str(error))
            save()
            raise
    manifest["status"] = "solver_complete_unverified"
    save()


if __name__ == "__main__":
    main()
