"""Read-only archive regressions; all tampering occurs in memory."""
import copy
import unittest
from unittest.mock import patch

import numpy as np

import verify_copper_driven_history as verifier


class ArchiveTests(unittest.TestCase):
    def test_saved_raw_cases_audit_without_full_upgrade(self):
        result = verifier.audit(partial=True)
        self.assertTrue(result["independent_equation_source_energy_checks_passed"])
        self.assertFalse(result["all_six_declared_cases_present"])
        self.assertFalse(result["all_three_paired_short_histories_passed"])
        self.assertFalse(result["actual_feed_passed"])
        self.assertEqual(result["full_system_gates_closed"], 0)

    def test_runner_joint_claim_rejected_without_editing_summary(self):
        original = verifier.read_json

        def changed(path):
            result, digest = original(path)
            if path.name == "summary.json":
                result = copy.deepcopy(result)
                result["full_joint_simulation_passed"] = True
            return result, digest

        with patch.object(verifier, "read_json", side_effect=changed):
            with self.assertRaises(AssertionError):
                verifier.audit(partial=True)

    def test_source_coefficient_and_energy_tampering_rejected(self):
        original = np.load
        for target in ("snapshot_source_coefficients", "samples"):
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
                        if key == "samples":
                            value[0, 1] += .01
                        else:
                            value[0, 0] += 1.
                    return value

            with patch.object(np, "load", side_effect=lambda *args, **kwargs: Tampered(original(*args, **kwargs))):
                with self.assertRaises(AssertionError):
                    verifier.audit(partial=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
