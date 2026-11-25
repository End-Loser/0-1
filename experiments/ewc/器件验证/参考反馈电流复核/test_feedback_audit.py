"""Regression checks cover unchanged wiring and truthful per-case gate labels."""
import copy
import json
from pathlib import Path
import re
import unittest

from verify_feedback_audit import ROOT, sha, recalculate

PRIOR = ROOT.parent / "\u53c2\u8003\u8282\u70b9\u53bb\u8026"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def remove_probes(net):
    for i, node in enumerate(("cm", "refp", "refn"), 1):
        net = net.replace(f"VFB{i} {node} fb{i} 0\n", "")
        net = net.replace(f"XB{i} b{i} fb{i} ", f"XB{i} b{i} {node} ")
    return re.sub(r" i\(VFB[123]\)", "", net)


class FeedbackTests(unittest.TestCase):
    def test_seven_cases(self):
        self.assertEqual(len(read(ROOT / "summary.json")["runs"]), 7)

    def test_zero_voltage_probes_only(self):
        for run in read(ROOT / "summary.json")["runs"]:
            old = (PRIOR / "results" / run["id"] / "test.cir").read_text()
            new = (ROOT / "results" / run["id"] / "test.cir").read_text()
            self.assertEqual(remove_probes(new), old)

    def test_old_evidence_and_models_unchanged(self):
        summary = read(ROOT / "summary.json")
        for key in ("previous_evidence_sha256", "original_files_sha256"):
            for path, digest in summary[key].items():
                self.assertEqual(sha(Path(path)), digest)

    def test_fixed_physical_configuration(self):
        old = {r["id"]: r for r in read(PRIOR / "summary.json")["runs"]}
        for run in read(ROOT / "summary.json")["runs"]:
            for key in ("threshold_V", "overdrive_V", "bias_cap_F", "fda_load_F",
                        "step_s", "real_rails", "perturbation"):
                self.assertEqual(run[key], old[run["id"]][key])

    def test_gate_labels_match_original_limits(self):
        for run in read(ROOT / "verification.json")["runs"]:
            if "bias_cap_kcl_A" in run:
                self.assertEqual(run["capacitor_accounting_passed"],
                                 max(run["bias_cap_kcl_A"]) < 1e-6 and
                                 max(run["bias_cap_charge_relative_error"]) < .01)
                self.assertTrue(run["capacitor_accounting_passed"])
                self.assertEqual(run["checks"]["dc_amplitude"], max(run["dc_errors_V"]) < .001)
                self.assertFalse(run["checks"]["dc_amplitude"])
                self.assertTrue(run["reference_diagnostic"]["acceptance_threshold_unchanged"])
                self.assertEqual(run["reference_diagnostic"]["scope"], "not_an_acceptance_check")

    def test_complete_case_amplitude_reproduces_prior(self):
        old = {r["id"]: r for r in read(PRIOR / "verification.json")["runs"]}
        for run in read(ROOT / "verification.json")["runs"]:
            if "events" in run:
                self.assertLess(max(abs(a-b) for a, b in
                                    zip(run["dc_errors_V"], old[run["id"]]["dc_errors_V"])), 1e-8)

    def test_complete_waveforms_and_realrails_block(self):
        runs = read(ROOT / "verification.json")["runs"]
        self.assertEqual(sum("events" in r for r in runs), 5)
        self.assertEqual(next(r for r in runs if r["id"] == "bias_1000pF_realrails")["status"], "blocked")
        fine = next(r for r in runs if r["id"] == "bias_1000pF_5ps")
        self.assertEqual(fine["status"], "blocked")
        self.assertLess(fine["partial_waveform"]["last_time_s"], 1e-6)
        self.assertFalse(fine["partial_waveform"]["accepted"])
        self.assertFalse(read(ROOT / "verification.json")["refinement"]["passed"])

    def test_no_system_claim(self):
        for key in ("full_G4_G5_passed", "full_power_passed", "startup_passed", "full_joint_passed"):
            self.assertFalse(read(ROOT / "verification.json")[key])

    def test_measurement_tamper_rejected(self):
        run = copy.deepcopy(next(r for r in read(ROOT / "summary.json")["runs"] if "rows" in r))
        run["measurements"]["feedback_input_max_A"] += 1e-3
        with self.assertRaises(AssertionError):
            recalculate(run)


if __name__ == "__main__":
    unittest.main(verbosity=2)
