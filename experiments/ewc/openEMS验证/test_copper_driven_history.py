"""Declared pulse causality and small full-state runner smoke tests."""
import unittest

import numpy as np
from threadpoolctl import threadpool_limits

from run_copper_driven_history import coefficients, simulate, PULSES
from verify_copper_driven_state import check_step, equations_pass


class HistoryTests(unittest.TestCase):
    def test_pulses_are_causal_and_end_exactly(self):
        f = np.array([2.9e9, 3.06e9, 3.06e9])
        for t in (-1., 0., .5e-9, 12e-9, 16e-9):
            np.testing.assert_array_equal(coefficients(t, f, PULSES), np.zeros(3))
        self.assertGreater(np.linalg.norm(coefficients(2.2e-9, f, PULSES)), 0)

    def test_overlapping_sources_add_without_state_reset(self):
        f, t = np.array([2.9e9, 3.06e9, 3.06e9]), 2.2e-9
        expected = sum((coefficients(t, f, [p]) for p in PULSES), np.zeros(3))
        np.testing.assert_array_equal(coefficients(t, f, PULSES), expected)
        self.assertEqual(np.count_nonzero(expected), 2)

    def test_small_runner_saved_steps_satisfy_driven_equations(self):
        config = dict(shape=[3, 2, 2], lengths_m=[.16, .10, .06], dt_s=2e-12,
                      duration_s=20e-12, sample_interval_s=2e-12, snapshots=10,
                      case="mixed_history", initial_seed=None,
                      pulses=[dict(mode=0, start_s=0., end_s=20e-12,
                                   amplitude=1e9, phase_rad=0.)])
        r, l = np.array([.01, .1]), np.array([1e-10, 1e-11])
        with threadpool_limits(limits=1):
            data = simulate(config, r, l, 58.58e6)
        from verify_finite_copper_cavity import geometry
        m = geometry(config["shape"], config["lengths_m"])[0]
        for n in range(10):
            before = tuple(data["snapshot_" + key][n] for key in ("e_before", "b_before", "i_before"))
            after = tuple(data["snapshot_" + key][n] for key in ("e_after", "b_after", "i_after"))
            source = m * (data["seeds"] @ data["snapshot_source_coefficients"][n])
            metrics = check_step(config["shape"], config["lengths_m"], config["dt_s"],
                                 r, l, before, after, data["snapshot_j_mid"][n], source)
            self.assertTrue(equations_pass(metrics))
            self.assertAlmostEqual(metrics["heat_J"], data["per_step_heat_J"][n], delta=1e-20)
            self.assertAlmostEqual(metrics["source_work_J"], data["per_step_source_work_J"][n], delta=1e-20)
        samples = data["samples"]
        self.assertGreater(samples[-1, 1], 0.)
        closure = np.max(abs(samples[:, 1] + samples[:, 2] - samples[:, 3]))
        self.assertLess(closure / samples[-1, 1], 1e-7)


if __name__ == "__main__":
    unittest.main(verbosity=2)
