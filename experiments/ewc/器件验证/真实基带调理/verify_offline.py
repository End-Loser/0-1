"""Independent CSV and resistor/KCL audit; no solver/network/TEMP dependency."""
import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parent.parent
EDGES = [(200e-9, True), (400e-9, False), (600e-9, True), (800e-9, False)]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def crossings(rows, index, level, direction, begin, end, subtract=None):
    found = []
    for a, b in zip(rows, rows[1:]):
        ya = a[index]-(a[subtract] if subtract else 0)-level
        yb = b[index]-(b[subtract] if subtract else 0)-level
        changed = ya < 0 <= yb if direction else ya > 0 >= yb
        if changed and begin <= b[0] <= end:
            found.append(a[0]+(b[0]-a[0])*(-ya)/(yb-ya))
    return found


def derivative(rows, values, i):
    if i == 0:
        return (values[1]-values[0])/(rows[1][0]-rows[0][0])
    if i == len(rows)-1:
        return (values[i]-values[i-1])/(rows[i][0]-rows[i-1][0])
    left = rows[i][0]-rows[i-1][0]
    right = rows[i+1][0]-rows[i][0]
    return (-right/(left*(left+right))*values[i-1]
            +(right-left)/(left*right)*values[i]
            +left/(right*(left+right))*values[i+1])


def recalculate(run):
    with (ROOT/"results"/run["id"]/"wave.csv").open(encoding="ascii", newline="") as stream:
        reader = csv.reader(stream)
        header = next(reader)
        rows = [tuple(map(float, row)) for row in reader]
    import math
    assert len(header) == 13 and len(rows) == run["rows"]
    assert all(len(row) == 13 and all(math.isfinite(v) for v in row) for row in rows)
    assert all(a[0] < b[0] for a, b in zip(rows, rows[1:]))
    assert rows[0][0] <= 1e-12 and rows[-1][0] >= 1e-6-1e-12
    gain = 500/1044
    dc_errors, cm_errors, events, platform_statistics = [], [], [], []
    for start, up in EDGES:
        window = [r for r in rows if start+120e-9 <= r[0] <= start+170e-9]
        errors = [r[8]-r[9]-gain*(r[1]-run["threshold_V"]) for r in window]
        dc_errors.append(max(map(abs, errors)))
        cm_errors.append(max(abs((r[8]+r[9])/2-1.65) for r in window))
        platform_statistics.append(dict(mean_error_V=sum(errors)/len(errors),
                                        error_peak_to_peak_V=max(errors)-min(errors)))
        ic = crossings(rows, 1, run["threshold_V"], up, start, start+10e-9)
        fc = crossings(rows, 8, 0, up, start, start+100e-9, subtract=9)
        oc = crossings(rows, 10, 1.65, up, start, start+180e-9)
        total = oc[0]-ic[0] if len(oc) == len(ic) == 1 else None
        fd = fc[0]-ic[0] if len(fc) == len(ic) == 1 else None
        cd = oc[0]-fc[0] if len(oc) == len(fc) == 1 else None
        events.append(dict(input_crossings_s=ic, fda_crossings_s=fc, output_crossings_s=oc,
                           total_delay_s=total, fda_delay_s=fd, comparator_delay_s=cd,
                           level_ok=all(r[10] > 2.31 if up else r[10] < .99 for r in window),
                           delay_ok=total is not None and 0 < total < 30e-9,
                           low_V=min(r[10] for r in window), high_V=max(r[10] for r in window)))
    vp = [r[6]-r[5] for r in rows]
    vn = [r[7]-r[4] for r in rows]
    diff = [r[8]-r[9] for r in rows]
    cp = [r[8] for r in rows]
    cn = [r[9] for r in rows]
    current_max = 0
    cp_kcl, cn_kcl, input_current = [], [], []
    source_max = 0
    for i, r in enumerate(rows):
        ip = vp[i]/1000+(r[6]-r[8])/22+1e-12*derivative(rows, vp, i)
        im = vn[i]/1000+(r[7]-r[9])/22+1e-12*derivative(rows, vn, i)
        current_max = max(current_max, abs(ip), abs(im))
        dp = (r[2]/100+(1.65-run["threshold_V"]/2)/100+r[4]/950)/(2/100+1/950)
        dn = (r[3]/100+(1.65+run["threshold_V"]/2)/100+r[5]/950)/(2/100+1/950)
        source_max = max(source_max, abs((r[2]-dp)/100), abs((r[3]-dn)/100))
        if any(t+120e-9 <= r[0] <= t+170e-9 for t, _ in EDGES):
            d = derivative(rows, diff, i)
            cp_kcl.append(abs((r[6]-r[8])/22-diff[i]/1000-run["fda_load_F"]*d
                              -2e-12*derivative(rows, cp, i)-2e-12))
            cn_kcl.append(abs((r[7]-r[9])/22+diff[i]/1000+run["fda_load_F"]*d
                              -2e-12*derivative(rows, cn, i)-2e-12))
            input_current.extend([(dp-r[4])/950+vn[i]/1000+1e-12*derivative(rows, vn, i),
                                  (dn-r[5])/950+vp[i]/1000+1e-12*derivative(rows, vp, i)])
    checks = dict(dc_amplitude=max(dc_errors) < .001, common_mode=max(cm_errors) < .005,
                  fda_pin_range=all(-.2 <= r[j] <= 3.8 for r in rows for j in [4, 5]),
                  fda_output_range=all(.23 <= r[j] <= 4.77 for r in rows for j in [6, 7]),
                  comparator_pin_range=all(-.2 <= r[j] <= 3.1 for r in rows for j in [8, 9]),
                  output_continuous_current=current_max < .020,
                  static_margin=gain*run["overdrive_V"] > .0095,
                  levels=all(e["level_ok"] for e in events), delays=all(e["delay_ok"] for e in events))
    expected = run["measurements"]
    assert checks == expected["checks"]
    for name, values in [("dc_errors_V", dc_errors), ("cm_errors_V", cm_errors)]:
        assert max(abs(a-b) for a, b in zip(values, expected[name])) < 1e-10
    assert abs(current_max-expected["output_current_max_A"]) < 1e-9
    for event, saved in zip(events, expected["events"]):
        for key in ["input_crossings_s", "fda_crossings_s", "output_crossings_s"]:
            assert len(event[key]) == len(saved[key])
            assert all(abs(a-b) < 1e-15 for a, b in zip(event[key], saved[key]))
        for key in ["total_delay_s", "fda_delay_s", "comparator_delay_s"]:
            assert event[key] is None and saved[key] is None or (
                event[key] is not None and saved[key] is not None and abs(event[key]-saved[key]) < 1e-15)
    return dict(id=run["id"], checks=checks, events=events, dc_errors_V=dc_errors,
                cm_errors_V=cm_errors, platform_statistics=platform_statistics,
                max_rc_kcl_residual_A=max(cp_kcl+cn_kcl),
                max_abs_fda_input_current_A=max(map(abs, input_current)),
                max_synthetic_source_current_A=source_max, output_current_max_A=current_max,
                independently_reproduced_status="pass" if all(checks.values()) else "fail")


def audit():
    summary = json.loads((ROOT/"summary.json").read_text(encoding="utf-8"))
    previous_ok = all((WORKSPACE/p).is_file() and sha(WORKSPACE/p) == h
                      for p, h in summary["previous_evidence_sha256"].items())
    assert previous_ok, "Existing evidence changed"
    for run in summary["runs"]:
        for path, digest in run["files"].items():
            assert sha(ROOT/path) == digest, path
    runs = [recalculate(r) for r in summary["runs"] if "rows" in r]
    return dict(scope="independent_offline_artifact_and_waveform_audit",
                previous_evidence_files=len(summary["previous_evidence_sha256"]),
                previous_evidence_unchanged=previous_ok, runs=runs,
                reproduced_runs=len(runs), solver_attempts=0, network_requests=0)


if __name__ == "__main__":
    result = audit()
    (ROOT/"verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n",
                                         encoding="utf-8")
    print("Independent waveform audits:", result["reproduced_runs"])
    for r in result["runs"]:
        print(r["id"], r["independently_reproduced_status"],
              "KCL_A", r["max_rc_kcl_residual_A"],
              "bias_A", r["max_abs_fda_input_current_A"])
