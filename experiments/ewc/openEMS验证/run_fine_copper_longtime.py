"""Actual 4-million-step fine-grid run, immutable chunks and resumable progress."""
import hashlib
import json
from pathlib import Path
import time

import numpy as np
from threadpoolctl import threadpool_limits

from copper_midpoint_cg import MidpointCG
from finite_copper_cavity import CopperCavity

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "\u7ec6\u7f51\u683c\u94dc\u81548us\u957f\u65f6\u7ed3\u679c"
CONFIG = dict(shape=[36, 23, 14], mode=[0, 1, 1], lengths_m=[.16, .10, .06],
              dt_s=2e-12, duration_s=8e-6, steps=4000000, chunk_steps=50000,
              sample_stride=10, cg_rtol=1e-13, maxiter=80, terms=64)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def advance_chunk(solver, seed, e, b, state, heat, start, end, stride):
    if start < 0 or end <= start or stride < 1 or int(stride) != stride:
        raise ValueError("Nonempty forward chunk and positive integer sampling stride required")
    grid, dt = solver.grid, solver.dt
    estart, bstart, istart = e.copy(), b.copy(), state.copy()
    initial_energy = .5 * (grid.m @ e**2 + grid.q @ b**2 + np.sum(solver.ll * state**2))
    times, energies, heats, probes = [start * dt], [initial_energy], [heat], [float(seed @ (grid.m * e))]
    residuals, counts = [], []
    started = time.perf_counter()
    for n in range(start, end):
        e0, b0, i0 = e, b, state
        e, b, state, j, count, residual = solver.step(e, b, state)
        heat += dt * np.sum(solver.rr * (j[:, None] - (i0 + state) / 2)**2)
        residuals.append(residual); counts.append(count)
        if (n + 1) % stride == 0 or n + 1 == end:
            energy = .5 * (grid.m @ e**2 + grid.q @ b**2 + np.sum(solver.ll * state**2))
            times.append((n + 1) * dt); energies.append(energy); heats.append(heat)
            probes.append(float(seed @ (grid.m * e)))
    return dict(
        start_step=start, end_step=end, dt_s=dt, shape=grid.shape, lengths_m=grid.lengths,
        e_start=estart, b_start=bstart, i_start=istart,
        e_before=e0, b_before=b0, i_before=i0, e_after=e, b_after=b, i_after=state, j_mid=j,
        sample_time_s=np.array(times), total_energy_J=np.array(energies),
        cumulative_wall_heat_J=np.array(heats), modal_e_probe=np.array(probes),
        scaled_residual=np.array(residuals), cg_iterations=np.array(counts),
        stepping_seconds=time.perf_counter() - started)


def main():
    modelpath = ROOT / "\u539a\u94dc\u8fb9\u754c\u57fa\u51c6\u7ed3\u679c/passive_surface_models.json"
    approval_path = ROOT / "\u94dc\u8154CG\u5b8c\u6574\u72b6\u6001\u5bf9\u7167/verification.json"
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    assert approval["independent_full_state_comparison_passed"] is True
    assert approval["cases"][1]["dt_s"] == CONFIG["dt_s"]
    assert approval["cases"][1]["short_window_frequency_Q_pass"] is True
    model = next(row for row in json.loads(modelpath.read_text(encoding="utf-8"))["models"]
                 if row["terms"] == CONFIG["terms"])
    sources = {name: sha(ROOT / name) for name in (
        "run_fine_copper_longtime.py", "copper_midpoint_cg.py",
        "finite_copper_cavity.py", "finite_pec_cavity.py")}
    manifest_path = OUT / "progress.json"
    if OUT.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["configuration"] == CONFIG
        assert manifest["source_sha256"] == sources
        assert manifest["model_sha256"] == sha(modelpath)
        assert manifest["approval_sha256"] == sha(approval_path)
        for row in manifest["chunks"]:
            assert sha(OUT / row["file"]) == row["sha256"]
        if manifest["status"] == "solver_complete_unverified":
            print("Already finished; independent long-time acceptance still required", flush=True)
            return
    else:
        OUT.mkdir()
        manifest = dict(
            scope="actual fine-grid 011 full E/H/64-RL 8 us; not full G1 or system approval",
            configuration=CONFIG, source_sha256=sources, model_sha256=sha(modelpath),
            approval_sha256=sha(approval_path), chunks=[], status="running",
            completed_steps=0, full_long_time_passed=False,
            manufactured_cavity_passed=False, full_joint_simulation_passed=False)
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    with threadpool_limits(limits=1):
        grid = CopperCavity(tuple(CONFIG["shape"]), tuple(CONFIG["lengths_m"]))
        solver = MidpointCG(grid, CONFIG["dt_s"], model["R_ohm"], model["L_H"])
        seed = grid.pec.lift(grid.pec.sample_mode(CONFIG["mode"]))
        if manifest["chunks"]:
            last = manifest["chunks"][-1]
            with np.load(OUT / last["file"]) as data:
                e, b, state = data["e_after"], data["b_after"], data["i_after"]
                heat = float(data["cumulative_wall_heat_J"][-1])
                start = int(data["end_step"])
            assert start == manifest["completed_steps"]
        else:
            e, b, state = seed.copy(), np.zeros(grid.nf), np.zeros_like(solver.rr)
            heat, start = 0., 0
        manifest["status"] = "running"
        try:
            while start < CONFIG["steps"]:
                assert all(sha(ROOT / name) == digest for name, digest in sources.items())
                assert sha(modelpath) == manifest["model_sha256"]
                end = min(start + CONFIG["chunk_steps"], CONFIG["steps"])
                path = OUT / f"steps_{start:07d}_{end:07d}.npz"
                assert not path.exists(), "Unlisted original chunk exists; audit it before resuming"
                data = advance_chunk(solver, seed, e, b, state, heat,
                                     start, end, CONFIG["sample_stride"])
                np.savez_compressed(path, **data)
                energy, cumulative = data["total_energy_J"], data["cumulative_wall_heat_J"]
                manifest["chunks"].append(dict(
                    file=path.name, sha256=sha(path), start_step=start, end_step=end,
                    stepping_seconds=data["stepping_seconds"],
                    max_scaled_residual=float(max(data["scaled_residual"])),
                    max_CG_iterations=int(max(data["cg_iterations"])),
                    global_energy_closure=float(np.max(abs(energy + cumulative - .5)) / .5),
                    final_energy_fraction=float(energy[-1] / .5)))
                manifest["completed_steps"] = end
                manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
                e, b, state = data["e_after"], data["b_after"], data["i_after"]
                heat, start = float(cumulative[-1]), end
                print("Actual long-time steps", start, "/", CONFIG["steps"],
                      "simulated seconds", start * CONFIG["dt_s"],
                      "chunk seconds", data["stepping_seconds"], flush=True)
        except Exception as exc:
            manifest.update(status="failed_preserve_partial", failure_type=type(exc).__name__,
                            failure_message=str(exc))
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            raise
    manifest["status"] = "solver_complete_unverified"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print("Actual 8 us stepping finished; independent verification required", flush=True)


if __name__ == "__main__":
    main()
