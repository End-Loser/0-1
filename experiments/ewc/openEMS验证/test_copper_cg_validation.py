"""CG archive integrity and unchanged failure/acceptance boundaries."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from verify_copper_cg_validation import OUT, audit


class ComparisonTests(unittest.TestCase):
    def test_full_states_and_original_accuracy_failure(self):
        result = audit()
        self.assertEqual(result["full_state_frame_comparisons"], 36)
        self.assertFalse(result["cases"][0]["short_window_frequency_Q_pass"])
        self.assertTrue(result["cases"][1]["short_window_frequency_Q_pass"])
        self.assertFalse(result["full_long_time_passed"])
        self.assertFalse(result["full_joint_simulation_passed"])

    def reject_change(self, change):
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

    def test_relaxed_archive_tolerance_rejected(self):
        self.reject_change(lambda data: data["cases"][0].update(cg_rtol=1e-9))

    def test_wrong_original_archive_rejected(self):
        self.reject_change(lambda data: data["cases"][0].update(LU_sha256="wrong"))

    def test_unearned_long_time_pass_rejected(self):
        self.reject_change(lambda data: data.update(full_long_time_passed=True))


if __name__ == "__main__":
    unittest.main(verbosity=2)
