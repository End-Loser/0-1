"""Finite-PEC topology tests; no copper or whole-system acceptance."""

import unittest

import numpy as np
from scipy.linalg import eigh

from finite_pec_cavity import FinitePEC, transient, C0
from verify_finite_pec_cavity import (
    curl_array, transpose_array, divergence_array, gradient_array,
    interior_divergence, lift, mask, weights,
)


class FiniteTests(unittest.TestCase):
    def setUp(self):
        self.g = FinitePEC((4, 3, 2), (.16, .10, .06))
        self.rng = np.random.default_rng(612)

    def test_full_curl_array(self):
        e = self.rng.normal(size=self.g.ne_full)
        np.testing.assert_allclose(self.g.full_curl @ e, curl_array(e, self.g.shape), atol=1e-14)

    def test_full_transpose_array(self):
        b = self.rng.normal(size=self.g.nf)
        np.testing.assert_allclose(self.g.full_curl.T @ b, transpose_array(b, self.g.shape), atol=1e-14)

    def test_divergence_curl_all_boundary_cells(self):
        e = self.rng.normal(size=self.g.ne_full)
        b = self.g.full_curl @ e
        np.testing.assert_allclose(self.g.divergence(b), 0, atol=1e-14)
        np.testing.assert_allclose(divergence_array(b, self.g.shape), 0, atol=1e-14)

    def test_gradient_and_boundary_restriction(self):
        phi = self.rng.normal(size=(5, 4, 3))
        np.testing.assert_allclose(self.g.full_gradient @ phi.ravel(), gradient_array(phi))
        np.testing.assert_allclose(self.g.full_curl @ gradient_array(phi), 0, atol=1e-14)
        phi[[0, -1], :, :] = 0
        phi[:, [0, -1], :] = 0
        phi[:, :, [0, -1]] = 0
        restricted = self.g.gradient @ phi[1:-1, 1:-1, 1:-1].ravel()
        np.testing.assert_allclose(restricted, gradient_array(phi)[mask(self.g.shape)])
        np.testing.assert_allclose(self.g.curl @ restricted, 0, atol=1e-14)

    def test_interior_charge_sign(self):
        e = self.rng.normal(size=self.g.ne)
        np.testing.assert_allclose(self.g.charge(e),
                                   interior_divergence(lift(self.g.m * e, self.g.shape), self.g.shape),
                                   atol=1e-26)

    def test_independent_weights_and_zero_tangential_fields(self):
        m, q = weights(self.g.shape, self.g.lengths)
        np.testing.assert_allclose(self.g.m, m, rtol=1e-14)
        np.testing.assert_allclose(self.g.q, q, rtol=1e-14)
        e = self.rng.normal(size=self.g.ne)
        np.testing.assert_allclose(self.g.lift(e), lift(e, self.g.shape))
        self.assertTrue(np.all(m > 0) and np.all(q > 0))
        self.assertTrue(np.all(self.g.lift(e)[~mask(self.g.shape)] == 0))

    def test_dense_first_eigenfrequency_and_gradient_nullspace(self):
        values = eigh(self.g.stiffness.toarray(), np.diag(self.g.m), eigvals_only=True)
        first = values[values > np.max(values) * 1e-10][0]
        indices = np.array([1, 1, 0])
        h = self.g.spacing
        expected = C0**2 * np.sum((2 * np.sin(np.pi * indices * h /
                                 (2 * np.asarray(self.g.lengths))) / h)**2)
        self.assertLess(abs(first / expected - 1), 1e-12)
        self.assertEqual(np.sum(abs(values) < np.max(values) * 1e-10),
                         np.prod(np.array(self.g.shape) - 1))

    def test_three_positive_indices_and_targeted_eigensolve(self):
        g = FinitePEC((8, 5, 3), (.16, .10, .06))
        value, e = g.solve_mode((1, 1, 1))
        residual = g.stiffness @ e - value * g.m * e
        self.assertLess(np.linalg.norm(residual) / np.linalg.norm(value * g.m * e), 1e-10)
        self.assertLess(np.linalg.norm(g.charge(e)) / np.linalg.norm(g.m * e), 1e-10)

    def test_forced_midpoint_charge_and_energy(self):
        data = transient(self.g, 20e-12, 60)
        e, b, j, dt = data["e_V"], data["b_Wb"], data["current_A"], float(data["dt_s"])
        energy = .5 * (e**2 @ self.g.m + b**2 @ self.g.q)
        expected = dt * np.r_[0., np.cumsum(data["source_work_W"])]
        self.assertLess(np.max(abs(energy - expected)) / max(energy), 1e-11)
        final_charge = self.g.charge(e[-1])
        injected = -dt * self.g.gradient.T @ j.sum(axis=0)
        self.assertGreater(np.linalg.norm(final_charge), 0)
        self.assertLess(np.linalg.norm(final_charge - injected) / np.linalg.norm(injected), 1e-11)

    def test_invalid_arguments(self):
        for shape, lengths in [((1, 3, 2), (.1, .1, .1)), ((2.5, 3, 2), (.1, .1, .1)),
                               ((2, 3, 2), (.1, np.nan, .1)), ((2, 3, 2), (.1, 0, .1)),
                               ((np.nan, 3, 2), (.1, .1, .1))]:
            with self.assertRaises(ValueError):
                FinitePEC(shape, lengths)
        for indices in [(1, 0, 0), (-1, 1, 0), (4, 1, 0), (1.5, 1, 0)]:
            with self.assertRaises(ValueError):
                self.g.sample_mode(indices)
        for dt, steps in [(0, 10), (np.nan, 10), (1e-12, 1), (1e-12, 2.5)]:
            with self.assertRaises(ValueError):
                transient(self.g, dt, steps)


if __name__ == "__main__":
    unittest.main(verbosity=2)
