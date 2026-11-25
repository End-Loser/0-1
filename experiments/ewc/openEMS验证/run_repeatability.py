"""Repeatability/determinism check for the in-house FIT solver.

The project plan (构想.md, section 5, stage 2) requires recording repeatability,
and section 6 asks whether identical inputs give identical results at different
times. This runner answers only the numerical part: the same case is executed in
separate Python processes at different wall-clock times and the saved arrays are
compared bitwise. It does not claim hardware repeatability, temperature drift or
device-level determinism; those stay capability gaps.

Each case is replayed three times with the thread count pinned to one, plus once
with four threads as a separate environment-variant observation.
"""
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "重复性确定性校核结果"
SHAPE = (16, 10, 6)
LENGTHS = (0.16, 0.10, 0.06)
THREAD_KEYS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
               "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")
CASES = (
    dict(case="mode_011", kind="mode", indices=[0, 1, 1]),
    dict(case="mode_111", kind="mode", indices=[1, 1, 1]),
    dict(case="transient_800ns", kind="transient", dt_s=2e-12, steps=400, seed=541),
)
SAME_ENV_REPLAYS = 3
VARIANT_THREADS = 4


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def array_digest(array):
    array = np.ascontiguousarray(array)
    h = hashlib.sha256()
    h.update(str(array.dtype).encode())
    h.update(str(array.shape).encode())
    h.update(array.tobytes())
    return h.hexdigest()


def thread_env_value():
    return dict((key, os.environ.get(key)) for key in THREAD_KEYS)


def worker(name, out_dir, requested_threads):
    from threadpoolctl import threadpool_limits, threadpool_info

    from finite_pec_cavity import FinitePEC, transient

    spec = next(row for row in CASES if row["case"] == name)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with threadpool_limits(limits=requested_threads):
        started_at = datetime.now(timezone.utc)
        started = time.perf_counter()
        grid = FinitePEC(SHAPE, LENGTHS)
        scalars, arrays = {}, {}
        if spec["kind"] == "mode":
            eigenvalue, vector = grid.solve_mode(spec["indices"])
            scalars.update(eigenvalue_rad2_per_s2=float(eigenvalue),
                           mode_mass_norm=float(np.sqrt(grid.m @ (vector * vector))))
            arrays["mode_e"] = vector
        else:
            result = transient(grid, spec["dt_s"], spec["steps"], seed=spec["seed"])
            arrays.update(e_V=result["e_V"], b_Wb=result["b_Wb"],
                          current_A=result["current_A"], source_work_W=result["source_work_W"])
            scalars.update(final_e_energy_J=float(.5 * grid.m @ result["e_V"][-1] ** 2),
                           final_b_energy_J=float(.5 * np.sum(result["b_Wb"][-1] ** 2 / grid.q)),
                           source_work_total_J=float(np.sum(result["source_work_W"]) * spec["dt_s"]))
        elapsed = time.perf_counter() - started
    np.savez_compressed(out_dir / "arrays.npz", **arrays)
    record = dict(
        case=name, pid=os.getpid(), requested_threads=requested_threads,
        threadpool_info=threadpool_info(), thread_env=thread_env_value(),
        started_at_utc=started_at.isoformat(), ended_at_utc=datetime.now(timezone.utc).isoformat(),
        solve_seconds=elapsed, grid=dict(shape=list(SHAPE), lengths_m=list(LENGTHS),
                                         unknowns=int(grid.ne), faces=int(grid.nf)),
        array_shapes=dict((key, list(value.shape)) for key, value in arrays.items()),
        array_sha256=dict((key, array_digest(value)) for key, value in arrays.items()),
        scalars=scalars,
    )
    (out_dir / "record.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(dict(case=name, pid=record["pid"], solve_seconds=elapsed,
                          array_sha256=record["array_sha256"], scalars=scalars)))


def compare(reference, other):
    if reference.shape != other.shape or reference.dtype != other.dtype:
        return dict(bitwise_identical=False, shape_mismatch=True, max_abs_diff=None,
                    max_rel_diff=None)
    difference = np.abs(reference.astype(np.float64) - other.astype(np.float64))
    scale = float(np.max(np.abs(reference))) if reference.size else 0.
    return dict(bitwise_identical=bool(np.array_equal(reference, other)),
                shape_mismatch=False, max_abs_diff=float(difference.max()) if difference.size else 0.,
                max_rel_diff=float(difference.max() / scale) if scale > 0 else 0.)


def main():
    assert not OUT.exists(), "New evidence only; do not overwrite a prior result"
    OUT.mkdir()
    configuration = dict(
        scope="Same input, separate processes, different wall-clock times",
        evidence_level="A (independent-process replay of the in-house FIT solver)",
        limitation=("Numerical repeatability of the model on this machine only. "
                    "No temperature, supply, fabrication or device-level claim."),
        grid=dict(shape=list(SHAPE), lengths_m=list(LENGTHS)),
        cases=list(CASES),
        plan=[dict(replay=index + 1, requested_threads=1) for index in range(SAME_ENV_REPLAYS)]
             + [dict(replay=SAME_ENV_REPLAYS + 1, requested_threads=VARIANT_THREADS)],
        environment=dict(python=sys.version, executable=sys.executable,
                         numpy=np.__version__, platform=platform.platform(),
                         cpu_count=os.cpu_count()),
    )
    config_path = OUT / "configurations.json"
    config_path.write_text(json.dumps(configuration, indent=2) + "\n", encoding="utf-8")
    summary = dict(
        scope=configuration["scope"], evidence_level=configuration["evidence_level"],
        limitation=configuration["limitation"],
        configuration_sha256=sha(config_path),
        source_sha256=dict(runner=sha(Path(__file__)),
                           finite_pec_cavity=sha(ROOT / "finite_pec_cavity.py")),
        environment=configuration["environment"], cases=[], status="running")
    log_dir = OUT / "子进程日志"
    log_dir.mkdir()

    def checkpoint():
        (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    checkpoint()
    base_env = dict(os.environ)
    for key in THREAD_KEYS:
        base_env[key] = "1"
    for case in CASES:
        name = case["case"]
        entry = dict(case=name, replays=[], comparisons=[], verdict=None)
        summary["cases"].append(entry)
        for replay in configuration["plan"]:
            tag = "replay%d_threads%d" % (replay["replay"], replay["requested_threads"])
            target = OUT / name / tag
            env = dict(base_env)
            for key in THREAD_KEYS:
                env[key] = str(replay["requested_threads"])
            command = [sys.executable, str(Path(__file__).resolve()), "--worker", name,
                       str(target), str(replay["requested_threads"])]
            finished = subprocess.run(command, cwd=str(ROOT), env=env, capture_output=True,
                                      text=True, encoding="utf-8", errors="replace")
            (log_dir / (name + "_" + tag + ".stdout.log")).write_text(finished.stdout,
                                                                      encoding="utf-8")
            (log_dir / (name + "_" + tag + ".stderr.log")).write_text(finished.stderr,
                                                                      encoding="utf-8")
            record_path = target / "record.json"
            record = json.loads(record_path.read_text(encoding="utf-8")) if record_path.exists() else None
            entry["replays"].append(dict(replay=replay["replay"],
                                         requested_threads=replay["requested_threads"],
                                         returncode=finished.returncode,
                                         record=record))
            checkpoint()
        good = [row["record"] for row in entry["replays"] if row["returncode"] == 0 and row["record"]]
        if len(good) == len(entry["replays"]):
            reference = np.load(OUT / name / "replay1_threads1" / "arrays.npz")
            for row in entry["replays"][1:]:
                other = np.load(OUT / name / ("replay%d_threads%d"
                                              % (row["replay"], row["requested_threads"])) / "arrays.npz")
                entry["comparisons"].append(dict(
                    replay=row["replay"], requested_threads=row["requested_threads"],
                    digests_equal=(row["record"]["array_sha256"]
                                   == entry["replays"][0]["record"]["array_sha256"]),
                    arrays=dict((key, compare(reference[key], other[key])) for key in reference.files)))
            same_env = [row for row in entry["comparisons"] if row["requested_threads"] == 1]
            entry["verdict"] = dict(
                same_environment_bitwise_identical=bool(
                    same_env and all(row["digests_equal"]
                                     and all(item["bitwise_identical"] for item in row["arrays"].values())
                                     for row in same_env)),
                same_environment_replays=len(same_env) + 1,
                thread_variant_identical=bool(
                    all(row["digests_equal"] and all(item["bitwise_identical"]
                                                     for item in row["arrays"].values())
                        for row in entry["comparisons"] if row["requested_threads"] != 1)))
        else:
            entry["verdict"] = dict(same_environment_bitwise_identical=False,
                                    same_environment_replays=None,
                                    thread_variant_identical=None,
                                    note="One or more replays failed; see returncode and logs.")
        checkpoint()
    summary["status"] = "complete"
    checkpoint()
    print(json.dumps(dict(status=summary["status"],
                          verdicts=dict((row["case"], row["verdict"]) for row in summary["cases"])),
                     indent=2))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        worker(sys.argv[2], sys.argv[3], int(sys.argv[4]))
    else:
        main()
