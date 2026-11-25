"""Fixed TI chain DC holdout; ideal ADL-informed sources, not an ADL model."""
import ctypes
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

import numpy as np
import pypdfium2

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "ADL共模接口留出"
BASE = ROOT / "C输出64跳转与电流观测_D盘缓存"
CACHE = ROOT.parent.parent / "ewc-solver-cache"


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def save(path, value):
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, indent=2) + "\n")


def configurations():
    return [dict(id=f"cm{int(cm * 1000)}_state{state:03b}", state=state,
                 common_mode_V=cm, analysis="DC operating point",
                 source_model="Ideal independent input sources; no ADL macro",
                 feedback_resistance_ohm=32750, bias_retuned=False,
                 amplitude_gate_V=.001, solver_timeout_s=60)
            for cm in (3.2, 2.5, 3.8) for state in range(8)]


def deck(original, config):
    text = original
    for name, node, shift in (("VA", "a", 2), ("VB", "b", 1), ("VCI", "cin", 0)):
        value = .3 * ((config["state"] >> shift) & 1)
        text, count = re.subn(rf"(?m)^{name} .+$", f"{name} {node} 0 {value}", text)
        assert count == 1
    text, count = re.subn(r"(?m)^VSC .+$",
                         f"VSC scm 0 {config['common_mode_V']}", text)
    assert count == 1
    text, count = re.subn(r"(?m)^tran .+$", "op", text)
    assert count == 1
    assert text.count("wrdata wave.txt ") == 1
    text = text.replace("wrdata wave.txt ", "wrdata dc.txt ")
    assert "RCFEED cfeed im2 32750" in text and ".ic " not in text.lower()
    return text


def available_commit():
    class Status(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
            (name, ctypes.c_ulonglong) for name in
            ("physical", "available_physical", "pagefile", "available_pagefile",
             "virtual", "available_virtual", "extended")]
    status = Status()
    status.length = ctypes.sizeof(status)
    assert ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    return status.available_pagefile


def prepare():
    assert not OUT.exists(), "Refuse overwrite of holdout evidence"
    manifest_path = ROOT / "source_manifest.json"
    source = next(s for s in json.loads(manifest_path.read_bytes())["sources"]
                  if s["id"] == "rx_pdf")
    pdf = Path(source["cache"])
    assert sha(pdf) == source["raw_sha256"]
    frozen = json.loads((BASE / "running_source.json").read_bytes())
    protected = frozen["source_sha256"].copy()
    protected[str(Path(__file__))] = sha(Path(__file__))
    protected[str(manifest_path)] = sha(manifest_path)
    protected[str(pdf)] = sha(pdf)
    original = BASE / "probe_equivalence_10ps" / "test.cir"
    protected[str(original)] = sha(original)
    assert all(sha(Path(p)) == digest for p, digest in protected.items())
    OUT.mkdir()
    with pypdfium2.PdfDocument(pdf) as doc:
        for page in (3, 21):
            with (OUT / f"ADL5380_page_{page}.png").open("xb") as stream:
                doc[page - 1].render(scale=2).to_pil().save(stream, format="PNG")
    save(OUT / "declared_configurations.json", configurations())
    save(OUT / "provenance.json", dict(
        protected_sha256=protected, columns=["op_scale"] + frozen["columns"][1:],
        configuration_sha256=sha(OUT / "declared_configurations.json"),
        original_pdf_url=source["url"], original_pdf_sha256=sha(pdf),
        images_sha256={p.name: sha(p) for p in OUT.glob("*.png")},
        renderer="pypdfium2", render_scale=2,
        original_download_repeated=False, source_tables_reconciled=False,
        table1_open_cm_expression="VS - 1.2 V", table1_VS_V=5,
        table4_open_cm_approx_V=3.2, table4_1540ohm_to_supply_cm_approx_V=2.5,
        manufacturer_ADL_transient_model_available=False,
        real_RF_passed=False, full_system_gates_closed=0))
    print(str(OUT), flush=True)


def run():
    provenance = json.loads((OUT / "provenance.json").read_bytes())
    protected = provenance["protected_sha256"]
    assert all(sha(Path(p)) == digest for p, digest in protected.items())
    declared = OUT / "declared_configurations.json"
    assert sha(declared) == provenance["configuration_sha256"]
    configs = json.loads(declared.read_bytes())
    assert not (OUT / "summary.json").exists()
    assert not any((OUT / c["id"]).exists() for c in configs), "No implicit reruns"
    original_path = BASE / "probe_equivalence_10ps" / "test.cir"
    original = original_path.read_text(encoding="ascii")
    simulator = next(Path(p) for p in protected if p.endswith(".exe"))
    CACHE.mkdir(exist_ok=True)
    cache = Path(tempfile.mkdtemp(prefix="ewc-adl-dc-holdout-", dir=CACHE))
    runs = []
    for config in configs:
        assert all(sha(Path(p)) == digest for p, digest in protected.items())
        if available_commit() < 512 * 1024**2 or shutil.disk_usage(cache).free < 8 * 1024**3:
            save(OUT / "resource_stop.json", dict(
                reason="Insufficient commit or disk before solver call",
                configurations_completed=len(runs), next_configuration=config,
                completed_runs=runs, no_solver_call_for_next_case=True))
            return
        folder, work = OUT / config["id"], cache / config["id"]
        folder.mkdir()
        work.mkdir()
        text = deck(original, config)
        for target in (folder / "test.cir", work / "test.cir"):
            with target.open("x", encoding="ascii") as stream:
                stream.write(text)
        for target in (folder / "spiceinit.txt", work / ".spiceinit"):
            with target.open("x", encoding="ascii") as stream:
                stream.write("set ngbehavior=ps\n")
        record = dict(config, status="failed_preserve_output")
        try:
            proc = subprocess.run([str(simulator), "-b", "test.cir"], cwd=work,
                                  capture_output=True, timeout=config["solver_timeout_s"])
            record["returncode"] = proc.returncode
            stdout, stderr = proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as error:
            stdout, stderr = error.stdout or b"", error.stderr or b""
            record["reason"] = "Solver timeout; original output retained"
        (folder / "solver_stdout.log").write_bytes(stdout)
        (folder / "solver_stderr.log").write_bytes(stderr)
        if (work / "dc.txt").exists():
            shutil.copyfile(work / "dc.txt", folder / "dc.txt")
            try:
                data = np.loadtxt(folder / "dc.txt", skiprows=1, ndmin=2)
                assert data.shape == (1, len(provenance["columns"]))
                assert np.isfinite(data).all()
                if record.get("returncode") == 0:
                    record["status"] = "dc_operating_point_observed_unverified"
            except Exception as error:
                record["reason"] = repr(error)
        else:
            record.setdefault("reason", "No DC vector export")
        record["files_sha256"] = {p.name: sha(p) for p in folder.iterdir()}
        save(folder / "run.json", record)
        runs.append(record)
        print(config["id"], record["status"], flush=True)
    save(OUT / "summary.json", dict(
        runs=runs, cache=str(cache), provenance_sha256=sha(OUT / "provenance.json"),
        no_transient_history=True, actual_ADL_model=False, real_rails=False,
        startup_passed=False, full_power_passed=False, full_system_gates_closed=0))


if __name__ == "__main__":
    if "--prepare" in sys.argv:
        prepare()
    elif "--run" in sys.argv:
        run()
    else:
        raise SystemExit("Use --prepare, inspect original tables, then --run")
