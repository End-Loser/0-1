"""Continuous 64-transition TI carry-pin history, with observation-only probes."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

import numpy as np

ROOT = Path(__file__).resolve().parent
PRIOR = ROOT / "C输出被动反馈"
OUT = ROOT / "C输出64跳转与电流观测"
HOLD, RAMP = 200e-9, 2e-9
NEW_NAMES = ["i_cm1", "i_cm2", "i_refout", "cref"]
NEW_VECTORS = ["i(VCM1)", "i(VCM2)", "i(VROUT)", "v(cref)"]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, indent=2) + "\n")


def history():
    remaining = {i: list(reversed(range(8))) for i in range(8)}
    stack, trail = [0], []
    while stack:
        vertex = stack[-1]
        if remaining[vertex]:
            stack.append(remaining[vertex].pop())
        else:
            trail.append(stack.pop())
    result = list(reversed(trail))
    assert len(result) == 65 and result[0] == result[-1] == 0
    assert set(zip(result, result[1:])) == {(i, j) for i in range(8) for j in range(8)}
    return result


def configurations():
    return [
        dict(id=name, states=states, step_s=step, hold_s=HOLD, ramp_s=RAMP,
             duration_s=len(states) * HOLD, real_rails=False,
             solver_timeout_s=timeout, row_chunk_size=10000,
             reset_between_transitions=False, amplitude_gate_V=.001,
             edge_time_difference_gate_s=20e-12,
             source="Actual C pin; independent input sources and passive raw summation")
        for name, states, step, timeout in (
            ("probe_equivalence_10ps", list(range(8)) + [0], 10e-12, 600),
            ("continuous64_10ps", history(), 10e-12, 3600),
            ("continuous64_5ps", history(), 5e-12, 3600),
        )
    ]


def stimulus(states, bit):
    values = [(0., .3 if states[0] & (1 << bit) else 0.)]
    for i, state in enumerate(states[1:], 1):
        previous = .3 if states[i - 1] & (1 << bit) else 0.
        value = .3 if state & (1 << bit) else 0.
        values.extend(((i * HOLD, previous), (i * HOLD + RAMP, value)))
    values.append((len(states) * HOLD, values[-1][1]))
    return " ".join(f"{t:.15g} {v:.15g}" for t, v in values)


def deck(original, config):
    lines = original.splitlines()
    for source, bit, node in (("VA", 2, "a"), ("VB", 1, "b"), ("VCI", 0, "cin")):
        matches = [i for i, line in enumerate(lines) if line.startswith(source + " ")]
        assert len(matches) == 1
        lines[matches[0]] = f"{source} {node} 0 PWL({stimulus(config['states'], bit)})"
    text = "\n".join(lines) + "\n"
    for stage in (1, 2):
        old = f"XF{stage} ip{stage} im{stage} op{stage} om{stage} cm vs{stage} 0 EWC_FDA"
        new = (f"VCM{stage} cm cm{stage} 0\n"
               f"XF{stage} ip{stage} im{stage} op{stage} om{stage} cm{stage} vs{stage} 0 EWC_FDA")
        assert text.count(old) == 1
        text = text.replace(old, new)
    old = "XREF refvin tempref 0 trimref vref EWC_REF"
    assert text.count(old) == 1
    text = text.replace(old, "VROUT refout vref 0\nXREF refvin tempref 0 trimref refout EWC_REF")
    lines = text.splitlines()
    tran = [i for i, line in enumerate(lines) if line.startswith("tran ")]
    export = [i for i, line in enumerate(lines) if line.startswith("wrdata ")]
    assert len(tran) == len(export) == 1
    lines[tran[0]] = (f"tran {config['step_s']} {config['duration_s']} 0 "
                     f"{config['step_s']}")
    lines[export[0]] += " " + " ".join(NEW_VECTORS)
    return "\n".join(lines) + "\n"


def archive_rows(path, output, names):
    chunks, batch = [], []
    count, previous, monotonic = 0, None, True

    def flush():
        data = np.array(batch)
        target = output / f"vectors_{len(chunks):05d}.npz"
        assert not target.exists()
        np.savez_compressed(target, data=data, columns=np.array(names))
        chunks.append(dict(file=target.name, sha256=sha(target),
                           rows=len(data), first_time_s=float(data[0, 0]),
                           last_time_s=float(data[-1, 0])))
        batch.clear()

    with path.open(encoding="ascii") as stream:
        header = next(stream)
        for line in stream:
            if not line.strip():
                continue
            row = np.fromstring(line, sep=" ")
            assert len(row) == len(names) and np.isfinite(row).all()
            monotonic &= previous is None or row[0] > previous
            previous = float(row[0])
            batch.append(row)
            count += 1
            if len(batch) == 10000:
                flush()
        if batch:
            flush()
    return dict(chunks=chunks, rows=count, observed_last_time_s=previous,
                time_strictly_monotonic=bool(monotonic), raw_header=header.strip())


def main():
    assert not OUT.exists(), "Refuse overwrite of retained history evidence"
    old = json.loads((PRIOR / "summary.json").read_bytes())
    prior_check = json.loads((PRIOR / "verification_945ae03187c3.json").read_bytes())
    protected = {str(ROOT / p): digest for p, digest in prior_check["evidence_sha256"].items()}
    protected.update(old["original_files_sha256"])
    protected[str(ROOT / "verify_c_output_feedback.py")] = prior_check["verifier_sha256"]
    protected[str(Path(__file__))] = sha(Path(__file__))
    assert all(sha(Path(p)) == digest for p, digest in protected.items())
    original = (PRIOR / "actual_C_10ps_diagnostic_rails" / "test.cir").read_text(encoding="ascii")
    names = old["columns"] + NEW_NAMES
    configs = configurations()
    OUT.mkdir()
    cache = Path(tempfile.mkdtemp(prefix="ewc-continuous64-c-"))
    save(OUT / "declared_configurations.json", configs)
    save(OUT / "running_source.json", dict(
        source_sha256=protected, cache=str(cache), columns=names,
        configuration_sha256=sha(OUT / "declared_configurations.json"),
        full_system_gates_closed=0, goal_complete=False))
    simulator = next(Path(p) for p in old["original_files_sha256"] if p.endswith(".exe"))
    runs = []
    for config in configs:
        assert all(sha(Path(p)) == digest for p, digest in protected.items())
        assert min(shutil.disk_usage(OUT).free, shutil.disk_usage(cache).free) > 8 * 1024**3
        work, folder = cache / config["id"], OUT / config["id"]
        work.mkdir()
        folder.mkdir()
        net = deck(original, config)
        for target in (work / "test.cir", folder / "test.cir"):
            with target.open("x", encoding="ascii") as stream:
                stream.write(net)
        for target in (work / ".spiceinit", folder / "spiceinit.txt"):
            with target.open("x", encoding="ascii") as stream:
                stream.write("set ngbehavior=ps\n")
        run = dict(config, status="failed_preserve_partial")
        try:
            proc = subprocess.run([str(simulator), "-b", "test.cir"], cwd=work,
                                  capture_output=True, timeout=config["solver_timeout_s"])
            run["returncode"] = proc.returncode
            (folder / "solver_stdout.log").write_bytes(proc.stdout)
            (folder / "solver_stderr.log").write_bytes(proc.stderr)
        except subprocess.TimeoutExpired as error:
            (folder / "solver_stdout.log").write_bytes(error.stdout or b"")
            (folder / "solver_stderr.log").write_bytes(error.stderr or b"")
            run["reason"] = "Solver timeout, original partial output retained"
        if (work / "wave.txt").exists():
            run["raw_wave"] = dict(path=str(work / "wave.txt"), sha256=sha(work / "wave.txt"))
            try:
                run.update(archive_rows(work / "wave.txt", folder, names))
                if (run.get("returncode") == 0 and run["time_strictly_monotonic"] and
                        run["observed_last_time_s"] >= config["duration_s"] - 1e-12):
                    run["status"] = "solver_complete_unverified"
                else:
                    run.setdefault("reason", "Did not reach declared endpoint with valid time")
            except Exception as error:
                run["reason"] = repr(error)
        else:
            run.setdefault("reason", "No waveform")
        run["files_sha256"] = {p.name: sha(p) for p in folder.iterdir()}
        save(folder / "run.json", run)
        runs.append(run)
        print(run["id"], run["status"], run.get("observed_last_time_s"),
              run.get("reason"), flush=True)
    assert all(sha(Path(p)) == digest for p, digest in protected.items())
    save(OUT / "summary.json", dict(
        runs=runs, columns=names, source_sha256=protected, cache=str(cache),
        configuration_sha256=sha(OUT / "declared_configurations.json"),
        actual_RF_feedback=False, real_rail_acceptance=False,
        cold_start_passed=False, full_power_passed=False,
        G4_closed=False, G5_closed=False, G6_closed=False, G7_closed=False,
        full_system_gates_closed=0, goal_complete=False))


if __name__ == "__main__":
    main()
