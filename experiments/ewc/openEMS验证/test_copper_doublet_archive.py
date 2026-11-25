"""Reject in-memory misstatements without altering the archived physical failures."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np

from verify_copper_doublet import OUT, audit


class ArchiveTests(unittest.TestCase):
    def altered_json(self, path, change):
        original = Path.read_text

        def read(target, *args, **kwargs):
            text = original(target, *args, **kwargs)
            if target == path:
                data = json.loads(text)
                change(data)
                return json.dumps(data)
            return text
        with patch.object(Path, "read_text", read):
            with self.assertRaises(AssertionError):
                audit()

    def test_full_archive_equations_and_scope(self):
        result = audit()
        self.assertEqual(result["full_fields_checked"], 42)
        self.assertTrue(result["finest_doublet_frequency_model_passed"])
        self.assertFalse(result["cases"][0]["selected_doublet_frequency_Q_passed"])
        self.assertGreater(min(result["cases"][0]["Q_relative_error"]), .01)
        for key in ("doublet_long_transient_passed", "arbitrary_modes_passed", "general_boundaries_passed",
                    "manufactured_cavity_passed", "full_joint_simulation_passed"):
            self.assertFalse(result[key])
        self.assertEqual(result["full_system_gates_closed"], 0)

    def test_wrong_mode_rejected(self):
        self.altered_json(OUT / "configurations.json", lambda data: data[0].update(mode=[0, 1, 1]))

    def test_coarse_accuracy_failure_cannot_be_promoted(self):
        self.altered_json(OUT / "summary.json",
                          lambda data: data["cases"][0].update(selected_doublet_frequency_Q_passed=True))

    def test_joint_pass_without_evidence_rejected(self):
        self.altered_json(OUT / "summary.json", lambda data: data.update(full_joint_simulation_passed=True))

    def test_false_pole_eigenvector_rejected(self):
        path = OUT / "111_24x15x9_peaks.npz"
        original = np.load
        with original(path) as archive:
            values = {name: archive[name] for name in archive.files}
        values["fitted_vectors"] = np.eye(2, dtype=complex)

        class Snapshot:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def __getitem__(self, key):
                return values[key]

        def load(target, *args, **kwargs):
            return Snapshot() if target == path else original(target, *args, **kwargs)

        with patch.object(np, "load", load):
            with self.assertRaises(AssertionError):
                audit()


if __name__ == "__main__":
    unittest.main(verbosity=2)
