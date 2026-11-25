"""Unchanged TI devices with actual carry-pin passive baseband cancellation."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

import numpy as np

ROOT = Path(__file__).resolve().parent
PRIOR = ROOT / "参考输入去耦"
OUT = ROOT / "C输出被动反馈"
HOLD = 200e-9
RAMP = 2e-9
STATES = [0, 1, 2, 3, 4, 5, 6, 7, 0]
NAMES = ["time", "a", "b", "cin", "raw", "carry", "sum", "vref",
         "rail5", "rail3", "upstream", "cm"]
VECTORS = ["v(a)", "v(b)", "v(cin)", "v(raw)", "v(carry)", "v(sum)",
           "v(vref)", "v(rail5)", "v(rail3)", "v(upstream)", "v(cm)"]
for stage in (1, 2):
    for node in ("ip", "im", "op", "om", "cp", "cn", "sp", "sn", "dp", "dn"):
        NAMES.append(f"{node}{stage}")
        VECTORS.append(f"v({node}{stage})")
for i in range(1, 6):
    NAMES += [f"b{i}", f"bias{i}", f"i_bin{i}", f"i_fb{i}", f"i_bout{i}", f"i_buf{i}"]
    VECTORS += [f"v(b{i})", "v(cm)" if i == 1 else f"v(bias{i})",
                f"i(VBIN{i})", f"i(VFB{i})", f"i(VBOUT{i})", f"i(VBUF{i})"]
for source in ("UP", "5", "3", "REF", "F1", "F2", "C1", "C2", "A", "B", "CI",
               "SP1", "SN1", "SP2", "SN2", "SC"):
    NAMES.append(f"i_{source.lower()}")
    VECTORS.append(f"i(V{source})")
NAMES.append("i_c_feedback")
VECTORS.append("i(VCFEED)")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, data):
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(data, indent=2) + "\n")


def configurations():
    return [dict(id=name, step_s=step, real_rails=rails,
                 duration_s=len(STATES) * HOLD, states=STATES, hold_s=HOLD,
                 ramp_s=RAMP, input_high_V=.3, summing_resistance_ohm=300,
                 carry_threshold_V=.15, sum_threshold_V=.05,
                 cancellation_input_equivalent_V=.2,
                 feedback_resistance_ohm=32750,
                 sum_bias_difference_V=.05 + 2 * (.1 / 3.3) * 2.0375)
            for name, step, rails in (
                ("actual_C_10ps_diagnostic_rails", 10e-12, False),
                ("actual_C_5ps_diagnostic_rails", 5e-12, False),
                ("actual_C_10ps_real_rails", 10e-12, True),
            )]


def pwl(bit):
    values = [(0., .3 if STATES[0] & (1 << bit) else 0.)]
    for i, state in enumerate(STATES[1:], 1):
        before = .3 if STATES[i - 1] & (1 << bit) else 0.
        after = .3 if state & (1 << bit) else 0.
        values += [(i * HOLD, before), (i * HOLD + RAMP, after)]
    values.append((len(STATES) * HOLD, values[-1][1]))
    return " ".join(f"{t:.15g} {v:.15g}" for t, v in values)


def deck(c, models):
    lines = [
        "Actual TI carry pin drives passive second-stage input; NOT RF or G6 acceptance",
        "VUP upstream 0 6", "VF1 rail5 vs1 0", "VF2 rail5 vs2 0",
        "VC1 rail3 vc1 0", "VC2 rail3 vc2 0", "VSC scm 0 3.2",
        f"VA a 0 PWL({pwl(2)})", f"VB b 0 PWL({pwl(1)})",
        f"VCI cin 0 PWL({pwl(0)})",
        "RA a raw 300", "RB b raw 300", "RCI cin raw 300",
    ]
    if c["real_rails"]:
        lines += [
            f'.include "{models["regulator"].as_posix()}"',
            ".subckt EWC_RAIL VIN EN VO PARAMS: LEVEL=3.3",
            ".param V_OUT={LEVEL}",
            "XU VIN 0 EN unused VO TPS7A20_ADJ_TRANS", ".ends EWC_RAIL",
            "VEN en 0 3.3",
            "XR5 upstream en r5 EWC_RAIL PARAMS: LEVEL=5",
            "XR3 upstream en r3 EWC_RAIL PARAMS: LEVEL=3.3",
            "RCE5 r5 ce5 .05", "CCE5 ce5 0 1u",
            "RCE3 r3 ce3 .05", "CCE3 ce3 0 1u",
            "V5 r5 rail5 0", "V3 r3 rail3 0",
        ]
    else:
        lines += ["V5 rail5 0 5", "V3 rail3 0 3.3"]
    lines += ["VREF upstream refvin 0",
              "XREF refvin tempref 0 trimref vref EWC_REF",
              "RREFESR vref cref 1", "CREF cref 0 1u"]
    thresholds = (c["carry_threshold_V"], c["sum_bias_difference_V"])
    biases = [1.65] + [v for threshold in thresholds
                       for v in (1.65 - threshold / 2, 1.65 + threshold / 2)]
    for i, value in enumerate(biases, 1):
        node = "cm" if i == 1 else f"bias{i}"
        ratio = value / 5
        lines += [
            f"RDT{i} vref b{i} {100000 * (1 - ratio):.15g}",
            f"RDB{i} b{i} 0 {100000 * ratio:.15g}", f"CBIN{i} b{i} 0 1n",
            f"VBIN{i} b{i} bin{i} 0", f"VFB{i} {node} fb{i} 0",
            f"VBOUT{i} bout{i} {node} 0", f"VBUF{i} rail3 vb{i} 0",
            f"XB{i} bin{i} fb{i} vb{i} 0 bout{i} EWC_BUFFER",
            f"CB{i} {node} 0 100p",
        ]
    for k, (rp, rn, logic) in enumerate(((2, 3, "carry"), (4, 5, "sum")), 1):
        lines += [
            f"VSP{k} spgen{k} srcp{k} 0", f"VSN{k} sngen{k} srcn{k} 0",
            f"BP{k} spgen{k} 0 V=v(scm)+v(raw)/2",
            f"BN{k} sngen{k} 0 V=v(scm)-v(raw)/2",
            f"RSP{k} srcp{k} sp{k} 25", f"RSN{k} srcn{k} sn{k} 25",
            f"RTP{k} sp{k} dp{k} 75", f"RTN{k} sn{k} dn{k} 75",
            f"RBP{k} dp{k} bias{rp} 100", f"RBN{k} dn{k} bias{rn} 100",
            f"RGP{k} dp{k} ip{k} 950", f"RGN{k} dn{k} im{k} 950",
            f"RFP{k} ip{k} om{k} 1000", f"RFN{k} im{k} op{k} 1000",
            f"CFP{k} ip{k} om{k} 1p", f"CFN{k} im{k} op{k} 1p",
            f"XF{k} ip{k} im{k} op{k} om{k} cm vs{k} 0 EWC_FDA",
            f"ROP{k} op{k} cp{k} 22", f"RON{k} om{k} cn{k} 22",
            f"CD{k} cp{k} cn{k} 100p", f"RD{k} cp{k} cn{k} 1000",
            f"CP{k} cp{k} 0 1p", f"CN{k} cn{k} 0 1p",
            f"XC{k} cp{k} cn{k} sh vc{k} 0 {logic} EWC_CMP",
            f"RL{k} {logic} 0 100000", f"CL{k} {logic} 0 1p",
        ]
    lines += [
        "VSH sh 0 0", "VCFEED carry cfeed 0",
        f"RCFEED cfeed im2 {c['feedback_resistance_ohm']}",
    ]
    for wrapper, pins, key, chip in (
        ("EWC_FDA", "IP IM OP OM CM VP VN", "fda", "THS4551"),
        ("EWC_CMP", "IP IM SH VP VN OUT", "comparator", "TLV3501"),
        ("EWC_REF", "VIN TEMP GND TRIM OUT", "reference", "REF5050"),
        ("EWC_BUFFER", "IP IM VP VN OUT", "buffer", "OPAx320-Q1"),
    ):
        lines += [f".subckt {wrapper} {pins}", f'.include "{models[key].as_posix()}"',
                  f"XDEVICE {pins} {chip}", f".ends {wrapper}"]
    lines += [
        ".options method=gear maxord=2", ".temp 25", ".control",
        "set wr_singlescale", "set wr_vecnames", "set numdgt=15",
        f"tran {c['step_s']} {c['duration_s']} 0 {c['step_s']}",
        "wrdata wave.txt " + " ".join(VECTORS), "quit", ".endc", ".end",
    ]
    return "\n".join(lines) + "\n"


def main():
    assert not OUT.exists(), "Refuse overwrite of feedback evidence"
    prior = json.loads((PRIOR / "summary.json").read_bytes())
    original = prior["original_files_sha256"]
    assert all(sha(Path(p)) == digest for p, digest in original.items())
    models = {}
    for p in original:
        path = Path(p)
        for text, key in (("ths4551", "fda"), ("tlv3501", "comparator"),
                          ("ref5050", "reference"), ("tps7a20", "regulator"),
                          ("opax320", "buffer")):
            if text in p.lower():
                models[key] = path
    simulator = next(Path(p) for p in original if p.endswith(".exe"))
    assert len(models) == 5
    OUT.mkdir()
    configs = configurations()
    write_json(OUT / "declared_configurations.json", configs)
    write_json(OUT / "running_source.json", dict(
        runner_sha256=sha(Path(__file__)), original_files_sha256=original,
        previous_summary_sha256=sha(PRIOR / "summary.json")))
    cache = Path(tempfile.mkdtemp(prefix="ewc-actual-c-feedback-"))
    runs = []
    for config in configs:
        assert all(sha(Path(p)) == h for p, h in original.items())
        folder = OUT / config["id"]
        work = cache / config["id"]
        folder.mkdir()
        work.mkdir()
        net = deck(config, models)
        for target in (folder / "test.cir", work / "test.cir"):
            with target.open("x", encoding="ascii") as stream:
                stream.write(net)
        for target in (folder / "spiceinit.txt", work / ".spiceinit"):
            with target.open("x", encoding="ascii") as stream:
                stream.write("set ngbehavior=ps\n")
        run = dict(config, status="failed_preserve_partial")
        try:
            proc = subprocess.run([str(simulator), "-b", "test.cir"], cwd=work,
                                  capture_output=True, timeout=600)
            run["returncode"] = proc.returncode
            (folder / "solver_stdout.log").write_bytes(proc.stdout)
            (folder / "solver_stderr.log").write_bytes(proc.stderr)
            if (work / "wave.txt").exists():
                data = np.loadtxt(work / "wave.txt", skiprows=1)
                run["raw_wave"] = dict(path=str(work / "wave.txt"),
                                      sha256=sha(work / "wave.txt"))
                assert data.ndim == 2 and data.shape[1] == len(NAMES)
                assert np.isfinite(data).all() and np.all(np.diff(data[:, 0]) > 0)
                np.savez_compressed(folder / "full_vectors.npz", data=data,
                                    columns=np.array(NAMES))
                run["rows"] = len(data)
                run["observed_last_time_s"] = float(data[-1, 0])
                assert proc.returncode == 0
                assert data[-1, 0] >= config["duration_s"] - 1e-12
                run["status"] = "solver_complete_unverified"
            else:
                run["reason"] = "No waveform"
        except subprocess.TimeoutExpired as error:
            (folder / "solver_stdout.log").write_bytes(error.stdout or b"")
            (folder / "solver_stderr.log").write_bytes(error.stderr or b"")
            run["reason"] = "Solver timeout; not complete"
        except Exception as error:
            run["reason"] = repr(error)
        run["files_sha256"] = {p.name: sha(p) for p in folder.iterdir()}
        write_json(folder / "run.json", run)
        runs.append(run)
        print(config["id"], run["status"], run.get("observed_last_time_s"),
              run.get("reason"), flush=True)
    assert all(sha(Path(p)) == h for p, h in original.items())
    write_json(OUT / "summary.json", dict(
        runs=runs, columns=NAMES, cache=str(cache),
        original_files_sha256=original, runner_sha256=sha(Path(__file__)),
        configuration_sha256=sha(OUT / "declared_configurations.json"),
        scope="Actual carry-pin passive baseband cancellation; NOT RF or G6",
        real_RF_receiver=False, real_material_multiport=False,
        startup_passed=False, full_power_passed=False, G4_closed=False,
        G5_closed=False, G6_closed=False, G7_closed=False,
        full_system_gates_closed=0, goal_complete=False))


if __name__ == "__main__":
    main()
