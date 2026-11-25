"""Mutate only in-memory copies of real solver vectors; retain all originals."""
import contextlib
import json
import unittest
from unittest.mock import patch

import numpy as np

import verify_c_output_feedback as verifier


class FeedbackEvidence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.summary = json.loads((verifier.OUT / "summary.json").read_bytes())
        cls.case_record = cls.summary["runs"][0]
        cls.path = verifier.OUT / cls.case_record["id"] / "full_vectors.npz"
        cls.original_sha = verifier.sha(cls.path)
        with np.load(cls.path, allow_pickle=False) as ar:
            cls.data = ar["data"].copy()
            cls.columns = ar["columns"].copy()
        cls.col = {name: i for i, name in enumerate(cls.columns.tolist())}

    def mutated_inspection(self, data, run=None):
        archive = {"data": data, "columns": self.columns}
        with patch.object(verifier.np, "load",
                          return_value=contextlib.nullcontext(archive)):
            return verifier.inspect_case(run or self.case_record)

    def tearDown(self):
        self.assertEqual(verifier.sha(self.path), self.original_sha)

    def test_actual_full_case_and_original_failures(self):
        cases = [verifier.inspect_case(run) for run in self.summary["runs"]]
        self.assertEqual([c["status"] for c in cases],
                         ["local_pass", "incomplete", "missing_waveform"])
        self.assertFalse(cases[0]["G4_closed"])
        self.assertFalse(cases[0]["G6_closed"])
        self.assertGreater(cases[0]["feedback_resistor_heat_J"], 0)
        self.assertTrue(cases[0]["unclosed_accounting"])

    def test_actual_C_current_sign_flip_rejected(self):
        d = self.data.copy()
        d[:, self.col["i_c_feedback"]] *= -1
        with self.assertRaises(AssertionError):
            self.mutated_inspection(d)

    def test_actual_C_current_omission_rejected(self):
        d = self.data.copy()
        d[:, self.col["i_c_feedback"]] = 0
        with self.assertRaises(AssertionError):
            self.mutated_inspection(d)

    def test_synthetic_raw_answer_replacement_rejected(self):
        d = self.data.copy()
        d[:, self.col["raw"]] += .002
        with self.assertRaises(AssertionError):
            self.mutated_inspection(d)

    def test_input_source_current_omission_rejected(self):
        d = self.data.copy()
        d[:, self.col["i_a"]] = 0
        with self.assertRaises(AssertionError):
            self.mutated_inspection(d)

    def test_failed_amplitude_not_approved_by_logic(self):
        d = self.data.copy()
        d[:, self.col["cp2"]] += .002
        result = self.mutated_inspection(d)
        self.assertTrue(result["checks"]["logic"])
        self.assertFalse(result["checks"]["amplitude"])
        self.assertFalse(result["local_passed"])

    def test_missing_input_charge_is_not_approved(self):
        d = self.data.copy()
        d[:, self.col["i_bin4"]] += 5e-6
        result = self.mutated_inspection(d)
        self.assertFalse(result["checks"]["observed_capacitor_accounting"])
        self.assertFalse(result["local_passed"])

    def test_return_zero_cannot_approve_short_window(self):
        record = self.summary["runs"][1]
        self.assertEqual(record["returncode"], 0)
        self.assertLess(record["observed_last_time_s"], record["duration_s"])
        result = verifier.inspect_case(record)
        self.assertEqual(result["status"], "incomplete")
        self.assertFalse(result["local_passed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
