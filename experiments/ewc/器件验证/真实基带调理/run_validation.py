"""Eight bounded transient cases, original models included without edits."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

import numpy as np
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parent.parent
EDGES = [(200e-9, True), (400e-9, False), (600e-9, True), (800e-9, False)]
FDA_HASH = "0a8c0db4398d7849ef46ba831bac4bc5f09352c9ead3a7be6a3a7e36d5a21527"
CMP_HASH = "4e4345dc2929ee91a99a0b5437369c32b3817e9c8673a7ae90310d5485a81ba9"
SIM_HASH = "22d5cae2bd32b2e39157a8d27bf457122f68285b72a9ebefdf41551b628233ab"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def configurations():
    base = dict(source_cm_V=3.2, threshold_V=.1, overdrive_V=.08,
                fda_load_F=10e-12, comparator_load_F=1e-12,
                step_s=50e-12, ramp_s=2e-9)
    return [dict(base, id="d01_nominal"),
            dict(base, id="d02_large", overdrive_V=1),
            dict(base, id="d03_negative_threshold", threshold_V=-.1),
            dict(base, id="d04_source_cm", source_cm_V=3.6),
            dict(base, id="d05_fda_100pF", fda_load_F=100e-12),
            dict(base, id="d06_comparator_1000pF", comparator_load_F=1000e-12),
            dict(base, id="d07_low_overdrive", overdrive_V=.004),
            dict(base, id="d08_refined", step_s=25e-12)]


def deck(c, fda, comparator):
    low, high = c["threshold_V"]-c["overdrive_V"], c["threshold_V"]+c["overdrive_V"]
    points = [(0, low)]
    for t, up in EDGES:
        points += [(t, low if up else high), (t+c["ramp_s"], high if up else low)]
    points += [(1e-6, low)]
    p = " ".join(f"{t:.14g} {v:.14g}" for t, v in points)
    return f"""DC FDA + unchanged comparator; synthetic baseband, NOT RF receiver
.include "{fda.as_posix()}"
.include "{comparator.as_posix()}"
VF vs 0 5
VC vc 0 3.3
VCM cm 0 1.65
VRP refp 0 {1.65-c['threshold_V']/2}
VRN refn 0 {1.65+c['threshold_V']/2}
VSC scm 0 {c['source_cm_V']}
VD raw 0 PWL({p})
* Ideal split source and explicit ideal references only, no ideal conditioner.
BP srcp 0 V=v(scm)+v(raw)/2
BN srcn 0 V=v(scm)-v(raw)/2
* Per-leg synthetic source impedance 25 ohm; not an ADL5380 model.
RSP srcp sp 25
RSN srcn sn 25
RTP sp dp 75
RTN sn dn 75
RBP dp refp 100
RBN dn refn 100
* Divider Thevenin R=50 ohm; Rg total=950+50=1000, Rf=1000.
RGP dp ip 950
RGN dn im 950
RFP ip om 1000
RFN im op 1000
CFP ip om 1p
CFN im op 1p
XF ip im op om cm vs 0 THS4551
* Feedback senses amplifier outputs before the real isolation RC network.
ROP op cp 22
RON om cn 22
CD cp cn {c['fda_load_F']}
RD cp cn 1000
CP cp 0 1p
CN cn 0 1p
VSH sh 0 0
XC cp cn sh vc 0 logic TLV3501
RL logic 0 100000
CL logic 0 {c['comparator_load_F']}
.temp 25
.control
set wr_singlescale
set wr_vecnames
set numdgt=15
tran {c['step_s']} 1u 0 {c['step_s']}
wrdata wave.txt v(raw) v(srcp) v(srcn) v(ip) v(im) v(op) v(om) v(cp) v(cn) v(logic) i(VF) i(VC)
quit
.endc
.end
"""


def cross(t, y, level, rising, start, end):
    a, b = y[:-1]-level, y[1:]-level
    direction = (a < 0) & (b >= 0) if rising else (a > 0) & (b <= 0)
    indices = np.flatnonzero(direction & (t[1:] >= start) & (t[1:] <= end))
    return [float(t[i]+(t[i+1]-t[i])*(-a[i])/(b[i]-a[i])) for i in indices]


def measure(c, data):
    t, raw, sp, sn, ip, im, op, om, cp, cn, logic, fi, ci = data.T
    events = []
    violations = []
    gain = .5 * 1000/1044
    target = gain*(raw-c["threshold_V"])
    error, cmerror = [], []
    for start, rising in EDGES:
        window = (t >= start+120e-9) & (t <= start+170e-9)
        error.append(float(np.max(np.abs(cp[window]-cn[window]-target[window]))))
        cmerror.append(float(np.max(np.abs((cp[window]+cn[window])/2-1.65))))
        ic = cross(t, raw, c["threshold_V"], rising, start, start+10e-9)
        fc = cross(t, cp-cn, 0, rising, start, start+100e-9)
        oc = cross(t, logic, 1.65, rising, start, start+180e-9)
        delay = oc[0]-ic[0] if len(ic) == len(oc) == 1 else None
        fd = fc[0]-ic[0] if len(ic) == len(fc) == 1 else None
        cd = oc[0]-fc[0] if len(fc) == len(oc) == 1 else None
        values = logic[window]
        events.append(dict(rising=rising, input_crossings_s=ic, fda_crossings_s=fc,
                           output_crossings_s=oc, total_delay_s=delay, fda_delay_s=fd,
                           comparator_delay_s=cd, low_V=float(values.min()),
                           high_V=float(values.max()),
                           level_ok=bool(values.min() > 2.31 if rising else values.max() < .99),
                           delay_ok=delay is not None and 0 < delay < 30e-9))
    # Conservative 25 C EC boundaries, not full-temperature acceptance.
    pins_ok = bool(min(ip.min(), im.min()) >= -.2 and max(ip.max(), im.max()) <= 3.8)
    output_ok = bool(min(op.min(), om.min()) >= .23 and max(op.max(), om.max()) <= 4.77)
    comparator_ok = bool(min(cp.min(), cn.min()) >= -.2 and max(cp.max(), cn.max()) <= 3.1)
    current_p = (op-im)/1000+(op-cp)/22+1e-12*np.gradient(op-im, t)
    current_n = (om-ip)/1000+(om-cn)/22+1e-12*np.gradient(om-ip, t)
    current_max = float(max(np.abs(current_p).max(), np.abs(current_n).max()))
    checks = dict(dc_amplitude=max(error) < .001,
                  common_mode=max(cmerror) < .005, fda_pin_range=pins_ok,
                  fda_output_range=output_ok, comparator_pin_range=comparator_ok,
                  output_continuous_current=current_max < .020,
                  static_margin=gain*c["overdrive_V"] > .0095,
                  levels=all(e["level_ok"] for e in events),
                  delays=all(e["delay_ok"] for e in events))
    violations = [key for key, passed in checks.items() if not passed]
    return dict(checks=checks, violations=violations, events=events,
                expected_loaded_gain=gain, dc_errors_V=error, cm_errors_V=cmerror,
                fda_pin_min_V=float(min(ip.min(), im.min())),
                fda_pin_max_V=float(max(ip.max(), im.max())),
                output_current_max_A=current_max,
                comparator_pin_min_V=float(min(cp.min(), cn.min())),
                comparator_pin_max_V=float(max(cp.max(), cn.max())),
                mean_fda_supply_A=float(np.mean(-fi[t > 200e-9])),
                mean_comparator_supply_A=float(np.mean(-ci[t > 200e-9])))


def run_one(c, fda, comparator, sim, temp):
    work = temp/c["id"]
    work.mkdir()
    evidence = ROOT/"results"/c["id"]
    evidence.mkdir()
    net = deck(c, fda, comparator)
    (work/"test.cir").write_text(net, encoding="ascii")
    (work/".spiceinit").write_text("set ngbehavior=ps\n", encoding="ascii")
    (evidence/"test.cir").write_text(net, encoding="ascii")
    (evidence/"spiceinit.txt").write_text("set ngbehavior=ps\n", encoding="ascii")
    result = dict(c, status="blocked", attempted=True)
    try:
        proc = subprocess.run([str(sim), "-b", "test.cir"], cwd=work,
                              capture_output=True, text=True, errors="replace", timeout=90)
        log = proc.stdout+"\n"+proc.stderr
        (evidence/"solver.log").write_text(log, encoding="utf-8")
        result["returncode"] = proc.returncode
        if proc.returncode != 0 or not (work/"wave.txt").exists():
            raise RuntimeError("No transient data; retained solver.log")
        data = np.loadtxt(work/"wave.txt", skiprows=1)
        if data.ndim != 2 or data.shape[1] != 13 or not np.isfinite(data).all():
            raise ValueError("Malformed waveform")
        if not np.all(np.diff(data[:, 0]) > 0) or data[-1, 0] < 1e-6-1e-12:
            raise ValueError("Incomplete time window")
        np.savetxt(evidence/"wave.csv", data, delimiter=",", fmt="%.15e", comments="",
                   header="time_s,raw_V,srcp_V,srcn_V,ip_V,im_V,op_V,om_V,cp_V,cn_V,logic_V,fda_supply_A,comparator_supply_A")
        result["rows"] = len(data)
        result["measurements"] = measure(c, data)
        result["status"] = "fail" if result["measurements"]["violations"] else "pass"
    except Exception as exc:
        result["reason"] = str(exc)
    result["files"] = {p.relative_to(ROOT).as_posix(): sha(p) for p in evidence.iterdir()}
    return result


def main():
    if (ROOT/"summary.json").exists() or (ROOT/"results").exists():
        raise RuntimeError("Refuse to overwrite formal evidence")
    sources = json.loads((ROOT/"sources.json").read_text())
    cache = Path(sources["cache"])
    fda = cache/"ths4551.lib"
    comparator = Path(tempfile.gettempdir())/"ewc-device-audit-20261118/tlv3501.lib"
    sim = Path(tempfile.gettempdir())/"ewc-openems/ngspice47/Spice64/bin/ngspice_con.exe"
    evidence = list(ROOT.parent.rglob("*")) + [WORKSPACE/"EWC真实材料与器件统一规划.md"]
    previous = {str(p.relative_to(WORKSPACE)): sha(p) for p in evidence
                if p.is_file() and ROOT not in p.parents and "__pycache__" not in p.parts}
    models = {str(fda): FDA_HASH, str(comparator): CMP_HASH, str(sim): SIM_HASH}
    blockers = [str(p)+" missing or changed" for p, h in models.items()
                if not Path(p).is_file() or sha(Path(p)) != h]
    summary = dict(scope="R12_R13_typical_DC_interface_only", workers=3,
                   previous_evidence_sha256=previous, original_files_sha256=models,
                   temperature_C=25, ideal_references=True, synthetic_baseband=True,
                   RF_models=0, full_chain_accepted=False, blockers=blockers,
                   criteria=dict(dc_error_V=.001, cm_error_V=.005, delay_s=30e-9,
                                 conservative_static_guard_V=.0095, refinement_s=20e-12))
    if blockers:
        runs = [dict(c, attempted=False, status="blocked", reason="; ".join(blockers))
                for c in configurations()]
    else:
        reader = PdfReader(cache/"ths4551_datasheet.pdf")
        (ROOT/"datasheet_snapshot.txt").write_text(
            "\n".join("PAGE "+str(i+1)+"\n"+p.extract_text()
                      for i, p in enumerate(reader.pages)), encoding="utf-8")
        temp = Path(tempfile.mkdtemp(prefix="ewc-dc-runs-"))
        summary["temporary_directory"] = str(temp)
        (ROOT/"results").mkdir()
        with ThreadPoolExecutor(max_workers=3) as pool:
            runs = list(pool.map(lambda c: run_one(c, fda, comparator, sim, temp),
                                 configurations()))
    summary["runs"] = runs
    summary["counts"] = dict(configurations=len(runs), solver_attempts=sum(r["attempted"] for r in runs),
                            datasets=sum("rows" in r for r in runs),
                            **{s: sum(r["status"] == s for r in runs) for s in ("pass", "fail", "blocked")})
    summary["original_hashes_unchanged"] = all(Path(p).is_file() and sha(Path(p)) == h
                                               for p, h in models.items())
    summary["previous_evidence_unchanged"] = all(sha(WORKSPACE/p) == h for p, h in previous.items())
    if "measurements" in runs[0] and "measurements" in runs[7]:
        differences = [abs(a["total_delay_s"]-b["total_delay_s"])
                       for a, b in zip(runs[0]["measurements"]["events"], runs[7]["measurements"]["events"])
                       if a["total_delay_s"] is not None and b["total_delay_s"] is not None]
        summary["refinement"] = dict(differences_s=differences,
                                    passed=len(differences) == 4 and max(differences) < 20e-12)
    summary["executed_runner_sha256"] = sha(Path(__file__))
    save(ROOT/"summary.json", summary)
    print(json.dumps(summary["counts"]))
    for r in runs:
        print(r["id"], r["status"], r.get("reason", r.get("measurements", {}).get("violations")))


if __name__ == "__main__":
    main()
