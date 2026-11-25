"""Run bounded tests of an unchanged, publicly downloaded TI macro model."""

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tempfile
import zipfile

import numpy as np
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent
CACHE = Path(tempfile.gettempdir()) / "ewc-device-audit-20261118"
EXPECTED = "4e4345dc2929ee91a99a0b5437369c32b3817e9c8673a7ae90310d5485a81ba9"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def crossing(time, values, level, rising, start, stop):
    for i in range(1, len(time)):
        if not start <= time[i] <= stop:
            continue
        a, b = values[i - 1] - level, values[i] - level
        if (a < 0 <= b) if rising else (a > 0 >= b):
            return float(time[i - 1] + (time[i] - time[i - 1]) * (-a) / (b - a))
    return None


def deck(case, model):
    supply = case["supply_V"]
    midpoint = supply / 2
    od = case["overdrive_V"]
    stimulus = (
        f"VIN inp 0 {midpoint + .1}\n"
        f"VSH shdn 0 PWL(0 0 500n 0 500.1n {supply} 700n {supply} 700.1n 0)"
        if case["kind"] == "shutdown" else
        f"VIN inp 0 PULSE({midpoint - (.1 - od)} {midpoint + od} "
        "200n 100p 100p 120n 240n)\nVSH shdn 0 0"
    )
    return f"""TI TLV3501 unchanged macro: bounded interface test
.include "{model.as_posix()}"
VS vcc 0 {supply}
VREF inn 0 {midpoint}
{stimulus}
XDUT inp inn shdn vcc 0 out TLV3501
RL out 0 100k
CL out 0 {case["load_F"]}
.temp 25
.control
set wr_singlescale
set wr_vecnames
tran {case["max_step_s"]} 950n 0 {case["max_step_s"]}
wrdata wave.txt v(inp) v(inn) v(out) v(shdn) i(VS)
quit
.endc
.end
"""


def run_case(case, simulator, model):
    directory = CACHE / "runs" / case["id"]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / ".spiceinit").write_text(
        "set ngbehavior=ps\n" if case["mode"] == "ps" else "", encoding="ascii")
    netlist = directory / "test.cir"
    netlist.write_text(deck(case, model), encoding="ascii")
    result = dict(case)
    result["netlist"] = str(netlist)
    result["netlist_sha256"] = digest(netlist)
    try:
        process = subprocess.run([str(simulator), "-b", str(netlist)],
                                 cwd=directory, capture_output=True, text=True,
                                 errors="replace", timeout=60)
        text = process.stdout + "\n" + process.stderr
        logfile = ROOT / "results" / (case["id"] + ".log")
        logfile.write_text(text, encoding="utf-8")
        result.update(returncode=process.returncode,
                      log=logfile.relative_to(ROOT).as_posix(),
                      log_sha256=digest(logfile))
        wave = directory / "wave.txt"
        # quit may return zero after failed analysis: require numerical evidence.
        if not wave.is_file() or "No. of Data Rows" not in text:
            result["status"] = "blocked"
            result["reason"] = "No successful transient dataset"
            return result
        data = np.loadtxt(wave, skiprows=1)
        if data.ndim != 2 or data.shape[1] != 6 or not np.isfinite(data).all():
            raise ValueError("Unexpected or nonfinite waveform")
        target = ROOT / "results" / (case["id"] + ".csv")
        np.savetxt(target, data, delimiter=",", fmt="%.12e",
                   header="time_s,inp_V,inn_V,out_V,shdn_V,supply_current_A",
                   comments="")
        result.update(wave=target.relative_to(ROOT).as_posix(),
                      wave_sha256=digest(target), rows=len(data))
        time, inp, inn, out, shdn, current = data.T
        if case["kind"] == "shutdown":
            mean = lambda lo, hi: float(np.mean(out[(time >= lo) & (time <= hi)]))
            result["measurements"] = {
                "active_before_V": mean(400e-9, 450e-9),
                "disabled_pulldown_V": mean(640e-9, 680e-9),
                "active_after_V": mean(850e-9, 900e-9)}
            m = result["measurements"]
            ok = (m["active_before_V"] > .7 * case["supply_V"] and
                  m["disabled_pulldown_V"] < .3 * case["supply_V"] and
                  m["active_after_V"] > .7 * case["supply_V"])
        else:
            tin = crossing(time, inp - inn, 0, True, 195e-9, 210e-9)
            tout = crossing(time, out, case["supply_V"] / 2, True, 200e-9, 230e-9)
            tf_in = crossing(time, inp - inn, 0, False, 315e-9, 330e-9)
            tf_out = crossing(time, out, case["supply_V"] / 2, False, 320e-9, 350e-9)
            high = float(np.mean(out[(time > 260e-9) & (time < 300e-9)]))
            low = float(np.mean(out[(time > 380e-9) & (time < 410e-9)]))
            result["measurements"] = {
                "high_V": high, "low_V": low,
                "rise_delay_s": None if tin is None or tout is None else tout - tin,
                "fall_delay_s": None if tf_in is None or tf_out is None else tf_out - tf_in,
                "active_supply_current_A": float(np.mean(
                    -current[(time > 260e-9) & (time < 300e-9)]))}
            m = result["measurements"]
            ok = (high > .7 * case["supply_V"] and low < .3 * case["supply_V"] and
                  m["rise_delay_s"] is not None and
                  m["fall_delay_s"] is not None and
                  0 < m["rise_delay_s"] < 30e-9 and
                  0 < m["fall_delay_s"] < 30e-9)
        result["status"] = "pass" if ok else "fail"
    except Exception as exc:
        result.update(status="blocked", reason=f"{type(exc).__name__}: {exc}")
    return result


def main(simulator):
    (ROOT / "results").mkdir(exist_ok=True)
    with zipfile.ZipFile(CACHE / "cmp_pspice_zip.zip") as bundle:
        raw = bundle.read("tlv3501.lib")
    if hashlib.sha256(raw).hexdigest() != EXPECTED:
        raise ValueError("Model changed; re-audit required before running")
    model = CACHE / "tlv3501.lib"
    model.write_bytes(raw)
    text = raw.decode("utf-8")
    metadata = {
        "model_sha256": EXPECTED, "original_model_cache": str(model),
        "unchanged": True, "redistributed": False,
        "version": "2.0", "date": "2021-07-26", "target": "PSpice 17.4 TRANSIENT",
        "pin_order": ["IN+", "IN-", "SHDN", "V+", "V-", "OUT"],
        "header_notice": text[:text.index("* source TLV3501")],
        "fixed_delay_evidence": re.findall(r"^T_TPD.*TD=4\.5n.*$", text, re.MULTILINE),
        "coverage": ["Iq", "tpd", "Ibias", "Vcm", "Vs", "tr", "tf", "SHDN", "Ishdn"],
        "limitations": ["typical EC table only", "fixed internal delay",
                        "noise not validated", "temperature corners not validated",
                        "not semiconductor physics or physical-device acceptance"]}
    with zipfile.ZipFile(CACHE / "tipd_zip.zip") as bundle:
        notice = bundle.read("IMP NOTICE for REF DESIGNS.pdf")
    metadata["reference_design_notice"] = "\n".join(
        page.extract_text() for page in PdfReader(io.BytesIO(notice)).pages)
    (ROOT / "model_inspection.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    base = {"supply_V": 5.0, "overdrive_V": .02, "load_F": 1e-12,
            "max_step_s": 50e-12, "kind": "pulse", "mode": "ps"}
    cases = [
        dict(base, id="native_5V_20mV", mode="native"),
        dict(base, id="ps_5V_20mV"),
        dict(base, id="ps_5V_5mV", overdrive_V=.005),
        dict(base, id="ps_3p3V_20mV", supply_V=3.3),
        dict(base, id="ps_5V_20pF", load_F=20e-12),
        dict(base, id="ps_5V_refined", max_step_s=25e-12),
        dict(base, id="ps_shutdown", kind="shutdown"),
    ]
    version = subprocess.run([str(simulator), "--version"], capture_output=True,
                             text=True, timeout=15)
    results = [run_case(case, simulator, model) for case in cases]
    output = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "unchanged manufacturer typical macro only; not real-device acceptance",
        "simulator": str(simulator), "simulator_sha256": digest(simulator),
        "simulator_version": version.stdout + version.stderr,
        "script_sha256": digest(Path(__file__)), "model_sha256": digest(model),
        "runs": results,
        "counts": {status: sum(r["status"] == status for r in results)
                   for status in ("pass", "fail", "blocked")}}
    (ROOT / "model_test_results.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for result in results:
        print(result["id"], result["status"], result.get("measurements", result.get("reason")))
    print(output["counts"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--simulator", type=Path, default=Path(tempfile.gettempdir()) /
                        "ewc-openems/ngspice47/Spice64/bin/ngspice_con.exe")
    main(parser.parse_args().simulator.resolve())
