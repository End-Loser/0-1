"""Measure unchanged full-state Gauss stepping; no long-window acceptance."""
import argparse
import cProfile
import hashlib
import io
import json
from pathlib import Path
import pstats
import time

import numpy as np
from threadpoolctl import threadpool_limits

from copper_gauss_legendre import GaussLegendre
from finite_copper_cavity import CopperCavity

ROOT = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def measure(steps):
    if not 1 <= steps <= 256:
        raise ValueError("Bounded cost measurement required")
    anchor = ROOT / "\u94dc\u8154\u56db\u9636\u53cc\u6781\u5316\u4e0e\u591a\u7d22\u5f158us\u7ed3\u679c" / "111_loss0_2.5ps" / "steps_0000000_0040000.npz"
    model_path = ROOT / "\u539a\u94dc\u8fb9\u754c\u57fa\u51c6\u7ed3\u679c" / "passive_surface_models.json"
    evidence = {str(p): sha(p) for p in (
        anchor, model_path, ROOT / "copper_gauss_legendre.py",
        ROOT / "finite_copper_cavity.py", Path(__file__))}
    model = next(x for x in json.loads(model_path.read_bytes())["models"] if x["terms"] == 64)
    with np.load(anchor, allow_pickle=False) as data:
        initial = tuple(data[k].copy() for k in ("e_after", "b_after", "i_after"))
        shape, lengths, dt = tuple(data["shape"]), tuple(data["lengths_m"]), float(data["dt_s"])
    grid = CopperCavity(shape, lengths)
    solver = GaussLegendre(grid, dt, model["R_ohm"], model["L_H"])
    sources = np.zeros((2, grid.ne))

    def advance():
        state = tuple(x.copy() for x in initial)
        heat, residual, iterations = 0., 0., 0
        for _ in range(steps):
            result = solver.step(*state, sources)
            is_, js = result[5:7]
            heat += float(dt / 2 * np.sum(solver.rr[None] * (js[:, :, None] - is_)**2))
            residual = max(residual, result[8])
            iterations += result[7]
            state = result[:3]
        return state, heat, residual, iterations

    with threadpool_limits(limits=1):
        start = time.perf_counter()
        final, heat, residual, iterations = advance()
        elapsed = time.perf_counter() - start
        profiler = cProfile.Profile()
        profiler.enable()
        replay, replay_heat, _, _ = advance()
        profiler.disable()
    stream = io.StringIO()
    pstats.Stats(profiler, stream=stream).sort_stats("cumulative").print_stats(25)
    assert all(np.array_equal(a, b) for a, b in zip(final, replay))
    assert heat == replay_heat
    assert all(sha(Path(p)) == digest for p, digest in evidence.items())
    return dict(scope="Full 64-branch state cost measurement from 100 ns anchor; not acceptance",
                shape=list(map(int, shape)), dt_s=dt, steps=steps,
                measured_duration_s=steps * dt, stepping_seconds=elapsed,
                seconds_per_step=elapsed / steps, max_scaled_residual=residual,
                GMRES_iterations=iterations, heat_J=heat,
                exact_replay=True, evidence_sha256=evidence,
                profile=stream.getvalue(), full_long_history_passed=False,
                full_system_gates_closed=0, goal_complete=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--steps", type=int, default=32)
    args = parser.parse_args()
    report = measure(args.steps)
    content = json.dumps(report, indent=2) + "\n"
    path = ROOT / ("gauss_complete_cost_" + hashlib.sha256(content.encode()).hexdigest()[:12] + ".json")
    if path.exists():
        assert path.read_text(encoding="ascii") == content
    else:
        path.write_text(content, encoding="ascii")
    print(json.dumps(dict(report=str(path), seconds_per_step=report["seconds_per_step"],
                          profile=report["profile"]), indent=2), flush=True)
