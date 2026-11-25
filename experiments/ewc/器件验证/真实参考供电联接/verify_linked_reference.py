"""Independent CSV/R-C/rail accounting; no import of the simulation runner."""
import csv
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parent
OLD = ROOT.parent / "\u771f\u5b9e\u57fa\u5e26\u8c03\u7406"
EDGES = [(200e-9, True), (400e-9, False), (600e-9, True), (800e-9, False)]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def helpers():
    spec = importlib.util.spec_from_file_location("independent_rc_oracle", OLD / "verify_offline.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.crossings, module.derivative


def read_rows(run):
    with (ROOT / "results" / run["id"] / "wave.csv").open(encoding="ascii", newline="") as stream:
        reader = csv.reader(stream)
        assert len(next(reader)) == 29
        rows = [tuple(map(float, row)) for row in reader]
    assert len(rows) == run["rows"]
    assert all(len(r) == 29 and all(math.isfinite(v) for v in r) for r in rows)
    assert all(a[0] < b[0] for a, b in zip(rows, rows[1:]))
    assert rows[-1][0] >= (.005 if run["slow_startup"] else 1e-6)-1e-12
    return rows


def recalculate(run):
    rows = read_rows(run)
    rail5_kcl = max(abs(r[25]-r[11]) for r in rows)
    rail3_kcl = max(abs(r[26]-r[12]-r[22]-r[23]-r[24]) for r in rows)
    assert rail5_kcl < 1e-9 and rail3_kcl < 1e-9
    local_upstream, local_source = 0, 0
    for a, b in zip(rows, rows[1:]):
        dt = b[0]-a[0]
        local_upstream += -.5*dt*(a[19]*a[20]+b[19]*b[20])
        local_source += .5*dt*(a[2]*a[27]+a[3]*a[28]+b[2]*b[27]+b[3]*b[28])
    if run["slow_startup"]:
        window = [r for r in rows if .004 <= r[0] <= .0049]
        result = dict(final_rail5_error_V=max(abs(r[13]-5) for r in window),
                      final_rail3_error_V=max(abs(r[14]-3.3) for r in window),
                      final_reference_error_V=max(abs(r[18]-5) for r in window),
                      final_logic_range_V=[min(r[10] for r in window), max(r[10] for r in window)])
        saved = run["measurements"]
        for key in ("final_rail5_error_V", "final_rail3_error_V", "final_reference_error_V"):
            assert abs(saved[key]-result[key]) < 1e-10
        assert saved["final_logic_range_V"] == result["final_logic_range_V"]
        result.update(id=run["id"], status="observed", safe_startup_passed=False,
                      reason="active synthetic source during supply ramp; no validated gating/reset model")
    else:
        cross, derivative = helpers()
        errors, common, events = [], [], []
        gain = 500/1044
        for edge, rising in EDGES:
            window = [r for r in rows if edge+120e-9 <= r[0] <= edge+170e-9]
            errors.append(max(abs(r[8]-r[9]-gain*(r[1]-run["threshold_V"])) for r in window))
            common.append(max(abs((r[8]+r[9])/2-1.65) for r in window))
            ic = cross(rows, 1, run["threshold_V"], rising, edge, edge+10e-9)
            fc = cross(rows, 8, 0, rising, edge, edge+100e-9, subtract=9)
            oc = cross(rows, 10, 1.65, rising, edge, edge+180e-9)
            delay = oc[0]-ic[0] if len(oc) == len(ic) == 1 else None
            fd = fc[0]-ic[0] if len(fc) == len(ic) == 1 else None
            cd = oc[0]-fc[0] if len(oc) == len(fc) == 1 else None
            events.append(dict(total_delay_s=delay, fda_delay_s=fd, comparator_delay_s=cd,
                               input_crossings_s=ic, fda_crossings_s=fc, output_crossings_s=oc,
                               level_ok=all(r[10] > 2.31 if rising else r[10] < .99 for r in window),
                               delay_ok=delay is not None and 0 < delay < 30e-9))
        vp = [r[6]-r[5] for r in rows]
        vn = [r[7]-r[4] for r in rows]
        diff = [r[8]-r[9] for r in rows]
        cp = [r[8] for r in rows]
        cn = [r[9] for r in rows]
        max_current, max_kcl, max_source_error = 0, 0, 0
        buffer_sink = [0, 0]
        for i, r in enumerate(rows):
            op_current = vp[i]/1000+(r[6]-r[8])/22+1e-12*derivative(rows, vp, i)
            om_current = vn[i]/1000+(r[7]-r[9])/22+1e-12*derivative(rows, vn, i)
            max_current = max(max_current, abs(op_current), abs(om_current))
            dp = (r[2]/100+r[16]/100+r[4]/950)/(2/100+1/950)
            dn = (r[3]/100+r[17]/100+r[5]/950)/(2/100+1/950)
            max_source_error = max(max_source_error, abs(r[27]-(r[2]-dp)/100),
                                   abs(r[28]-(r[3]-dn)/100))
            buffer_sink[0] = max(buffer_sink[0], (dp-r[16])/100)
            buffer_sink[1] = max(buffer_sink[1], (dn-r[17])/100)
            if any(t+120e-9 <= r[0] <= t+170e-9 for t, _ in EDGES):
                d = derivative(rows, diff, i)
                kp = (r[6]-r[8])/22-diff[i]/1000-run["fda_load_F"]*d \
                    -2e-12*derivative(rows, cp, i)-2e-12
                kn = (r[7]-r[9])/22+diff[i]/1000+run["fda_load_F"]*d \
                    -2e-12*derivative(rows, cn, i)-2e-12
                max_kcl = max(max_kcl, abs(kp), abs(kn))
        assert max_source_error < 1e-9 and max_kcl < 1e-6
        steady = [r for r in rows if r[0] >= 200e-9]
        checks = dict(dc_amplitude=max(errors) < .001, common_mode=max(common) < .005,
                      fda_pin_range=all(-.2 <= r[j] <= 3.8 for r in rows for j in (4, 5)),
                      fda_output_range=all(.23 <= r[j] <= 4.77 for r in rows for j in (6, 7)),
                      comparator_pin_range=all(-.2 <= r[j] <= 3.1 for r in rows for j in (8, 9)),
                      output_continuous_current=max_current < .020,
                      static_margin=gain*run["overdrive_V"] > .0095,
                      levels=all(e["level_ok"] for e in events), delays=all(e["delay_ok"] for e in events),
                      rail5=max(abs(r[13]-5) for r in steady) < .075,
                      rail3=max(abs(r[14]-3.3) for r in steady) < .0495,
                      reference=max(abs(r[18]-5) for r in steady) < .005)
        saved = run["measurements"]
        assert checks == saved["checks"]
        for name, values in (("dc_errors_V", errors), ("cm_errors_V", common)):
            assert max(abs(a-b) for a, b in zip(values, saved[name])) < 1e-10
        for e, s in zip(events, saved["events"]):
            for key in ("total_delay_s", "fda_delay_s", "comparator_delay_s"):
                assert e[key] is None and s[key] is None or (
                    e[key] is not None and s[key] is not None and abs(e[key]-s[key]) < 1e-15)
        status = "pass" if all(checks.values()) else "fail"
        assert status == run["status"]
        result = dict(id=run["id"], status=status, checks=checks, events=events,
                      dc_errors_V=errors, cm_errors_V=common,
                      max_rc_kcl_residual_A=max_kcl, source_probe_error_A=max_source_error,
                      max_reference_sink_currents_A=buffer_sink,
                      bias_threshold_range_V=[min(r[17]-r[16] for r in steady),
                                              max(r[17]-r[16] for r in steady)])
    result.update(rail5_kcl_A=rail5_kcl, rail3_kcl_A=rail3_kcl,
                  local_upstream_energy_J=local_upstream, synthetic_source_energy_J=local_source,
                  full_system_energy_passed=False)
    return result


def audit():
    record = json.loads((ROOT / "summary.json").read_text(encoding="utf-8"))
    for path, digest in record["previous_device_evidence_sha256"].items():
        assert sha(Path(path)) == digest, path
    for path, digest in record["original_files_sha256"].items():
        assert sha(Path(path)) == digest, path
    sources = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
    matching = json.loads((ROOT / "matching_datasheet.json").read_text(encoding="utf-8"))
    members = 0
    for source in sources["sources"] + [matching["source"]]:
        assert source["status"] == 200
        assert sha(Path(source["cache"])) == source["sha256"]
        if "archive_members" in source:
            with zipfile.ZipFile(source["cache"]) as archive:
                for member in source["archive_members"]:
                    assert hashlib.sha256(archive.read(member["name"])).hexdigest() == member["sha256"]
                    members += 1
    runs = []
    for run in record["runs"]:
        for path, digest in run["files"].items():
            assert sha(ROOT / path) == digest, path
        if "rows" in run:
            runs.append(recalculate(run))
        else:
            assert run["status"] == "blocked"
            runs.append(dict(id=run["id"], status="blocked", reason=run["reason"]))
    selected = [r for r in runs if r["id"] in ("linked_real_rails", "linked_real_rails_5ps")]
    refinement = dict(passed=False, reason="complete matching four-edge waveforms required")
    if len(selected) == 2 and all("events" in r for r in selected):
        delays = [abs(a["total_delay_s"]-b["total_delay_s"])
                  for a, b in zip(selected[0]["events"], selected[1]["events"])
                  if a["total_delay_s"] is not None and b["total_delay_s"] is not None]
        refinement = dict(delay_differences_s=delays,
                          passed=len(delays) == 4 and max(delays) < 20e-12,
                          local_screens_passed=all(r["status"] == "pass" for r in selected))
    result = dict(independent_evidence_verification_pass=True, runs=runs,
                  original_device_files_checked=len(record["previous_device_evidence_sha256"]),
                  official_sources_checked=4, zip_members_checked=members,
                  refinement=refinement, full_G4_G5_passed=False, full_joint_passed=False,
                  safe_startup_passed=False, vendor_library_edits=False,
                  summary_sha256=sha(ROOT / "summary.json"), verifier_sha256=sha(Path(__file__)),
                  sources_sha256=sha(ROOT / "sources.json"),
                  matching_datasheet_sha256=sha(ROOT / "matching_datasheet.json"))
    (ROOT / "verification.json").write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    audit()
