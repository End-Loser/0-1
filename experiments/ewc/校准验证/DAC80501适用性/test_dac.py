"""Reference-limit and quantization regression tests, not silicon tests."""
from fractions import Fraction
import unittest
from unittest.mock import patch

from evaluate_specs import reference_window, transfer
from verify_dac import audit, read


class DacTests(unittest.TestCase):
    def test_supply_limits(self):
        for supply in (2.6, 5.6, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                reference_window(supply, 0)

    def test_reference_boundary_branch(self):
        self.assertAlmostEqual(reference_window(3.299, 1)[1], 3.099)
        self.assertEqual(reference_window(3.3, 1), (2.4, 3.3))

    def test_division_not_external_pin_overvoltage_fix(self):
        self.assertLess(reference_window(3.3, 1)[1], 5)

    def test_default_reference_needs_divider_on3p3(self):
        self.assertLess(reference_window(3.3, 0)[1], 2.5)
        self.assertGreater(reference_window(3.3, 1)[1], 2.5)

    def test_small_supply_drop_invalidates_default_reference(self):
        self.assertLess(reference_window(4.99, 0)[1], 2.5)
        self.assertEqual(reference_window(5, 0)[1], 2.5)

    def test_invalid_settings(self):
        for divider in (-1, 2, True):
            with self.assertRaises(ValueError):
                reference_window(5, divider)
        for gain in (-1, 2, True):
            with self.assertRaises(ValueError):
                transfer(32768, 2.5, 1, gain)
        for code in (-1, 65536, True, 1.5):
            with self.assertRaises(ValueError):
                transfer(code, 2.5, 1, 1)
        for reference in (-1, 0, float("nan")):
            with self.assertRaises(ValueError):
                transfer(32768, reference, 1, 1)

    def test_all_code_transfer_against_rational_oracle(self):
        for divider in (0, 1):
            for gain in (0, 1):
                for code in range(65536):
                    expected = Fraction(code, 65536) * Fraction(5, 2) / (
                        1 + divider) * (1 + gain)
                    self.assertEqual(transfer(code, 2.5, divider, gain), float(expected))

    def test_full_code_is_one_lsb_short(self):
        self.assertEqual(2.5 - transfer(65535, 2.5, 1, 1), 2.5 / 65536)

    def test_current_evidence_not_device_acceptance(self):
        result = audit()
        self.assertEqual(result["failed_model_runs_preserved"], 4)
        self.assertEqual(result["model_waveforms"], 0)
        self.assertFalse(result["device_accuracy_passed"])
        self.assertFalse(result["full_power_passed"])
        self.assertFalse(result["full_joint_passed"])

    def reject_metadata_change(self, filename, change):
        def altered_read(name):
            record = read(name)
            if name == filename:
                change(record)
            return record
        # Mutate only fresh in-memory records; archived evidence stays untouched.
        with patch("verify_dac.read", side_effect=altered_read):
            with self.assertRaises(AssertionError):
                audit()

    def test_reject_runtime_configuration_mismatch(self):
        self.reject_metadata_change(
            "analog_summary.json",
            lambda data: data["runs"][0].update(temp_C=25))

    def test_reject_unsupported_headroom_acceptance(self):
        self.reject_metadata_change(
            "spec_summary.json",
            lambda data: data["reference_cases"][0].update(output_load_headroom_verified=True))

    def test_reject_wrong_sync_register(self):
        self.reject_metadata_change(
            "spec_summary.json",
            lambda data: data["register_contract"].update(ldac_bit=0))

    def test_reject_device_acceptance_upgrade(self):
        self.reject_metadata_change(
            "spec_summary.json",
            lambda data: data.update(device_accuracy_passed=True))


if __name__ == "__main__":
    unittest.main()
