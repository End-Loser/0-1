"""Six-face topology, admittance and power tests, not physical cavity approval."""

import unittest

import numpy as np

from finite_copper_cavity import CopperCavity, continuum_q, fit_pole
from verify_finite_copper_cavity import geometry, node_divergence, quadrature_q


class CopperTests(unittest.TestCase):
    def setUp(self):
        self.g = CopperCavity((4, 3, 2), (.16, .10, .06))

    def test_full_weights_and_records(self):
        m, q, edges, scale, labels, _ = geometry(self.g.shape, self.g.lengths)
        np.testing.assert_allclose(self.g.m, m, rtol=1e-14)
        np.testing.assert_allclose(self.g.q, q, rtol=1e-14)
        np.testing.assert_array_equal(self.g.wall_edges, edges)
        np.testing.assert_allclose(self.g.wall_scale, scale, rtol=1e-14)
        np.testing.assert_array_equal(self.g.wall_normals, labels[:, 0])
        np.testing.assert_array_equal(self.g.wall_sides, labels[:, 1])
        np.testing.assert_array_equal(self.g.wall_components, labels[:, 2])
        self.assertTrue(np.all(m > 0) and np.all(q > 0))

    def test_each_surface_tangent_area(self):
        _, _, _, _, labels, areas = geometry(self.g.shape, self.g.lengths)
        for normal in range(3):
            area = np.prod(np.delete(self.g.lengths, normal))
            for side in (0, 1):
                for tangent in range(3):
                    if tangent != normal:
                        select = np.all(labels == (normal, side, tangent), axis=1)
                        self.assertAlmostEqual(sum(areas[select]), area, places=14)

    def test_box_edges_two_parallel_records(self):
        counts = np.bincount(self.g.wall_edges, minlength=self.g.ne)
        self.assertEqual(max(counts), 2)
        self.assertGreater(sum(counts == 2), 0)
        z, per_record, total = self.g.admittance(3e9, [1., 2.], [1e-9, 2e-9])
        edge = int(np.flatnonzero(counts == 2)[0])
        self.assertAlmostEqual(abs(total[edge] / sum(per_record[self.g.wall_edges == edge]) - 1), 0)
        self.assertGreater(z.real, 0)

    def test_all_node_charge_orientation(self):
        rng = np.random.default_rng(291)
        flux = rng.normal(size=self.g.ne)
        np.testing.assert_allclose(-self.g.gradient.T @ flux,
                                   node_divergence(flux, self.g.shape), atol=1e-14)
        self.assertLess(abs(sum(node_divergence(flux, self.g.shape))), 1e-13)

    def test_continuum_Q_independent_quadrature(self):
        for mode in [(0, 1, 1), (1, 0, 1), (1, 1, 0)]:
            a = continuum_q(np.array(mode), np.array(self.g.lengths), 58.58e6)
            b = quadrature_q(np.array(mode), np.array(self.g.lengths), 58.58e6)
            np.testing.assert_allclose(a, b, rtol=1e-12)

    def test_copper_conductivity_scaling(self):
        a = continuum_q(np.array([0, 1, 1]), np.array(self.g.lengths), 58.58e6)[1]
        b = continuum_q(np.array([0, 1, 1]), np.array(self.g.lengths), 58.58e6 / 4)[1]
        self.assertAlmostEqual(a / b, 2., places=12)

    def test_driven_power_and_branch_loss(self):
        seed = self.g.pec.lift(self.g.pec.sample_mode((0, 1, 1)))
        data = self.g.solve(3e9, [1., 2.], [1e-9, 2e-9], self.g.m * seed)
        self.assertGreater(data["wall_power_W"], 0)
        self.assertLess(abs(data["source_power_W"] / data["wall_power_W"] - 1), 1e-10)
        r, l, s = np.array([1., 2.]), np.array([1e-9, 2e-9]), 2j * np.pi * 3e9
        j = data["wall_current_A"][:, None]
        i = j * r / (r + s * l)
        joule = .5 * np.sum(self.g.wall_scale[:, None] * r * abs(j - i)**2)
        self.assertLess(abs(joule / data["wall_power_W"] - 1), 1e-12)

    def test_local_pole_known_response(self):
        f = 3e9 + np.linspace(-1e5, 1e5, 9)
        pole = 3e9 + 2e4j
        result = fit_pole(f, (2. + 1j) / (f - pole))
        self.assertLess(abs(result["pole_Hz"] - pole), 1e-5)
        self.assertAlmostEqual(result["Q_from_local_pole"] / 75000, 1, places=10)

    def test_invalid_admittance(self):
        for frequency, r, l in [(0, [1], [1]), (3e9, [0], [1]),
                                (3e9, [1], [np.nan]), (3e9, [], []),
                                (3e9, [1, 2], [1])]:
            with self.assertRaises(ValueError):
                self.g.admittance(frequency, r, l)


if __name__ == "__main__":
    unittest.main(verbosity=2)
