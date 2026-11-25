import unittest

import numpy as np

from test_c_history64_accounting import HistoryAccounting
from verify_adl_offset_review import accounting_flags, branch_accounting, output_current_max


class AccountingContract(unittest.TestCase):
    def test_actual_capacitor_return_field(self):
        values = branch_accounting(*HistoryAccounting().fixture())
        self.assertTrue(all(accounting_flags(values)))
        self.assertIn("capacitor_passed", values)
        self.assertNotIn("capacitor_windows_passed", values)

    def test_missing_capacitor_result_rejected(self):
        with self.assertRaises(KeyError):
            accounting_flags({"algebraic_passed": True})

    def test_failed_capacitor_not_granted_pass(self):
        values = {"algebraic_passed": True, "capacitor_passed": False}
        self.assertEqual(accounting_flags(values), (True, False))

    def test_output_current_derived_without_unexported_vector(self):
        d = {"time": np.array([0., 1e-9])}
        for stage in (1, 2):
            for name in ("op", "om", "cp", "cn", "ip", "im"):
                d[f"{name}{stage}"] = np.full(2, 1.65)
        d["op1"] += .01
        self.assertNotIn("i_op1", d)
        self.assertAlmostEqual(output_current_max(d), .01 / 1000 + .01 / 22)

    def test_excess_output_current_rejected_by_original_20mA_gate(self):
        d = {"time": np.array([0., 1e-9])}
        for stage in (1, 2):
            for name in ("op", "om", "cp", "cn", "ip", "im"):
                d[f"{name}{stage}"] = np.full(2, 1.65)
        d["op1"] += 1
        self.assertGreater(output_current_max(d), .020)


if __name__ == "__main__":
    unittest.main(verbosity=2)
