"""Regression for independent short-window decay, never long-time acceptance."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np

from verify_fine_copper_ringdown import OUT, audit, decay


class FineRingdownTests(unittest.TestCase):
    def sample(self):
        t = np.arange(801) * 20e-12
        frequency, q = 2.9e9, 23000.
        return t, .5 * np.exp(-2 * np.pi * frequency * t / q), np.cos(2 * np.pi * frequency * t)

    def test_analytic_decay_oracle(self):
        frequency, fits = decay(*self.sample())
        self.assertLess(abs(frequency / 2.9e9 - 1), 1e-4)
        for row in fits:
            self.assertLess(abs(row["Q_decay"] / 23000 - 1), 1e-4)

    def test_growth_rejected(self):
        t, u, probe = self.sample()
        with self.assertRaises(AssertionError):
            decay(t, 1 / u, probe)

    def test_nonpositive_energy_rejected(self):
        t, u, probe = self.sample()
        u[3] = 0
        with self.assertRaises(AssertionError):
            decay(t, u, probe)

    def test_no_oscillating_probe_rejected(self):
        t, u, _ = self.sample()
        with self.assertRaises(AssertionError):
            decay(t, u, np.ones(len(t)))

    def test_actual_equations_not_long_time_acceptance(self):
        result = audit()
        self.assertEqual(result["snapshots_checked"], 18)
        self.assertFalse(result["fine_grid_long_time_passed"])
        self.assertFalse(result["manufactured_cavity_passed"])
        self.assertFalse(result["full_joint_simulation_passed"])

    def test_preserve_5ps_accuracy_failure(self):
        rows = audit()["cases"]
        self.assertEqual([row["dt_s"] for row in rows], [5e-12, 2e-12])
        self.assertGreater(rows[0]["Q_vs_continuum_error"], .01)
        self.assertFalse(rows[0]["short_window_frequency_Q_pass"])
        self.assertLess(rows[1]["Q_vs_continuum_error"], .01)
        self.assertTrue(rows[1]["short_window_frequency_Q_pass"])

    def test_unearned_long_time_upgrade_rejected(self):
        original = Path.read_text

        def altered(path, *args, **kwargs):
            text = original(path, *args, **kwargs)
            if path == OUT / "summary.json":
                record = json.loads(text)
                record["fine_grid_long_time_passed"] = True
                return json.dumps(record)
            return text
        with patch.object(Path, "read_text", altered):
            with self.assertRaises(AssertionError):
                audit()


if __name__ == "__main__":
    unittest.main(verbosity=2)
