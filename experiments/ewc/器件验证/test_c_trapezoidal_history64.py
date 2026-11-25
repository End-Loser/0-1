"""Reject physical retuning, shortened histories and relaxed comparison gates."""
import unittest

import numpy as np

from run_c_saved_history64 import configurations as originals
from run_c_trapezoidal_history64 import PRIOR, configurations, deck
from verify_c_saved_history64 import compare_signals
from verify_c_trapezoidal_history64 import check_deck


class TrapezoidalTests(unittest.TestCase):
    def test_only_second_order_method_changes(self):
        for c in originals():
            net = (PRIOR / c["id"] / "test.cir").read_text(encoding="ascii")
            candidate = deck(net)
            check_deck(candidate, net)
            self.assertEqual(candidate.replace(".options method=trap maxord=2",
                                               ".options method=gear maxord=2"), net)

    def test_original_histories_steps_and_gates(self):
        extra = ("integration_method", "integration_max_order", "original_failure_replaced",
                 "tolerances_relaxed")
        observed = configurations()
        self.assertEqual([{k: v for k, v in c.items() if k not in extra}
                          for c in observed], originals())
        self.assertEqual([c["integration_max_order"] for c in observed], [2, 2, 2])
        self.assertTrue(all(not c["reset_between_transitions"] and not c["real_rails"]
                            for c in observed))

    def test_network_options_exports_and_history_tampering_rejected(self):
        net = (PRIOR / originals()[0]["id"] / "test.cir").read_text(encoding="ascii")
        candidate = deck(net)
        for changed in (
            candidate.replace("RCFEED cfeed im2 32750", "RCFEED cfeed im2 32751"),
            candidate.replace("maxord=2", "maxord=1"),
            candidate.replace("maxord=2", "maxord=2 reltol=1e-2"),
            candidate.replace("tran 1e-11 1.8e-06", "tran 1e-11 1e-06"),
            candidate.replace("wrdata wave.txt ", "wrdata wave.txt v(missing) ")):
            with self.assertRaises(ValueError):
                check_deck(changed, net)

    def test_strict_voltage_gate_and_no_alignment(self):
        names = ["time", "carry", "sum", "extra"]
        a = np.array([[0., 0., 0., 0.], [200e-9, 0., 0., 0.], [400e-9, 0., 0., 0.]])
        b = a.copy()
        b[:, 3] = .001
        result = compare_signals((names, a), (names, b), 200e-9, [0, 0])
        self.assertFalse(result["passed"])
        self.assertFalse(result["phase_alignment_used"])
        self.assertEqual(result["all_coarse_times_compared"], len(a))
        self.assertEqual(result["voltage_difference_V"]["extra"], .001)

    def test_edge_difference_and_missing_observable_rejected(self):
        names = ["time", "carry", "sum"]
        a = np.array([[0., 0., 0.], [200e-9, 0., 0.],
                      [200e-9 + 100e-12, 3.3, 0.], [400e-9, 3.3, 0.]])
        b = a.copy()
        b[2, 0] += 50e-12
        result = compare_signals((names, a), (names, b), 200e-9, [0, 1])
        edge = next(e for e in result["edge_records"] if e["signal"] == "carry")
        self.assertFalse(edge["passed"])
        self.assertGreater(edge["differences_s"][0], 20e-12)
        with self.assertRaises(AssertionError):
            compare_signals((names, a), (names[:-1], b[:, :-1]), 200e-9, [0, 1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
