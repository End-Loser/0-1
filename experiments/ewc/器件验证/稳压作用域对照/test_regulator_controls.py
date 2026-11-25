"""Guard the limits of the control experiment conclusions."""
import json
import unittest

import numpy as np

import verify_regulator_controls as v


class RegulatorControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = json.loads((v.ROOT / "summary.json").read_text(encoding="utf-8"))
        cls.result = json.loads((v.ROOT / "verification.json").read_text(encoding="utf-8"))
        cls.waves = {r["id"]: v.read(r) for r in cls.source["runs"]}

    def test_declared_seven_complete(self):
        self.assertEqual(len(self.waves), 7)
        self.assertTrue(all(r["waveform_complete"] for r in self.result["runs"]))

    def test_original_baseline_is_only_pass(self):
        self.assertEqual([r["id"] for r in self.result["runs"] if r["reproduced_status"] == "pass"],
                         ["original_single_control"])
        self.assertTrue(self.result["runs"][0]["original_netlist_unchanged"])

    def test_raw_scope_pair_equality(self):
        self.assertEqual(len(self.result["scope_pairs"]), 3)
        for pair in self.result["scope_pairs"]:
            self.assertTrue(np.array_equal(self.waves[pair["first"]], self.waves[pair["second"]]))
            self.assertTrue(pair["identical_within_1e_12"])

    def test_target_selection_and_output_disagreement(self):
        for run in self.source["runs"][1:]:
            wave = self.waves[run["id"]]
            self.assertLess(abs(wave[-1, 10] - run["target_V"]), .001)
            self.assertLess(wave[-1, 2], .01)
            self.assertEqual(wave[-1, 11], 1)

    def test_constant_output_coupling_is_diagnostic(self):
        for run in self.result["runs"][1:]:
            low, high = run["inferred_stationary_output_coupling_ohm"]
            self.assertGreater(low, 1e6)
            self.assertLess(high, 1.002e6)
            self.assertIn("diagnostic_only", run["coupling_scope"])

    def test_vendor_and_system_boundaries(self):
        self.assertFalse(self.source["vendor_library_edits"])
        for key in ("root_cause_proven", "supported_pspice_verified", "full_supply_acceptance_passed"):
            self.assertFalse(self.result[key])


if __name__ == "__main__":
    unittest.main()
