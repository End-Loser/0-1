"""Independent checks of archived causal-chain SPICE measurements."""

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


def verify(folder):
    summary = json.loads((folder / "coupled_summary.json").read_text(encoding="utf-8"))
    gains = json.loads((folder / "coupled_calibration.json").read_text(encoding="utf-8"))
    static = read_csv(folder / "coupled_static.csv")
    decisions = read_csv(folder / "coupled_decisions.csv")
    fields = np.load(folder / "coupled_waveforms.npz", allow_pickle=False)
    order = summary["order"]
    states = [f"{i:03b}" for i in range(8)]
    assert len(order) == 65 and set(zip(order, order[1:])) == set(itertools.product(states, repeat=2))
    assert len(static) == 144
    assert not summary["solver_failures"] and len(summary["transient_results"]) == 16
    assert len(decisions) == 2080
    for coefficients in gains.values():
        assert coefficients["first_max_residual"] < 1e-10
        assert coefficients["second_max_residual"] < 1e-10
    static_rates = {}
    for row in static:
        bits = np.array(list(map(int, row["state"])))
        count = int(bits.sum())
        assert int(row["expected_carry"]) == int(count >= 2)
        assert int(row["expected_sum"]) == count % 2
        wave = gains[row["wave_scenario"]]
        cancel = float(row["cancel_voltage_V"])
        predicted_first = 1.0 + 0.4 * np.dot(bits, wave["first_gains"])
        predicted_second = (1.0 + 0.4 * np.dot(bits, wave["second_gains"]) -
                            0.8 * wave["second_gains"][2] * cancel / 3.3)
        assert abs(predicted_first - float(row["carry_input_V"])) < 5e-8
        assert abs(predicted_second - float(row["sum_input_V"])) < 5e-8
        assert abs(cancel - float(row["carry_output_V"])) < 1e-8
        supply = 3.0 if row["scenario"] == "supply_3V" else 3.3
        good_c = (float(row["carry_output_V"]) >= 0.7 * supply if count >= 2
                  else float(row["carry_output_V"]) <= 0.3 * supply)
        good_s = (float(row["sum_output_V"]) >= 0.7 * supply if count % 2
                  else float(row["sum_output_V"]) <= 0.3 * supply)
        good = good_c and good_s and row["internal_voltages_valid"] == "True"
        assert good == (row["correct"] == "True")
        key = row["scenario"]
        passed, total = static_rates.get(key, (0, 0))
        static_rates[key] = passed + int(good), total + 1
    observed = []
    for item in summary["transient_results"]:
        label = item["scenario"]
        times = fields[label + "_time"]
        supply, slot = item["supply_V"], item["slot_ns"] * 1e-9
        assert times[0] == 0 and np.all(np.diff(times) > 0)
        assert np.max(np.diff(times)) <= item["dt_ps"] * 1e-12 * 1.001
        assert abs(times[-1] - len(order) * slot) < 1e-15
        for name in fields.files:
            if name.startswith(label + "_"):
                assert np.isfinite(fields[name]).all()
        energy = float(np.trapz(-supply * fields[label + "_supply_current"], times))
        assert np.isclose(energy, item["supply_energy_J"], rtol=1e-12, atol=1e-22)
        selected = [r for r in decisions if r["scenario"] == label]
        assert len(selected) == 130
        late_margins, settling, passed, sample_passed, glitches = [], [], 0, 0, 0
        for row in selected:
            index = int(row["slot_index"])
            assert row["state"] == order[index]
            assert row["from_state"] == order[max(0, index - 1)]
            count = sum(map(int, row["state"]))
            expected = int(count >= 2) if row["channel"] == "carry" else count % 2
            assert int(row["expected"]) == expected
            start, end = index * slot, (index + 1) * slot
            mask = (times >= start) & (times < end)
            local_t = times[mask]
            values = fields[label + "_" + row["channel"]]
            output = values[mask]
            margin = output - 0.7 * supply if expected else 0.3 * supply - output
            good = margin >= 0
            late = local_t >= start + 0.8 * slot
            late_good = bool(np.any(late) and np.all(good[late]))
            late_margins.append(float(np.min(margin[late])))
            sample_time = start + 0.9 * slot
            assert abs(sample_time - float(row["sample_time_s"])) < 1e-18
            sampled = float(np.interp(sample_time, times, values))
            assert abs(sampled - float(row["sampled_output_V"])) < 1e-9
            sample_good = bool(sampled >= 0.7 * supply if expected else sampled <= 0.3 * supply)
            assert sample_good == (row["sample_correct"] == "True")
            assert late_good == (row["late_window_correct"] == "True")
            assert (sample_good and late_good) == (row["correct"] == "True")
            passed += int(sample_good and late_good)
            sample_passed += int(sample_good)
            bad = np.flatnonzero(~good)
            established = (float(local_t[bad[-1] + 1] - start)
                           if len(bad) and bad[-1] + 1 < len(local_t)
                           else (0.0 if not len(bad) else None))
            if established is None:
                assert row["settling_to_30_70_percent_s"] == ""
            else:
                assert abs(established - float(row["settling_to_30_70_percent_s"])) < 1e-18
            settling.append(established)
            prev_count = sum(map(int, row["from_state"]))
            previous = int(prev_count >= 2) if row["channel"] == "carry" else prev_count % 2
            excursion = bool(index and previous == expected and len(bad))
            assert excursion == (row["unchanged_expected_with_excursion"] == "True")
            glitches += int(excursion)
        assert passed == item["passed_decisions"]
        assert sample_passed == item["sample_passed_decisions"]
        assert glitches == item["unchanged_expected_excursions"]
        assert sum(t is None for t in settling) == item["unsettled_decisions"]
        assert abs(max((t for t in settling if t is not None), default=0) -
                   item["max_observed_settling_s"]) < 1e-18
        observed.append({
            "scenario": label, "passed": passed, "total": len(selected),
            "minimum_late_voltage_margin_V": min(late_margins),
            "max_observed_settling_ns": item["max_observed_settling_s"] * 1e9,
            "unchanged_expected_excursions": glitches,
            "energy_per_state_slot_pJ": energy / len(order) * 1e12,
        })
    # Confirm a disconnected cancellation branch does not masquerade as a full adder.
    negative = next(r for r in observed if r["scenario"] == "negative_control_no_cancel")
    assert negative["passed"] < negative["total"]
    comparisons = []
    for left, right in (("nominal_200ps", "nominal_100ps"),
                        ("nominal_100ps", "nominal_50ps"),
                        ("nominal_100ps", "nominal_gear_100ps")):
        tl, tr = fields[left + "_time"], fields[right + "_time"]
        el = next(i["supply_energy_J"] for i in summary["transient_results"] if i["scenario"] == left)
        er = next(i["supply_energy_J"] for i in summary["transient_results"] if i["scenario"] == right)
        comparison = {"left": left, "right": right, "energy_relative_difference": abs(el-er)/abs(er)}
        for name in ("carry", "sum", "sum_input", "cancel"):
            comparison[name + "_max_difference_V"] = float(np.max(abs(
                fields[left + "_" + name] - np.interp(tl, tr, fields[right + "_" + name]))))
        comparisons.append(comparison)
    result = {
        "data_integrity": "PASS", "directed_transition_coverage": 64,
        "static_rates": {k: {"passed": p, "total": n} for k, (p, n) in static_rates.items()},
        "static_passed": sum(r["correct"] == "True" for r in static),
        "observed_transient_results": observed, "comparisons": comparisons,
        "note": "PASS verifies measurements including failures; not zero-error hardware certification.",
    }
    (folder / "coupled_verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    plot(folder, summary, fields)
    print(json.dumps(result, indent=2))


def plot(folder, summary, fields):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    order = summary["order"]
    index = next(i for i in range(1, len(order)) if order[i-1] == "000" and order[i] == "011")
    figure, axes = plt.subplots(3, 2, figsize=(11, 8))
    for col, label in enumerate(("nominal_100ps", "nominal_10ns")):
        item = next(i for i in summary["transient_results"] if i["scenario"] == label)
        event = index * item["slot_ns"]
        relative = fields[label + "_time"] * 1e9 - event
        mask = (relative >= -4) & (relative <= item["slot_ns"])
        for name in ("a", "b", "cin_bit"):
            axes[0, col].plot(relative[mask], fields[label + "_" + name][mask], label=name)
        for name in ("carry", "sum"):
            axes[1, col].plot(relative[mask], fields[label + "_" + name][mask], label=name)
        axes[1, col].axhline(0.99, color="gray", linewidth=0.6, linestyle="--")
        axes[1, col].axhline(2.31, color="gray", linewidth=0.6, linestyle="--")
        axes[2, col].plot(relative[mask], fields[label + "_sum_input"][mask], label="sum input")
        axes[2, col].axhline(1.2, color="gray", linewidth=0.8, linestyle="--", label="sum reference")
        axes[0, col].set_title(f"{label}: 000 -> 011")
        for row in range(3):
            axes[row, col].axvline(0.8 * item["slot_ns"], color="green", linewidth=0.8,
                                  linestyle=":", label="late-window start")
            axes[row, col].set(xlabel="Time from transition (ns)", ylabel="Voltage (V)")
            axes[row, col].legend(fontsize=8)
            axes[row, col].grid(alpha=0.2)
    figure.tight_layout()
    figure.savefig(folder / "carry_cancellation_transition.png", dpi=160)
    plt.close(figure)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "第七轮结果")
    verify(parser.parse_args().output)
