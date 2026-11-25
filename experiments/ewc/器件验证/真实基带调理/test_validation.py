"""Evidence tests, including retained failures; not 18 device pass claims."""
import hashlib
import json
from pathlib import Path
import unittest

from verify_offline import audit, crossings, derivative

ROOT = Path(__file__).resolve().parent


class EvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.summary = json.loads((ROOT/"summary.json").read_text(encoding="utf-8"))
        cls.verified = audit()
        cls.runs = {r["id"]: r for r in cls.verified["runs"]}

    def test_01_bounded_sources(self):
        sources = json.loads((ROOT/"sources.json").read_text())
        self.assertEqual(len(sources["sources"]), 3)
        self.assertTrue(all(s["url"].startswith("https://www.ti.com/") for s in sources["sources"]))
        self.assertTrue(all(s.get("status") == 200 and len(s["sha256"]) == 64 for s in sources["sources"]))

    def test_02_model_readable(self):
        inspection = json.loads((ROOT/"model_inspection.json").read_text())
        self.assertEqual(inspection["candidate"], "THS4551")
        fda = next(m for m in inspection["readable_models"] if m["path"].endswith("ths4551.lib"))
        self.assertFalse(fda["encrypted_marker"])
        self.assertTrue(fda["subcircuits"][0].endswith("IN+ IN- OUT+ OUT- VOCM VCC VEE"))
        self.assertNotIn("C_C1", fda["header"])

    def test_03_actual_counts(self):
        self.assertEqual(self.summary["counts"], dict(configurations=8, solver_attempts=8,
                                                    datasets=8, **{"pass": 1, "fail": 7, "blocked": 0}))

    def test_04_unchanged_evidence(self):
        self.assertTrue(self.summary["original_hashes_unchanged"])
        self.assertTrue(self.verified["previous_evidence_unchanged"])
        self.assertGreater(self.verified["previous_evidence_files"], 50)

    def test_05_scope_labels(self):
        self.assertTrue(self.summary["synthetic_baseband"])
        self.assertTrue(self.summary["ideal_references"])
        self.assertFalse(self.summary["full_chain_accepted"])
        self.assertEqual(self.summary["RF_models"], 0)

    def test_06_physical_network(self):
        for r in self.summary["runs"]:
            deck = (ROOT/"results"/r["id"]/"test.cir").read_text()
            self.assertIn("RGP dp ip 950", deck)
            self.assertIn("RFP ip om 1000", deck)
            self.assertIn("ROP op cp 22", deck)
            self.assertIn("XF ip im op om cm vs 0 THS4551", deck)
            self.assertNotIn("BP inp", deck)
            self.assertEqual(deck.count(".include"), 2)
            self.assertNotIn(".subckt", deck.lower())

    def test_07_independent_measurements(self):
        self.assertEqual(self.verified["reproduced_runs"], 8)
        for r in self.summary["runs"]:
            self.assertEqual(self.runs[r["id"]]["independently_reproduced_status"], r["status"])

    def test_08_loaded_dc_gain(self):
        self.assertAlmostEqual(500/1044, .4789272030651341, places=14)
        self.assertLess(max(self.runs["d05_fda_100pF"]["dc_errors_V"]), .001)

    def test_09_common_mode(self):
        self.assertTrue(all(max(r["cm_errors_V"]) < .005 for r in self.runs.values()))

    def test_10_pin_and_current_range(self):
        for r in self.runs.values():
            for key in ["fda_pin_range", "fda_output_range", "comparator_pin_range",
                        "output_continuous_current"]:
                self.assertTrue(r["checks"][key])

    def test_11_light_load_failure_retained(self):
        r = self.runs["d01_nominal"]
        self.assertFalse(r["checks"]["dc_amplitude"])
        self.assertGreater(max(r["dc_errors_V"]), .01)
        self.assertTrue(r["checks"]["levels"])

    def test_12_large_failure_retained(self):
        self.assertGreater(max(self.runs["d02_large"]["dc_errors_V"]), .1)

    def test_13_comparator_load_failure_retained(self):
        r = self.runs["d06_comparator_1000pF"]
        self.assertFalse(r["checks"]["delays"])
        self.assertTrue(all(e["total_delay_s"] > 30e-9 for e in r["events"]))

    def test_14_low_overdrive_failure_retained(self):
        r = self.runs["d07_low_overdrive"]
        self.assertFalse(r["checks"]["static_margin"])
        self.assertFalse(r["checks"]["levels"])
        self.assertTrue(any(e["total_delay_s"] is None for e in r["events"]))

    def test_15_refinement_not_physical_pass(self):
        self.assertTrue(self.summary["refinement"]["passed"])
        self.assertFalse(self.runs["d08_refined"]["checks"]["dc_amplitude"])

    def test_16_rc_kcl(self):
        # Finite-difference reconstruction tolerance, not a hardware error budget.
        self.assertTrue(all(r["max_rc_kcl_residual_A"] < 1e-6 for r in self.runs.values()))
        self.assertLess(self.runs["d05_fda_100pF"]["max_rc_kcl_residual_A"], 1e-8)

    def test_17_crossing_direction_and_multiplicity(self):
        rows = [(0., -1.), (1., 1.), (2., -1.), (3., 1.)]
        self.assertEqual(crossings(rows, 1, 0, True, 0, 3), [.5, 2.5])
        self.assertEqual(crossings(rows, 1, 0, False, 0, 3), [1.5])

    def test_18_nonuniform_derivative(self):
        rows = [(0.,), (1.,), (3.,)]
        self.assertAlmostEqual(derivative(rows, [0, 1, 9], 1), 2)


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(EvidenceTests))
    output = dict(tests_run=result.testsRun, failures=len(result.failures),
                  errors=len(result.errors), successful=result.wasSuccessful(),
                  purpose="evidence_consistency_tests_not_device_acceptance",
                  solver_attempts=0, script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (ROOT/"tests.json").write_text(json.dumps(output, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    raise SystemExit(0 if result.wasSuccessful() else 1)
