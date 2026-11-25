"""Explicit plot storage tests; actual trajectory admission is separate."""
import unittest

import run_c_history64 as original
import run_c_saved_history64 as candidate


class SavedHistory(unittest.TestCase):
    def test_same_complete_history_and_gates(self):
        for old, new in zip(original.configurations(), candidate.configurations()):
            reduced = new.copy()
            self.assertEqual(reduced.pop("plot_save_mode"),
                             "All declared external observables; full circuit integration")
            self.assertEqual(old, reduced)

    def test_only_plot_save_command_added(self):
        baseline = (original.PRIOR / "actual_C_10ps_diagnostic_rails" / "test.cir").read_text(
            encoding="ascii")
        for config in candidate.configurations():
            old = original.deck(baseline, config)
            new = candidate.deck(baseline, config)
            self.assertEqual("\n".join(s for s in new.splitlines()
                                      if not s.startswith("save ")) + "\n", old)
            saves = [s.split()[1:] for s in new.splitlines() if s.startswith("save ")]
            outputs = [s.split()[2:] for s in new.splitlines() if s.startswith("wrdata ")]
            self.assertEqual(len(saves), 1)
            self.assertEqual(len(outputs[0]), 82)
            self.assertEqual(set(saves[0]), set(outputs[0]))
            self.assertIn("i(VCM1)", saves[0])
            self.assertIn("i(VCM2)", saves[0])
            self.assertIn("i(VROUT)", saves[0])
            self.assertIn("v(cref)", saves[0])

    def test_missing_output_observable_is_detectable(self):
        baseline = (original.PRIOR / "actual_C_10ps_diagnostic_rails" / "test.cir").read_text(
            encoding="ascii")
        net = candidate.deck(baseline, candidate.configurations()[0])
        saves = next(s.split()[1:] for s in net.splitlines() if s.startswith("save "))
        exports = next(s.split()[2:] for s in net.splitlines() if s.startswith("wrdata "))
        saves.remove("i(VROUT)")
        self.assertNotEqual(set(saves), set(exports))


if __name__ == "__main__":
    unittest.main(verbosity=2)
