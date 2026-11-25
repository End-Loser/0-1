"""Independent uneliminated equations, source work and charge regressions."""
import unittest

import numpy as np
from threadpoolctl import threadpool_limits

from copper_driven_midpoint import DrivenMidpoint
from copper_midpoint_cg import MidpointCG
from finite_copper_cavity import CopperCavity
from verify_copper_driven_state import check_step, equations_pass


class DrivenTests(unittest.TestCase):
    def setUp(self):
        self.grid = CopperCavity((3, 2, 2), (.16, .10, .06))
        self.r = np.array([.01, .1])
        self.l = np.array([1e-10, 1e-11])
        self.solver = DrivenMidpoint(self.grid, 2e-12, self.r, self.l)
        rng = np.random.default_rng(193)
        self.before = (rng.normal(size=self.grid.ne),
                       rng.normal(size=self.grid.nf) * 1e-9,
                       rng.normal(size=self.solver.rr.shape) * .01)
        self.source = rng.normal(size=self.grid.ne) * .02

    def metrics(self, after, j, source=None):
        return check_step((3, 2, 2), (.16, .10, .06), self.solver.dt,
                          self.r, self.l, self.before, after, j,
                          self.source if source is None else source)

    def test_full_uneliminated_LU(self):
        g, s = self.grid, self.solver
        ne, nf, nw = g.ne, g.nf, len(s.edges)
        ni = s.rr.size
        n = ne + nf + ni + nw
        a, rhs = np.zeros((n, n)), np.zeros(n)
        ie, ib, ii, ij = 0, ne, ne + nf, ne + nf + ni
        a[:ne, :ne] = np.diag(2 * g.m / s.dt)
        a[:ne, ib:ii] = -g.curl.T.toarray() * g.q
        a[ib:ii, :ne] = g.curl.toarray()
        a[ib:ii, ib:ii] = np.eye(nf) * 2 / s.dt
        rhs[:ne] = 2 * g.m / s.dt * self.before[0] + self.source
        rhs[ib:ii] = 2 / s.dt * self.before[1]
        for wall, edge in enumerate(s.edges):
            a[edge, ij + wall] += 1
            a[ij + wall, edge] = 1
            a[ij + wall, ij + wall] = -s.rr[wall].sum()
            for branch in range(len(self.r)):
                row = ii + wall * len(self.r) + branch
                a[row, row] = 2 * s.ll[wall, branch] / s.dt + s.rr[wall, branch]
                a[row, ij + wall] = -s.rr[wall, branch]
                a[ij + wall, row] = s.rr[wall, branch]
                rhs[row] = 2 * s.ll[wall, branch] / s.dt * self.before[2][wall, branch]
        with threadpool_limits(limits=1):
            midpoint = np.linalg.solve(a, rhs)
            actual = s.step(*self.before, self.source)
        expected = (2 * midpoint[:ne] - self.before[0],
                    2 * midpoint[ib:ii] - self.before[1],
                    2 * midpoint[ii:ij].reshape(s.rr.shape) - self.before[2],
                    midpoint[ij:])
        for result, wanted in zip(actual, expected):
            self.assertLess(np.linalg.norm(result - wanted) /
                            max(np.linalg.norm(wanted), 1e-25), 1e-10)
        self.assertTrue(equations_pass(self.metrics(actual[:3], actual[3])))

    def test_zero_source_is_original_full_CG(self):
        old = MidpointCG(self.grid, self.solver.dt, self.r, self.l)
        with threadpool_limits(limits=1):
            actual = self.solver.step(*self.before, np.zeros(self.grid.ne))
            expected = old.step(*self.before)
        for a, b in zip(actual, expected):
            np.testing.assert_array_equal(a, b)

    def test_source_work_and_charge_are_not_optional(self):
        with threadpool_limits(limits=1):
            actual = self.solver.step(*self.before, self.source)
        metrics = self.metrics(actual[:3], actual[3])
        self.assertTrue(equations_pass(metrics))
        self.assertGreater(abs(metrics["source_work_J"]), 0)
        omitted = self.metrics(actual[:3], actual[3], np.zeros(self.grid.ne))
        self.assertGreater(omitted["electric"], 1e-3)
        self.assertGreater(omitted["charge"], 1e-3)
        self.assertGreater(omitted["energy"], 1e-9)

    def test_wrong_source_sign_and_wall_current_rejected(self):
        with threadpool_limits(limits=1):
            actual = self.solver.step(*self.before, self.source)
        self.assertFalse(equations_pass(self.metrics(actual[:3], actual[3], -self.source)))
        self.assertFalse(equations_pass(self.metrics(actual[:3], actual[3] * 1.01)))

    def test_source_can_extract_energy_with_signed_work(self):
        before = (self.before[0], np.zeros(self.grid.nf), np.zeros(self.solver.rr.shape))
        source = -self.grid.m * before[0] / (100 * self.solver.dt)
        with threadpool_limits(limits=1):
            actual = self.solver.step(*before, source)
        metrics = check_step((3, 2, 2), (.16, .10, .06), self.solver.dt,
                             self.r, self.l, before, actual[:3], actual[3], source)
        self.assertTrue(equations_pass(metrics))
        self.assertLess(metrics["source_work_J"], 0.)
        self.assertLess(metrics["after_energy_J"], metrics["before_energy_J"])

    def test_bad_full_states_and_source_rejected(self):
        for index in range(4):
            for bad in (np.ones(1), np.full_like((*self.before, self.source)[index], np.nan),
                        np.asarray((*self.before, self.source)[index], complex)):
                args = list((*self.before, self.source))
                args[index] = bad
                with self.assertRaises(ValueError):
                    self.solver.step(*args)

    def test_nonconvergence_is_not_accepted(self):
        s = DrivenMidpoint(self.grid, 20e-12, self.r, self.l, maxiter=1)
        with threadpool_limits(limits=1):
            with self.assertRaises(RuntimeError):
                s.step(*self.before, self.source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
