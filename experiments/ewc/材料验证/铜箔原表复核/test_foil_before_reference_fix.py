"""Read-only source/units regressions; no material acceptance is inferred."""
import copy
import json
import unittest

from verify_foil_tables import OUT, audit, check_table


class FoilTables(unittest.TestCase):
    def setUp(self):
        self.table = json.loads((OUT / "table_transcription.json").read_bytes())

    def test_original_evidence(self):
        result = audit()
        self.assertEqual(result["rows_checked"], 5)
        self.assertFalse(result["G2_closed"])
        self.assertFalse(result["loss_multiplier_computed"])
        self.assertFalse(result["foil_sigma_measured"])

    def test_millimeter_conversion_rejected(self):
        self.table["printed_to_si_factor"] = 1e-3
        with self.assertRaises(AssertionError):
            check_table(self.table)

    def test_si_numeric_error_rejected(self):
        self.table["rows"][0]["dielectric_side_Sq_m"] *= 1000
        with self.assertRaises(AssertionError):
            check_table(self.table)

    def test_sides_rejected(self):
        row = self.table["rows"][2]
        for unit in ("um", "m"):
            a, b = f"dielectric_side_Sq_{unit}", f"top_side_Sq_{unit}"
            row[a], row[b] = row[b], row[a]
        with self.assertRaises(AssertionError):
            check_table(self.table)

    def test_missing_or_changed_row_rejected(self):
        for mutation in ("missing", "type", "thickness"):
            table = copy.deepcopy(self.table)
            if mutation == "missing":
                table["rows"].pop()
            elif mutation == "type":
                table["rows"][0]["foil_type"] = "C11000"
            else:
                table["rows"][-1]["thickness_um"] = 8
                table["rows"][-1]["thickness_m"] = 8e-6
            with self.subTest(mutation=mutation):
                with self.assertRaises(AssertionError):
                    check_table(table)

    def test_typical_not_guarantee(self):
        self.table["statistics"] = "Guaranteed batch limits"
        with self.assertRaises(AssertionError):
            check_table(self.table)

    def test_false_acceptance_rejected(self):
        for key in ("resistivity_adopted", "roughness_loss_model_identified",
                    "c11000_parameters_changed", "bom_frozen", "G2_closed"):
            table = copy.deepcopy(self.table)
            table[key] = True
            with self.subTest(key=key):
                with self.assertRaises(AssertionError):
                    check_table(table)

    def test_scale_not_loss_estimate(self):
        result = audit()
        scales = result["dimensional_scales"]
        self.assertGreater(scales[0]["reference_skin_depth_m"],
                           scales[-1]["reference_skin_depth_m"])
        center = scales[1]
        self.assertAlmostEqual(center["reference_skin_depth_m"] * 1e6,
                               1.200577, places=5)
        self.assertGreater(center["rows"][2]["dielectric_Sq_over_reference_delta"], 1)
        self.assertFalse(result["target_band_material_validated"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
