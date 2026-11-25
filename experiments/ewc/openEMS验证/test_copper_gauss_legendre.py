"""Uneliminated two-stage full equations and matrix-exponential order reference."""
import unittest

import numpy as np
from scipy.linalg import expm
from threadpoolctl import threadpool_limits

from copper_gauss_legendre import GaussLegendre
from finite_copper_cavity import CopperCavity
from verify_copper_gauss_state import check_collocation, equations_pass


def uneliminated(grid, r, l):
    ne, nf, nw = grid.ne, grid.nf, len(grid.wall_edges)
    rr = grid.wall_scale[:, None] * r
    ll = grid.wall_scale[:, None] * l
    ni, ij = rr.size, ne + nf + rr.size
    n = ij + nw
    mass = np.concatenate((grid.m, np.ones(nf), ll.ravel()))
    flux = np.zeros((n, n))
    flux[:ne, ne:ne+nf] = grid.curl.T.toarray() * grid.q
    flux[ne:ne+nf, :ne] = -grid.curl.toarray()
    jmap = np.zeros((nw, ij))
    for w, edge in enumerate(grid.wall_edges):
        flux[edge, ij + w] -= 1
        flux[ij + w, edge] = 1
        flux[ij + w, ij + w] = -rr[w].sum()
        jmap[w, edge] = 1 / rr[w].sum()
        for k in range(len(r)):
            row = ne + nf + w * len(r) + k
            flux[row, row] = -rr[w, k]
            flux[row, ij + w] = rr[w, k]
            flux[ij + w, row] = rr[w, k]
            jmap[w, row] = rr[w, k] / rr[w].sum()
    return mass, flux, jmap


class GaussTests(unittest.TestCase):
    def setUp(self):
        self.grid = CopperCavity((3, 2, 2), (.16, .10, .06))
        self.r, self.l = np.array([.01, .1]), np.array([1e-10, 1e-11])
        rng = np.random.default_rng(423)
        self.before = (rng.normal(size=self.grid.ne),
                       rng.normal(size=self.grid.nf) * 1e-9,
                       rng.normal(size=(len(self.grid.wall_edges), 2)) * .01)
        self.sources = rng.normal(size=(2, self.grid.ne)) * .02

    def test_two_stage_full_LU_and_energy_charge(self):
        mass, flux, _ = uneliminated(self.grid, self.r, self.l)
        n, ni = len(flux), len(mass)
        initial = np.concatenate([x.ravel() for x in self.before])
        tableau = np.array([[.25, .25 - np.sqrt(3) / 6],
                            [.25 + np.sqrt(3) / 6, .25]])
        with threadpool_limits(limits=1):
            for dt in (2e-12, 20e-12):
                system, rhs = np.zeros((2 * n, 2 * n)), np.zeros(2 * n)
                for s in range(2):
                    rhs[s*n:s*n+ni] = mass * initial
                    for t in range(2):
                        system[s*n:s*n+ni, t*n:(t+1)*n] = -dt * tableau[s, t] * flux[:ni]
                        rhs[s*n:s*n+self.grid.ne] += dt * tableau[s, t] * self.sources[t]
                    system[s*n:s*n+ni, s*n:s*n+ni] += np.diag(mass)
                    system[s*n+ni:(s+1)*n, s*n:(s+1)*n] = flux[ni:]
                expected = np.linalg.solve(system, rhs).reshape(2, n)
                solver = GaussLegendre(self.grid, dt, self.r, self.l)
                result = solver.step(*self.before, self.sources)
                actual = np.concatenate((result[3], result[4],
                                         result[5].reshape(2, -1), result[6]), axis=1)
                for a, b in zip(actual.T, expected.T):
                    self.assertLess(np.linalg.norm(a - b) / max(np.linalg.norm(b), 1e-25), 1e-9)
                force = expected @ flux[:ni].T
                force[:, :self.grid.ne] += self.sources
                final = initial + dt / 2 * force.sum(axis=0) / mass
                actual_final = np.concatenate([x.ravel() for x in result[:3]])
                self.assertLess(np.linalg.norm((final - actual_final) * np.sqrt(mass)) /
                                np.linalg.norm(final * np.sqrt(mass)), 1e-10)
                metrics = check_collocation((3, 2, 2), (.16, .10, .06), dt,
                                            self.r, self.l, self.before, result[:3],
                                            result[3:7], self.sources)
                self.assertTrue(equations_pass(metrics), metrics)

    def test_fourth_order_against_complete_matrix_exponential(self):
        mass, flux, jmap = uneliminated(self.grid, self.r, self.l)
        ni = len(mass)
        generator = (flux[:ni, :ni] + flux[:ni, ni:] @ jmap) / mass[:, None]
        initial = np.concatenate([x.ravel() for x in self.before])
        weight = np.sqrt(np.concatenate((self.grid.m, self.grid.q,
                                         (self.grid.wall_scale[:, None] * self.l).ravel())))
        scaled_generator = weight[:, None] * generator / weight[None, :]
        errors = []
        with threadpool_limits(limits=1):
            # Resolve the ~9 fs surface relaxation before measuring formal order.
            reference = expm(1e-14 * scaled_generator) @ (weight * initial)
            for steps in (2, 4, 8):
                solver = GaussLegendre(self.grid, 1e-14 / steps, self.r, self.l)
                states = tuple(x.copy() for x in self.before)
                for _ in range(steps):
                    states = solver.step(*states, np.zeros_like(self.sources))[:3]
                final = np.concatenate([x.ravel() for x in states])
                errors.append(np.linalg.norm(final * weight - reference) / np.linalg.norm(reference))
        for coarse, fine in zip(errors, errors[1:]):
            self.assertGreater(coarse / fine, 8.)
            self.assertLess(coarse / fine, 24.)

    def test_unresolved_general_initial_state_is_not_approved(self):
        mass, flux, jmap = uneliminated(self.grid, self.r, self.l)
        ni = len(mass)
        weight = np.sqrt(np.concatenate((self.grid.m, self.grid.q,
                                         (self.grid.wall_scale[:, None] * self.l).ravel())))
        generator = (flux[:ni, :ni] + flux[:ni, ni:] @ jmap) / mass[:, None]
        scaled = weight[:, None] * generator / weight[None, :]
        initial = np.concatenate([x.ravel() for x in self.before])
        with threadpool_limits(limits=1):
            reference = expm(80e-12 * scaled) @ (weight * initial)
            solver = GaussLegendre(self.grid, 10e-12, self.r, self.l)
            states = self.before
            for _ in range(8):
                states = solver.step(*states, np.zeros_like(self.sources))[:3]
        error = np.linalg.norm(weight * np.concatenate([x.ravel() for x in states]) - reference) / np.linalg.norm(reference)
        self.assertGreater(error, .01, "Preserve unresolved-fast-state failure; no general approval")

    def test_signed_source_work_and_tampering(self):
        solver = GaussLegendre(self.grid, 2e-12, self.r, self.l)
        before = (self.before[0], np.zeros(self.grid.nf), np.zeros_like(self.before[2]))
        sources = np.tile(-self.grid.m * before[0] / (100 * solver.dt), (2, 1))
        with threadpool_limits(limits=1):
            result = solver.step(*before, sources)
        metrics = check_collocation((3, 2, 2), (.16, .10, .06), solver.dt, self.r, self.l,
                                    before, result[:3], result[3:7], sources)
        self.assertTrue(equations_pass(metrics))
        self.assertLess(metrics["source_work_J"], 0.)
        for changed in (np.zeros_like(sources), -sources):
            bad = check_collocation((3, 2, 2), (.16, .10, .06), solver.dt, self.r, self.l,
                                    before, result[:3], result[3:7], changed)
            self.assertFalse(equations_pass(bad))
        stages = list(result[3:7])
        stages[3] = stages[3] * 1.01
        bad = check_collocation((3, 2, 2), (.16, .10, .06), solver.dt, self.r, self.l,
                                before, result[:3], stages, sources)
        self.assertFalse(equations_pass(bad))

    def test_stiff_full_memories_dissipate_without_negative_heat(self):
        r, l = np.array([.01, .1]), np.array([1e-16, 1e-12])
        solver = GaussLegendre(self.grid, 20e-12, r, l)
        before = self.before
        with threadpool_limits(limits=1):
            for _ in range(5):
                result = solver.step(*before, np.zeros_like(self.sources))
                metrics = check_collocation((3, 2, 2), (.16, .10, .06), solver.dt, r, l,
                                            before, result[:3], result[3:7], np.zeros_like(self.sources))
                self.assertTrue(equations_pass(metrics), metrics)
                self.assertGreaterEqual(metrics["heat_J"], 0)
                self.assertLessEqual(metrics["after_energy_J"], metrics["before_energy_J"] * (1 + 1e-10))
                before = result[:3]

    def test_bad_states_sources_and_nonconvergence_rejected(self):
        solver = GaussLegendre(self.grid, 20e-12, self.r, self.l)
        for index in range(4):
            for value in (np.ones(1), np.full_like((*self.before, self.sources)[index], np.nan),
                          np.asarray((*self.before, self.sources)[index], complex)):
                args = list((*self.before, self.sources)); args[index] = value
                with self.assertRaises(ValueError):
                    solver.step(*args)
        strict = GaussLegendre(self.grid, 20e-12, self.r, self.l, maxiter=1)
        with threadpool_limits(limits=1):
            with self.assertRaises(RuntimeError):
                strict.step(*self.before, self.sources)
        with self.assertRaises(ValueError):
            GaussLegendre(self.grid, 20e-12, self.r, self.l, rtol=1e-9)
        with self.assertRaises(ValueError):
            GaussLegendre(self.grid, 20e-12, self.r.astype(complex), self.l)


if __name__ == "__main__":
    unittest.main(verbosity=2)
