"""Recover existing raw evidence, then execute only unattempted offset cases."""
import json
from pathlib import Path
import shutil
import subprocess

import numpy as np

import run_adl_offset_holdout as runner

OUT = runner.OUT


def recover_chunks(wave, folder, names):
    chunks, count, last, monotonic = [], 0, None, True
    with wave.open(encoding="ascii") as stream:
        header = next(stream).strip()
        for i, path in enumerate(sorted(folder.glob("vectors_*.npz"))):
            assert path.name == f"vectors_{i:05d}.npz"
            with np.load(path, allow_pickle=False) as raw:
                assert raw["columns"].tolist() == names
                data = raw["data"]
            assert data.ndim == 2 and data.shape[1] == len(names) and np.isfinite(data).all()
            for row in data:
                line = next(stream)
                observed = np.fromstring(line, sep=" ")
                assert np.array_equal(row, observed), "Retained NPZ differs from raw ASCII"
                monotonic &= last is None or float(row[0]) > last
                last = float(row[0])
                count += 1
            chunks.append(dict(file=path.name, sha256=runner.sha(path), rows=len(data),
                               first_time_s=float(data[0, 0]), last_time_s=float(data[-1, 0])))
        assert not any(line.strip() for line in stream), "Unregistered raw rows"
    return dict(chunks=chunks, rows=count, observed_last_time_s=last,
                time_strictly_monotonic=bool(monotonic), raw_header=header)


def main():
    source = json.loads((OUT / "running_source.json").read_bytes())
    assert all(runner.sha(Path(p)) == h for p, h in source["source_sha256"].items())
    configs = json.loads((OUT / "declared_configurations.json").read_bytes())
    assert runner.sha(OUT / "declared_configurations.json") == source["configuration_sha256"]
    assert not (OUT / "continuation_source.json").exists()
    first_folder = OUT / configs[0]["id"]
    assert not (first_folder / "run.json").exists()
    digest = runner.sha(first_folder / "test.cir")
    matches = [p for p in runner.CACHE.glob("ewc-adl-offset-*/offset_0/test.cir")
               if runner.sha(p) == digest and (p.parent / "wave.txt").exists()]
    assert len(matches) == 1, "Ambiguous or absent original raw cache"
    cache = matches[0].parent.parent
    runner.save(OUT / "continuation_source.json", dict(
        source_sha256={str(Path(__file__)): runner.sha(Path(__file__)),
                       str(runner.ROOT / "test_adl_offset_archive.py"):
                           runner.sha(runner.ROOT / "test_adl_offset_archive.py")},
        original_source_sha256=runner.sha(OUT / "running_source.json"), cache=str(cache),
        first_case_reexecuted=False, first_solver_returncode=None,
        archive_failure="ValueError: too many values to unpack (expected 3)",
        reason="archive_rows returns a dictionary; existing NPZ and ASCII retained",
        solver_calls_before_archive_failure=1))
    wave = matches[0].parent / "wave.txt"
    first = dict(configs[0], columns=source["columns"],
                 status="full_waveform_exitcode_unrecorded", returncode=None,
                 raw_ascii_path=str(wave), raw_ascii_sha256=runner.sha(wave),
                 reason="Original solver returncode was not persisted before archive exception")
    first.update(recover_chunks(wave, first_folder, source["columns"]))
    first["files_sha256"] = {p.name: runner.sha(p) for p in first_folder.iterdir()}
    runner.save(first_folder / "run.json", first)
    runs = [first]
    original = (runner.BASE / "probe_equivalence_10ps" / "test.cir").read_text(encoding="ascii")
    simulator = next(Path(p) for p in source["source_sha256"] if p.endswith(".exe"))
    for config in configs[1:]:
        assert all(runner.sha(Path(p)) == h for p, h in source["source_sha256"].items())
        if runner.available_commit() <= 4 * 1024**3 or shutil.disk_usage(cache).free <= 8 * 1024**3:
            runner.save(OUT / "continuation_resource_stop.json",
                        dict(completed_runs=runs, next_configuration=config))
            return
        folder, work = OUT / config["id"], cache / config["id"]
        assert not folder.exists() and not work.exists(), "No implicit rerun"
        folder.mkdir()
        work.mkdir()
        for path in (folder / "test.cir", work / "test.cir"):
            with path.open("x", encoding="ascii") as stream:
                stream.write(runner.deck(original, config))
        for path in (folder / "spiceinit.txt", work / ".spiceinit"):
            with path.open("x", encoding="ascii") as stream:
                stream.write("set ngbehavior=ps\n")
        run = dict(config, columns=source["columns"], status="failed_preserve_partial")
        try:
            proc = subprocess.run([str(simulator), "-b", "test.cir"], cwd=work,
                                  capture_output=True, timeout=config["solver_timeout_s"])
            run["returncode"] = proc.returncode
            stdout, stderr = proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as error:
            stdout, stderr = error.stdout or b"", error.stderr or b""
            run["returncode"] = None
            run["reason"] = "Solver timeout; evidence retained"
        runner.save(folder / "solver_exit.json", dict(returncode=run["returncode"],
                                                     reason=run.get("reason")))
        (folder / "solver_stdout.log").write_bytes(stdout)
        (folder / "solver_stderr.log").write_bytes(stderr)
        if (work / "wave.txt").exists():
            wave = work / "wave.txt"
            run.update(raw_ascii_path=str(wave), raw_ascii_sha256=runner.sha(wave))
            run.update(runner.archive_rows(wave, folder, source["columns"]))
            if run.get("returncode") == 0 and run["observed_last_time_s"] >= config["duration_s"] - 1e-12:
                run["status"] = "solver_complete_unverified"
            else:
                run.setdefault("reason", "Exit or time coverage incomplete")
        else:
            run.setdefault("reason", "No waveform")
        run["files_sha256"] = {p.name: runner.sha(p) for p in folder.iterdir()}
        runner.save(folder / "run.json", run)
        runs.append(run)
        print(config["id"], run["status"], flush=True)
    runner.save(OUT / "summary.json", dict(
        runs=runs, cache=str(cache), first_solver_returncode_missing=True,
        actual_ADL_model=False, full_system_gates_closed=0))


if __name__ == "__main__":
    main()
