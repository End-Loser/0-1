"""Independent source waveform and strict physical acceptance boundaries."""
import unittest
from unittest.mock import patch

import numpy as np

from run_copper_driven_history import coefficients, PULSES
from verify_copper_driven_history import source_coefficients, short_physical_pass, pair_pass, audit


class VerifierTests(unittest.TestCase):
    def test_independent_cosine_envelope_matches_full_declared_history(self):
        frequencies = np.array([2.9e9, 3.060381342e9, 3.060381342e9])
        for dt in (5e-12, 2e-12):
            times = (np.arange(round(16e-9 / dt)) + .5) * dt
            expected = np.array([source_coefficients(t, frequencies, PULSES) for t in times])
            actual = np.array([coefficients(t, frequencies, PULSES) for t in times])
            self.assertLess(np.linalg.norm(actual - expected) / np.linalg.norm(expected), 1e-13)
            np.testing.assert_array_equal(actual[times >= 12e-9], np.zeros((sum(times >= 12e-9), 3)))

    def test_one_percent_boundary_and_nonfinite_never_pass(self):
        self.assertTrue(short_physical_pass(.009, .002, .003))
        for bad in (.01, .02, -1., float("nan"), float("inf")):
            self.assertFalse(short_physical_pass(bad, .002, .003))
            self.assertFalse(short_physical_pass(.002, bad, .003))
            self.assertFalse(short_physical_pass(.002, .003, bad))
            self.assertFalse(pair_pass(bad))
        self.assertTrue(pair_pass(.009))

    def test_running_or_failed_batch_cannot_enter_complete_audit(self):
        for status in ("running", "failed_preserve_partial"):
            with patch("verify_copper_driven_history.read_json",
                       return_value=({"status": status}, "irrelevant")):
                with self.assertRaises(AssertionError):
                    audit()


if __name__ == "__main__":
    unittest.main(verbosity=2)
