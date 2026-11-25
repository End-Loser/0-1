"""Verify cascade archives and generate a summary chart without solver runs."""

import csv
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).parent / "第四轮结果"


def read_rows(name):
    with (ROOT / name).open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def main():
    summary = json.loads((ROOT / "cascade_summary.json").read_text(encoding="utf-8"))
    rows = read_rows("cascade_all_inputs.csv")
    direct = read_rows("cascade_direct_cases.csv")
    assert len(rows) == 4096 and len(direct) == 24
    assert summary["direct_fdtd_runs"] == 34
    assert summary["dc_runs"] == sum(item["status"] == "ok" for item in rows + direct)
    assert summary["source_actual_frequencies_Hz"] == summary["frequencies_Hz"]
    nominal = [item for item in rows if item["scenario"] == "nominal"]
    assert len(nominal) == 1024 and all(item["pass"] == "True" for item in nominal)
    assert sum(item["status"] == "division_by_zero" for item in nominal) == 16
    assert all(item["pass"] == "True" for item in direct if item["scenario"] == "direct_nominal")
    assert summary["collision_control"]["same_frequency_data_basis_rank"] == 1
    assert summary["collision_control"]["same_frequency_relative_waveform_difference"] < 1e-10

    frontend = np.load(ROOT / "fixed_frontend.npz")
    waves = np.load(ROOT / "cascade_waveforms.npz")
    templates, weights = frontend["templates"], frontend["weights"]
    assert np.linalg.matrix_rank(templates) == 3
    assert np.max(np.abs(weights @ templates - np.eye(3))) < 1e-12
    for key in waves.files:
        assert np.all(np.isfinite(waves[key]))
    for item in direct:
        operation, a, b = item["requested_operation"], item["A"], item["B"]
        key = (f"direct_{operation}_{a}_{b}" if item["scenario"] == "direct_nominal"
               else f"direct_perturbed_{operation}")
        coefficients = weights @ waves[key]
        assert abs(coefficients[2] - float(item["measured_operator_code"])) < 1e-12
        for index, label in enumerate(("A", "B")):
            assert abs(15 * coefficients[index] - float(item["measured_" + label])) < 1e-9

    maximum_error = 0.0
    r = 1e-6
    for item in rows + direct:
        if item["status"] != "ok":
            assert item["output_current_A"] == "" and item["common_output_coordinate"] == ""
            continue
        a, b = float(item["measured_A"]), float(item["measured_B"])
        operation = item["measured_operation"]
        expected = {
            "add": lambda: a + b,
            "subtract": lambda: a - b,
            "multiply": lambda: (a + r) * (b + r) / (1 + r) - r,
            "divide": lambda: (a + r) * (1 + r) / (b + r) - r,
        }[operation]()
        measured = float(item["common_output_coordinate"])
        assert abs(float(item["output_current_A"]) / 1e-6 - measured) < 1e-12
        maximum_error = max(maximum_error, abs(measured - expected))
    assert maximum_error < 1e-7

    result = {
        "status": "PASS",
        "scope": "Archive consistency, linear frontend and measured-input circuit equations; not real RF hardware validation",
        "nominal_valid_arithmetic_cases": 1008,
        "nominal_division_zero_rejections": 16,
        "independent_direct_cascade_cases": len(direct),
        "maximum_coordinate_error_to_measured_input_circuit_formula": maximum_error,
        "checks": [
            "Counts, input identifiability, stored decoder weights",
            "Direct FDTD waveform recovery and nominal arithmetic",
            "One common output gain and absent output on rejection",
            "Closed-form circuit response to measured, not requested inputs",
            "Same-frequency collision negative control",
        ],
    }
    (ROOT / "cascade_verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(12, 4), constrained_layout=True)
    t = waves["time"] * 1e9
    for index, label in enumerate(("A: 3 GHz", "B: 3.6 GHz", "OP: 4.5 GHz")):
        axes[0].plot(t, templates[:, index], label=label, lw=0.8)
    axes[0].set(xlabel="Time (ns)", ylabel="Ez (normalized)",
                title="Three measured basis waveforms, one pickup")
    axes[0].legend()
    scenarios = list(dict.fromkeys(item["scenario"] for item in summary["results"]))
    for index, operation in enumerate(("add", "subtract", "multiply", "divide")):
        selected = [item for item in summary["results"] if item["operation"] == operation]
        heights = [100 * item["passed"] / item["cases"] for item in selected]
        axes[1].bar(np.arange(len(scenarios)) + (index - 1.5) * 0.18,
                    heights, width=0.18, label=operation)
    axes[1].set_xticks(range(len(scenarios)),
                      ["Nominal", "B gain +0.1%", "B gain +1%", "Epsilon +1%"],
                      rotation=20, ha="right")
    axes[1].set(ylabel="Arithmetic pass rate (%)", ylim=(0, 110),
                title="Fixed frontend and common output gain")
    axes[1].legend(fontsize=8)
    for ax in axes:
        ax.grid(alpha=0.2)
    fig.savefig(ROOT / "cascade_results.png", dpi=150)
    plt.close(fig)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
