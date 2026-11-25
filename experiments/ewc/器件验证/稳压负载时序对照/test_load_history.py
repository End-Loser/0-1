"""Regression guards preserve numeric failures and isolated-switch scope."""
import json
import unittest

import numpy as np

import verify_load_history as history
import verify_switch_boundary as switch


class LoadHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = json.loads((history.ROOT / "summary.json").read_text(encoding="utf-8"))
        cls.records = [history.recalculate(r)[0] for r in cls.source["runs"]]
        cls.result = json.loads((history.ROOT / "verification.json").read_text(encoding="utf-8"))
        cls.boundary = json.loads((switch.RESULT / "verification.json").read_text(encoding="utf-8"))

    def test_eight_declared_no_output_pass(self):
        self.assertEqual(len(self.records), 8)
        self.assertEqual(sum(r["status"] == "fail" for r in self.records), 6)
        self.assertEqual(sum(r["status"] == "blocked" for r in self.records), 2)
        self.assertEqual(self.result["terminal_output_pass_count"], 0)

    def test_truncated_waveform_not_accepted(self):
        for r in self.records:
            if r["status"] == "blocked":
                self.assertFalse(r["waveform_accepted"])
                self.assertFalse(r["partial_waveform"]["accepted"])
                self.assertLess(r["partial_waveform"]["last_time_s"], .01)
                self.assertIn("timestep too small", r["reason"])

    def test_strict_behavioral_load_failures_preserved(self):
        for r in self.records:
            if not r["waveform_accepted"]:
                continue
            if r["id"].startswith("resistive"):
                self.assertFalse(r["algebraic_passed"])
                self.assertGreater(r["algebraic_errors"]["load_A"], 1e-9)
            else:
                self.assertTrue(r["algebraic_passed"])

    def test_capacitor_pass_not_output_pass(self):
        for r in self.records:
            if r["waveform_accepted"]:
                self.assertTrue(r["output_capacitor"]["accounting_passed"])
                self.assertEqual(r["status"], "fail")

    def test_negative_ideal_current_trajectories(self):
        for r in self.records:
            if r["waveform_accepted"] and r["id"].startswith("current"):
                self.assertLess(r["output_min_V"], -40)
                self.assertGreater(r["negative_output_samples"], 0)
                self.assertTrue(r["ideal_current_fixture_not_startup_safety"])

    def test_control_breakpoints(self):
        t = np.array([0, .0015, .001501, .001502, .006, .008501, .01])
        np.testing.assert_allclose(history.control(t), [0, 0, .5, 1, 0, .5, 1], atol=1e-12)

    def test_isolated_zero_boundary(self):
        self.assertEqual(len(self.boundary["runs"]), 5)
        self.assertTrue(self.boundary["zero_control_high_resistance_observed"])
        self.assertTrue(self.boundary["negative_control_low_resistance_observed"])
        for r in self.boundary["runs"]:
            self.assertEqual(r["expanded_model"], "XSPICE pswitch")
            self.assertFalse(r["physical_device_or_supported_pspice_claim"])

    def test_system_and_support_claims_remain_false(self):
        for source in (self.result, self.boundary):
            for key in ("supported_pspice_verified", "full_power_passed", "full_joint_passed"):
                self.assertFalse(source[key])
        self.assertFalse(self.result["startup_safety_passed"])
        for r in self.records:
            if r["waveform_accepted"]:
                self.assertIn("enable", r["source_energies_J"])
                self.assertFalse(r["internal_output_current_independently_measured"])


if __name__ == "__main__":
    unittest.main()
