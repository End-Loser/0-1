"""Separate 1/0.5 ps full-state batch; preserves all failed 5/2 ps evidence."""
import json

import numpy as np
from threadpoolctl import threadpool_limits

from run_copper_driven_history import ROOT, LENGTHS, SHAPE, PULSES, SOURCES, sha, simulate

OUT = ROOT / "\u94dc\u8154\u5e26\u6e90\u56e0\u679c\u5386\u53f2\u7ec6\u6b65\u7ed3\u679c"


def main():
    assert not OUT.exists(), "Separate new batch only; no overwrite or replacement"
    modelpath = ROOT / "\u539a\u94dc\u8fb9\u754c\u57fa\u51c6\u7ed3\u679c/passive_surface_models.json"
    model = next(m for m in json.loads(modelpath.read_text(encoding="utf-8"))["models"]
                 if m["terms"] == 64)
    configs = [dict(case=case, initial_seed=seed, shape=list(SHAPE), lengths_m=list(LENGTHS),
                    dt_s=dt, duration_s=16e-9, sample_interval_s=20e-12, snapshots=9,
                    pulses=PULSES if case == "mixed_history" else [])
               for case, seed in (("111_loss0", 1), ("111_loss1", 2), ("mixed_history", None))
               for dt in (1e-12, .5e-12)]
    OUT.mkdir()
    declaration = dict(configurations=configs, thresholds=dict(
        Q_continuum=.01, frequency_continuum=.01, fit_window_Q=.01,
        paired_complete_state_energy_norm=.01, sampled_source_energy_closure=1e-7),
        scope="same 16 ns and source history, 1/0.5 ps; full-state time refinement, not actual feeds")
    (OUT / "configurations.json").write_text(json.dumps(declaration, indent=2) + "\n", encoding="utf-8")
    source_names = (*SOURCES, "run_copper_driven_refinement.py")
    summary = dict(status="running", cases=[], configuration_sha256=sha(OUT / "configurations.json"),
                   model_sha256=sha(modelpath), source_sha256={name: sha(ROOT / name) for name in source_names},
                   full_long_history_passed=False, actual_feed_passed=False,
                   manufactured_cavity_passed=False, full_joint_simulation_passed=False,
                   full_system_gates_closed=0)

    def save():
        (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    save()
    try:
        with threadpool_limits(limits=1):
            for config in configs:
                print("Starting unchanged-history refinement", config["case"], config["dt_s"], flush=True)
                data = simulate(config, np.array(model["R_ohm"]), np.array(model["L_H"]),
                                model["sigma_S_per_m"])
                path = OUT / (f"{config['case']}_{config['dt_s'] * 1e12:g}ps.npz")
                np.savez_compressed(path, **data)
                summary["cases"].append(dict(config, file=path.name, sha256=sha(path),
                                             stepping_seconds=float(data["stepping_seconds"])))
                save()
                print("Saved refinement", path.name, data["stepping_seconds"], flush=True)
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
