"""Cross-check archived precision data without running either solver."""

import csv
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).parent / "第三轮结果"


def rows(name):
    with (ROOT / name).open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def main():
    circuit = json.loads((ROOT / "precision_circuit_summary.json").read_text(encoding="utf-8"))
    em = json.loads((ROOT / "precision_fdtd_summary.json").read_text(encoding="utf-8"))
    dc_rows = rows("dc_precision_all_inputs.csv")
    nonideal = rows("nonideal_lambertW_checks.csv")
    tran = rows("capacitive_transient_summary.csv")
    assert len(dc_rows) == 992 and len(nonideal) == 12 and len(tran) == 14
    assert circuit["dc_runs"] == 1004 and circuit["transient_runs"] == 14
    assert circuit["max_DC_closed_form_error_A"] < 1e-14
    assert circuit["max_nonideal_closed_form_error_A"] < 1e-13
    assert circuit["max_input_KCL_residual_A"] < 1e-15
    assert all(item["correct"] for item in em["direct_arithmetic"])
    max_settling_difference = max(abs(float(item["settling_difference_s"])) for item in tran)
    assert max_settling_difference < 25e-12
    assert all(float(item["sampled_settling_s"]) < 39e-9 for item in tran)
    orders = []
    for operation in ("multiply", "divide"):
        for a0, a1 in (("1", "15"), ("15", "1")):
            selected = {
                int(item["dt_ps"]): float(item["maximum_coordinate_difference_from_analytic"])
                for item in tran if item["operation"] == operation
                and item["A_before"] == a0 and item["A_after"] == a1
                and float(item["output_shunt_capacitance_F"]) == 0
            }
            assert selected[20] > selected[10] > selected[5]
            orders.append({
                "operation": operation, "A_before": int(a0), "A_after": int(a1),
                "order_20_to_10_ps": float(np.log2(selected[20] / selected[10])),
                "order_10_to_5_ps": float(np.log2(selected[10] / selected[5])),
            })
    arrays = np.load(ROOT / "capacitive_transient_waveforms.npz")
    assert len(arrays.files) == 42
    assert all(np.all(np.isfinite(arrays[key])) for key in arrays.files)
    for key in arrays.files:
        if key.endswith("_time"):
            assert np.all(np.diff(arrays[key]) > 0)
    r = 1e-6
    for item in rows("exact_finite_IS_bias.csv"):
        a, b = int(item["A"]), int(item["B"])
        expected = (-r * (a - 1) * (b - 1) / (1 + r)
                    if item["operation"] == "multiply"
                    else r * (b - 1) * (a - b) / (b * (b + r)))
        assert abs(float(item["exact_normalized_bias"]) - expected) < 1e-18
    result = {
        "status": "PASS",
        "checks": "Counts, DC analytic agreement, KCL, direct arithmetic, settling, transient refinement, finite waveforms, finite-IS formulas",
        "max_settling_difference_ps": max_settling_difference * 1e12,
        "transient_observed_orders": orders,
        "euler_cross_check": "FAILED, not treated as a passed test",
        "scope": "Archived model consistency only, not hardware accuracy or independent solver validation",
    }
    (ROOT / "precision_verification.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
