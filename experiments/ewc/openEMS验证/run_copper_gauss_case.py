"""Run one specified 8 us case in its own directory; chunks identical to the sequential runner.

Purpose: the sequential runner owns a single progress.json and therefore must step its six
configurations one after another on one core. This parallel runner executes exactly one
configuration per process, reusing the unchanged solver modules and the same chunk layout so
each case can be audited independently while the original run continues untouched.
"""
import argparse
import hashlib
import json
import shutil
import time
from pathlib import Path

import numpy as np
from threadpoolctl import threadpool_limits

from copper_gauss_legendre import GaussLegendre
from finite_copper_cavity import CopperCavity
from run_copper_gauss_longtime import FILES, SHAPE, LENGTHS, advance, configurations, sources

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "\u94dc\u8154\u56db\u9636\u5e76\u884c\u5355\u7b97\u4f8b\u7ed3\u679c"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("case", help="configuration id, e.g. 111_loss0_1.25ps")
    args = parser.parse_args()
    config = next((c for c in configurations() if c["id"] == args.case), None)
    assert config is not None, "Unknown case id: " + args.case
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
    declaration_path = ROOT / "\u94dc\u8154\u56db\u9636\u53cc\u6781\u5316\u4e0e\u591a\u7d22\u5f158us\u7ed3\u679c/configurations.json"
    declaration_bytes = declaration_path.read_bytes()
    declaration = json.loads(declaration_bytes)
    assert declaration["configurations"] == configurations(), "Approved configurations differ from the loaded module"
    version = {name: sha(ROOT / name) for name in FILES}
    runner_digest = sha(Path(__file__).resolve())
    case_dir = OUT / config["id"]
    manifest_path = case_dir / "progress.json"
    if case_dir.exists():
        manifest = json.loads(manifest_path.read_bytes())
        assert manifest["case"] == config, "Case declaration differs from the approved configuration"
        assert sha(case_dir / "declaration.json") == manifest["declaration_sha256"]
        assert sha(case_dir / "declaration.json") == sha(declaration_path)
        assert manifest["source_sha256"] == version
        assert manifest["runner_sha256"] == runner_digest
        assert manifest["model_sha256"] == sha(model_path)
        assert manifest["approval_sha256"] == sha(approval_path)
        for row in manifest["chunks"]:
            assert sha(case_dir / row["file"]) == row["sha256"]
        if manifest["status"] == "solver_complete_unverified":
            print("Already stepped; independent long-window acceptance still required", flush=True)
            return
    else:
        case_dir.mkdir(parents=True)
        (case_dir / "declaration.json").write_bytes(declaration_bytes)
        manifest = dict(case=config, status="running", chunks=[], completed_steps=0,
                        source_sha256=version, runner_sha256=runner_digest,
                        model_sha256=sha(model_path), approval_sha256=sha(approval_path),
                        declaration_sha256=sha(case_dir / "declaration.json"),
                        full_long_history_passed=False, general_initial_state_approved=False,
                        actual_feed_passed=False, full_joint_simulation_passed=False,
                        full_system_gates_closed=0)

    def save():
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="ascii")

    save()
    with threadpool_limits(limits=1):
        try:
            steps = round(config["duration_s"] / config["dt_s"])
            if manifest["completed_steps"] == steps:
                manifest["status"] = "solver_complete_unverified"
                save()
                print("Already complete", config["id"], flush=True)
                return
            grid = CopperCavity(tuple(SHAPE), tuple(LENGTHS))
            solver = GaussLegendre(grid, config["dt_s"], model["R_ohm"], model["L_H"])
            seeds, freqs, basis = sources(grid, model)
            if manifest["chunks"]:
                with np.load(case_dir / manifest["chunks"][-1]["file"]) as d:
                    state = tuple(d[key] for key in ("e_after", "b_after", "i_after"))
                    heat, work = map(float, d["samples"][-1, 2:4])
            else:
                initial = np.zeros(grid.ne) if config["initial_seed"] is None else seeds[:, config["initial_seed"]].copy()
                state = initial, np.zeros(grid.nf), np.zeros_like(solver.rr)
                heat = work = 0.
            start = manifest["completed_steps"]
            chunk_steps = round(config["chunk_duration_s"] / config["dt_s"])
            stride = round(config["sample_interval_s"] / config["dt_s"])
            while start < steps:
                assert all(sha(ROOT / name) == value for name, value in version.items())
                assert sha(Path(__file__).resolve()) == runner_digest
                assert sha(model_path) == manifest["model_sha256"]
                assert sha(approval_path) == manifest["approval_sha256"]
                if shutil.disk_usage(case_dir).free < 8 * 1024**3:
                    raise RuntimeError("Less than 8 GiB free; preserve originals and request storage")
                end = min(start + chunk_steps, steps)
                target = case_dir / f"steps_{start:07d}_{end:07d}.npz"
                assert not target.exists(), "Unlisted original chunk; audit before resume"
                data = advance(solver, seeds, freqs, state, heat, work,
                               start, end, stride, config["pulses"])
                data["physical_loss_basis"] = basis
                data["R_ohm"], data["L_H"] = np.array(model["R_ohm"]), np.array(model["L_H"])
                np.savez_compressed(target, **data)
                manifest["chunks"].append(dict(file=target.name, sha256=sha(target),
                    start_step=start, end_step=end, stepping_seconds=data["stepping_seconds"]))
                manifest["completed_steps"] = end
                save()
                state = tuple(data[key] for key in ("e_after", "b_after", "i_after"))
                heat, work = map(float, data["samples"][-1, 2:4])
                start = end
                print(config["id"], start, "/", steps, "actual_s", start * config["dt_s"], flush=True)
            manifest["status"] = "solver_complete_unverified"
            save()
        except Exception as error:
            manifest.update(status="failed_preserve_partial", failure_type=type(error).__name__,
                            failure_message=str(error))
            save()
            raise


if __name__ == "__main__":
    main()
