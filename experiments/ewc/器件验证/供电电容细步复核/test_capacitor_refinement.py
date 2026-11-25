"""Declared refinement and strict accounting limits; no new waveform claim."""
import importlib.util
import json
from pathlib import Path
import unittest

import numpy as np

from run_capacitor_refinement import CASE_IDS, PRIOR, configurations, load_prior_runner


class RefinementTests(unittest.TestCase):
    def test_exact_five_failures_and_two_finer_steps(self):
        rows = configurations()
        self.assertEqual(len(rows), 10)
        self.assertEqual({r["baseline_id"] for r in rows}, set(CASE_IDS))
        self.assertEqual({r["step_s"] for r in rows}, {50e-9, 20e-9})
        self.assertTrue(all(not r["ideal_rails"] for r in rows))

    def test_no_circuit_duration_or_tolerance_change(self):
        fixture = load_prior_runner()
        old = json.loads((PRIOR / "declared_configurations.json").read_text(encoding="utf-8"))
        models = dict(regulator=Path("original_ldo.lib"), reference=Path("original_ref.lib"),
                      buffer=Path("original_buffer.lib"))
        for config in configurations():
            original = next(r for r in old if r["id"] == config["baseline_id"])
            baseline_net, headers = fixture.deck(original, models)
            net, actual_headers = fixture.deck(config, models)
            fine = f"tran {config['step_s']} {config['finish_s']} 0 {config['step_s']}"
            coarse = f"tran {original['step_s']} {original['finish_s']} 0 {original['step_s']}"
            self.assertEqual(net.replace(fine, coarse), baseline_net)
            self.assertEqual(actual_headers, headers)
            self.assertEqual(config["finish_s"], original["finish_s"])

    def test_charge_failure_not_promoted_by_terminal_kcl(self):
        spec = importlib.util.spec_from_file_location("accounting", PRIOR / "verify_supply_layers.py")
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        t = np.linspace(0, 1., 1001)
        voltage = np.ones_like(t)
        current = np.where(t < .1, 1., 0.)
        result = helper.capacitor(t, voltage, current, 1., t > .8)
        self.assertLess(result["window_kcl_A"], 1e-6)
        self.assertGreater(result["charge_relative_error"], .01)
        self.assertFalse(result["accounting_passed"])

    def test_archived_truncations_and_supply_failures_preserved(self):
        root = Path(__file__).resolve().parent
        summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
        result = json.loads((root / "verification.json").read_text(encoding="utf-8"))
        self.assertEqual(result["complete_waveforms"], 8)
        self.assertEqual(result["interface_pass_count"], 0)
        truncated = [r for r in summary["runs"] if r["status"] == "blocked"]
        self.assertEqual({r["id"] for r in truncated},
                         {"dual_reference_ramp_20ns", "buffer_real_ramp_20ns"})
        for row in truncated:
            self.assertEqual(row["returncode"], 0)
            self.assertFalse(row["partial_accepted"])
            self.assertLess(row["observed_last_time_s"], row["finish_s"])
            self.assertTrue((root / "results" / row["id"] / "partial_wave.txt").is_file())
        for key in ("full_G4_G5_passed", "full_power_passed", "startup_safety_passed", "full_joint_passed"):
            self.assertFalse(result[key])

    def test_remaining_integral_failure_and_old_failures_preserved(self):
        root = Path(__file__).resolve().parent
        result = json.loads((root / "verification.json").read_text(encoding="utf-8"))
        row = next(r for r in result["runs"] if r["id"] == "buffer_real_rails_20ns")
        self.assertGreater(row["capacitors"]["bout2"]["charge_relative_error"], .01)
        self.assertFalse(row["capacitor_accounting_passed"])
        self.assertFalse(row["buffer_range"]["recommended_terminal_window"])
        self.assertTrue(all(r["original_100ns_failure_preserved"] for r in result["comparisons"]))
        self.assertFalse(any(r["both_fine_accounting_passed"] for r in result["comparisons"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
