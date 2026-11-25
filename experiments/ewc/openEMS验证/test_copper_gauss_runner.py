"""Candidate raw-state runner smoke, using small geometry only in memory."""
import unittest
from unittest.mock import patch

import numpy as np
from threadpoolctl import threadpool_limits

import run_copper_gauss_validation as runner
from verify_copper_gauss_state import check_collocation, equations_pass


class RunnerTests(unittest.TestCase):
    def test_preflight_retains_actual_unresolved_state_failure(self):
        with threadpool_limits(limits=1):
            data = runner.small_preflight(80e-12, 8)
        self.assertGreater(float(data["complete_state_energy_norm_error"]), .01)
        self.assertEqual(data["stage_i"].shape[0], 8)
        self.assertEqual(data["stage_i"].shape[1], 2)
        for n in range(1, 8):
            for field in ("e", "b", "i"):
                np.testing.assert_array_equal(data[field + "_before"][n], data[field + "_after"][n-1])

    def test_saved_two_node_sources_and_energy(self):
        config = dict(case="mixed_history", initial_seed=None, dt_s=2e-12,
                      duration_s=20e-12, sample_interval_s=2e-12,
                      pulses=[dict(mode=0, start_s=0., end_s=20e-12, amplitude=1e9, phase_rad=0.)])
        model = dict(R_ohm=[.01, .1], L_H=[1e-10, 1e-11], sigma_S_per_m=58.58e6)
        with patch.object(runner, "SHAPE", (3, 2, 2)), threadpool_limits(limits=1):
            data = runner.physical_case(config, model)
        for i, step in enumerate(data["snapshot_step"]):
            before = tuple(data["snapshot_" + k][i] for k in ("e_before", "b_before", "i_before"))
            after = tuple(data["snapshot_" + k][i] for k in ("e_after", "b_after", "i_after"))
            stages = tuple(data["snapshot_stage_" + k][i] for k in ("e", "b", "i", "j"))
            metrics = check_collocation((3, 2, 2), runner.LENGTHS, config["dt_s"],
                                        np.array(model["R_ohm"]), np.array(model["L_H"]),
                                        before, after, stages, data["snapshot_source_currents"][i])
            self.assertTrue(equations_pass(metrics), metrics)
            np.testing.assert_allclose(metrics["heat_J"], data["per_step_heat_J"][step], rtol=1e-12, atol=1e-25)
            np.testing.assert_allclose(metrics["source_work_J"], data["per_step_source_work_J"][step],
                                       rtol=1e-12, atol=1e-25)
        samples = np.array(data["samples"])
        self.assertLess(np.max(abs(samples[:, 1] + samples[:, 2] - samples[:, 3])) /
                        samples[-1, 1], 1e-7)


if __name__ == "__main__":
    unittest.main(verbosity=2)
