"""Formula tests explicitly do not execute the modified C code."""
import json
import math
import unittest

import verify_source as v


class SwitchSourceTests(unittest.TestCase):
    def test_inverted_zero_boundary(self):
        self.assertEqual(v.source_formula(0, 1e6, 1e-6), 1e6)
        self.assertAlmostEqual(v.source_formula(0, 1e6, 1e-6, fixed=True), 1e-6, places=18)

    def test_endpoint_limits(self):
        for u in (-.001, 0, .001, .002):
            expected = 1e-6 if u <= 0 else 1e6
            self.assertAlmostEqual(v.source_formula(u, 1e6, 1e-6, fixed=True) / expected, 1, places=12)

    def test_normal_ordering_unchanged(self):
        for ron, roff in ((230, 1e6), (80, 100), (100, 100)):
            for i in range(101):
                u = i * .001 / 100
                self.assertEqual(v.source_formula(u, ron, roff), v.source_formula(u, ron, roff, fixed=True))

    def test_signed_control_and_resistance_orders(self):
        for ron, roff in ((230, 1e6), (1e6, 1e-6)):
            for on, off in ((.001, 0), (0, .001)):
                for i in range(101):
                    u = i * .001 / 100
                    actual = v.source_formula(u, ron, roff, on, off, fixed=True)
                    expected = v.independent_oracle(u, ron, roff, on, off)
                    self.assertLess(abs(actual - expected) / expected, 1e-12)
                    self.assertGreater(actual, 0)

    def test_existing_global_clamp_explicitly_retained(self):
        # The separate release r_on floor is not part of this candidate fix.
        self.assertEqual(v.source_formula(.002, 1e-6, 1e6, fixed=True), .001)

    def test_equal_control_threshold_protection(self):
        for u in (-.002, 0, .002):
            actual = v.source_formula(u, 230, 1e6, 0, 0, fixed=True)
            expected = v.independent_oracle(u, 230, 1e6, 0, 0)
            self.assertTrue(math.isfinite(actual))
            self.assertLess(abs(actual - expected) / expected, 1e-12)

    def test_runtime_points_reproduced_not_candidate_run(self):
        result = json.loads((v.ROOT / "verification.json").read_text(encoding="utf-8"))
        self.assertEqual(result["original_runtime_points_reproduced"], 5)
        self.assertEqual(result["candidate_formula_oracle_checks"], 1421)
        self.assertTrue(result["source_level_cause_for_isolated_zero_boundary_proven"])
        for key in ("compiled_candidate", "candidate_solver_run", "full_regulator_fix_verified",
                    "supported_pspice_verified", "full_power_passed", "full_joint_passed"):
            self.assertFalse(result[key])

    def test_source_notice_and_original_hash(self):
        sources = json.loads((v.ROOT / "sources.json").read_text(encoding="utf-8"))
        for record in sources["files"]:
            self.assertEqual(v.sha(v.ROOT / record["local"]), record["sha256"])
        candidate = (v.ROOT / "fixed/cfunc.mod").read_text(encoding="utf-8")
        self.assertIn("Not compiled or upstream validated.", candidate)
        self.assertEqual(candidate.count("if(r<fmin(r_on,r_off))"), 2)


if __name__ == "__main__":
    unittest.main()
