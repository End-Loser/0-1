"""Protect frozen gates and distinguish ideal-rail candidates from system acceptance."""
import copy
import json
from pathlib import Path
import re
import unittest

from verify_input_decoupling import ROOT, PRIOR, sha, recalculate


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def collapse(net, run):
    old_cap = 1e-9 if run["real_rails"] or run["perturbation"] else 10e-12
    for i, node in enumerate(("cm", "refp", "refn"), 1):
        net = re.sub(rf"(?m)^VBIN{i} b{i} bin{i} 0\n", "", net)
        net = re.sub(rf"(?m)^CBIN{i} b{i} 0 .*\n", "", net)
        net = net.replace(f"XB{i} bin{i} ", f"XB{i} b{i} ")
        net = re.sub(rf"(?m)^CB{i} {node} 0 .*$", f"CB{i} {node} 0 {old_cap}", net)
    net = re.sub(r"(?m)^tran .*$", "tran 1e-11 1u 0 1e-11", net)
    net = net.replace(" v(b1) v(b2) v(b3) i(VBIN1) i(VBIN2) i(VBIN3)", "")
    return net


class InputDecouplingTests(unittest.TestCase):
    def test_nine_predeclared_cases(self):
        summary = read(ROOT / "summary.json")
        declared = read(ROOT / "declared_configurations.json")
        self.assertEqual(len(declared), 9)
        self.assertEqual(sha(ROOT / "declared_configurations.json"),
                         summary["declared_configurations_sha256"])
        for config, run in zip(declared, summary["runs"]):
            for key in config:
                self.assertEqual(config[key], run[key])

    def test_physical_changes_only(self):
        for run in read(ROOT / "summary.json")["runs"]:
            old_id = ("bias_1000pF_realrails" if run["real_rails"] else
                      "bias_1000pF_ratio" if run["perturbation"] else "bias_10pF")
            before = (PRIOR / "results" / old_id / "test.cir").read_text()
            after = (ROOT / "results" / run["id"] / "test.cir").read_text()
            self.assertEqual(collapse(after, run), before)

    def test_originals_and_previous_evidence(self):
        summary = read(ROOT / "summary.json")
        for key in ("original_files_sha256", "previous_evidence_sha256"):
            for path, digest in summary[key].items():
                self.assertEqual(sha(Path(path)), digest)

    def test_frozen_source_and_gates(self):
        for run in read(ROOT / "summary.json")["runs"]:
            self.assertEqual(run["threshold_V"], .1)
            self.assertEqual(run["overdrive_V"], .08)
            self.assertEqual(run["fda_load_F"], 100e-12)
            self.assertIn(run["input_cap_F"], (0, 100e-12, 1e-9, 10e-9))
            self.assertEqual(run["slow_startup"], False)

    def test_input_cap_equations_and_labels(self):
        for run in read(ROOT / "verification.json")["runs"]:
            if "input_cap_kcl_A" in run:
                charge = [x for x in run["input_cap_charge_relative_error"] if x is not None]
                expected = max(run["input_cap_kcl_A"]) < 1e-6 and (not charge or max(charge) < .01)
                self.assertEqual(run["input_cap_accounting_passed"], expected)
                self.assertTrue(run["initial_operating_point_not_startup"])
                self.assertEqual(run["checks"]["dc_amplitude"], max(run["dc_errors_V"]) < .001)

    def test_refinement_requires_four_complete_events(self):
        by_id = {r["id"]: r for r in read(ROOT / "verification.json")["runs"]}
        for result in read(ROOT / "verification.json")["refinement"]:
            ids = [f"input_{result['input_cap_pF']}pF{s}" for s in ("", "_5ps")]
            complete = all("events" in by_id[key] and len(by_id[key]["events"]) == 4 for key in ids)
            diffs = result["delay_differences_s"]
            self.assertEqual(result["passed"], complete and len(diffs) == 4 and max(diffs) < 20e-12)

    def test_blocked_runs_have_no_complete_csv(self):
        for run in read(ROOT / "summary.json")["runs"]:
            if run["status"] == "blocked":
                self.assertFalse((ROOT / "results" / run["id"] / "wave.csv").exists())
                self.assertTrue(run["reason"])

    def test_no_whole_system_or_startup_claim(self):
        for key in ("full_G4_G5_passed", "full_power_passed", "startup_passed", "full_joint_passed"):
            self.assertFalse(read(ROOT / "verification.json")[key])
        summary = read(ROOT / "summary.json")
        self.assertTrue(summary["candidate_components_ideal"])
        self.assertTrue(summary["input_capacitors_require_startup_and_tolerance_validation"])

    def test_measurement_tamper_rejected(self):
        run = copy.deepcopy(next(r for r in read(ROOT / "summary.json")["runs"] if "rows" in r))
        run["measurements"]["positive_input_max_A"] += 1e-3
        with self.assertRaises(AssertionError):
            recalculate(run)


if __name__ == "__main__":
    unittest.main(verbosity=2)
