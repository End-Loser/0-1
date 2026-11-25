"""Save/recheck complete 64-RL startup witnesses; never approve a long run."""
import hashlib
import json
from pathlib import Path

import numpy as np
from threadpoolctl import threadpool_limits

from copper_gauss_legendre import GaussLegendre
from finite_copper_cavity import CopperCavity
from run_copper_gauss_longtime import advance, sources
from verify_copper_gauss_longtime import check_chunk

ROOT = Path(__file__).resolve().parent
BASE = ROOT / "\u94dc\u8154\u957f\u7a97\u5b8c\u6574\u72b6\u6001\u524d\u7f6e\u539f\u4ef6"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    modelpath = ROOT / "\u539a\u94dc\u8fb9\u754c\u57fa\u51c6\u7ed3\u679c/passive_surface_models.json"
    model = next(x for x in json.loads(modelpath.read_bytes())["models"] if x["terms"] == 64)
    files = ("run_copper_longtime_preflight.py", "run_copper_gauss_longtime.py",
             "verify_copper_gauss_longtime.py", "copper_gauss_legendre.py",
             "copper_doublet.py", "finite_copper_cavity.py", "finite_pec_cavity.py")
    version = {name: sha(ROOT / name) for name in files}
    tag = hashlib.sha256(json.dumps(version, sort_keys=True).encode("ascii")).hexdigest()[:12]
    OUT = BASE.with_name(BASE.name + "_" + tag)
    rows = []
    if OUT.exists():
        old = json.loads((OUT / "summary.json").read_bytes())
        assert old["source_sha256"] == version and old["model_sha256"] == sha(modelpath)
    else:
        OUT.mkdir()
        old = None
    with threadpool_limits(limits=1):
        grid = CopperCavity((36, 23, 14), (.16, .10, .06))
        seeds, freqs, _ = sources(grid, model)
        for seed in (1, 2):
            for dt in (2.5e-12, 1.25e-12):
                steps = round(20e-12 / dt)
                config = dict(shape=[36, 23, 14], lengths_m=[.16, .10, .06],
                              dt_s=dt, sample_interval_s=20e-12, initial_seed=seed, pulses=[])
                path = OUT / f"seed{seed}_{dt * 1e12:g}ps.npz"
                if old is None:
                    assert not path.exists()
                    solver = GaussLegendre(grid, dt, model["R_ohm"], model["L_H"])
                    state = seeds[:, seed], np.zeros(grid.nf), np.zeros_like(solver.rr)
                    data = advance(solver, seeds, freqs, state, 0., 0., 0, steps, steps, [])
                    data["R_ohm"], data["L_H"] = np.array(model["R_ohm"]), np.array(model["L_H"])
                    np.savez_compressed(path, **data)
                else:
                    row = next(x for x in old["cases"] if x["file"] == path.name)
                    assert sha(path) == row["sha256"]
                    with np.load(path) as archive:
                        data = {key: archive[key] for key in archive.files}
                check, _ = check_chunk(data, config, seeds, freqs)
                rows.append(dict(file=path.name, sha256=sha(path), configuration=config,
                                 actual_steps=steps, actual_duration_s=20e-12, **check))
    result = dict(cases=rows, model_sha256=sha(modelpath), source_sha256=version,
                  full_state_startup_equations_passed=True, actual_8us_passed=False,
                  general_initial_state_approved=False, real_feed_passed=False,
                  full_joint_passed=False, full_system_gates_closed=0,
                  scope="Four complete-grid 20 ps no-source startup witnesses only")
    content = (json.dumps(result, indent=2) + "\n").encode("ascii")
    summary = OUT / "summary.json"
    if summary.exists():
        assert summary.read_bytes() == content
    else:
        summary.write_bytes(content)
    print("Four raw complete-grid startup witnesses passed; 8 us not approved")


if __name__ == "__main__":
    main()
