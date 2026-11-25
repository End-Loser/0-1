"""Regression, boundary and evidence-tampering tests (standard library only)."""

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from model import Controller, nominal_trace, quantization, timing
from runner import scenarios
from verify import verify


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.unknown = scenarios()[0]["parameters"]
        self.p = scenarios()[1]["parameters"]

    def test_eight_readings_floor(self):
        self.assertAlmostEqual(timing(self.unknown)["adc_throughput_only_s"], 80e-6)
        self.assertAlmostEqual(80e-6 / 640e-9, 125)

    def test_unknown_overheads_not_complete(self):
        result = timing(self.unknown)
        self.assertEqual(len(result["missing"]), 10)
        self.assertAlmostEqual(result["pause_s"], 85e-6)
        self.assertIn("lower_bound", result["kind"])

    def test_explicit_serial_schedule(self):
        self.assertAlmostEqual(timing(self.p)["pause_s"], 161.2e-6)

    def test_repeat_scaling(self):
        for m in (1, 4, 16):
            result = timing(dict(self.p, averages=m))
            self.assertEqual(result["conversions"], 8 * m)
            self.assertAlmostEqual(result["pause_s"], 161.2e-6 + (m - 1) * 92.8e-6)

    def test_discarded_samples_cost_time(self):
        result = timing(dict(self.p, discard_per_point=2))
        self.assertEqual(result["discarded_readings"], 16)
        self.assertAlmostEqual(result["pause_s"], 346.8e-6)

    def test_multiple_dac_updates(self):
        delta = timing(dict(self.p, updates=4))["pause_s"] - timing(self.p)["pause_s"]
        self.assertAlmostEqual(delta, 3 * 10.4e-6)

    def test_pause_denominators_differ(self):
        result = timing(self.p)
        self.assertNotEqual(result["pause_fraction_active_plus_pause"],
                            result["pause_fraction_start_to_start"])
        self.assertAlmostEqual(result["active_interval_for_pause_budget_s"], 0.0159588)

    def test_impossible_start_period(self):
        result = timing(dict(self.p, start_interval_s=100e-6))
        self.assertFalse(result["start_to_start_feasible"])
        self.assertIsNone(result["pause_fraction_start_to_start"])

    def test_invalid_timing_inputs(self):
        for change in ({"averages": 0}, {"averages": 1.5}, {"averages": True},
                       {"sample_rate_Hz": 100001}, {"solve_s": -1},
                       {"updates": 0}, {"restore_s": float("nan")},
                       {"active_interval_s": 0}, {"pause_budget_relative": 1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                timing(dict(self.p, **change))

    def test_coarse_worst_rounding(self):
        q = quantization(self.unknown)
        self.assertAlmostEqual(q["coarse_positive_relative"], 0.029200527194428227)
        self.assertAlmostEqual(q["coarse_negative_relative"], 1 - 10**(-0.25 / 20))
        self.assertFalse(q["coarse_only_within_1pct"])

    def test_dac_lsb_and_endpoint(self):
        f = quantization(self.p)["fine"]
        self.assertAlmostEqual(f["ideal_lsb_V"], 38.14697265625e-6)
        self.assertAlmostEqual(f["max_code_V"], 2.5 - 38.14697265625e-6)

    def test_fine_resolution_and_coverage_separate(self):
        f = quantization(dict(self.p, fine_slope_dB_per_V=0.1))["fine"]
        self.assertTrue(f["necessary_rounding_condition_ok"])
        self.assertFalse(f["coarse_residual_coverage_ok"])

    def test_fine_steep_slope_fails(self):
        f = quantization(dict(self.p, fine_slope_dB_per_V=20000))["fine"]
        self.assertFalse(f["necessary_rounding_condition_ok"])
        self.assertEqual(f["minimum_bits_rounding_only"], 21)

    def test_fine_boundary(self):
        step_limit = 40 * math.log10(1.002)
        slope = step_limit * 65536 / 2.5
        self.assertTrue(quantization(dict(self.p, fine_slope_dB_per_V=slope * (1 - 1e-9)))["fine"]
                        ["necessary_rounding_condition_ok"])
        self.assertFalse(quantization(dict(self.p, fine_slope_dB_per_V=slope * (1 + 1e-9)))["fine"]
                         ["necessary_rounding_condition_ok"])

    def test_adc_range_guard_and_top_code(self):
        self.assertFalse(quantization(dict(self.p, input_min_V=0.005))["adc"]["range_with_guard_ok"])
        self.assertFalse(quantization(dict(self.p, input_max_V=2.5, input_guard_V=0))["adc"]
                         ["range_with_guard_ok"])

    def test_uncorrelated_noise_average(self):
        a = quantization(dict(self.p, averages=16))["adc"]
        self.assertAlmostEqual(a["mean_noise_sigma_V"], 0.00025)
        self.assertAlmostEqual(a["bounded_noise_coeff_relative"], 0.006)
        self.assertFalse(a["quantization_bound_reduced_by_average"])

    def test_correlated_noise_not_reduced(self):
        a = quantization(dict(self.p, averages=16, noise_rho=1))["adc"]
        self.assertAlmostEqual(a["mean_noise_sigma_V"], 0.001)

    def test_small_signal_sensitivity_not_bits_precision(self):
        a = quantization(dict(self.p, adc_coeff_sensitivity_per_V=10000))["adc"]
        self.assertGreater(a["quantization_coeff_bound_relative"], 0.19)

    def test_missing_static_errors_block_combined(self):
        q = quantization(self.p)
        self.assertIsNone(q["conditional_combined_bound_relative"])
        self.assertEqual(len(q["missing"]), 5)
        self.assertEqual(q["real_effective_accuracy_status"], "BLOCKED_NO_HARDWARE_MODEL")

    def test_synthetic_complete_is_still_not_hardware(self):
        q = quantization(scenarios()[10]["parameters"])
        self.assertIsNotNone(q["conditional_combined_bound_relative"])
        self.assertEqual(q["real_effective_accuracy_status"], "BLOCKED_NO_HARDWARE_MODEL")

    def test_invalid_quantization_inputs(self):
        for change in ({"adc_span_V": 0}, {"noise_rho": 1.1},
                       {"fine_budget_relative": 0}, {"fine_slope_dB_per_V": 0},
                       {"noise_sigma_V": float("inf")}, {"input_min_V": 2.1},
                       {"fine_center_V": 2.5}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                quantization(dict(self.p, **change))


class ControlTests(unittest.TestCase):
    def test_nominal_order_and_freeze(self):
        for m in (1, 4, 16):
            trace = nominal_trace(m)
            self.assertEqual(sum(r["event"] == "read" for r in trace), 8 * m)
            self.assertEqual(sum(r["event"] == "program" for r in trace), 1)
            data = [r for r in trace if r["event"] == "data"]
            self.assertEqual([r["version"] for r in data], [1] + [2] * 8)

    def test_all_eight_runtime_input_labels_no_programming(self):
        ctl = Controller()
        for label in range(8):
            row = ctl.step("data", float(label))
            self.assertEqual(row["version"], 1)
        with self.assertRaises(ValueError):
            ctl.step("program", 8, 5e-6)
        self.assertEqual(ctl.version, 1)

    def test_incomplete_measurement_and_extra_read(self):
        for n, event in ((7, "solve"), (8, "read")):
            ctl = Controller(state="MEASURE", readings=n)
            before = vars(ctl).copy()
            with self.assertRaises(ValueError):
                ctl.step(event, 1)
            self.assertEqual(vars(ctl), before)

    def test_wait_boundaries(self):
        for state, event in (("DAC_SETTLE", "settled"), ("RESTORE", "resume"),
                             ("PROBE_SETTLE", "settled")):
            ctl = Controller(state=state, deadline=5e-6)
            with self.assertRaises(ValueError):
                ctl.step(event, 5e-6 - 1e-12)
            ctl.step(event, 5e-6)

    def test_fault_cannot_resume_directly(self):
        ctl = Controller()
        ctl.step("fault", 0)
        with self.assertRaises(ValueError):
            ctl.step("resume", 1)
        ctl.step("reset", 2)
        self.assertEqual(ctl.state, "PAUSE")

    def test_pause_blocks_runtime_data(self):
        ctl = Controller()
        ctl.step("request", 0)
        with self.assertRaises(ValueError):
            ctl.step("data", 1)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(os.environ["EWC_CAL_RESULTS"])

    def test_independent_verify(self):
        self.assertTrue(verify(self.directory)["passed"])

    def test_numeric_and_control_tampering_rejected(self):
        # Refresh the digest to ensure failures are semantic, not only hash mismatches.
        mutations = (
            ("budgets.json", lambda x: x[1]["timing"].__setitem__("pause_s", 80e-6)),
            ("budgets.json", lambda x: x[2]["quantization"]["adc"].__setitem__("mean_noise_sigma_V", 0)),
            ("budgets.json", lambda x: x[6]["quantization"]["fine"].__setitem__("necessary_rounding_condition_ok", True)),
            ("budgets.json", lambda x: x[0]["quantization"].__setitem__("missing", [])),
            ("control.json", lambda x: x["nominal_m1"][-1].__setitem__("version", 3)),
            ("control.json", lambda x: x["transition_matrix"][0].__setitem__("accepted", False)),
            ("control.json", lambda x: x["negative_attempts"][0].__setitem__("rejected", False)),
            ("control.json", lambda x: x["transition_matrix"].pop()),
        )
        for filename, mutate in mutations:
            with self.subTest(filename=filename), tempfile.TemporaryDirectory(prefix="ewc-cal-mutation-") as temp:
                root = Path(temp)
                for item in self.directory.glob("*.json"):
                    shutil.copy2(item, root / item.name)
                path = root / filename
                value = json.loads(path.read_text(encoding="utf-8"))
                mutate(value)
                path.write_text(json.dumps(value, allow_nan=False), encoding="utf-8")
                summary_path = root / "summary.json"
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
                summary["artifact_sha256"][filename] = hashlib.sha256(path.read_bytes()).hexdigest()
                summary_path.write_text(json.dumps(summary), encoding="utf-8")
                with self.assertRaises(AssertionError):
                    verify(root)

    def test_hash_tampering_rejected(self):
        with tempfile.TemporaryDirectory(prefix="ewc-cal-hash-") as temp:
            root = Path(temp)
            for item in self.directory.glob("*.json"):
                shutil.copy2(item, root / item.name)
            with (root / "budgets.json").open("ab") as handle:
                handle.write(b" ")
            with self.assertRaises(AssertionError):
                verify(root)

    def test_unearned_system_claim_rejected(self):
        with tempfile.TemporaryDirectory(prefix="ewc-cal-claim-") as temp:
            root = Path(temp)
            for item in self.directory.glob("*.json"):
                shutil.copy2(item, root / item.name)
            path = root / "summary.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["system_1pct_passed"] = True
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(AssertionError):
                verify(root)


if __name__ == "__main__":
    unittest.main(verbosity=2)
