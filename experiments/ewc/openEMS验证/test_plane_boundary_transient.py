"""Offline tests of the standalone TEM integrator, not openEMS acceptance."""

import unittest
import numpy as np

from run_plane_boundary_transient import simulate, source, dft, Z0
from verify_plane_boundary_transient import ladder_reflection


class PlaneTransientTests(unittest.TestCase):
    def test_exact_source_cutoff(self):
        self.assertTrue(np.all(source(np.array([2.4e-9, 3e-9, 10e-9])) == 0))
        self.assertNotEqual(source(np.array([1.2e-9]))[0], 0)

    def test_positive_rl_energy(self):
        data = simulate(20, 5e-12, 4e-9, [1., 2.], [1e-9, 2e-9])
        self.assertTrue(np.all(data["energy_J"] >= 0))
        self.assertTrue(np.all(data["copper_power_W"] >= 0))
        self.assertLess(data["metrics"]["relative_accumulated_energy_defect"], 1e-10)
        self.assertEqual(data["metrics"]["source_off_max_relative_energy_increase"], 0)

    def test_rl_midpoint_voltage_relation(self):
        data = simulate(20, 5e-12, 4e-9, [1., 2.], [1e-9, 2e-9])
        states = data["RL_states_scaled"]
        mid = (states[1:] + states[:-1]) / (2 * Z0)
        voltage = np.sum(data["R_ohm"] * (data["wall_I_A"][:, None] - mid), axis=1)
        np.testing.assert_allclose(voltage, data["wall_V"], rtol=1e-9, atol=1e-14)

    def test_pec_zero_wall_work(self):
        data = simulate(20, 5e-12, 4e-9, [], [], "pec")
        self.assertTrue(np.all(data["wall_V"] == 0))
        self.assertEqual(data["metrics"]["terminal_work_J"], 0)
        self.assertEqual(data["metrics"]["copper_resistor_loss_J"], 0)
        self.assertLess(data["metrics"]["relative_accumulated_energy_defect"], 1e-10)

    def test_matched_positive_loss(self):
        data = simulate(20, 5e-12, 4e-9, [], [], "matched")
        np.testing.assert_allclose(data["wall_V"], Z0 * data["wall_I_A"], rtol=1e-12, atol=1e-18)
        self.assertGreater(data["metrics"]["terminal_work_J"], 0)
        self.assertLess(data["metrics"]["relative_accumulated_energy_defect"], 1e-10)

    def test_discrete_reflection_converges(self):
        f = np.array([3e9])
        expected = -np.exp(-4j * np.pi * f * .12 / 299792458.)
        coarse = ladder_reflection(f, 5e-12, .001, 120, np.array([0j]))
        fine = ladder_reflection(f, 1e-12, .0005, 240, np.array([0j]))
        self.assertLess(abs(fine - expected)[0], .01)
        self.assertGreater(abs(coarse - expected)[0], .01)

    def test_midpoint_spectrum(self):
        t = (np.arange(1000) + .5) / 1000
        signal = np.cos(2 * np.pi * 7 * t)
        np.testing.assert_allclose(dft(t, signal, np.array([7.])), [.5 + 0j], atol=1e-13)

    def test_invalid_arguments(self):
        for dt in (0, -1, np.nan):
            with self.assertRaises(ValueError):
                simulate(20, dt, 4e-9, [1.], [1e-9])
        with self.assertRaises(ValueError):
            simulate(1, 5e-12, 4e-9, [1.], [1e-9])
        with self.assertRaises(ValueError):
            simulate(20, 5e-12, 4e-9, [-1.], [1e-9])
        with self.assertRaises(ValueError):
            simulate(20, 5e-12, 4e-9, [], [], "unknown")


if __name__ == "__main__":
    unittest.main(verbosity=2)
