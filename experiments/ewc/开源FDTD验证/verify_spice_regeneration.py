"""Independently check stored SPICE measurements, including failed decisions."""

import argparse
import csv
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).parent


def read_csv(path):
    with path.open(encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def verify(folder):
    summary = json.loads((folder / "spice_regeneration_summary.json").read_text(encoding="utf-8"))
    static = read_csv(folder / "spice_static_gate_states.csv")
    boundary = read_csv(folder / "spice_boundary_margin.csv")
    decisions = read_csv(folder / "spice_continuous_decisions.csv")
    fields = np.load(folder / "spice_regeneration_waveforms.npz", allow_pickle=False)
    assert len(static) == 288 and len(boundary) == 36
    assert len(summary["transient_results"]) == 6 and not summary["solver_failures"]
    rates = {}
    for row in static + boundary:
        supply = 3.0 if row["scenario"] == "supply_3V" else 3.3
        measured, expected = float(row["output_V"]), int(row["expected"])
        good = measured >= 0.7 * supply if expected else measured <= 0.3 * supply
        good = good and row["internal_voltages_valid"] == "True"
        assert good == (row["correct"] == "True")
        assert float(row["supply_power_W"]) >= 0
        if "state" in row:
            count = sum(map(int, row["state"]))
            logic = int(count >= 2) if row["channel"] == "carry" else count % 2
            assert logic == expected
            key = row["scenario"]
            passed, total = rates.get(key, (0, 0))
            rates[key] = (passed + int(good), total + 1)
    energy_checks = []
    for item in summary["transient_results"]:
        label = item["scenario"]
        t = fields[label + "_time"]
        assert t[0] == 0 and np.all(np.diff(t) > 0)
        assert np.all(np.diff(t) <= item["dt_ps"] * 1e-12 * 1.001)
        supply = 3.3
        energy = float(np.trapz(-supply * fields[label + "_supply_current"], t))
        assert np.isclose(energy, item["supply_energy_J"], rtol=1e-12, atol=1e-24)
        rows = [r for r in decisions if r["scenario"] == label]
        assert len(rows) == 32
        passed = 0
        settling = []
        for row in rows:
            measured = float(np.interp(float(row["sample_time_s"]), t,
                                      fields[label + "_" + row["channel"]]))
            assert abs(measured - float(row["sampled_output_V"])) < 1e-9
            expected = int(row["expected"])
            count = sum(map(int, row["state"]))
            assert expected == (int(count >= 2) if row["channel"] == "carry" else count % 2)
            good = measured >= 0.7 * supply if expected else measured <= 0.3 * supply
            assert good == (row["correct"] == "True")
            passed += int(good)
            slot = item["slot_ns"] * 1e-9
            start = int(row["slot_index"]) * slot
            end = (int(row["slot_index"]) + 1) * slot
            mask = (t >= start) & (t < end)
            local_t = t[mask]
            local_y = fields[label + "_" + row["channel"]][mask]
            good_window = local_y >= 0.7 * supply if expected else local_y <= 0.3 * supply
            bad = np.flatnonzero(~good_window)
            established = (float(local_t[bad[-1] + 1] - start)
                           if len(bad) and bad[-1] + 1 < len(local_t)
                           else (0.0 if not len(bad) else None))
            stored = row["settling_to_30_70_percent_s"]
            if established is None:
                assert stored == ""
            else:
                assert abs(established - float(stored)) < 1e-18
            settling.append(established)
        assert passed == item["passed_decisions"]
        assert sum(v is None for v in settling) == item["unsettled_decisions"]
        assert abs(max((v for v in settling if v is not None), default=0) -
                   item["max_observed_settling_s"]) < 1e-18
        energy_checks.append({"scenario": label, "energy_J": energy})
    convergence = []
    for coarse, fine in (("nominal_200ps", "nominal_100ps"),
                         ("nominal_100ps", "nominal_50ps")):
        tc, tf = fields[coarse + "_time"], fields[fine + "_time"]
        differences = {}
        for name in ("carry", "sum"):
            vc = fields[coarse + "_" + name]
            vf = np.interp(tc, tf, fields[fine + "_" + name])
            differences[name + "_waveform_max_difference_V"] = float(np.max(abs(vc - vf)))
        ec = next(r["energy_J"] for r in energy_checks if r["scenario"] == coarse)
        ef = next(r["energy_J"] for r in energy_checks if r["scenario"] == fine)
        convergence.append({
            "coarse": coarse, "fine": fine, **differences,
            "supply_energy_relative_difference": abs(ec - ef) / abs(ef),
        })
    result = {
        "data_integrity": "PASS",
        "static_rates": {k: {"passed": p, "total": n} for k, (p, n) in rates.items()},
        "static_total_passed": sum(r["correct"] == "True" for r in static),
        "boundary_total_passed": sum(r["correct"] == "True" for r in boundary),
        "energy_checks": energy_checks, "time_step_comparisons": convergence,
        "note": "Integrity PASS includes accurately recorded logic failures; not hardware certification.",
    }
    (folder / "spice_regeneration_verification.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    figure, axes = plt.subplots(2, 2, figsize=(11, 6), sharey=True)
    for col, label in enumerate(("nominal_50ps", "too_fast_5ns")):
        for row, name in enumerate(("carry", "sum")):
            ax = axes[row, col]
            ax.plot(fields[label + "_time"] * 1e9, fields[label + "_" + name],
                    linewidth=1, label="MOS output")
            selected = [r for r in decisions if r["scenario"] == label and r["channel"] == name]
            ax.scatter([float(r["sample_time_s"]) * 1e9 for r in selected],
                       [float(r["sampled_output_V"]) for r in selected],
                       c=["green" if r["correct"] == "True" else "red" for r in selected], s=14)
            ax.axhline(0.99, color="gray", linestyle="--", linewidth=0.7)
            ax.axhline(2.31, color="gray", linestyle="--", linewidth=0.7)
            ax.set(title=f"{label}: {name}", xlabel="Time (ns)", ylabel="Output (V)")
            ax.grid(alpha=0.2)
    figure.tight_layout()
    figure.savefig(folder / "circuit_continuous_waveforms.png", dpi=160)
    plt.close(figure)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "第六轮结果")
    verify(parser.parse_args().output)
