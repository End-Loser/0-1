"""Doublet physics/reference regressions; archive checks are separate."""
import unittest

import numpy as np
from threadpoolctl import threadpool_limits

from copper_doublet import reference, discrete_sources, fit_matrix_poles
from finite_copper_cavity import CopperCavity
from run_copper_doublet import solve_drives, MIXED
from verify_copper_doublet import gaussian_reference, matrix_poles, seed_basis


class DoubletTests(unittest.TestCase):
    def test_two_loss_eigenvalues_match_independent_volume_surface_integrals(self):
        ref = reference((.16, .10, .06), 58.58e6)
        independent = gaussian_reference((.16, .10, .06), 58.58e6)
        np.testing.assert_allclose(np.sort(ref["Q"]), independent["Q"], rtol=1e-12)
        self.assertGreater(ref["frequency_Hz"], 2.5e9)
        self.assertLess(ref["frequency_Hz"], 3.5e9)
        self.assertGreater(max(ref["Q"]) / min(ref["Q"]), 1.01)

    def test_full_reference_matrices_not_just_scalar_q(self):
        ref = reference((.16, .10, .06), 58.58e6)
        independent = gaussian_reference((.16, .10, .06), 58.58e6, ref["basis"])
        for key in ("mass", "surface"):
            np.testing.assert_allclose(ref[key], independent[key], rtol=1e-12, atol=1e-29)
        self.assertGreater(abs(ref["surface"][0, 1]), .001 * np.linalg.norm(ref["surface"]))

    def test_cubic_cavity_has_equal_doublet_loss(self):
        ref = reference((.10, .10, .10), 58.58e6)
        np.testing.assert_allclose(ref["Q"][0], ref["Q"][1], rtol=1e-12)

    def test_reference_rotation_invariance(self):
        basis = reference((.16, .10, .06), 58.58e6)["basis"]
        rotation = np.array([[np.cos(.7), -np.sin(.7)], [np.sin(.7), np.cos(.7)]])
        first = gaussian_reference((.16, .10, .06), 58.58e6, basis)
        rotated = gaussian_reference((.16, .10, .06), 58.58e6, basis @ rotation)
        np.testing.assert_allclose(first["Q"], rotated["Q"], rtol=1e-12)

    def test_full_rank_transverse_discrete_basis(self):
        grid = CopperCavity((8, 5, 3), (.16, .10, .06))
        basis = reference(grid.lengths, 58.58e6)["basis"]
        seeds, drives, _ = discrete_sources(grid, basis)
        np.testing.assert_allclose(seeds.T @ (grid.m[:, None] * seeds), np.eye(2), atol=1e-13)
        independent = seed_basis(grid.shape, np.asarray(grid.lengths), basis, grid.m)
        np.testing.assert_allclose(seeds, independent, rtol=1e-12, atol=1e-10)
        for seed in seeds.T:
            self.assertLess(np.linalg.norm(grid.pec.charge(seed[grid.pec.active])), 1e-18)
        np.testing.assert_array_equal(drives, grid.m[:, None] * seeds)

    def synthetic(self, growing=False):
        f = 3.06e9 + np.linspace(-2e5, 2e5, 5)
        poles = np.array([3.06e9 - 1e4 + 7e4j, 3.06e9 + 1e4 + 9e4j])
        if growing:
            poles[0] = poles[0].conjugate()
        rotation = np.array([[np.cos(.7), -np.sin(.7)], [np.sin(.7), np.cos(.7)]])
        h = np.array([rotation @ np.diag(1 / (freq - poles)) @ rotation.T for freq in f])
        return f, poles, h, rotation

    def test_two_poles_recovered_independently_and_rotation_invariant(self):
        f, poles, h, rotation = self.synthetic()
        expected = poles[np.argsort(poles.real / (2 * poles.imag))]
        fit = fit_matrix_poles(f, h)
        independent, _ = matrix_poles(f, h)
        rotated = fit_matrix_poles(f, [rotation.T @ a @ rotation for a in h])
        np.testing.assert_allclose(fit["poles_Hz"], expected, atol=1e-5, rtol=1e-14)
        np.testing.assert_allclose(independent, expected, atol=1e-5, rtol=1e-14)
        np.testing.assert_allclose(rotated["poles_Hz"], expected, atol=1e-5, rtol=1e-14)

    def test_growing_pole_and_missing_polarization_rejected(self):
        f, _, h, _ = self.synthetic(growing=True)
        with self.assertRaises(ValueError):
            fit_matrix_poles(f, h)
        with self.assertRaises(AssertionError):
            matrix_poles(f, h)
        with self.assertRaises(ValueError):
            fit_matrix_poles(f, h[:, :1, :1])

    def test_independent_mixed_rhs_full_fields_and_reciprocity(self):
        grid = CopperCavity((8, 5, 3), (.16, .10, .06))
        ref = reference(grid.lengths, 58.58e6)
        _, basis_drives, _ = discrete_sources(grid, ref["basis"])
        drives = np.column_stack((basis_drives, basis_drives @ MIXED))
        with threadpool_limits(limits=1):
            e, b, j, power, _ = solve_drives(
                grid, ref["frequency_Hz"], np.array([.01, .1]), np.array([1e-10, 1e-11]), drives)
        for values in (e, b, j):
            expected = values[:, :2] @ MIXED
            self.assertLess(np.linalg.norm(values[:, 2] - expected) / np.linalg.norm(expected), 1e-10)
        h = basis_drives.T @ e[:, :2]
        np.testing.assert_allclose(h, h.T, rtol=1e-10, atol=1e-20)
        self.assertTrue(np.all(power > 0))
        self.assertTrue(np.all(np.linalg.eigvalsh((h + h.conj().T) / 2) > 0))

    def test_invalid_material_reference_rejected(self):
        for lengths, sigma in (((.16, .10, 0.), 58.58e6), ((.16, .10, .06), -1.)):
            with self.assertRaises(ValueError):
                reference(lengths, sigma)


if __name__ == "__main__":
    unittest.main(verbosity=2)
