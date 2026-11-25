"""Offline numerical tests, not a hardware or coupled-boundary acceptance."""

import unittest
import numpy as np

from run_thick_copper_boundary import (
    MU0, EPS0, SIGMA_TYPICAL, solve_conductor, finite_depth_reference,
    halfspace_reference, foster_parameters, foster_impedance,
)


class ThickCopperTests(unittest.TestCase):
    def test_second_order_fem(self):
        f = np.array([2.5e9, 3e9, 3.5e9])
        expected = finite_depth_reference(f, SIGMA_TYPICAL, 35e-6)
        errors = []
        for cells in (64, 128, 256):
            z, _, _, _ = solve_conductor(f, SIGMA_TYPICAL, 35e-6, cells)
            errors.append(np.max(abs(z / expected - 1)))
        self.assertGreater(errors[0], .01)
        self.assertLess(errors[2], .01)
        self.assertTrue(all(3.8 < a / b < 4.2 for a, b in zip(errors, errors[1:])))

    def test_finite_depth_limits(self):
        f = np.array([3e9])
        sigma = SIGMA_TYPICAL
        half = halfspace_reference(f, sigma)
        thick = finite_depth_reference(f, sigma, 35e-6)
        np.testing.assert_allclose(thick, half, rtol=1e-12)
        thin = finite_depth_reference(f, sigma, 1e-10)
        np.testing.assert_allclose(thin, 2j * np.pi * f * MU0 * 1e-10, rtol=1e-7)
        self.assertGreater(abs(finite_depth_reference(f, sigma, 2e-6) / half - 1)[0], .05)

    def test_halfspace_branch(self):
        f = np.geomspace(.5e9, 6e9, 100)
        z = halfspace_reference(f, SIGMA_TYPICAL)
        self.assertTrue(np.all(z.real > 0) and np.all(z.imag > 0))
        np.testing.assert_allclose(z * z, 2j * np.pi * f * MU0 /
                                   (SIGMA_TYPICAL + 2j * np.pi * f * EPS0), rtol=1e-14)

    def test_fem_energy(self):
        z, e, loss, incoming = solve_conductor(np.array([3e9]), SIGMA_TYPICAL, 8e-6, 128)
        self.assertEqual(e[0, -1], 0)
        self.assertEqual(z[0], e[0, 0])
        np.testing.assert_allclose(loss, incoming, rtol=1e-10)

    def test_positive_real_model(self):
        p, r, l = foster_parameters(SIGMA_TYPICAL, 64)
        self.assertTrue(np.all(p > 0) and np.all(r > 0) and np.all(l > 0))
        np.testing.assert_allclose(r / l, p, rtol=1e-14)
        # Positive real s checks are a numerical supplement to positive RL structure.
        for real_s in (1e3, 1e9, 1e12):
            s = real_s + 2j * np.pi * np.geomspace(.5e9, 6e9, 100)
            z = np.sum(r * s[:, None] / (s[:, None] + p), axis=1)
            self.assertTrue(np.all(z.real > 0))

    def test_model_accuracy_and_failed_order(self):
        f = np.geomspace(.5e9, 6e9, 401)
        for terms in (16, 32, 64):
            p, r, _ = foster_parameters(SIGMA_TYPICAL, terms)
            error = np.max(abs(foster_impedance(f, p, r) /
                               halfspace_reference(f, SIGMA_TYPICAL) - 1))
            if terms == 16:
                self.assertGreater(error, .01)
            else:
                self.assertLess(error, .001)

    def test_bilinear_step_limit(self):
        f = np.linspace(2.5e9, 3.5e9, 101)
        p, r, _ = foster_parameters(SIGMA_TYPICAL, 64)
        expected = halfspace_reference(f, SIGMA_TYPICAL)
        self.assertLess(np.max(abs(foster_impedance(f, p, r, 20e-12) / expected - 1)), .01)
        self.assertGreater(np.max(abs(foster_impedance(f, p, r, 50e-12) / expected - 1)), .01)

    def test_invalid_inputs(self):
        for sigma, depth, cells in ((0, 8e-6, 64), (58e6, 0, 64), (58e6, 8e-6, 1)):
            with self.assertRaises(ValueError):
                solve_conductor(np.array([3e9]), sigma, depth, cells)
        with self.assertRaises(ValueError):
            foster_parameters(SIGMA_TYPICAL, 4)
        p, r, _ = foster_parameters(SIGMA_TYPICAL, 64)
        for dt in (0, -1, np.nan, 1e-9):
            with self.assertRaises(ValueError):
                foster_impedance(np.array([3e9]), p, r, dt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
