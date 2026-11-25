"""Observe unchanged full vendor LDO fixtures; no repair or acceptance claim."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
PRIOR = ROOT.parent / "\u7a33\u538b\u4f5c\u7528\u57df\u5bf9\u7167"
NAMES = ["switch_control", "switch_terminal", "vout_int", "output_current",
         "shunt_current", "feedback_current", "en_ok"]
VECTORS = ["v(xu.load_dis)", "v(xu.n15528889)", "v(xu.vout_int)",
           "i(v.xu.v_v2)", "i(v.xu.v_v3)", "i(v.xu.v_v4)", "v(xu.en_ok)"]
SHOW = ["show a.xu.x_s2.as_s2", "showmod xu.x_s2:a_s2"]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def configurations():
    return [
        dict(id="original_single_control", finish_s=.01, window_s=[.0097, .0099]),
        dict(id="direct_5Vin_3.3Vout", finish_s=.002, window_s=[.0017, .0019]),
    ]


def instrument(net):
    assert net.count(".control\n") == net.count("wrdata wave.txt ") == 1
    net = net.replace("set numdgt=15\n",
                      "set numdgt=15\nlisting e\nop\n" + "\n".join(SHOW) + "\n")
    lines = net.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("wrdata wave.txt "):
            lines[index] = line + " " + " ".join(VECTORS)
    return "\n".join(lines).replace("\nquit\n", "\n" + "\n".join(SHOW) + "\nquit\n") + "\n"


def main():
    assert not (ROOT / "results").exists(), "Never overwrite a prior batch"
    old = json.loads((PRIOR / "summary.json").read_bytes())
    originals = old["original_files_sha256"]
    for path, digest in originals.items():
        assert sha(Path(path)) == digest, path
    simulator = next(Path(p) for p in originals if p.endswith(".exe"))
    protected = {str(p): sha(p) for p in PRIOR.rglob("*") if p.is_file()}
    protected.update(originals)
    runner_digest = sha(Path(__file__))
    configs = configurations()
    declaration = ROOT / "declared_configurations.json"
    declaration.write_text(json.dumps(configs, indent=2) + "\n", encoding="ascii")
    (ROOT / "results").mkdir()
    cache = Path(tempfile.mkdtemp(prefix="ewc-full-ldo-state-"))
    records = []
    for config in configs:
        base = PRIOR / "results" / config["id"]
        work, result = cache / config["id"], ROOT / "results" / config["id"]
        work.mkdir()
        result.mkdir()
        net = instrument((base / "test.cir").read_text(encoding="ascii"))
        columns = (base / "wave.csv").read_text(encoding="ascii").splitlines()[0].split(",") + NAMES
        for path in (result / "test.cir", work / "test.cir"):
            path.write_text(net, encoding="ascii")
        init = "set ngbehavior=ps\n"
        (work / ".spiceinit").write_text(init, encoding="ascii")
        (result / "spiceinit.txt").write_text(init, encoding="ascii")
        row = dict(config, columns=columns, status="unverified",
                   base_net_sha256=sha(base / "test.cir"))
        try:
            proc = subprocess.run([str(simulator), "-b", "test.cir"], cwd=work,
                                  capture_output=True, text=True, errors="replace", timeout=180)
            row["returncode"] = proc.returncode
            log = proc.stdout + "\n" + proc.stderr
        except subprocess.TimeoutExpired as error:
            row.update(returncode=None, status="timeout")
            log = str(error.stdout) + "\n" + str(error.stderr)
        (result / "solver.log").write_text(log, encoding="utf-8")
        if (work / "wave.txt").is_file():
            shutil.copy2(work / "wave.txt", result / "raw_wave.txt")
        row["files_sha256"] = {p.relative_to(ROOT).as_posix(): sha(p) for p in result.iterdir()}
        records.append(row)
    assert all(sha(Path(p)) == digest for p, digest in protected.items())
    assert sha(Path(__file__)) == runner_digest
    summary = dict(runs=records, protected_files_sha256=protected,
                   declared_configurations_sha256=sha(declaration),
                   runner_sha256=runner_digest, cache=str(cache),
                   scope="unchanged_complete_vendor_LDO_internal_path_observation",
                   vendor_library_edits=False, full_power_passed=False,
                   supported_pspice_verified=False, full_joint_passed=False)
    (ROOT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    for row in records:
        print(row["id"], row["returncode"], list(row["files_sha256"]))


if __name__ == "__main__":
    main()
