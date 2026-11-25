"""Holdout topology tests; not physical acceptance."""
import unittest

import run_adl_dc_holdout as runner


class Holdout(unittest.TestCase):
    def test_declared_inputs_not_retuned(self):
        configs = runner.configurations()
        self.assertEqual(len(configs), 24)
        self.assertEqual({c["common_mode_V"] for c in configs}, {2.5, 3.2, 3.8})
        self.assertTrue(all(not c["bias_retuned"] and c["amplitude_gate_V"] == .001
                            and c["feedback_resistance_ohm"] == 32750 for c in configs))
        self.assertTrue(all("no ADL macro" in c["source_model"] for c in configs))

    def test_only_inputs_and_analysis_changed(self):
        original = (runner.BASE / "probe_equivalence_10ps" / "test.cir").read_text(
            encoding="ascii")
        ignore = ("VA ", "VB ", "VCI ", "VSC ", "tran ", "op", "wrdata ")
        baseline = [s for s in original.splitlines() if not s.startswith(ignore)]
        for config in runner.configurations():
            text = runner.deck(original, config)
            self.assertEqual([s for s in text.splitlines() if not s.startswith(ignore)],
                             baseline)
            self.assertEqual(text.count("\nop\n"), 1)
            self.assertNotIn("\ntran ", text)

    def test_independent_static_inputs(self):
        original = (runner.BASE / "probe_equivalence_10ps" / "test.cir").read_text(
            encoding="ascii")
        for config in runner.configurations():
            text = runner.deck(original, config)
            for name, node, bit in (("VA", "a", 2), ("VB", "b", 1), ("VCI", "cin", 0)):
                self.assertIn(f"{name} {node} 0 {float(.3 * ((config['state'] >> bit) & 1))}",
                              text)
            behavior = [s for s in text.splitlines() if s.startswith("B")]
            self.assertEqual(len(behavior), 4)
            self.assertTrue(all("v(raw)" in s and "carry" not in s and "sum" not in s
                                for s in behavior))

    def test_resource_query_is_positive(self):
        self.assertGreater(runner.available_commit(), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
