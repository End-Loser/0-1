"""Regression tests for public-source identity, scope and preserved failures."""
import json
from pathlib import Path
import unittest

from verify_supply_screen import ROOT, RESULT, pwl, recalculate


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


class PublicModelsTests(unittest.TestCase):
    def test_original_download_count(self):
        self.assertEqual(read(ROOT / "source_verification.json")["original_downloads_checked"], 9)

    def test_adc_mismatch_not_accepted(self):
        self.assertFalse(read(ROOT / "source_verification.json")["ads8881_to_ads8866_substitution_accepted"])

    def test_symbols_not_spice(self):
        hits = read(ROOT / "open_hits.json")["hits"]
        self.assertEqual(len(hits), 2)
        self.assertTrue(all(r["kicad_symbol_library"] and not r["spice_subcircuits"] for r in hits))

    def test_initial_failures_retained(self):
        runs = read(ROOT / "supply_screen.json")["runs"]
        self.assertEqual(len(runs), 7)
        self.assertTrue(all(r["status"] == "blocked" for r in runs))

    def test_reference_and_regulator_counts(self):
        runs = read(RESULT / "supply_screen.json")["runs"]
        self.assertEqual(sum(r["status"] == "pass" for r in runs), 3)
        self.assertEqual(sum(r["status"] == "fail" for r in runs), 4)

    def test_headroom_failure_preserved(self):
        runs = read(RESULT / "supply_screen.json")["runs"]
        for identity in ("ref_5V_headroom", "ldo_3V3_headroom"):
            self.assertFalse(next(r for r in runs if r["id"] == identity)["checks"]["input_headroom"])

    def test_discharge_requirement_failure(self):
        runs = read(ROOT / "supply_verification.json")["runs"]
        ldos = [r for r in runs if r["id"].startswith("ldo")]
        self.assertTrue(all(not r["checks"]["enable_discharge"] for r in ldos))

    def test_integrated_capacitor_accounting(self):
        runs = read(ROOT / "supply_verification.json")["runs"]
        self.assertTrue(all(r["capacitor_integral_relative_error"] < .01 for r in runs))

    def test_no_full_power_or_temperature_claim(self):
        summary = read(RESULT / "supply_screen.json")
        self.assertFalse(summary["whole_power_chain_passed"])
        self.assertFalse(summary["regulator_quiescent_current_modeled"])
        self.assertFalse(summary["full_temperature_accuracy_passed"])

    def test_interpolation(self):
        points = [(0, 0), (1, 2), (3, -2)]
        self.assertEqual([pwl(t, points) for t in (0, .5, 1, 2, 3, 4)], [0, 1, 2, 0, -2, -2])

    def test_changed_screen_measurement_rejected(self):
        run = read(RESULT / "supply_screen.json")["runs"][0]
        run["output_error_V"] += .01
        with self.assertRaises(AssertionError):
            recalculate(run)


if __name__ == "__main__":
    unittest.main(verbosity=2)
