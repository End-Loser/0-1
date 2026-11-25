"""Independent raw-waveform audit and calibrated reliability tables."""

import argparse
import csv
import itertools
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).parent


def read_csv(path):
    with path.open(encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def write_csv(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def means(folder, key, slots):
    with np.load(folder / (key+".npz"), allow_pickle=False) as data:
        t = data["time"]
        assert np.all(np.diff(t) > 0)
        assert abs(t[-1]-slots*80e-9) < 1e-15
        assert all(np.isfinite(data[k]).all() for k in data.files)
        rows = []
        for i in range(slots):
            mask = (t >= (i+.8)*80e-9) & (t < (i+1)*80e-9)
            rows.append({k: float(data[k][mask].mean()) for k in data.files if k != "time"})
        return rows, float(np.trapz(data["supply_current"], t))


def verify(folder):
    summary = json.loads((folder / "gain_calibration_summary.json").read_text(encoding="utf-8"))
    training = json.loads((folder / "training.json").read_text(encoding="utf-8"))
    assert training == summary["training"] and len(training) == 8
    states = [f"{i:03b}" for i in range(8)]
    order = summary["order"]
    assert set(zip(order, order[1:])) == set(itertools.product(states, repeat=2))
    for record in training:
        label = record["profile"]
        rails, i1 = means(folder, label+"_training_rails", 2)
        paths, i2 = means(folder, label+"_training_paths", 6)
        low, high = rails[0]["carry"], rails[1]["carry"]
        second = [paths[i]["sum_input"]-paths[0]["sum_input"] for i in (1, 2, 3)]
        response = paths[4]["sum_input"]-paths[5]["sum_input"]
        trim = 2*np.mean(second)/response
        assert np.isclose(trim, record["trim"], rtol=1e-12)
        assert np.isclose(low, record["carry_low_V"], atol=1e-12)
        assert np.isclose(high, record["carry_high_V"], atol=1e-12)
        assert record["training_means"] == paths
        assert np.allclose(second, record["second_steps_V"], rtol=1e-12)
        assert record["acquisition_ns"] == 640
        supply = record["frozen_profile"].get("supply", 3.3)
        assert np.isclose(-supply*(i1+i2), record["local_supply_energy_J"], rtol=1e-12)
        assert record["frozen_profile"] == summary["frozen_profiles"][label]
        assert record["trim"] == record["frozen_profile"]["cancellation_trim"]
        # Settled training is an acquisition check, not noise/ADC accuracy.
        # Allow at most 1% of the report's assumed 0.5 mV readout error.
        assert max(r[k] for r in record["rail_spans"]+record["training_spans"]
                   for k in ("carry", "carry_input", "sum_input", "cancel")) < 5e-6
    static = read_csv(folder / "calibration_static.csv")
    envelopes = read_csv(folder / "residual_envelopes.csv")
    drifts = read_csv(folder / "cancellation_drift.csv")
    assert len(static) == 128 and len(envelopes) == 8*7*32 and len(drifts) == 8*61*8
    for row in static+envelopes+drifts:
        count, rail = sum(map(int, row["state"])), float(row["supply_V"])
        exp = (int(count >= 2), count % 2)
        assert exp == (int(row["expected_carry"]), int(row["expected_sum"]))
        good = row["internal_valid"] == "True"
        for channel, expected in zip(("carry", "sum"), exp):
            value = float(row[channel+"_V"])
            margin = value-.7*rail if expected else .3*rail-value
            assert abs(margin-float(row[channel+"_margin_V"])) < 1e-12
            good = good and margin >= 0
        assert bool(good) == (row["correct"] == "True")
    decisions = read_csv(folder / "calibration_decisions.csv")
    grouped = {}
    for row in decisions:
        grouped.setdefault(row["case"], []).append(row)
    assert len(decisions) == len(summary["cases"])*130
    checked, comparisons = [], []
    for case in summary["cases"]:
        key, rail, period = case["case"], case["supply_V"], case["period_ns"]*1e-9
        assert not case["log_mentions_warning"]
        text = (folder / (key+".cir")).read_text(encoding="ascii")
        assert "Vprobe " not in text and "Rcancel cout cancel 1000" in text
        lines = text.splitlines()
        # Independently check frozen actuator coefficients, including residual changes.
        profile = summary["frozen_profiles"][case["profile"]]
        gains = summary["calibrations"][profile.get("wave", "nominal")]["second_gains"]
        multiplier = profile.get("second_gain_scale", (1, 1, 1))[2]
        expected_coeff = (-.8*gains[2]*multiplier*profile.get("cancel_gain_scale", 1)
                          *profile["cancellation_trim"]/3.3)
        if case["scope"] == "residual_corner":
            sign = int(key.split("_s")[-1].split("_")[0])
            expected_coeff *= 1-sign*.01
        actual_coeff = float(next(l for l in lines if l.startswith("Ecancel ")).split()[-1])
        assert np.isclose(actual_coeff, expected_coeff, rtol=1e-10)
        for prefix, gain_key, scale_key in (("c", "first_gains", "first_gain_scale"),
                                           ("s", "second_gains", "second_gain_scale")):
            raw_gains = summary["calibrations"][profile.get("wave", "nominal")][gain_key]
            for bit, name in enumerate(("a", "b", "d")):
                coefficient = (.4*raw_gains[bit]*profile.get(scale_key, (1, 1, 1))[bit]
                               *profile.get("path_gain_scale", (1, 1, 1))[bit])
                if case["scope"] == "residual_corner":
                    token = "_c" if prefix == "c" else "_s"
                    sign = int(key.split(token)[-1].split("_")[0])
                    coefficient *= 1+sign*.01
                actual = float(next(l for l in lines if l.startswith(
                    f"E{prefix}{name} ")).split()[-1])
                assert np.isclose(actual, coefficient, rtol=1e-10)
        with np.load(folder / (key+".npz"), allow_pickle=False) as data:
            t = data["time"]
            assert t[0] == 0 and np.all(np.diff(t) > 0)
            assert max(np.diff(t)) <= case["dt_ps"]*1e-12*1.001
            assert abs(t[-1]-65*period) < 1e-15
            assert all(np.isfinite(data[k]).all() for k in data.files)
            energy = float(np.trapz(-rail*data["supply_current"], t))
            assert np.isclose(energy, case["supply_energy_J"], rtol=1e-12)
            rows, passed, sampled_count, margins, settling = grouped[key], 0, 0, [], []
            assert len(rows) == 130
            for row in rows:
                index = int(row["slot_index"])
                assert row["state"] == order[index]
                count = sum(map(int, row["state"]))
                expected = int(count >= 2) if row["channel"] == "carry" else count % 2
                assert expected == int(row["expected"])
                start, end = index*period, (index+1)*period
                mask = (t >= start) & (t < end)
                local_t, values = t[mask], data[row["channel"]][mask]
                margin = values-.7*rail if expected else .3*rail-values
                late = local_t >= start+.8*period
                late_good = bool(np.any(late) and min(margin[late]) >= 0)
                sample = float(np.interp(start+.9*period, t, data[row["channel"]]))
                sample_good = sample >= .7*rail if expected else sample <= .3*rail
                assert abs(sample-float(row["sampled_output_V"])) < 1e-9
                assert late_good == (row["late_window_correct"] == "True")
                assert sample_good == (row["sample_correct"] == "True")
                assert (sample_good and late_good) == (row["correct"] == "True")
                passed += int(sample_good and late_good)
                sampled_count += int(sample_good)
                margins.append(float(min(margin[late])))
                bad = np.flatnonzero(margin < 0)
                settled = (float(local_t[bad[-1]+1]-start)
                           if len(bad) and bad[-1]+1 < len(local_t)
                           else (0.0 if not len(bad) else None))
                if settled is None:
                    assert row["settling_to_30_70_percent_s"] == ""
                else:
                    assert abs(settled-float(row["settling_to_30_70_percent_s"])) < 1e-18
                    settling.append(settled*1e9)
            assert passed == case["passed"] and sampled_count == case["sample_passed"]
            assert abs(min(margins)-case["minimum_late_margin_V"]) < 1e-12
            checked.append({"case": key, "profile": case["profile"], "scope": case["scope"],
                            "period_ns": case["period_ns"], "passed": passed,
                            "late_margin_V": min(margins), "max_settling_ns": max(settling),
                            "power_mW": energy/t[-1]*1e3, "energy_per_slot_pJ": energy/65*1e12})
    costs = []
    for profile in summary["profiles"]:
        label = profile["profile"]
        primary = [c for c in checked if c["profile"] == label and c["scope"] == "primary"]
        passing = [c["period_ns"] for c in primary if c["passed"] == 130]
        minimum = min(passing) if passing else None
        assert minimum == profile["minimum_grid_period_ns"]
        cross = [c for c in summary["cases"] if c["profile"] == label and c["scope"] == "crosscheck"]
        reference = next(c for c in cross if c["dt_ps"] == 100 and c["method"] == "trap")
        for other in cross:
            if other == reference:
                continue
            with np.load(folder / (reference["case"]+".npz")) as left, np.load(
                    folder / (other["case"]+".npz")) as right:
                comparisons.append({"profile": label, "method": other["method"],
                                    "dt_ps": other["dt_ps"],
                                    "energy_relative_difference": abs(reference["supply_energy_J"]/
                                                                       other["supply_energy_J"]-1),
                                    "max_output_difference_V": max(float(np.max(abs(
                                        left[k]-np.interp(left["time"], right["time"], right[k]))))
                                        for k in ("carry", "sum"))})
        levels = []
        for level in profile["residual_envelopes"]:
            rows = [r for r in envelopes if r["profile"] == label and
                    float(r["residual"]) == level["residual"]]
            assert len(rows) == 32
            assert all(r["correct"] == "True" for r in rows) == level["all_correct"]
            levels.append(level)
        drift_good = {}
        for drift in summary["drift_grid"]:
            rows = [r for r in drifts if r["profile"] == label and float(r["drift"]) == drift]
            assert len(rows) == 8
            drift_good[drift] = all(r["correct"] == "True" for r in rows)
        negative, positive = 0, 0
        for sign in (-1, 1):
            limit = 0
            for step in range(1, 31):
                if not drift_good[round(sign*step*.01, 8)]:
                    break
                limit = step*.01
            if sign < 0:
                negative = limit
            else:
                positive = limit
        train = next(r for r in training if r["profile"] == label)
        costs.append({"profile": label, "static_before": sum(r["correct"] == "True" for r in static
                     if r["profile"] == label and r["mode"] == "before"),
                      "static_after": sum(r["correct"] == "True" for r in static
                     if r["profile"] == label and r["mode"] == "after"),
                      "trim": train["trim"], "minimum_grid_period_ns": minimum,
                      "power_mW": reference["mean_supply_power_W"]*1e3,
                      "energy_per_slot_pJ": reference["energy_per_slot_J"]*1e12,
                      "acquisition_ns": 640, "training_energy_pJ": train["local_supply_energy_J"]*1e12,
                      "largest_tested_contiguous_residual": next(
                          (levels[i-1]["residual"] for i, level in enumerate(levels)
                           if not level["all_correct"]), levels[-1]["residual"]),
                      "static_cancel_loss_last_passing": negative,
                      "static_cancel_increase_last_passing": positive})
    acceptance = (all(c["static_after"] == 8 for c in costs) and
                  all(c["passed"] == 130 for c in checked if c["scope"] != "primary") and
                  all(p["residual_envelopes"][1]["all_correct"] for p in summary["profiles"]))
    assert acceptance == summary["acceptance"]
    write_csv(folder / "calibration_case_table.csv", checked)
    write_csv(folder / "calibration_costs.csv", costs)
    result = {"data_integrity": "PASS", "bounded_proxy_acceptance": acceptance,
              "checked_decisions": len(decisions), "static_checks": len(static)+len(envelopes)+len(drifts),
              "costs": costs, "numerical_comparisons": comparisons,
              "performance_gate": "Not full hardware reliability: dynamic continuum, sensor accuracy "
                                  "and real trim implementation remain unverified"}
    (folder / "gain_calibration_verification.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "第九轮结果")
    verify(parser.parse_args().output)
