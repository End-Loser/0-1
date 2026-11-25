"""TI chain holdout under ADL5380 typical offset; not an actual ADL model."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

import numpy as np

from run_adl_dc_holdout import available_commit
from run_c_saved_history64 import archive_rows, sha, save

ROOT = Path(__file__).resolve().parent
BASE = ROOT / "C输出64跳转_显式观测保存"
OUT = ROOT / "ADL偏移典型留出_观测列修正"
CACHE = ROOT.parent.parent / "ewc-solver-cache"


def configurations():
    base = json.loads((BASE / "declared_configurations.json").read_bytes())[0]
    return [dict(base, id=f"offset_{i}", offsets_V=[a, b],
                 offset_scope="ADL5380 Rev.C Table 1, 900 MHz typical only",
                 actual_ADL_model=False, calibration_retuned=False)
            for i, (a, b) in enumerate(((-.01, -.01), (-.01, .01),
                                        (.01, -.01), (.01, .01)))]


def deck(original, config):
    net = original
    for stage, value in enumerate(config["offsets_V"], 1):
        for prefix, sign in (("BP", "+"), ("BN", "-")):
            old = f"{prefix}{stage} s{'p' if prefix == 'BP' else 'n'}gen{stage} 0 V=v(scm){sign}v(raw)/2"
            new = old.replace("v(raw)/2", f"(v(raw)+({value}))/2")
            assert net.count(old) == 1
            net = net.replace(old, new)
    assert "RCFEED cfeed im2 32750" in net
    return net


def column_names(source, run):
    names = source["columns"]
    assert len(names) == 83 and names[0] == "time" and len(set(names)) == 83
    first = BASE / "probe_equivalence_10ps" / run["chunks"][0]["file"]
    with np.load(first, allow_pickle=False) as data:
        assert data["columns"].tolist() == names
    return names


def main():
    assert not OUT.exists(), "Preserve evidence; no implicit rerun"
    admission = BASE / "probe_admission.json"
    assert json.loads(admission.read_bytes())["all_original_observables_passed"]
    sources = json.loads((BASE / "running_source.json").read_bytes())
    protected = sources["source_sha256"].copy()
    adl_provenance = ROOT / "ADL共模接口留出" / "provenance.json"
    protected.update(json.loads(adl_provenance.read_bytes())["protected_sha256"])
    reference = BASE / "probe_equivalence_10ps" / "run.json"
    prior = json.loads(reference.read_bytes())
    assert prior["status"] == "solver_complete_unverified" and prior["returncode"] == 0
    columns = column_names(sources, prior)
    for name, digest in prior["files_sha256"].items():
        protected[str(reference.parent / name)] = digest
    for path in (reference, admission, ROOT / "run_adl_dc_holdout.py",
                 Path(__file__), ROOT / "verify_adl_offset_holdout.py",
                 ROOT / "test_adl_offset_holdout.py",
                 adl_provenance):
        protected[str(path)] = sha(path)
    for name in ("startup_column_failure.json", "runner_before_column_binding_fix.py",
                 "verifier_before_output_path_fix.py", "declared_configurations.json"):
        path = ROOT / "ADL偏移典型留出" / name
        protected[str(path)] = sha(path)
    assert all(sha(Path(p)) == h for p, h in protected.items())
    OUT.mkdir()
    configs = configurations()
    save(OUT / "declared_configurations.json", configs)
    save(OUT / "running_source.json", dict(
        source_sha256=protected, columns=columns,
        source_model="Ideal signed offset sources; original TI macro models",
        real_rails=False, actual_ADL_model=False, worst_case_guarantee=False,
        configuration_sha256=sha(OUT / "declared_configurations.json")))
    original = (reference.parent / "test.cir").read_text(encoding="ascii")
    simulator = next(Path(p) for p in protected if p.endswith(".exe"))
    cache = Path(tempfile.mkdtemp(prefix="ewc-adl-offset-", dir=CACHE))
    runs = []
    for config in configs:
        assert all(sha(Path(p)) == h for p, h in protected.items())
        if available_commit() <= 4 * 1024**3 or shutil.disk_usage(cache).free <= 8 * 1024**3:
            save(OUT / "resource_stop.json", dict(
                completed_runs=runs, next_configuration=config,
                reason="Commit or disk admission failed before next solver call"))
            return
        folder, work = OUT / config["id"], cache / config["id"]
        folder.mkdir()
        work.mkdir()
        net = deck(original, config)
        for path in (folder / "test.cir", work / "test.cir"):
            with path.open("x", encoding="ascii") as stream:
                stream.write(net)
        for path in (folder / "spiceinit.txt", work / ".spiceinit"):
            with path.open("x", encoding="ascii") as stream:
                stream.write("set ngbehavior=ps\n")
        run = dict(config, status="failed_preserve_partial", columns=columns)
        try:
            proc = subprocess.run([str(simulator), "-b", "test.cir"], cwd=work,
                                  capture_output=True, timeout=config["solver_timeout_s"])
            stdout, stderr = proc.stdout, proc.stderr
            run["returncode"] = proc.returncode
        except subprocess.TimeoutExpired as error:
            stdout, stderr = error.stdout or b"", error.stderr or b""
            run["reason"] = "Solver timeout; all generated output retained"
        (folder / "solver_stdout.log").write_bytes(stdout)
        (folder / "solver_stderr.log").write_bytes(stderr)
        if (work / "wave.txt").exists():
            wave = work / "wave.txt"
            rows, chunks, error = archive_rows(wave, folder, columns)
            run.update(rows=rows, chunks=chunks, raw_ascii_path=str(wave),
                       raw_ascii_sha256=sha(wave))
            if error:
                run["reason"] = error
            elif chunks:
                run["observed_last_time_s"] = chunks[-1]["last_time_s"]
                if run.get("returncode") == 0 and chunks[-1]["last_time_s"] >= config["duration_s"] - 1e-12:
                    run["status"] = "solver_complete_unverified"
                else:
                    run.setdefault("reason", "Solver exit or insufficient time coverage")
        else:
            run.setdefault("reason", "No waveform")
        run["files_sha256"] = {p.name: sha(p) for p in folder.iterdir()}
        save(folder / "run.json", run)
        runs.append(run)
        print(config["id"], run["status"], run.get("reason"), flush=True)
    save(OUT / "summary.json", dict(runs=runs, cache=str(cache),
                                  actual_ADL_model=False, full_system_gates_closed=0))


if __name__ == "__main__":
    main()
