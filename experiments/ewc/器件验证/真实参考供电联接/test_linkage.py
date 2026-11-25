"""Regression tests protect scope, wiring, failed trials and measurement integrity."""
import json
from pathlib import Path
import unittest

import verify_linked_reference as oracle
from verify_scoped_linkage import normalize

ROOT = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


class LinkageTests(unittest.TestCase):
    def test_seven_cases_in_each_batch(self):
        for folder in (ROOT, ROOT / "scoped", ROOT / "gear"):
            self.assertEqual(len(read(folder / "summary.json")["runs"]), 7)

    def test_initial_incomplete_cases_preserved(self):
        runs = read(ROOT / "summary.json")["runs"]
        self.assertEqual(sum(r["status"] == "blocked" for r in runs), 5)
        self.assertTrue(all("rows" not in r for r in runs if r["status"] == "blocked"))

    def test_passive_failure_retained(self):
        for folder in (ROOT, ROOT / "scoped", ROOT / "gear"):
            run = next(r for r in read(folder / "summary.json")["runs"]
                       if r["id"] == "linked_passive_bias")
            self.assertEqual(run["status"], "fail")
            self.assertFalse(run["measurements"]["checks"]["dc_amplitude"])

    def test_scoping_keeps_external_circuit(self):
        for run in read(ROOT / "summary.json")["runs"]:
            before = (ROOT / "results" / run["id"] / "test.cir").read_text()
            after = (ROOT / "scoped/results" / run["id"] / "test.cir").read_text()
            self.assertEqual(normalize(before), normalize(after))

    def test_gear_changes_only_integrator(self):
        for run in read(ROOT / "gear/summary.json")["runs"]:
            before = (ROOT / "scoped/results" / run["id"] / "test.cir").read_text()
            after = (ROOT / "gear/results" / run["id"] / "test.cir").read_text()
            self.assertEqual(after.count(".options method=gear maxord=2"), 1)
            self.assertEqual(normalize(before), normalize(after.replace(".options method=gear maxord=2\n", "")))

    def test_original_library_bytes(self):
        for folder in (ROOT, ROOT / "scoped", ROOT / "gear"):
            for path, digest in read(folder / "summary.json")["original_files_sha256"].items():
                self.assertEqual(oracle.sha(Path(path)), digest)

    def test_q1_variant_not_substituted(self):
        matching = read(ROOT / "matching_datasheet.json")
        self.assertFalse(matching["model_to_commercial_substitution_accepted"])
        self.assertIn("opa320-q1", matching["source"]["url"])

    def test_passive_threshold_sign_reversal(self):
        value = next(r for r in read(ROOT / "verification.json")["runs"]
                     if r["id"] == "linked_passive_bias")
        self.assertLess(value["bias_threshold_range_V"][1], 0)

    def test_real_bias_load_not_zero(self):
        value = next(r for r in read(ROOT / "verification.json")["runs"]
                     if r["id"] == "linked_ideal_control")
        self.assertGreater(min(value["max_reference_sink_currents_A"]), .007)

    def test_no_joint_or_startup_claim(self):
        for folder in (ROOT, ROOT / "scoped", ROOT / "gear"):
            value = read(folder / "verification.json")
            self.assertFalse(value["full_joint_passed"])
            self.assertFalse(value["safe_startup_passed"])
            self.assertFalse(value["full_G4_G5_passed"])

    def test_changed_measurement_rejected(self):
        run = read(ROOT / "summary.json")["runs"][0]
        run["measurements"]["dc_errors_V"][0] += .001
        with self.assertRaises(AssertionError):
            oracle.recalculate(run)

    def test_wrong_row_count_rejected(self):
        run = read(ROOT / "summary.json")["runs"][0]
        run["rows"] += 1
        with self.assertRaises(AssertionError):
            oracle.read_rows(run)


if __name__ == "__main__":
    unittest.main(verbosity=2)
