"""Orientation/reference regression; software checks are not system acceptance."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np

from finite_copper_cavity import CopperCavity, continuum_q
from verify_finite_copper_cavity import quadrature_q
from verify_copper_orientation import OUT, audit, pole_fit
from verify_finite_pec_cavity import layouts, split


class OrientationTests(unittest.TestCase):
    def test_120_source_is_z_electric(self):
        grid = CopperCavity((8, 5, 3), (.16, .10, .06))
        seed = grid.pec.lift(grid.pec.sample_mode((1, 2, 0)))
        fields = split(seed, layouts(grid.shape)[0])
        self.assertTrue(np.all(fields[0] == 0))
        self.assertTrue(np.all(fields[1] == 0))
        self.assertGreater(np.linalg.norm(fields[2]), 0)

    def test_reference_in_previously_verified_copper_band(self):
        frequency, q = continuum_q(np.array([1, 2, 0]), np.array([.16, .10, .06]), 58.58e6)
        self.assertGreater(frequency, 2.5e9)
        self.assertLess(frequency, 3.5e9)
        self.assertGreater(q, 0)

    def test_120_reference_matches_independent_integrals(self):
        mode, lengths = np.array([1, 2, 0]), np.array([.16, .10, .06])
        np.testing.assert_allclose(continuum_q(mode, lengths, 58.58e6),
                                   quadrature_q(mode, lengths, 58.58e6), rtol=1e-12)

    def test_pole_oracle(self):
        frequencies = 3.1e9 + np.linspace(-1e5, 1e5, 5)
        pole = 3.1e9 + 2.5e4j
        observed, q = pole_fit(frequencies, (1 + 2j) / (frequencies - pole))
        self.assertLess(abs(observed - pole), 1e-5)
        self.assertAlmostEqual(q, 62000, places=6)

    def test_growing_pole_rejected(self):
        frequencies = 3.1e9 + np.linspace(-1e5, 1e5, 5)
        with self.assertRaises(AssertionError):
            pole_fit(frequencies, 1 / (frequencies - (3.1e9 - 2.5e4j)))

    def test_archive_equations_and_open_gates(self):
        result = audit()
        self.assertEqual(result["field_frequency_points_checked"], 12)
        self.assertFalse(result["fine_grid_long_transient_passed"])
        self.assertFalse(result["arbitrary_modes_passed"])
        self.assertFalse(result["manufactured_cavity_passed"])
        self.assertFalse(result["full_joint_simulation_passed"])

    def reject_summary_change(self, change):
        original = Path.read_text

        def altered(path, *args, **kwargs):
            text = original(path, *args, **kwargs)
            if path == OUT / "summary.json":
                record = json.loads(text)
                change(record)
                return json.dumps(record)
            return text
        with patch.object(Path, "read_text", altered):
            with self.assertRaises(AssertionError):
                audit()

    def test_wrong_orientation_rejected(self):
        self.reject_summary_change(lambda data: data["cases"][0].update(mode=[0, 1, 1]))

    def test_unearned_joint_acceptance_rejected(self):
        self.reject_summary_change(lambda data: data.update(full_joint_simulation_passed=True))


if __name__ == "__main__":
    unittest.main(verbosity=2)
