"""Software screening tests, no manufacturer 3-GHz guarantee."""

import unittest
import numpy as np

from run_dielectric import gamma, budget, EPS, DK, TAN, F0
from verify_dielectric import components


class MaterialTests(unittest.TestCase):
    def test_lossless_limit(self):
        g = gamma(3e9, 2.2, 0)
        self.assertEqual(g.real, 0)
        self.assertGreater(g.imag, 0)

    def test_independent_loss_components(self):
        for f in (2.5e9, 3e9, 3.5e9):
            self.assertLess(abs(gamma(f, DK, TAN) / components(f, DK, TAN) - 1), 1e-12)

    def test_center_conductivity_match(self):
        sigma = 2 * np.pi * F0 * EPS * DK * TAN
        self.assertLess(abs(gamma(F0, DK, sigma, "conductivity") / gamma(F0, DK, TAN) - 1), 1e-13)

    def test_wrong_band_loss_preserved(self):
        sigma = 2 * np.pi * F0 * EPS * DK * TAN
        error = gamma(2.5e9, DK, sigma, "conductivity").real / gamma(2.5e9, DK, TAN).real - 1
        self.assertGreater(error, .19)

    def test_positive_loss(self):
        self.assertTrue(np.all(gamma(np.linspace(2.5e9, 3.5e9, 101), DK, TAN).real > 0))

    def test_nominal_budget(self):
        row = budget(2.2, 0, "fixed")
        self.assertEqual(row["phase_relative_error"], 0)
        self.assertTrue(row["allocated_phase_0p2percent_pass"])

    def test_Dk_spec_hypothesis_can_fail_allocation(self):
        self.assertFalse(budget(2.22, 0, "fixed")["allocated_phase_0p2percent_pass"])

    def test_temperature_and_expansion_are_distinct(self):
        fixed, z = budget(2.2, 60, "fixed"), budget(2.2, 60, "z")
        self.assertLess(fixed["phase_relative_error"], 0)
        self.assertGreater(z["phase_relative_error"], .01)

    def test_inverse_frequency_relation(self):
        row = budget(2.18, -20, "y")
        self.assertAlmostEqual((1 + row["phase_relative_error"]) *
                               (1 + row["filled_cavity_frequency_relative_error"]), 1, places=13)

    def test_invalid_inputs(self):
        for f, dk, loss in [(0, 2.2, .001), (3e9, -1, .001), (3e9, 2.2, -1),
                            (np.nan, 2.2, .001)]:
            with self.assertRaises(ValueError):
                gamma(f, dk, loss)
        for dk, dt, axis in [(np.nan, 0, "x"), (2.2, np.nan, "x"),
                             (2.2, 100, "z"), (2.2, 0, "invalid")]:
            with self.assertRaises(ValueError):
                budget(dk, dt, axis)


if __name__ == "__main__":
    unittest.main(verbosity=2)
