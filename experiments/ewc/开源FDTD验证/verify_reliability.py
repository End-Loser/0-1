"""Recompute reliability measurements without calling the test measurement code."""

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


def verify(folder):
    summary = json.loads((folder / "reliability_summary.json").read_text(encoding="utf-8"))
    static = read_csv(folder / "reliability_static.csv")
    decisions = read_csv(folder / "reliability_decisions.csv")
    assert not summary["failures"]
    assert len(static) == 64 and len(summary["cases"]) == 88
    assert len(decisions) == 88*130
    order = summary["order"]
    states = [f"{i:03b}" for i in range(8)]
    assert set(zip(order, order[1:])) == set(itertools.product(states, repeat=2))
    for row in static:
        count, rail = sum(map(int, row["state"])), float(row["supply_V"])
        expected = (int(count >= 2), count % 2)
        assert expected == (int(row["expected_carry"]), int(row["expected_sum"]))
        margins = []
        for channel, value in zip(("carry", "sum"), expected):
            voltage = float(row[channel + "_V"])
            margin = voltage - 0.7*rail if value else 0.3*rail - voltage
            assert abs(margin - float(row[channel + "_margin_V"])) < 1e-12
            margins.append(margin)
        good = min(margins) >= 0 and row["internal_valid"] == "True"
        assert good == (row["correct"] == "True")
    checked = []
    for case in summary["cases"]:
        with np.load(folder / (case["case"] + ".npz"), allow_pickle=False) as data:
            times, rail = data["time"], case["supply_V"]
            slot = case["period_ns"]*1e-9
            assert times[0] == 0 and np.all(np.diff(times) > 0)
            assert max(np.diff(times)) <= case["dt_ps"]*1e-12*1.001
            assert abs(times[-1] - 65*slot) < 1e-15
            assert all(np.isfinite(data[k]).all() for k in data.files)
            energy = float(np.trapz(-rail*data["supply_current"], times))
            assert np.isclose(energy, case["supply_energy_J"], rtol=1e-12, atol=1e-22)
            rows = [r for r in decisions if r["case"] == case["case"]]
            assert len(rows) == 130
            passed, samples, unsettled, excursions = 0, 0, 0, 0
            settling, all_margins = [], []
            phases = {str(p): 0 for p in (0.6, 0.7, 0.8, 0.9)}
            for row in rows:
                index = int(row["slot_index"])
                assert row["state"] == order[index] and row["from_state"] == order[max(0, index-1)]
                count = sum(map(int, row["state"]))
                expected = int(count >= 2) if row["channel"] == "carry" else count % 2
                assert expected == int(row["expected"])
                start, end = index*slot, (index+1)*slot
                mask = (times >= start) & (times < end)
                local_t, output = times[mask], data[row["channel"]][mask]
                margin = output - 0.7*rail if expected else 0.3*rail - output
                late = local_t >= start+0.8*slot
                late_good = bool(np.any(late) and np.min(margin[late]) >= 0)
                all_margins.append(float(np.min(margin[late])))
                sample_time = start+0.9*slot
                assert abs(sample_time-float(row["sample_time_s"])) < 1e-18
                sampled = float(np.interp(sample_time, times, data[row["channel"]]))
                assert abs(sampled-float(row["sampled_output_V"])) < 1e-9
                sample_good = sampled >= 0.7*rail if expected else sampled <= 0.3*rail
                assert sample_good == (row["sample_correct"] == "True")
                assert late_good == (row["late_window_correct"] == "True")
                assert (sample_good and late_good) == (row["correct"] == "True")
                passed += int(sample_good and late_good)
                samples += int(sample_good)
                bad = np.flatnonzero(margin < 0)
                settled = (float(local_t[bad[-1]+1] - start)
                           if len(bad) and bad[-1]+1 < len(local_t)
                           else (0.0 if not len(bad) else None))
                if settled is None:
                    assert row["settling_to_30_70_percent_s"] == ""
                    unsettled += 1
                else:
                    assert abs(settled-float(row["settling_to_30_70_percent_s"])) < 1e-18
                    settling.append(settled*1e9)
                prev_count = sum(map(int, row["from_state"]))
                previous = int(prev_count >= 2) if row["channel"] == "carry" else prev_count % 2
                excursion = bool(index and previous == expected and len(bad))
                assert excursion == (row["unchanged_expected_with_excursion"] == "True")
                excursions += int(excursion)
                for phase in phases:
                    v = float(np.interp(start+float(phase)*slot, times, data[row["channel"]]))
                    phases[phase] += int(v >= 0.7*rail if expected else v <= 0.3*rail)
            assert passed == case["passed"] and samples == case["sample_passed"]
            assert unsettled == case["unsettled"] and excursions == case["unchanged_expected_excursions"]
            assert abs(max(settling, default=0)-case["max_settling_ns"]) < 1e-8
            assert abs(min(all_margins)-case["minimum_late_margin_V"]) < 1e-12
            assert phases == case["phase_sample_counts"]
            checked.append({
                "case": case["case"], "profile": case["profile"], "period_ns": case["period_ns"],
                "scope": case["scope"], "passed": passed, "decisions": len(rows),
                "minimum_late_margin_V": min(all_margins),
                "mean_supply_power_mW": energy/times[-1]*1000,
                "energy_per_slot_pJ": energy/65*1e12,
                "max_settling_ns": max(settling, default=0),
            })
    boundaries, sensitivity, comparisons = [], [], []
    for profile in summary["profiles"]:
        label = profile["profile"]
        primary = [c for c in summary["cases"] if c["profile"] == label and c["scope"] == "primary"]
        passing = [c for c in primary if c["passed"] == 130]
        minimum = min(c["period_ns"] for c in passing) if passing else None
        assert minimum == profile["minimum_passing_grid_period_ns"]
        static_rows = [r for r in static if r["profile"] == label]
        static_good = all(r["correct"] == "True" for r in static_rows)
        assert static_good == profile["static_all_correct"]
        checks = [c for c in summary["cases"] if c["profile"] == label and c["scope"] == "crosscheck"]
        assert len(checks) == 3
        cross_good = all(c["passed"] == 130 for c in checks)
        assert cross_good == profile["candidate_crosschecks_all_correct"]
        expected_class = ("static_logic_failure" if not static_good else
                          "tested_grid_candidate" if minimum is not None and cross_good
                          else "no_crosschecked_candidate_in_grid")
        assert profile["classification"] == expected_class
        reference = next(c for c in checks if c["dt_ps"] == 100 and c["method"] == "trap")
        for other in checks:
            if other["case"] == reference["case"]:
                continue
            with np.load(folder / (reference["case"]+".npz")) as left, np.load(
                    folder / (other["case"]+".npz")) as right:
                comparison = {
                    "profile": label, "left": reference["case"], "right": other["case"],
                    "energy_relative_difference": abs(reference["supply_energy_J"] -
                                                      other["supply_energy_J"])/abs(other["supply_energy_J"]),
                }
                for name in ("carry", "sum"):
                    comparison[name+"_waveform_max_difference_V"] = float(np.max(abs(
                        left[name]-np.interp(left["time"], right["time"], right[name]))))
                comparisons.append(comparison)
        boundary = {
            "profile": label, "static_passed": sum(r["correct"] == "True" for r in static_rows),
            "static_total": 8, "classification": expected_class, "minimum_grid_period_ns": minimum,
            "crosschecked_late_margin_V": reference["minimum_late_margin_V"],
            "crosschecked_mean_power_mW": reference["mean_supply_power_W"]*1000,
            "crosschecked_energy_per_slot_pJ": reference["energy_per_slot_J"]*1e12,
        }
        boundaries.append(boundary)
        if expected_class == "tested_grid_candidate":
            for aux in (0.0, 0.01, 0.1, 1.0):
                latency = 4*minimum*1e-9
                sensitivity.append({
                    "profile": label, "assumed_unmodeled_interface_power_W": aux,
                    "projected_serial_4bit_add_time_ns": minimum*4,
                    "projected_serial_4bit_adds_per_s": 1/latency,
                    "projected_partial_plus_assumed_add_energy_nJ":
                        (reference["mean_supply_power_W"]+aux)*latency*1e9,
                    "scope": "Illustrative extrapolation, not simulated multi-bit or measured RF energy",
                })
    write_csv(folder / "reliability_case_table.csv", checked)
    write_csv(folder / "reliability_boundaries.csv", boundaries)
    write_csv(folder / "interface_power_sensitivity.csv", sensitivity)
    result = {"data_integrity": "PASS", "case_count": len(checked),
              "checked_decisions": len(decisions), "boundaries": boundaries,
              "numerical_comparisons": comparisons,
              "note": "Deterministic test points; no empirical hardware error probability."}
    (folder / "reliability_verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    plot(folder, summary)
    print(json.dumps({"data_integrity": "PASS", "boundaries": boundaries}, indent=2))


def plot(folder, summary):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    profiles = [p["profile"] for p in summary["profiles"]]
    periods = summary["period_grid_ns"]
    values = np.array([[next(c["passed"] for c in summary["cases"]
                             if c["profile"] == p and c["period_ns"] == t and c["scope"] == "primary")
                        for t in periods] for p in profiles])
    figure, ax = plt.subplots(figsize=(11, 5))
    image = ax.imshow(values, vmin=0, vmax=130, cmap="RdYlGn", aspect="auto")
    ax.set_xticks(range(len(periods)), periods)
    ax.set_yticks(range(len(profiles)), profiles)
    ax.set(xlabel="State period (ns)", title="Deterministic coverage: passed output checks / 130")
    for row in range(len(profiles)):
        for col in range(len(periods)):
            ax.text(col, row, str(values[row, col]), ha="center", va="center", fontsize=9)
    figure.colorbar(image, ax=ax, label="Passed output checks")
    figure.tight_layout()
    figure.savefig(folder / "reliability_period_map.png", dpi=160)
    plt.close(figure)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "第八轮结果")
    verify(parser.parse_args().output)
