"""Only in-memory mutations; preserve the official package and failed logs."""
import unittest

from verify_vendor_project import audit, inspect_log


class VendorProjectTests(unittest.TestCase):
    def test_real_package_retains_failure_and_scope(self):
        result = audit()
        self.assertTrue(result["supplied_text_logs_both_failed"])
        for key in ("binary_dat_proves_completion", "full_DAC_passed",
                    "local_supported_pspice_executed", "full_power_passed",
                    "supplied_log_to_current_library_runtime_sha_binding"):
            self.assertFalse(result[key])
        self.assertEqual(result["network"]["parallel_code_inputs"], 16)
        self.assertFalse(result["network"]["serial_SPI_implemented"])
        self.assertEqual(result["full_system_gates_closed"], 0)

    def test_abort_overrides_statistics_and_binary_presence(self):
        result = inspect_log(b"PSpice 17.4.0 (Nov 2018)\r\nJOB ABORTED\r\n"
                             b"JOB STATISTICS SUMMARY\r\nTotal job time = 561.56\r\n"
                             b"test.dat 69396499 bytes\r\n")
        self.assertTrue(result["failed_or_aborted"])
        self.assertFalse(result["full_transient_verified"])

    def test_no_marker_does_not_prove_transient_completion(self):
        result = inspect_log(b"PSpice 17.4.0 (Nov 2018)\nJOB STATISTICS SUMMARY\n")
        self.assertFalse(result["failed_or_aborted"])
        self.assertFalse(result["full_transient_verified"])

    def test_divide_zero_failure_classified(self):
        result = inspect_log(b"ERROR(ORPSIM-16550): Floating point computation failed\n"
                             b"INTERNAL ERROR -- Divide by Zero, Divide\n"
                             b"ABORTING SIMULATION\n")
        self.assertTrue(result["failed_or_aborted"])
        self.assertEqual(len(result["excerpts"]), 3)

    def test_binary_not_decoded_as_ascii_log(self):
        with self.assertRaises(UnicodeDecodeError):
            inspect_log(b"\x90\x00\xff")


if __name__ == "__main__":
    unittest.main()
