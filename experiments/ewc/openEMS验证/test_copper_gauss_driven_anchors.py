"""Reject incomplete, nonfinite and out-of-gate anchor pairs."""
import unittest

import numpy as np

from compare_copper_gauss_driven_anchors import check_pair, declaration, PAIR_GATE
from copper_gauss_precomputed import PrecomputedGaussLegendre
from finite_copper_cavity import CopperCavity
from verify_copper_gauss_state import check_collocation, equations_pass


class DrivenAnchorTests(unittest.TestCase):
    def test_exact_pair(self):
        states = tuple(np.ones((2, 3)) for _ in range(7))
        self.assertEqual(check_pair(states, states), [0.] * 7)

    def test_missing_full_stage_rejected(self):
        states = tuple(np.ones(3) for _ in range(6))
        with self.assertRaises(ValueError):
            check_pair(states, states)

    def test_shape_change_rejected(self):
        states = tuple(np.ones(3) for _ in range(7))
        changed = (np.ones(4), *states[1:])
        with self.assertRaises(ValueError):
            check_pair(states, changed)

    def test_nonfinite_rejected(self):
        states = tuple(np.ones(3) for _ in range(7))
        changed = (np.full(3, np.nan), *states[1:])
        with self.assertRaises(ValueError):
            check_pair(states, changed)

    def test_gate_not_relaxed(self):
        states = tuple(np.ones(3) for _ in range(7))
        changed = (states[0] * (1 + 2 * PAIR_GATE), *states[1:])
        with self.assertRaises(ValueError):
            check_pair(states, changed)
        self.assertEqual(declaration()["gmres_rtol"], 1e-13)
        self.assertEqual(declaration()["residual_gate"], 5e-13)

    def test_zero_pair_and_scope(self):
        states = tuple(np.zeros(3) for _ in range(7))
        self.assertEqual(check_pair(states, states), [0.] * 7)
        limits = declaration()
        for key in ("continuous_16ns_history", "full_long_history_passed",
                    "general_initial_state_approved", "actual_feed_passed",
                    "frozen_long_solver_replaced"):
            self.assertIs(limits[key], False)
        self.assertEqual(limits["full_system_gates_closed"], 0)

    def test_independent_source_tamper_rejected(self):
        grid = CopperCavity((3, 2, 2), (.16, .10, .06))
        r, l, dt = np.array([.01, .1]), np.array([1e-10, 1e-11]), 2e-12
        solver = PrecomputedGaussLegendre(grid, dt, r, l)
        initial = (np.zeros(grid.ne), np.zeros(grid.nf), np.zeros_like(solver.rr))
        rng = np.random.default_rng(156)
        source = rng.normal(size=(2, grid.ne)) * 1e-3
        result = solver.step(*initial, source)
        metrics = check_collocation(grid.shape, grid.lengths, dt, r, l,
                                    initial, result[:3], result[3:7], source)
        self.assertTrue(equations_pass(metrics))
        changed = check_collocation(grid.shape, grid.lengths, dt, r, l,
                                    initial, result[:3], result[3:7], -source)
        self.assertFalse(equations_pass(changed))


if __name__ == "__main__":
    unittest.main(verbosity=2)
