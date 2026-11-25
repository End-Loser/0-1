"""Original output-switch subcircuit isolated from the LDO, diagnostic only."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

import numpy as np

ROOT = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    output = ROOT / "switch_boundary"
    assert not output.exists()
    output.mkdir()
    prior = json.loads((ROOT / "summary.json").read_text(encoding="utf-8"))
    originals = prior["original_files_sha256"]
    for path, digest in originals.items():
        assert sha(Path(path)) == digest
    library = next(Path(p) for p in originals if "tps7a20" in p.lower())
    simulator = next(Path(p) for p in originals if p.endswith(".exe"))
    protected = {str(p): sha(p) for p in ROOT.rglob("*") if p.is_file()}
    cache = Path(tempfile.mkdtemp(prefix="ewc-switch-boundary-"))
    configs = [dict(id=name, control_V=value) for name, value in
               (("negative", -.001), ("zero", 0), ("positive_near_zero", 1e-9),
                ("on_threshold", .001), ("above_threshold", .002))]
    (output / "declared_configurations.json").write_text(json.dumps(configs, indent=2) + "\n", encoding="utf-8")
    records = []
    for config in configs:
        work, result = cache / config["id"], output / config["id"]
        work.mkdir()
        result.mkdir()
        lines = ["Original TPS output switch boundary diagnostic",
                 f'.include "{library.as_posix()}"', ".param V_OUT=3.3",
                 f"VCTRL ctrl 0 {config['control_V']}", "VDRIVE drive 0 3.3",
                 "XS ctrl 0 drive out TPS7A20_ADJ_S2", "RLOAD out 0 1000",
                 ".control", "set wr_singlescale", "set wr_vecnames", "set numdgt=15",
                 "listing e", "op", "wrdata point.txt v(ctrl) v(out) i(VDRIVE)",
                 "quit", ".endc", ".end"]
        net = "\n".join(lines) + "\n"
        for path in (work / "test.cir", result / "test.cir"):
            path.write_text(net, encoding="ascii")
        (work / ".spiceinit").write_text("set ngbehavior=ps\n", encoding="ascii")
        (result / "spiceinit.txt").write_text("set ngbehavior=ps\n", encoding="ascii")
        proc = subprocess.run([str(simulator), "-b", "test.cir"], cwd=work,
                              capture_output=True, text=True, errors="replace", timeout=30)
        (result / "solver.log").write_text(proc.stdout + "\n" + proc.stderr, encoding="utf-8")
        record = dict(config, returncode=proc.returncode, status="blocked")
        if proc.returncode == 0 and (work / "point.txt").exists():
            data = np.loadtxt(work / "point.txt", skiprows=1, ndmin=2)
            assert data.shape == (1, 4) and np.isfinite(data).all()
            np.savetxt(result / "point.csv", data, delimiter=",", fmt="%.15e",
                       header="scale,control,out,i_drive", comments="")
            record.update(status="observed", output_V=float(data[0, 2]),
                          inferred_switch_resistance_ohm=float((3.3 - data[0, 2]) / -data[0, 3]))
        record["files_sha256"] = {p.relative_to(output).as_posix(): sha(p) for p in result.iterdir()}
        records.append(record)
    assert all(sha(Path(p)) == h for p, h in protected.items())
    summary = dict(runs=records, original_files_sha256=originals, previous_evidence_sha256=protected,
                   declared_configurations_sha256=sha(output / "declared_configurations.json"),
                   runner_sha256=sha(Path(__file__)), cache=str(cache),
                   scope="isolated_vendor_switch_not_regulator_or_real_chip_acceptance",
                   vendor_library_edits=False, supported_pspice_verified=False,
                   full_power_passed=False, full_joint_passed=False)
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    for record in records:
        print(record["id"], record["status"], record.get("inferred_switch_resistance_ohm"))


if __name__ == "__main__":
    main()
