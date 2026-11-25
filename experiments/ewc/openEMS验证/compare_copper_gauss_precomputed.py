"""Bounded fine-grid full-state comparison, separate from frozen long runs."""
import hashlib
import json
from pathlib import Path
import time

import numpy as np
from threadpoolctl import threadpool_limits

from copper_gauss_legendre import GaussLegendre
from copper_gauss_precomputed import PrecomputedGaussLegendre
from finite_copper_cavity import CopperCavity
from verify_copper_gauss_state import check_collocation, equations_pass

ROOT = Path(__file__).resolve().parent
STEPS, PAIR_GATE = 32, 1e-10


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    anchor = ROOT / "\u94dc\u8154\u56db\u9636\u53cc\u6781\u5316\u4e0e\u591a\u7d22\u5f158us\u7ed3\u679c" / "111_loss0_2.5ps" / "steps_0000000_0040000.npz"
    model_path = ROOT / "\u539a\u94dc\u8fb9\u754c\u57fa\u51c6\u7ed3\u679c" / "passive_surface_models.json"
    paths = [anchor, model_path, Path(__file__)]
    paths += [ROOT / name for name in (
        "copper_gauss_legendre.py", "copper_gauss_precomputed.py",
        "finite_copper_cavity.py", "verify_copper_gauss_state.py",
        "test_copper_gauss_precomputed.py", "test_copper_gauss_legendre.py")]
    evidence = {str(p): sha(p) for p in paths}
    model = next(x for x in json.loads(model_path.read_bytes())["models"] if x["terms"] == 64)
    with np.load(anchor, allow_pickle=False) as data:
        initial = tuple(data[k].copy() for k in ("e_after", "b_after", "i_after"))
        shape, lengths, dt = tuple(data["shape"]), tuple(data["lengths_m"]), float(data["dt_s"])
    grid = CopperCavity(shape, lengths)
    solvers = [cls(grid, dt, model["R_ohm"], model["L_H"])
               for cls in (GaussLegendre, PrecomputedGaussLegendre)]
    assert (solvers[0].operator != solvers[1].operator).nnz == 0
    states = [tuple(x.copy() for x in initial) for _ in solvers]
    sources = np.zeros((2, grid.ne))
    times, heats, records = [0., 0.], [0., 0.], []
    maximum = np.zeros(7)
    with threadpool_limits(limits=1):
        for n in range(STEPS):
            results, equations = [None, None], [None, None]
            # Alternate evaluation order to reduce a fixed timing-order advantage.
            for index in (range(2) if n % 2 == 0 else reversed(range(2))):
                solver, before = solvers[index], states[index]
                started = time.perf_counter()
                result = solver.step(*before, sources)
                dh = float(dt / 2 * np.sum(
                    solver.rr[None] * (result[6][:, :, None] - result[5])**2))
                times[index] += time.perf_counter() - started
                heats[index] += dh
                metrics = check_collocation(shape, lengths, dt, model["R_ohm"], model["L_H"],
                                            before, result[:3], result[3:7], sources)
                equations[index] = equations_pass(metrics)
                results[index] = result
                states[index] = result[:3]
            errors = [float(np.linalg.norm(a - b) / max(np.linalg.norm(a), 1e-300))
                      for a, b in zip(results[0][:7], results[1][:7])]
            maximum = np.maximum(maximum, errors)
            records.append(dict(step=n + 1, relative_state_and_stage_errors=errors,
                                equations_passed=equations,
                                scaled_residuals=[r[8] for r in results]))
    passed = bool(maximum.max() < PAIR_GATE and
                  all(all(r["equations_passed"]) for r in records))
    content = dict(scope="32 source-off steps from 100 ns, 111_loss0 only; no long-window admission",
                   steps=STEPS, dt_s=dt, shape=list(map(int, shape)), terms=64,
                   pair_gate=PAIR_GATE, maximum_relative_state_and_stage_errors=maximum.tolist(),
                   stepping_and_heat_seconds=times, observed_speed_ratio=times[0] / times[1],
                   heat_J=heats, records=records, evidence_sha256=evidence,
                   local_equivalence_passed=passed, general_initial_state_approved=False,
                   full_long_history_passed=False, full_system_gates_closed=0, goal_complete=False)
    assert all(sha(Path(p)) == digest for p, digest in evidence.items())
    tag = hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()[:12]
    waveform = ROOT / f"gauss_precomputed_pair_{tag}.npz"
    assert not waveform.exists()
    np.savez_compressed(waveform, **{
        f"{label}_{key}": value for label, values in zip(("original", "candidate"), states)
        for key, value in zip(("e_after", "b_after", "i_after"), values)})
    content["final_state_file"] = dict(path=str(waveform), sha256=sha(waveform))
    report = ROOT / f"gauss_precomputed_pair_{tag}.json"
    assert not report.exists()
    report.write_text(json.dumps(content, indent=2) + "\n", encoding="ascii")
    print(json.dumps(dict(report=str(report), local_equivalence_passed=passed,
                          maximum_relative_errors=maximum.tolist(),
                          observed_speed_ratio=content["observed_speed_ratio"]), indent=2))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
