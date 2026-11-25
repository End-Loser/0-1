"""Oblique reference and projection tests, not transient-boundary acceptance."""

import json
from pathlib import Path
import unittest

import numpy as np

from maxwell3d_boundary import Grid, MU, EPS
from run_oblique_boundary import projection, references, solve_case, KY
from verify_oblique_boundary import discrete_reference


class ObliqueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).parent / "\u539a\u94dc\u8fb9\u754c\u57fa\u51c6\u7ed3\u679c/passive_surface_models.json"
        cls.model = next(m for m in json.loads(path.read_text(encoding="utf-8"))["models"] if m["terms"] == 64)

    def test_normal_incidence_limit(self):
        f = np.array([3e9])
        s = 2j * np.pi * f
        expected = np.sqrt(s * MU / (self.model["sigma_S_per_m"] + s * EPS))
        for pol in ("TE", "TM"):
            air, metal, _ = references(f, 0, self.model["sigma_S_per_m"], pol)
            np.testing.assert_allclose(air, np.sqrt(MU / EPS), rtol=1e-14)
            np.testing.assert_allclose(metal, expected, rtol=1e-14)

    def test_air_impedance_duality(self):
        te = references(np.array([3e9]), KY, self.model["sigma_S_per_m"], "TE")[0]
        tm = references(np.array([3e9]), KY, self.model["sigma_S_per_m"], "TM")[0]
        np.testing.assert_allclose(te * tm, MU / EPS, rtol=1e-14)
        self.assertGreater(te[0], tm[0])

    def test_copper_positive_dissipation(self):
        for pol in ("TE", "TM"):
            _, z, _ = references(np.linspace(2.5e9, 3.5e9, 101), KY, self.model["sigma_S_per_m"], pol)
            self.assertTrue(np.all(z.real > 0))

    def test_cutoff_rejected(self):
        with self.assertRaises(ValueError):
            references(np.array([1e9]), KY, self.model["sigma_S_per_m"], "TE")

    def test_projection_orthonormal(self):
        g = Grid(6, 4, 2, .02, .05, .015)
        p = projection(g, KY)
        np.testing.assert_allclose((p.conj().T @ p).toarray(), np.eye(p.shape[1]), atol=1e-14)

    def test_full_matrix_projection_agreement(self):
        for pol in ("TE", "TM"):
            data = solve_case(6, 4, pol, self.model, np.array([3e9]), True)
            self.assertLess(data["full_solve_difference_relative"][0], 1e-10)

    def test_discrete_ABCD_agreement(self):
        r, l = np.array(self.model["R_ohm"]), np.array(self.model["L_H"])
        for pol in ("TE", "TM"):
            data = solve_case(10, 8, pol, self.model, np.array([3e9]))
            reflection, density = discrete_reference(3e9, 10, 8, pol, r, l)
            np.testing.assert_allclose(data["reflection"][0], reflection, atol=1e-10)
            np.testing.assert_allclose(data["absorbed_density_W_per_m2"][0], density, rtol=1e-10)

    def test_transverse_refinement(self):
        r, l = np.array(self.model["R_ohm"]), np.array(self.model["L_H"])
        for pol in ("TE", "TM"):
            air, metal, kx = references(np.array([3e9]), KY, self.model["sigma_S_per_m"], pol)
            expected = ((metal - air) / (metal + air) * np.exp(-2j * kx * .12))[0]
            coarse = discrete_reference(3e9, 240, 8, pol, r, l)[0]
            fine = discrete_reference(3e9, 240, 64, pol, r, l)[0]
            self.assertGreater(abs(coarse - expected), .01)
            self.assertLess(abs(fine - expected), .01)

    def test_TM_longitudinal_field(self):
        te = solve_case(10, 8, "TE", self.model, np.array([3e9]))["modal_edge_amplitudes_V"][0, :10]
        tm = solve_case(10, 8, "TM", self.model, np.array([3e9]))["modal_edge_amplitudes_V"][0, :10]
        self.assertLess(np.max(abs(te)), 1e-10)
        self.assertGreater(np.max(abs(tm)), 1e-5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
