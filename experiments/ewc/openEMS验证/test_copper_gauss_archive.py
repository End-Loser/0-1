"""Read-only independent candidate audit and in-memory corruption regressions."""
import unittest
from unittest.mock import patch

import numpy as np

import verify_copper_gauss_validation as verifier


class ArchiveTests(unittest.TestCase):
    def test_actual_archive_preserves_fast_state_failure_and_partial_boundary(self):
        result = verifier.audit(partial=True)
        self.assertTrue(result["independent_discrete_checks_passed"])
        self.assertTrue(result["preflight"]["unresolved_initial_state_failed"])
        self.assertFalse(result["general_initial_state_approved"])
        self.assertFalse(result["all_nine_physical_cases_present"])
        self.assertFalse(result["all_six_short_pairs_passed"])
        self.assertEqual(result["full_system_gates_closed"], 0)

    def test_running_batch_or_general_claim_cannot_enter_full_acceptance(self):
        with patch.object(verifier, "read_json", return_value=({"status": "running"}, "x")):
            with self.assertRaises(AssertionError):
                verifier.audit()
        original = verifier.read_json

        def changed(path):
            result, digest = original(path)
            if path == verifier.OUT / "summary.json":
                result["general_initial_state_approved"] = True
            return result, digest

        with patch.object(verifier, "read_json", side_effect=changed):
            with self.assertRaises(AssertionError):
                verifier.audit(partial=True)

    def test_raw_source_and_memory_tampering_rejected_without_file_edits(self):
        original = np.load
        for target in ("stage_i", "snapshot_source_currents"):
            class Tampered:
                def __init__(self, archive):
                    self.archive, self.files = archive, archive.files

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    self.archive.close()

                def __getitem__(self, key):
                    value = self.archive[key]
                    if key == target:
                        value = value.copy()
                        value.flat[0] += .01
                    return value

            with patch.object(np, "load", side_effect=lambda *a, **kw: Tampered(original(*a, **kw))):
                with self.assertRaises(AssertionError):
                    verifier.audit(partial=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
