"""Check the wave-logic experiment archives independently of the solvers."""

import csv
import json
from pathlib import Path

ROOT = Path(__file__).parent / "第五轮结果"


def rows(name):
    with (ROOT / name).open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def main():
    summary = json.loads((ROOT / "wave_logic_summary.json").read_text(encoding="utf-8"))
    gates = rows("wave_full_adder_direct.csv")
    arithmetic = rows("wave_arithmetic_exhaustive.csv")
    corners = rows("wave_logic_bounded_error_corners.csv")
    budgets = rows("speed_energy_assumptions.csv")
    assert summary["direct_fdtd_runs"] == 38
    assert len(gates) == 16 and len(arithmetic) == 2048 and len(budgets) == 12
    for gate in gates:
        count = sum(int(gate[key]) for key in ("a", "b", "carry_in"))
        assert int(gate["sum_bit"]) == count % 2
        assert int(gate["carry_out"]) == count // 2
        assert int(float(gate["first_measured_coordinate"]) >= 1.5) == int(gate["carry_out"])
        assert int(float(gate["second_measured_coordinate"]) >= 0.5) == int(gate["sum_bit"])
        assert gate["correct"] == "True"
    for item in arithmetic:
        a, b, operation = int(item["A"]), int(item["B"]), item["operation"]
        if operation == "divide":
            if b == 0:
                assert item["error"] == "division_by_zero" and item["quotient"] == ""
            else:
                q, r = divmod(a, b)
                assert int(item["quotient"]) == q and int(item["remainder"]) == r
                assert int(item["cells"]) == 40
        else:
            expected = {"add": a + b, "subtract": a - b, "multiply": a * b}[operation]
            assert int(item["value"]) == expected
            assert int(item["cells"]) == {"add": 4, "subtract": 5, "multiply": 32}[operation]
        assert item["correct"] == "True"
    assert all(item["cases"] == item["passed"] for item in corners[:2])
    assert any(item["cases"] != item["passed"] for item in corners[2:])
    for item in budgets:
        cells = int(item["full_adder_cell_count"])
        duration = cells * float(item["assumed_two_stage_cell_time_ns"]) * 1e-9
        assert abs(float(item["core_latency_s"]) - duration) < 1e-18
        assert abs(float(item["active_core_energy_J"])
                   - duration * float(item["assumed_active_core_power_W"])) < 1e-18
    result = {
        "status": "PASS",
        "scope": "Static gate truth states, bit algorithms, bounded scalar error tests and assumption arithmetic; not physical hardware validation",
        "verified_direct_gate_states": len(gates),
        "verified_arithmetic_cases": len(arithmetic),
        "zero_divisor_error_cases": sum(item["operation"] == "divide" and item["B"] == "0" for item in arithmetic),
        "budget_rows": len(budgets),
        "delay_and_energy_are_measured": False,
    }
    (ROOT / "wave_logic_verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
