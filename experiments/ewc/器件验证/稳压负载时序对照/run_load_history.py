"""Matched load/history/integration controls, never a substitute for real power."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

import numpy as np

ROOT = Path(__file__).resolve().parent
PRIOR = ROOT.parent / "\u7a33\u538b\u4f5c\u7528\u57df\u5bf9\u7167"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def configurations():
    return [dict(id=f"{load}_{enable}_{method}", load=load, enable=enable, method=method,
                 finish_s=.01, step_s=100e-9, vin_V=5, target_V=3.3)
            for load in ("resistive", "current")
            for enable in ("dc", "cycle") for method in ("gear", "trap")]


def deck(config, library):
    enable = ("3.3" if config["enable"] == "dc" else
              "PWL(0 0 100u 0 101u 3.3 6m 3.3 6.001m 0 7m 0 7.001m 3.3 10m 3.3)")
    # Both loads share exactly the same external activation history and nominal 10 mA.
    control = "PWL(0 0 1.5m 0 1.502m 1 5.998m 1 6m 0 8.5m 0 8.502m 1 10m 1)"
    load = "I=v(load)*v(ctl)/330" if config["load"] == "resistive" else "I=.01*v(ctl)"
    names = ["vin", "out", "cap", "en", "i_up", "i_load", "vyy", "vzz", "vxx",
             "vout_int", "en_ok", "vin_pre", "load_dis", "i_enable", "ctl"]
    vectors = ["v(vin)", "v(out)", "v(cap)", "v(en)", "i(VUP)", "i(VLS)"] + [
        f"v(xu.{node})" for node in names[6:13]] + ["i(VEN)", "v(ctl)"]
    lines = ["Matched regulator load and enable histories; diagnostic fixture",
             f'.include "{library.as_posix()}"', ".param V_OUT=3.3",
             "XU vin 0 en nc out TPS7A20_ADJ_TRANS",
             "VUP upstream 0 5", "RS upstream vin .05", "CIN vin 0 1u", f"VEN en 0 {enable}",
             "RESR out cap .05", "COUT cap 0 1u", "VLS out load 0",
             f"VCTL ctl 0 {control}", f"BLOAD load 0 {load}",
             f".options method={config['method']} maxord=2", ".temp 25", ".control",
             "set wr_singlescale", "set wr_vecnames", "set numdgt=15",
             "tran 100n 10m 0 100n", "wrdata wave.txt " + " ".join(vectors),
             "show s.xu.x_s2.s_s2", "quit", ".endc", ".end"]
    return "\n".join(lines) + "\n", ["time"] + names


def main():
    assert not (ROOT / "summary.json").exists()
    prior = json.loads((PRIOR / "summary.json").read_text(encoding="utf-8"))
    originals = prior["original_files_sha256"]
    for path, digest in originals.items():
        assert sha(Path(path)) == digest, path
    simulator = next(Path(p) for p in originals if p.endswith(".exe"))
    library = next(Path(p) for p in originals if "tps7a20" in p.lower())
    protected = {str(p): sha(p) for p in PRIOR.rglob("*")
                 if p.is_file() and p.suffix in (".json", ".py", ".cir", ".csv", ".log", ".txt")}
    configs = configurations()
    (ROOT / "declared_configurations.json").write_text(json.dumps(configs, indent=2) + "\n", encoding="utf-8")
    cache = Path(tempfile.mkdtemp(prefix="ewc-load-history-"))
    (ROOT / "results").mkdir()

    def execute(config):
        work, output = cache / config["id"], ROOT / "results" / config["id"]
        work.mkdir()
        output.mkdir()
        net, headers = deck(config, library)
        for path in (work / "test.cir", output / "test.cir"):
            path.write_text(net, encoding="ascii")
        (work / ".spiceinit").write_text("set ngbehavior=ps\n", encoding="ascii")
        (output / "spiceinit.txt").write_text("set ngbehavior=ps\n", encoding="ascii")
        record = dict(config, columns=headers, status="blocked")
        try:
            proc = subprocess.run([str(simulator), "-b", "test.cir"], cwd=work,
                                  capture_output=True, text=True, errors="replace", timeout=240)
            (output / "solver.log").write_text(proc.stdout + "\n" + proc.stderr, encoding="utf-8")
            record["returncode"] = proc.returncode
            assert proc.returncode == 0 and (work / "wave.txt").exists(), "No waveform"
            rows = np.loadtxt(work / "wave.txt", skiprows=1)
            assert rows.ndim == 2 and rows.shape[1] == len(headers) and np.isfinite(rows).all()
            assert np.all(np.diff(rows[:, 0]) > 0)
            record["last_time_s"] = float(rows[-1, 0])
            assert rows[-1, 0] >= config["finish_s"] - 1e-12, "Incomplete transient"
            np.savetxt(output / "wave.csv", rows, delimiter=",", fmt="%.15e",
                       header=",".join(headers), comments="")
            window = (rows[:, 0] >= .0097) & (rows[:, 0] <= .0099)
            error = float(abs(rows[window, 2] - 3.3).max())
            record.update(rows=len(rows), output_error_V=error,
                          output_min_V=float(rows[:, 2].min()), output_max_V=float(rows[:, 2].max()),
                          status="pass" if error < .0495 else "fail")
        except Exception as error:
            record["reason"] = str(error) or type(error).__name__
            if isinstance(error, subprocess.TimeoutExpired):
                text = error.stdout or b""
                if isinstance(text, bytes):
                    text = text.decode("utf-8", errors="replace")
                (output / "solver.log").write_text(text + "\nTIMEOUT\n", encoding="utf-8")
        if (work / "wave.txt").exists() and "rows" not in record:
            record["partial_waveform"] = dict(path=str(work / "wave.txt"),
                                             sha256=sha(work / "wave.txt"), accepted=False)
        record["files_sha256"] = {p.relative_to(ROOT).as_posix(): sha(p) for p in output.iterdir()}
        return record

    with ThreadPoolExecutor(max_workers=3) as pool:
        runs = list(pool.map(execute, configs))
    assert all(sha(Path(p)) == h for p, h in protected.items())
    result = dict(runs=runs, original_files_sha256=originals, previous_evidence_sha256=protected,
                  declared_configurations_sha256=sha(ROOT / "declared_configurations.json"),
                  runner_sha256=sha(Path(__file__)), cache=str(cache), vendor_library_edits=False,
                  current_load_is_ideal_unbounded=True, enable_control_is_ideal=True,
                  supported_pspice_verified=False, startup_safety_passed=False,
                  full_power_passed=False, full_joint_passed=False)
    (ROOT / "summary.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    for run in runs:
        print(run["id"], run["status"], run.get("output_error_V", run.get("reason")),
              run.get("output_min_V"))


if __name__ == "__main__":
    main()
