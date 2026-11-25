"""Full SPD solve regression, not permission to close a physical gate."""
import unittest

import numpy as np
from scipy.sparse import diags
from scipy.sparse.linalg import splu
from threadpoolctl import threadpool_limits

from copper_midpoint_cg import MidpointCG
from finite_copper_cavity import CopperCavity


class CGTests(unittest.TestCase):
    def setUp(self):
        self.grid = CopperCavity((8, 5, 3), (.16, .10, .06))
        self.r, self.l = np.array([.01, .1]), np.array([1e-10, 1e-11])

    def test_full_operator_identical_to_original_formula(self):
        solver = MidpointCG(self.grid, 2e-12, self.r, self.l)
        effective = np.sum(self.grid.wall_scale[:, None] * self.r /
                           (1 + 2e-12 * self.r / self.l / 2), axis=1)
        conductance = np.zeros(self.grid.ne)
        np.add.at(conductance, self.grid.wall_edges, 1 / effective)
        original = diags(2 * self.grid.m / 2e-12 + conductance) + 1e-12 * self.grid.stiffness
        np.testing.assert_allclose(solver.operator.toarray(), original.toarray(), rtol=1e-14)

    def test_general_rhs_matches_full_LU(self):
        with threadpool_limits(limits=1):
            for dt in (2e-12, 5e-12, 20e-12):
                solver = MidpointCG(self.grid, dt, self.r, self.l)
                rhs = np.random.default_rng(761).normal(size=self.grid.ne)
                actual, _, residual = solver.solve(rhs)
                expected = splu(solver.operator.tocsc()).solve(rhs)
                self.assertLess(np.linalg.norm(actual - expected) / np.linalg.norm(expected), 1e-11)
                self.assertLess(residual, 5e-13)

    def test_forced_iteration_failure_not_accepted(self):
        solver = MidpointCG(self.grid, 20e-12, self.r, self.l, maxiter=1)
        with threadpool_limits(limits=1):
            with self.assertRaises(RuntimeError):
                solver.solve(np.random.default_rng(8).normal(size=self.grid.ne))

    def test_relaxed_tolerance_rejected(self):
        with self.assertRaises(ValueError):
            MidpointCG(self.grid, 2e-12, self.r, self.l, rtol=1e-9)

    def test_invalid_physics_rejected(self):
        for dt, r, l in ((0., self.r, self.l), (2e-12, [0.], [1.]),
                         (2e-12, [1.], [np.nan])):
            with self.assertRaises(ValueError):
                MidpointCG(self.grid, dt, r, l)

    def test_bad_rhs_rejected(self):
        solver = MidpointCG(self.grid, 2e-12, self.r, self.l)
        for rhs in (np.ones(3), np.full(self.grid.ne, np.nan)):
            with self.assertRaises(ValueError):
                solver.solve(rhs)


if __name__ == "__main__":
    unittest.main(verbosity=2)
