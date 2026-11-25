"""Check fine-step wiring, incomplete records and explicitly scoped comparisons."""
import copy
import json
from pathlib import Path
import re
import unittest

from verify_fine_steps import ROOT, PRIOR, sha, oracle, comparison


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


class FineStepTests(unittest.TestCase):
    def test_four_predeclared_attempts(self):
        summary = read(ROOT / "summary.json")
        declared = read(ROOT / "declared_configurations.json")
        self.assertEqual(len(declared), 4)
        self.assertEqual(summary["declared_configurations_sha256"],
                         sha(ROOT / "declared_configurations.json"))
        for config, run in zip(declared, summary["runs"]):
            for k in config:
                self.assertEqual(config[k], run[k])

    def test_only_step_changes(self):
        for run in read(ROOT / "summary.json")["runs"]:
            old = (PRIOR / "results" / run["base_id"] / "test.cir").read_text()
            new = (ROOT / "results" / run["id"] / "test.cir").read_text()
            self.assertEqual(re.sub(r"(?m)^tran .*$", "tran 1e-11 1u 0 1e-11", new), old)

    def test_vendor_and_prior_hashes(self):
        summary = read(ROOT / "summary.json")
        for key in ("original_files_sha256", "previous_evidence_sha256"):
            for path, digest in summary[key].items():
                self.assertEqual(sha(Path(path)), digest)

    def test_frozen_limits_and_equations(self):
        for run in read(ROOT / "verification.json")["runs"]:
            if "events" in run:
                self.assertEqual(run["checks"]["dc_amplitude"], max(run["dc_errors_V"]) < .001)
                charge = [x for x in run["input_cap_charge_relative_error"] if x is not None]
                self.assertEqual(run["input_cap_accounting_passed"],
                                 max(run["input_cap_kcl_A"]) < 1e-6 and max(charge) < .01)

    def test_complete_four_edge_comparisons(self):
        for c in read(ROOT / "verification.json")["comparisons"]:
            d = c["delay_differences_s"]
            self.assertEqual(c["passed"], len(d) == 4 and max(d) < 20e-12)
        self.assertFalse(comparison(dict(id="a", status="blocked"),
                                    dict(id="b", status="pass", events=[]))["passed"])

    def test_previous_fine_failures_preserved(self):
        old = read(PRIOR / "summary.json")
        for key in ("input_1000pF_5ps", "input_10000pF_5ps"):
            self.assertEqual(next(r for r in old["runs"] if r["id"] == key)["status"], "blocked")

    def test_blocked_is_not_complete(self):
        for run in read(ROOT / "summary.json")["runs"]:
            if run["status"] == "blocked":
                self.assertFalse((ROOT / "results" / run["id"] / "wave.csv").exists())
                self.assertTrue(run["reason"])
                if "partial_waveform" in run:
                    self.assertFalse(run["partial_waveform"]["accepted"])

    def test_no_whole_system_claim(self):
        for key in ("full_G4_G5_passed", "full_power_passed", "startup_passed", "full_joint_passed"):
            self.assertFalse(read(ROOT / "verification.json")[key])
        summary = read(ROOT / "summary.json")
        self.assertFalse(summary["physical_network_changed"])
        self.assertFalse(summary["tolerances_changed"])

    def test_measurement_tamper_or_no_waveform_rejected(self):
        runs = [r for r in read(ROOT / "summary.json")["runs"] if "rows" in r]
        if runs:
            run = copy.deepcopy(runs[0])
            run["measurements"]["dc_errors_V"][0] += .01
            with self.assertRaises(AssertionError):
                oracle().recalculate(run)
        else:
            self.assertFalse(read(ROOT / "verification.json")["input_100pF_three_step_local_passed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
