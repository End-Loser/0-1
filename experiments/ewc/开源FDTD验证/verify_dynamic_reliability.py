"""Audit all raw runs without importing the dynamic test or its measurements."""

import argparse
import csv
import hashlib
import itertools
import json
import re
from pathlib import Path

import numpy as np

ROOT = Path(__file__).parent
GAIN_NAMES = ("ca", "cb", "cd", "sa", "sb", "sd", "cancel")


def read_csv(path):
    with path.open(encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def write_csv(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def prescribed_points(case, slots):
    duration, period = slots*case["period_ns"]*1e-9, case["period_ns"]*1e-9
    result = []
    for bit, error in enumerate(case["errors"]):
        if case["mode"] == "constant":
            points = [(0, error), (duration, error)]
        elif case["mode"] == "all_triangular":
            times = np.linspace(0, duration, slots+1)
            phase = (times/duration*(bit+2)+bit/7) % 1
            points = list(zip(times, .01*(1-4*abs(phase-.5))))
        else:
            start, end = error, error
            if bit == 6:
                start, end = {
                    "loss_ramp": (0, -.30), "gain_ramp": (0, .35),
                    "bounded_positive": (0, .01), "bounded_negative": (0, -.01),
                    "trigger_positive": (.005, 1.005*1.0049-1),
                    "trigger_negative": (-.005, .995*.9951-1),
                }[case["mode"]]
            points = [(0, start), ((slots-16)*period, end), (duration, end)]
        result.append(np.array(points, dtype=float))
    return result


def verify_netlist(text, case, profile, calibrations, points):
    lines = text.splitlines()
    assert "set numdgt=17" in lines and case["archive_numdgt"] == 17
    assert "Vprobe " not in text and "Rcancel cout cancel 1000" in lines
    assert not any(l.startswith(("Vcin ", "Vsin ")) for l in lines)
    raw = calibrations[profile.get("wave", "nominal")]
    base = []
    for key, scale_key in (("first_gains", "first_gain_scale"),
                           ("second_gains", "second_gain_scale")):
        base.extend(.4*np.array(raw[key])*profile.get(scale_key, (1, 1, 1))*
                    np.array(profile.get("path_gain_scale", (1, 1, 1))))
    base.append(-.8*raw["second_gains"][2]*profile.get("second_gain_scale", (1, 1, 1))[2]*
                profile.get("cancel_gain_scale", 1)*profile["cancellation_trim"]/3.3)
    for index, (name, coefficient) in enumerate(zip(GAIN_NAMES, base)):
        if case["mode"] == "constant":
            line = next(l for l in lines if l.startswith("E"+name+" "))
            assert np.isclose(float(line.split()[-1]), coefficient*(1+case["errors"][index]),
                              rtol=1e-10, atol=1e-12)
            source = f"DC {case['errors'][index]:.12g}"
        else:
            line = next(l for l in lines if l.startswith("B"+name+" "))
            expression = line.split("V=", 1)[1]
            match = re.fullmatch(r"([-+0-9.e]+)\*v\((\w+),(\w+)\)\*\(1\+v\(err([0-6])\)\)",
                                 expression)
            assert match and int(match[4]) == index
            assert np.isclose(float(match[1]), coefficient, rtol=1e-10, atol=1e-12)
            assert (match[2], match[3]) == (("cancel", "0") if index == 6 else
                                          (("a", "b", "d")[index % 3], "0"))
            source = "PWL("+" ".join(f"{t:.12g} {v:.12g}" for t, v in points[index])+")"
        assert f"Verr{index} err{index} 0 {source}" in lines
    assert f".options method={case['method']}" in lines
    assert sum(l.startswith("B") for l in lines) == (0 if case["mode"] == "constant" else 7)


def verify(folder):
    summary_path = folder / "dynamic_reliability_summary.json"
    summary_bytes = summary_path.read_bytes()
    summary = json.loads(summary_bytes.decode("utf-8"))
    assert len(summary["boundaries"]) == 8, "Wait for all eight profiles before final audit."
    source_folder = Path(summary["source"])
    source_path = source_folder / "gain_calibration_summary.json"
    source = json.loads(source_path.read_text(encoding="utf-8"))
    assert summary["archive_numdgt"] == 17
    assert hashlib.sha256(source_path.read_bytes()).hexdigest() == summary["signature"]["source_summary_sha256"]
    assert source["frozen_profiles"] == summary["frozen_profiles"]
    assert source["calibrations"] == summary["calibrations"]
    for name, digest in summary["signature"]["code_sha256"].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
    assert not summary["failures"]
    states = [f"{i:03b}" for i in range(8)]
    seqs, corners = summary["sequences"], summary["corners"]
    assert set(zip(seqs["euler"], seqs["euler"][1:])) == set(itertools.product(states, repeat=2))
    assert len(seqs["triples"]) == 514
    assert set(zip(seqs["triples"], seqs["triples"][1:], seqs["triples"][2:])) == set(
        itertools.product(states, repeat=3))
    assert len(corners) == 128
    expected_corners = {tuple(.01*s for s in signs) for signs in itertools.product((-1, 1), repeat=7)}
    assert {tuple(e) for _, e in corners} == expected_corners
    rng = np.random.default_rng(summary["seed"]+1)
    assert np.array_equal(np.array([e for _, e in summary["interior"]]), rng.uniform(-.01, .01, (32, 7)))
    assert len({c["case"] for c in summary["cases"]}) == len(summary["cases"])
    checked, total_decisions, regressions = [], 0, []
    for number, case in enumerate(summary["cases"], 1):
        key = case["case"]
        record = json.loads((folder / "runs" / (key+".json")).read_text(encoding="utf-8"))
        assert record == case
        descriptor = {k: case[k] for k in ("case", "profile", "scope", "tag", "errors",
                                          "period_ns", "sequence", "mode", "dt_ps", "method")}
        fingerprint = hashlib.sha256(json.dumps(
            {"spec": descriptor, "signature": summary["signature"]}, sort_keys=True).encode()).hexdigest()
        assert case["fingerprint"] == fingerprint
        order, rail = seqs[case["sequence"]], case["supply_V"]
        period = case["period_ns"]*1e-9
        assert case["slots"] == len(order) and case["decisions"] == 2*len(order)
        points = prescribed_points(case, len(order))
        for expected, archived in zip(points, case["control_points"]):
            assert np.allclose(expected, archived, rtol=1e-12, atol=1e-18)
        text = (folder / "runs" / (key+".cir")).read_text(encoding="ascii")
        profile = summary["frozen_profiles"][case["profile"]]
        assert rail == profile.get("supply", 3.3)
        verify_netlist(text, case, profile, summary["calibrations"], points)
        log = (folder / "runs" / (key+".log")).read_text(encoding="utf-8")
        assert ("warning" in log.lower()) == case["warning"]
        assert not case["warning"]
        rows = read_csv(folder / "runs" / (key+".csv"))
        assert len(rows) == 2*len(order)
        with np.load(folder / "runs" / (key+".npz"), allow_pickle=False) as archive:
            sampling = {name: archive[name] for name in archive.files}
            assert np.all(np.diff(sampling["time"]) > 0)
            if case["duplicate_time_points"]:
                with np.load(folder / "runs" / (key+".raw.npz"), allow_pickle=False) as raw_archive:
                    data = {name: raw_archive[name] for name in raw_archive.files}
            else:
                data = sampling
            times = data["time"]
            differences = np.diff(times)
            assert times[0] == 0 and np.all(differences >= 0)
            assert len(times) == case["raw_points"] and len(sampling["time"]) == case["unique_points"]
            assert sum(differences == 0) == case["duplicate_time_points"]
            keep = np.r_[differences != 0, True]
            assert all(np.array_equal(sampling[k], data[k][keep]) for k in sampling)
            starts = np.r_[0, np.flatnonzero(differences != 0)+1]
            stops = np.r_[starts[1:], len(times)]
            repeated = [(a, b) for a, b in zip(starts, stops) if b-a > 1]
            maximum = max((float(np.ptp(values[a:b])) for a, b in repeated
                           for name, values in data.items() if name != "time"), default=0)
            output_maximum = max((float(np.ptp(data[name][a:b])) for a, b in repeated
                                  for name in ("carry", "sum")), default=0)
            assert maximum <= 1e-9
            assert maximum == case["duplicate_vector_max_difference"]
            assert output_maximum == case["duplicate_output_max_difference_V"]
            assert abs(times[-1]-len(order)*period) < 1e-15
            assert all(np.isfinite(values).all() for values in data.values())
            # Bound decimal export rounding separately from same-time solver records.
            time_rounding = np.zeros_like(times)
            nonzero = times > 0
            time_rounding[nonzero] = .51*10.0**(np.floor(np.log10(times[nonzero]))-16)
            step_bound = case["dt_ps"]*1e-12*1.000001+time_rounding[:-1]+time_rounding[1:]
            assert np.all(np.diff(times) <= step_bound)
            for index, pts in enumerate(points):
                expected = np.interp(times, pts[:, 0], pts[:, 1])
                slope = max(abs(np.diff(pts[:, 1])/np.diff(pts[:, 0])))
                assert np.all(abs(data[f"error{index}"]-expected) <= 2e-9+slope*time_rounding)
                if case["scope"] != "beyond_budget":
                    assert max(abs(expected)) <= .0100000001
            for name in ("carry", "sum"):
                assert min(data[name]) >= -1e-6 and max(data[name]) <= rail+1e-6
            for bit, name in enumerate(("a", "b", "cin_bit")):
                skew = profile.get("input_skew_ns", (0, 0, 0))[bit]*1e-9
                t_input, v_input = [0.0], [int(order[0][bit])]
                for index in range(1, len(order)):
                    t_input.extend([index*period+skew, index*period+skew+1e-9])
                    v_input.extend([int(order[index-1][bit]), int(order[index][bit])])
                t_input.append(len(order)*period)
                v_input.append(int(order[-1][bit]))
                source_difference = abs(data[name]-np.interp(times, t_input, v_input))
                assert np.all(source_difference <= 2e-8+time_rounding/1e-9), (key, name)
            passed, samples, unsettled, margins, settling, first_bad = 0, 0, 0, [], [], None
            worst_slot = None
            for row_number, row in enumerate(rows):
                index = row_number//2
                channel = ("carry", "sum")[row_number % 2]
                assert int(row["slot_index"]) == index and row["channel"] == channel
                assert row["state"] == order[index] and row["from_state"] == order[max(0, index-1)]
                count = order[index].count("1")
                expected = int(count >= 2) if channel == "carry" else count % 2
                assert int(row["expected"]) == expected
                start, end = index*period, (index+1)*period
                left, right = np.searchsorted(times, (start, end), side="left")
                t, values = times[left:right], data[channel][left:right]
                margin = values-.7*rail if expected else .3*rail-values
                late = t >= start+.8*period
                minimum = float(min(margin[late]))
                margins.append(minimum)
                late_good = minimum >= 0
                sample = float(np.interp(start+.9*period, sampling["time"], sampling[channel]))
                sample_good = sample >= .7*rail if expected else sample <= .3*rail
                assert abs(start+.9*period-float(row["sample_time_s"])) < 1e-18
                assert abs(sample-float(row["sampled_output_V"])) < 1e-9
                assert sample_good == (row["sample_correct"] == "True")
                assert late_good == (row["late_window_correct"] == "True")
                good = sample_good and late_good
                assert good == (row["correct"] == "True")
                passed += int(good)
                samples += int(sample_good)
                bad = np.flatnonzero(margin < 0)
                previous_count = order[max(0, index-1)].count("1")
                previous = int(previous_count >= 2) if channel == "carry" else previous_count % 2
                excursion = bool(index and previous == expected and len(bad))
                assert excursion == (row["unchanged_expected_with_excursion"] == "True")
                established = (float(t[bad[-1]+1]-start)
                               if len(bad) and bad[-1]+1 < len(t)
                               else (0.0 if not len(bad) else None))
                if established is None:
                    unsettled += 1
                    assert row["settling_to_30_70_percent_s"] == ""
                else:
                    settling.append(established*1e9)
                    assert abs(established-float(row["settling_to_30_70_percent_s"])) < 1e-18
                if worst_slot is None or minimum < worst_slot["margin_V"]:
                    worst_slot = {"slot": index, "state": order[index], "channel": channel,
                                  "margin_V": minimum}
                if not good and first_bad is None:
                    first_bad = {"slot": index, "state": order[index], "channel": channel,
                                 "sample_error6": float(np.interp(start+.9*period, sampling["time"],
                                                                 sampling["error6"])),
                                 "slot_start_ns": start*1e9}
            assert passed == case["passed"] and samples == case["sample_passed"]
            assert unsettled == case["unsettled"] and worst_slot == case["worst_slot"]
            if first_bad is None:
                assert case["first_bad"] is None
            else:
                for field in ("slot", "state", "channel"):
                    assert first_bad[field] == case["first_bad"][field]
                assert abs(first_bad["sample_error6"]-case["first_bad"]["sample_error6"]) < 1e-10
                assert abs(first_bad["slot_start_ns"]-case["first_bad"]["slot_start_ns"]) < 1e-9
            assert abs(min(margins)-case["minimum_late_margin_V"]) < 1e-12
            assert abs(max(settling, default=0)-case["max_settling_ns"]) < 1e-9
            energy = float(np.trapz(-rail*data["supply_current"], times))
            assert np.isclose(energy, case["energy_J"], rtol=1e-12, atol=1e-22)
            assert np.isclose(energy/times[-1], case["power_W"], rtol=1e-12)
            total_decisions += len(rows)
            checked.append({"case": key, "profile": case["profile"], "scope": case["scope"],
                            "sequence": case["sequence"], "period_ns": case["period_ns"],
                            "decisions": len(rows), "passed": passed, "late_margin_V": min(margins),
                            "max_settling_ns": max(settling, default=0),
                            "power_mW": energy/times[-1]*1e3, "energy_per_slot_pJ": energy/len(order)*1e12})
            if case["scope"] == "center80":
                previous = f"{case['profile']}_80ns_200ps_trap"
                with np.load(source_folder / (previous+".npz")) as old:
                    waveform_difference = max(float(np.max(abs(
                        data[k]-np.interp(times, old["time"], old[k])))) for k in ("carry", "sum"))
                    old_energy = float(np.trapz(-rail*old["supply_current"], old["time"]))
                    energy_difference = abs(energy/old_energy-1)
                    regressions.append({"profile": case["profile"],
                                        "max_output_difference_V": waveform_difference,
                                        "energy_relative_difference": energy_difference})
        if number % 128 == 0:
            print(f"Raw audit {number}/{len(summary['cases'])}", flush=True)
    boundaries = []
    for boundary in summary["boundaries"]:
        label, candidate = boundary["profile"], boundary["candidate_period_ns"]
        cases = [c for c in summary["cases"] if c["profile"] == label]
        for scope in ("corners80", "interior80", "center80"):
            rows = [c for c in cases if c["scope"] == scope]
            assert len(rows) == {"corners80": 128, "interior80": 32, "center80": 1}[scope]
        assert {tuple(c["errors"]) for c in cases if c["scope"] == "corners80"} == expected_corners
        assert {tuple(c["errors"]) for c in cases if c["scope"] == "interior80"} == {
            tuple(e) for _, e in summary["interior"]}
        assert all(c["errors"] == [0]*7 for c in cases if c["scope"] == "center80")
        eighty = sorted((c for c in cases if c["scope"] == "corners80"), key=lambda c: c["case"])
        slow80 = max(eighty, key=lambda c: (c["max_settling_ns"], -c["minimum_late_margin_V"]))
        low80 = min(eighty, key=lambda c: c["minimum_late_margin_V"])
        probes80 = {(c["tag"], tuple(c["errors"])) for c in (slow80, low80)} | {("center", (0,)*7)}
        assert {(tag, tuple(errors)) for tag, errors in boundary["probes80"]} == probes80
        histories = [c for c in cases if c["scope"] == "history80"]
        expected_histories = {(tag+"_"+seq, errors, seq) for tag, errors in probes80
                              for seq in ("triples", "stress", "seeded_history")}
        assert len(histories) == len(expected_histories)
        assert {(c["tag"], tuple(c["errors"]), c["sequence"]) for c in histories} == expected_histories
        assert all(c["period_ns"] == 80 and c["mode"] == "constant" and
                   c["dt_ps"] == 200 and c["method"] == "trap" for c in histories)
        timing = [c for c in cases if c["scope"] == "timing_probe"]
        expected_timing = {(tag+"_"+seq, errors, seq, p) for tag, errors in probes80
                           for seq in ("euler", "stress") for p in summary["period_grid_ns"]}
        assert len(timing) == len(expected_timing)
        assert {(c["tag"], tuple(c["errors"]), c["sequence"], c["period_ns"])
                for c in timing} == expected_timing
        assert all(c["mode"] == "constant" and c["dt_ps"] == 200 and
                   c["method"] == "trap" for c in timing)
        candidate_cases = [c for c in cases if c["period_ns"] == candidate and c["scope"] ==
                           ("corners80" if candidate == 80 else "candidate_corners")]
        confirmed = False
        if candidate is not None:
            assert len(candidate_cases) == 128
            assert {tuple(c["errors"]) for c in candidate_cases} == expected_corners
            assert all(c["passed"] == c["decisions"] for c in candidate_cases)
            slow = max(sorted(candidate_cases, key=lambda c: c["case"]),
                       key=lambda c: (c["max_settling_ns"], -c["minimum_late_margin_V"]))
            low = min(sorted(candidate_cases, key=lambda c: c["case"]), key=lambda c: c["minimum_late_margin_V"])
            probes = {(c["tag"], tuple(c["errors"])) for c in (slow, low)} | {("center", (0,)*7)}
            checks = [c for c in cases if c["scope"] == "candidate_crosscheck"]
            assert len(checks) == len(probes)*9
            assert {(c["tag"], tuple(c["errors"]), c["sequence"], c["dt_ps"], c["method"]) for c in checks} == {
                (tag+"_"+seq, errors, seq, dt, method) for tag, errors in probes
                for seq in ("euler", "triples", "stress")
                for dt, method in ((100, "trap"), (50, "trap"), (100, "gear"))}
            drift = [c for c in cases if c["scope"] in ("bounded_drift", "drift_crosscheck")]
            assert len(drift) == 15
            modes = {
                "bounded_positive", "bounded_negative", "all_triangular", "trigger_positive", "trigger_negative"}
            assert {(c["mode"], c["dt_ps"], c["method"]) for c in drift} == {
                (mode, dt, method) for mode in modes
                for dt, method in ((100, "trap"), (50, "trap"), (100, "gear"))}
            assert all(c["errors"] == slow["errors"] and c["sequence"] == "triples" and
                       c["period_ns"] == candidate and c["tag"] == c["mode"] for c in drift)
            beyond = [c for c in cases if c["scope"] == "beyond_budget"]
            assert len(beyond) == 2 and {c["mode"] for c in beyond} == {"loss_ramp", "gain_ramp"}
            assert all(c["errors"] == [0]*7 and c["sequence"] == "triples" and
                       c["period_ns"] == candidate and c["dt_ps"] == 100 and
                       c["method"] == "trap" for c in beyond)
            for p in summary["period_grid_ns"]:
                if p >= candidate:
                    continue
                probes_pass = all(c["passed"] == c["decisions"] for c in timing if c["period_ns"] == p)
                if probes_pass:
                    rejected = [c for c in cases if c["scope"] == "candidate_corners" and c["period_ns"] == p]
                    assert len(rejected) == 128
                    assert {tuple(c["errors"]) for c in rejected} == expected_corners
                    assert any(c["passed"] < c["decisions"] for c in rejected)
            confirmed = all(c["passed"] == c["decisions"] for c in checks+drift)
        assert confirmed == boundary["candidate_confirmed"]
        boundaries.append({"profile": label, "candidate_period_ns": candidate,
                           "confirmed": confirmed,
                           "corner80_margin_V": min(c["minimum_late_margin_V"] for c in cases
                                                   if c["scope"] == "corners80"),
                           "candidate_corner_margin_V": min((c["minimum_late_margin_V"] for c in candidate_cases),
                                                            default=None),
                           "candidate_corner_max_settling_ns": max((c["max_settling_ns"] for c in candidate_cases),
                                                                  default=None)})
    accepted = (
        len(boundaries) == 8 and all(b["confirmed"] for b in boundaries) and
        all(c["passed"] == c["decisions"] for c in checked if c["scope"] in
            ("corners80", "interior80", "center80", "history80")))
    assert accepted == summary["finite_model_gate"]
    comparisons = []
    references = [c for c in summary["cases"] if c["dt_ps"] == 100 and c["method"] == "trap" and
                  c["scope"] in ("candidate_crosscheck", "bounded_drift")]
    for reference in references:
        others = [c for c in summary["cases"] if c["case"] != reference["case"] and
                  c["scope"] in ("candidate_crosscheck", "drift_crosscheck") and
                  all(c[k] == reference[k] for k in
                      ("profile", "tag", "sequence", "period_ns", "mode", "errors"))]
        assert len(others) == 2
        with np.load(folder / "runs" / (reference["case"]+".npz")) as left:
            for other in others:
                with np.load(folder / "runs" / (other["case"]+".npz")) as right:
                    comparisons.append({
                        "profile": reference["profile"], "reference": reference["case"],
                        "other": other["case"],
                        "energy_relative_difference": abs(reference["energy_J"]/other["energy_J"]-1),
                        "max_output_difference_V": max(float(np.max(abs(
                            left[k]-np.interp(left["time"], right["time"], right[k]))))
                            for k in ("carry", "sum"))})
    write_csv(folder / "dynamic_case_table.csv", checked)
    write_csv(folder / "dynamic_boundaries.csv", boundaries)
    assert summary_path.read_bytes() == summary_bytes, "Summary changed during independent audit."
    result = {"data_integrity": "PASS", "finite_model_gate": accepted, "case_count": len(checked),
              "summary_sha256": hashlib.sha256(summary_bytes).hexdigest(),
              "checked_decisions": total_decisions, "boundaries": boundaries,
              "raw_same_time_points": sum(c["duplicate_time_points"] for c in summary["cases"]),
              "max_same_time_output_difference_V": max(
                  (c["duplicate_output_max_difference_V"] for c in summary["cases"]), default=0),
              "center_regression": regressions, "numerical_comparisons": comparisons,
              "note": "Finite corners, interior samples and selected histories; "
              "not a mathematical guarantee over the continuous dynamic error box."}
    (folder / "dynamic_reliability_verification.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "第十轮结果")
    verify(parser.parse_args().output)
