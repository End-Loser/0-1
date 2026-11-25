"""Synthetic branch tests only; do not substitute for actual TI trajectories."""
import unittest

import numpy as np

import verify_c_history64 as verifier


class HistoryAccounting(unittest.TestCase):
    def fixture(self):
        t = np.linspace(0, 1e-6, 11)
        d = {"time": t}
        for name, value in (
            ("a", .3), ("b", .3), ("cin", 0), ("raw", .2),
            ("carry", 3.3), ("im2", 1.65), ("cm", 1.65), ("cref", 5),
            ("vref", 5), ("i_cm1", 2e-6), ("i_cm2", 3e-6),
            ("i_bout1", 5e-6), ("i_fb1", 0), ("i_refout", .00025),
            ("i_5", 0), ("i_3", 0), ("i_f1", 0), ("i_f2", 0),
            ("i_c1", 0), ("i_c2", 0)):
            d[name] = np.full(len(t), value)
        d["i_c_feedback"] = (d["carry"] - d["im2"]) / 32750
        for node, current in (("a", "a"), ("b", "b"), ("cin", "ci")):
            d["i_" + current] = -(d[node] - d["raw"]) / 300
        net = "RCFEED cfeed im2 32750\nRA a raw 300\nRB b raw 300\nRCI cin raw 300\n"
        net += "RREFESR vref cref 1\n"
        for i in range(1, 6):
            net += f"RDT{i} vref b{i} 50000\nRDB{i} b{i} 0 50000\n"
            d[f"b{i}"] = np.full(len(t), 2.5)
            d[f"i_bin{i}"] = np.zeros(len(t))
            d[f"i_buf{i}"] = np.zeros(len(t))
            if i != 1:
                d[f"bias{i}"] = np.full(len(t), 2.5)
                d[f"i_fb{i}"] = d[f"i_bout{i}"] = np.zeros(len(t))
        for name in ("dp1", "dn1", "dp2", "dn2"):
            d[name] = np.full(len(t), 2.5)
        return d, net, np.ones(len(t), dtype=bool)

    def test_observed_branches_close(self):
        result = verifier.branch_accounting(*self.fixture())
        self.assertTrue(result["algebraic_passed"])
        self.assertTrue(result["capacitor_passed"])
        self.assertAlmostEqual(result["capacitor_checks"]["reference_output"]
                               ["initial_energy_J"], 12.5e-6, places=15)

    def test_common_mode_current_omission_fails(self):
        d, net, mask = self.fixture()
        d["i_cm1"][:] = 0
        self.assertFalse(verifier.branch_accounting(d, net, mask)["capacitor_passed"])

    def test_reference_output_current_omission_fails(self):
        d, net, mask = self.fixture()
        d["i_refout"][:] = 0
        self.assertFalse(verifier.branch_accounting(d, net, mask)["algebraic_passed"])

    def powered_fixture(self):
        d, net, mask = self.fixture()
        d["i_f1"][:] = .002
        d["i_f2"][:] = .003
        d["i_5"][:] = -.005
        d["i_c1"][:] = .004
        d["i_c2"][:] = .006
        for i in range(1, 6):
            d[f"i_buf{i}"][:] = i * .0001
        d["i_3"][:] = -.0115
        return d, net, mask

    def test_nonzero_signed_supply_currents_close(self):
        result = verifier.branch_accounting(*self.powered_fixture())
        self.assertTrue(result["algebraic_passed"])
        for name in ("diagnostic_5V", "diagnostic_3V3"):
            self.assertLess(result["algebraic_kcl_A"][name], 1e-15)

    def test_reversed_supply_orientation_rejected(self):
        for name in ("i_5", "i_3"):
            with self.subTest(name=name):
                d, net, mask = self.powered_fixture()
                d[name] *= -1
                self.assertFalse(verifier.branch_accounting(d, net, mask)["algebraic_passed"])

    def test_missing_nonzero_supply_branch_rejected(self):
        for name in ("i_f2", "i_c2", "i_buf5"):
            with self.subTest(name=name):
                d, net, mask = self.powered_fixture()
                d[name][:] = 0
                self.assertFalse(verifier.branch_accounting(d, net, mask)["algebraic_passed"])

    def test_no_window_does_not_grant_capacitor_pass(self):
        d, net, mask = self.fixture()
        mask[:] = False
        self.assertFalse(verifier.branch_accounting(d, net, mask)["capacitor_passed"])

    def test_pair_uses_all_times_and_rejects_mismatch(self):
        names = ["time", "carry", "sum"]
        t = np.arange(11) * 40e-9
        x = np.column_stack((t, t * 1e6, np.zeros(len(t))))
        y = x.copy()
        same = verifier.compare_signals((names, x), (names, y), 200e-9, [0, 0])
        self.assertTrue(same["passed"])
        self.assertEqual(same["all_coarse_times_compared"], len(t))
        y[3, 2] = .002
        altered = verifier.compare_signals((names, x), (names, y), 200e-9, [0, 0])
        self.assertFalse(altered["passed"])
        self.assertFalse(altered["phase_alignment_used"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
