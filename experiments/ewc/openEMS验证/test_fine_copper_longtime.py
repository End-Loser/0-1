"""Long-window configuration and full-state chunk continuity, not completion."""
import unittest

import numpy as np
from threadpoolctl import threadpool_limits

from copper_midpoint_cg import MidpointCG
from finite_copper_cavity import CopperCavity
from run_fine_copper_longtime import CONFIG, advance_chunk


class LongtimeTests(unittest.TestCase):
    def setUp(self):
        self.grid = CopperCavity((8, 5, 3), (.16, .10, .06))
        self.solver = MidpointCG(self.grid, 2e-12, [.01, .1], [1e-10, 1e-11])
        self.seed = self.grid.pec.lift(self.grid.pec.sample_mode((0, 1, 1)))
        self.b = np.zeros(self.grid.nf)
        self.state = np.zeros_like(self.solver.rr)

    def test_no_shortened_target(self):
        self.assertEqual(CONFIG["steps"], 4000000)
        self.assertAlmostEqual(CONFIG["steps"] * CONFIG["dt_s"], 8e-6, places=18)
        self.assertEqual(CONFIG["shape"], [36, 23, 14])
        self.assertEqual(CONFIG["terms"], 64)
        self.assertEqual(CONFIG["cg_rtol"], 1e-13)

    def test_chunked_full_state_equals_unsplit_steps(self):
        with threadpool_limits(limits=1):
            whole = advance_chunk(self.solver, self.seed, self.seed, self.b,
                                  self.state, 0., 0, 40, 10)
            first = advance_chunk(self.solver, self.seed, self.seed, self.b,
                                  self.state, 0., 0, 20, 10)
            second = advance_chunk(
                self.solver, self.seed, first["e_after"], first["b_after"], first["i_after"],
                float(first["cumulative_wall_heat_J"][-1]), 20, 40, 10)
        for key in ("e_after", "b_after", "i_after", "j_mid"):
            np.testing.assert_array_equal(whole[key], second[key])
        for key in ("sample_time_s", "total_energy_J", "cumulative_wall_heat_J", "modal_e_probe"):
            np.testing.assert_array_equal(whole[key], np.r_[first[key], second[key][1:]])
        self.assertEqual(len(whole["scaled_residual"]), 40)

    def test_complete_chunk_initial_state_preserved(self):
        with threadpool_limits(limits=1):
            row = advance_chunk(self.solver, self.seed, self.seed, self.b,
                                self.state, 0., 0, 20, 10)
        np.testing.assert_array_equal(row["e_start"], self.seed)
        np.testing.assert_array_equal(row["b_start"], self.b)
        np.testing.assert_array_equal(row["i_start"], self.state)
        self.assertEqual(row["end_step"], 20)
        self.assertLess(max(row["scaled_residual"]), 5e-13)

    def test_invalid_chunk_rejected(self):
        for start, end, stride in ((0, 0, 10), (-1, 20, 10), (0, 20, 0)):
            with self.assertRaises(ValueError):
                advance_chunk(self.solver, self.seed, self.seed, self.b,
                              self.state, 0., start, end, stride)


if __name__ == "__main__":
    unittest.main(verbosity=2)
