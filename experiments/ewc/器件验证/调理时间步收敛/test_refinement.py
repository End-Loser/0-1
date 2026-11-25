"""Regression and rejection tests for the preserved timestep evidence."""
import json
import re
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from run_refinement import ORIGINAL, ROOT, load
import verify_refinement


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


class RefinementTests(unittest.TestCase):
    def test_fixed_circuit_except_timestep(self):
        old = (ORIGINAL / "results/d05_fda_100pF/test.cir").read_text(encoding="ascii")
        for directory in [ROOT / "results/d05_50ps", ROOT / "results/d05_25ps",
                          ROOT / "results/d05_10ps", ROOT / "fine_check/results/d05_5ps"]:
            new = (directory / "test.cir").read_text(encoding="ascii")
            normalize = lambda s: re.sub(r"(?m)^tran .*?$", "tran FIXED_WINDOW_STEP", s)
            self.assertEqual(normalize(old), normalize(new))

    def test_coarse_failure_retained(self):
        value = read(ROOT / "verification.json")
        self.assertFalse(value["comparisons"][1]["passed"])
        self.assertGreater(max(value["comparisons"][1]["delay_differences_s"]), 20e-12)

    def test_twentyfive_ten_pass(self):
        self.assertTrue(read(ROOT / "verification.json")["comparisons"][2]["passed"])

    def test_ten_five_pass(self):
        self.assertTrue(read(ROOT / "fine_check/verification.json")["ten_vs_five_ps_passed"])

    def test_no_system_claim(self):
        for file in [ROOT / "verification.json", ROOT / "fine_check/verification.json"]:
            self.assertFalse(read(file)["whole_chain_passed"])

    def test_four_edges_not_average(self):
        fine = read(ROOT / "fine_check/verification.json")
        self.assertEqual(len(fine["delay_differences_s"]), 4)
        self.assertLess(max(fine["delay_differences_s"]), 20e-12)

    def test_changed_original_hash_rejected(self):
        summary = read(ROOT / "summary.json")
        name = next(iter(summary["original_evidence_sha256"]))
        summary["original_evidence_sha256"][name] = "0" * 64
        with tempfile.TemporaryDirectory(prefix="ewc-refinement-test-") as temp:
            destination = Path(temp)
            (destination / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
            with patch.object(verify_refinement, "ROOT", destination):
                with self.assertRaises(AssertionError):
                    verify_refinement.audit()

    def test_changed_measurement_rejected(self):
        oracle = load("test_independent_oracle", ORIGINAL / "verify_offline.py")
        oracle.ROOT = ROOT
        run = read(ROOT / "summary.json")["runs"][0]
        run["measurements"]["dc_errors_V"][0] += .001
        with self.assertRaises(AssertionError):
            oracle.recalculate(run)


if __name__ == "__main__":
    unittest.main(verbosity=2)
