"""Protect vendor libraries, physical wiring, fixed gates and capacitor accounting."""
import json
from pathlib import Path
import re
import unittest

from verify_bias_decoupling import ROOT, LINKED, sha, recalculate
import sys
sys.path.insert(0, str(LINKED))
from verify_scoped_linkage import normalize


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def contract_instrumentation(net):
    net = re.sub(r"(?m)^VBOUT\d .*\n", "", net)
    for i, node in enumerate(("cm", "refp", "refn"), 1):
        net = net.replace("0 bout" + str(i) + " EWC_BUFFER", "0 " + node + " EWC_BUFFER")
        net = re.sub(r"(?m)^CB" + str(i) + r" " + node + r" 0 .*$", f"CB{i} {node} 0 10p", net)
    net = net.replace("VCMLOAD cm cmload 0\n", "").replace("op om cmload vs", "op om cm vs")
    net = re.sub(r"(?m)^wrdata .*\n", "", net)
    net = re.sub(r"(?m)^\.nodeset .*\n", "", net)
    return normalize(net)


class DecouplingTests(unittest.TestCase):
    def test_seven_cases(self):
        self.assertEqual(len(read(ROOT / "summary.json")["runs"]), 7)

    def test_unedited_libraries(self):
        for path, digest in read(ROOT / "summary.json")["original_files_sha256"].items():
            self.assertEqual(sha(Path(path)), digest)

    def test_documented_capacitance_range(self):
        for run in read(ROOT / "summary.json")["runs"]:
            self.assertLessEqual(run["bias_cap_F"], 1e-9)
            self.assertGreater(run["bias_cap_F"], 0)

    def test_only_bias_caps_and_probes_change(self):
        for run in read(ROOT / "summary.json")["runs"]:
            old_id = ("linked_real_rails" if run["real_rails"] else
                      "linked_buffered_bias")
            before = (LINKED / "gear/results" / old_id / "test.cir").read_text()
            after = (ROOT / "results" / run["id"] / "test.cir").read_text()
            for i, ratio in enumerate((.33, .32, .34), 1):
                sign = -1 if i == 2 else 1
                top, bottom = 100000*(1-ratio), 100000*ratio
                before = before.replace(
                    f"RDT{i} vref b{i} {top:.15g}",
                    f"RDT{i} vref b{i} {top*(1+sign*run['perturbation']):.15g}")
                before = before.replace(
                    f"RDB{i} b{i} 0 {bottom:.15g}",
                    f"RDB{i} b{i} 0 {bottom*(1-sign*run['perturbation']):.15g}")
            if run["step_s"] == 5e-12:
                after = after.replace("tran 5e-12", "tran 1e-11").replace("0 5e-12", "0 1e-11")
            self.assertEqual(contract_instrumentation(before), contract_instrumentation(after))

    def test_old_amplitude_failure_preserved(self):
        old = read(LINKED / "gear/summary.json")["runs"]
        run = next(r for r in old if r["id"] == "linked_buffered_bias")
        self.assertFalse(run["measurements"]["checks"]["dc_amplitude"])

    def test_fixed_threshold_and_load(self):
        for run in read(ROOT / "summary.json")["runs"]:
            self.assertEqual(run["threshold_V"], .1)
            self.assertEqual(run["overdrive_V"], .08)
            self.assertEqual(run["fda_load_F"], 100e-12)

    def test_capacitor_equations(self):
        runs = read(ROOT / "verification.json")["runs"]
        expected_passes = {"bias_100pF", "bias_470pF"}
        for run in runs:
            if "bias_cap_kcl_A" in run:
                passed = (max(run["bias_cap_kcl_A"]) < 1e-6
                          and max(run["bias_cap_charge_relative_error"]) < .01)
                self.assertEqual(run["capacitor_accounting_passed"], passed)
                self.assertEqual(passed, run["id"] in expected_passes)

    def test_no_whole_system_claim(self):
        verification = read(ROOT / "verification.json")
        for key in ("full_G4_G5_passed", "full_power_passed", "startup_passed", "full_joint_passed"):
            self.assertFalse(verification[key])

    def test_measurement_tamper_rejected(self):
        run = next(r for r in read(ROOT / "summary.json")["runs"] if "rows" in r)
        run["measurements"]["dc_errors_V"][0] += .01
        with self.assertRaises(AssertionError):
            recalculate(run)


if __name__ == "__main__":
    unittest.main(verbosity=2)
