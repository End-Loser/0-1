"""Topology and coupled-grid tests, not closed-cavity acceptance."""

import unittest
import numpy as np

from maxwell3d_boundary import Grid, simulate
from verify_maxwell3d_boundary import (
    curl_array, curl_transpose_array, divergence_array, gradient_array,
)


class Maxwell3DTests(unittest.TestCase):
    def setUp(self):
        self.g = Grid(4, 3, 2, .01, .02, .03)
        self.rng = np.random.default_rng(712)

    def test_curl_independent_components(self):
        e = self.rng.normal(size=self.g.ne)
        np.testing.assert_allclose(self.g.curl @ e, curl_array(e, self.g.shape), atol=1e-14)

    def test_transpose_independent_components(self):
        b = self.rng.normal(size=self.g.nf)
        np.testing.assert_allclose(self.g.curl.T @ b,
                                   curl_transpose_array(b, self.g.shape), atol=1e-14)

    def test_divergence_of_curl(self):
        e = self.rng.normal(size=self.g.ne)
        result = self.g.curl @ e
        np.testing.assert_allclose(divergence_array(result, self.g.shape), 0, atol=2e-14)
        np.testing.assert_allclose(self.g.divergence(result), 0, atol=2e-14)

    def test_curl_of_gradient(self):
        phi = self.rng.normal(size=(5, 3, 2))
        grad = gradient_array(phi)
        np.testing.assert_allclose(self.g.curl @ grad, 0, atol=2e-14)

    def test_discrete_energy_adjoint(self):
        e, b = self.rng.normal(size=self.g.ne), self.rng.normal(size=self.g.nf)
        electric = e @ (self.g.curl.T @ (self.g.q * b))
        magnetic = (self.g.q * b) @ (self.g.curl @ e)
        self.assertAlmostEqual(electric / magnetic, 1, places=13)

    def test_three_axis_bulk_dispersion(self):
        from maxwell3d_boundary import C0
        g = Grid(6, 5, 4, .01, .012, .014)
        wave = np.array([7., 2 * np.pi / (.012 * 5), 2 * np.pi / (.014 * 4)])
        spacing = np.array(g.spacing)
        discrete_wave = 2 * np.sin(wave * spacing / 2) / spacing
        polarization = np.cross(discrete_wave, [1., 2., 3.])
        fields = []
        for component, shape in enumerate(((6, 5, 4), (7, 5, 4), (7, 5, 4))):
            coords = np.indices(shape).astype(float)
            coords[component] += .5
            phase = sum(wave[c] * spacing[c] * coords[c] for c in range(3))
            fields.append(polarization[component] * spacing[component] * np.exp(1j * phase))
        e = np.concatenate([x.ravel() for x in fields])
        operator = (g.curl.T @ (g.q * (g.curl @ e))) / g.m
        expected = C0**2 * np.sum(discrete_wave**2) * e
        # Only bulk tangential nodes: terminal boundaries are not periodic in x.
        valid = np.ones(g.ne, bool)
        valid[g.left] = valid[g.wall] = False
        self.assertLess(np.linalg.norm((operator - expected)[valid]) /
                        np.linalg.norm(expected[valid]), 1e-12)

    def test_surface_mapping(self):
        self.assertTrue(np.all(self.g.m > 0) and np.all(self.g.q > 0))
        np.testing.assert_allclose(self.g.surface_scale[:6], .02 / .03)
        np.testing.assert_allclose(self.g.surface_scale[6:], .03 / .02)
        self.assertEqual(len(self.g.wall), 12)

    def test_nonuniform_energy(self):
        g = Grid(8, 2, 3, .015, .01, .015)
        data = simulate(g, 5e-12, 4e-9, [1., 2.], [1e-9, 2e-9], "both", True)
        self.assertLess(data["metrics"]["energy_closure_relative"], 1e-10)
        self.assertEqual(data["metrics"]["source_off_energy_increase_relative"], 0)
        self.assertTrue(np.all(data["component_peak_integral_units"] > 1e-20))
        self.assertGreater(data["metrics"]["copper_loss_J"], 0)

    def test_invalid_arguments(self):
        with self.assertRaises(ValueError):
            Grid(1, 2, 3, .01, .01, .01)
        with self.assertRaises(ValueError):
            Grid(2, 2, 3, .01, 0, .01)
        with self.assertRaises(ValueError):
            simulate(self.g, 0, 4e-9, [1.], [1e-9])
        with self.assertRaises(ValueError):
            simulate(self.g, 5e-12, 4e-9, [-1.], [1e-9])


if __name__ == "__main__":
    unittest.main(verbosity=2)
