"""Regression guards for independently measured evidence and fixed limits."""
import json
import unittest

import numpy as np

import verify_supply_layers as v


class SupplyEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.summary = json.loads((v.ROOT / "summary.json").read_text(encoding="utf-8"))
        cls.records = [v.recalculate(run) for run in cls.summary["runs"]]

    def test_fixed_limits(self):
        self.assertEqual(v.CURRENT_LIMIT_A, 1e-6)
        self.assertEqual(v.CHARGE_LIMIT, .01)

    def test_nine_complete_one_interface_pass(self):
        self.assertEqual(len(self.records), 9)
        self.assertTrue(all(r["waveform_complete"] for r in self.records))
        self.assertEqual([r["id"] for r in self.records if r["interface_passed"]],
                         ["buffer_ideal_control"])

    def test_algebraic_branches(self):
        self.assertTrue(all(r["algebraic_passed"] for r in self.records))

    def test_integral_failures_are_preserved(self):
        self.assertEqual({r["id"] for r in self.records if r["capacitor_accounting_passed"]},
                         {"dual_steady", "dual_load_step", "dual_reference", "buffer_ideal_control"})

    def test_model_range_separate_from_waveform(self):
        buffers = {r["id"]: r["buffer_range"] for r in self.records if r["buffer_range"]}
        self.assertTrue(buffers["buffer_ideal_control"]["recommended_all_samples"])
        for name in ("buffer_real_rails", "buffer_real_ramp"):
            self.assertFalse(buffers[name]["recommended_terminal_window"])
            self.assertLess(buffers[name]["terminal_supply_min_V"], 0)
            self.assertFalse(buffers[name]["real_chip_failure_inferred"])

    def test_component_units(self):
        self.assertEqual(v.component("R1 a b .05\n", "R1"), .05)
        self.assertEqual(v.component("C1 a b 1n\n", "C1"), 1e-9)
        with self.assertRaises(AssertionError):
            v.component("C1 a b bad\n", "C1")

    def test_capacitor_oracle_rejects_unaccounted_current(self):
        t = np.linspace(0, 1e-3, 101)
        voltage = 1 + 1000 * t
        cap = 1e-9
        window = np.ones(len(t), dtype=bool)
        good = v.capacitor(t, voltage, np.full(len(t), cap * 1000), cap, window)
        bad = v.capacitor(t, voltage, np.full(len(t), cap * 1000 + 1e-6), cap, window)
        self.assertTrue(good["accounting_passed"])
        self.assertFalse(bad["accounting_passed"])
        self.assertAlmostEqual(good["energy_change_J"], .5 * cap * (4 - 1))

    def test_no_full_power_or_startup_claim(self):
        for key in ("startup_safety_passed", "full_G4_G5_passed", "full_power_passed", "full_joint_passed"):
            self.assertFalse(self.summary[key])
        ideal = next(r for r in self.records if r["id"] == "buffer_ideal_control")
        self.assertIn("ideal_rail3_source_J", ideal["modeled_source_energy"])
        self.assertIn("ideal_signal_sources_not_measured", ideal["energy_scope"])


if __name__ == "__main__":
    unittest.main()
