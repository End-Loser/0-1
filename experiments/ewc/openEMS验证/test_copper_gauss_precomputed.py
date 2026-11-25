"""Reuse complete LU, exponential, source/heat and rejection tests."""
from unittest.mock import patch
import unittest

import numpy as np

import test_copper_gauss_legendre as original_tests
from copper_gauss_precomputed import PrecomputedGaussLegendre


class PrecomputedGaussTests(original_tests.GaussTests):
    def setUp(self):
        super().setUp()
        replacement = patch.object(original_tests, "GaussLegendre", PrecomputedGaussLegendre)
        replacement.start()
        self.addCleanup(replacement.stop)

    def test_operator_and_strict_solve_are_unchanged(self):
        from copper_gauss_legendre import GaussLegendre
        original = GaussLegendre(self.grid, 2e-12, self.r, self.l)
        candidate = PrecomputedGaussLegendre(self.grid, 2e-12, self.r, self.l)
        for name in ("operator", "scaled"):
            self.assertEqual((getattr(original, name) != getattr(candidate, name)).nnz, 0)
        for name in ("rr", "ll", "effective", "invroot", "nodes"):
            np.testing.assert_array_equal(getattr(original, name), getattr(candidate, name))
        self.assertIs(PrecomputedGaussLegendre.solve, GaussLegendre.solve)


if __name__ == "__main__":
    unittest.main(verbosity=2)
