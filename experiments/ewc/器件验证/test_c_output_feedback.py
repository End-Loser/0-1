"""Topology and nominal nodal equations; not hardware or joint acceptance."""
import copy
from pathlib import Path
import unittest

import numpy as np

import run_c_output_feedback as runner


class CarryFeedback(unittest.TestCase):
    def setUp(self):
        self.models = {key: Path(f"{key}.lib") for key in (
            "fda", "comparator", "reference", "buffer", "regulator")}

    def test_only_actual_carry_pin_drives_feedback(self):
        net = runner.deck(runner.configurations()[0], self.models)
        self.assertIn("VCFEED carry cfeed 0\nRCFEED cfeed im2 32750", net)
        behavior = [line for line in net.splitlines() if line.startswith("B")]
        self.assertEqual(len(behavior), 4)
        self.assertTrue(all("v(scm)" in line and "v(raw)" in line for line in behavior))
        self.assertTrue(all("carry" not in line and "sum" not in line for line in behavior))
        self.assertNotIn("Vcarry", net)
        self.assertNotIn("Vsum", net)
        comparators = [line for line in net.splitlines() if line.startswith("XC")]
        self.assertEqual(len(comparators), 2)
        self.assertTrue(all(line.endswith(" EWC_CMP") for line in comparators))

    def test_nominal_fixed_resistor_equations(self):
        c = runner.configurations()[0]
        # Solve u, op, om from two input KCL equations and fixed output common mode.
        g = 1000 / 32750
        matrix = np.array([[2, 0, -1], [2 + g, -1, 0], [0, 1, 1]])
        for raw in (0, .1, .2, .3):
            for carry in (0, 3.3):
                theta = c["sum_bias_difference_V"]
                wp = (3.2 + raw / 2 + 1.65 - theta / 2) / 2
                wn = (3.2 - raw / 2 + 1.65 + theta / 2) / 2
                _, op, om = np.linalg.solve(matrix, [wp, wn + g * carry, 3.3])
                expected = .5 * (raw - .05 - .2 * carry / 3.3)
                self.assertAlmostEqual(op - om, expected, places=13)

    def test_bias_does_not_depend_on_inputs_or_answer(self):
        a = runner.configurations()[0]
        b = copy.deepcopy(a)
        b["step_s"] = 5e-12
        na, nb = runner.deck(a, self.models), runner.deck(b, self.models)
        self.assertEqual(
            [line for line in na.splitlines() if line.startswith(("RDT", "RDB", "RCFEED"))],
            [line for line in nb.splitlines() if line.startswith(("RDT", "RDB", "RCFEED"))])

    def test_real_rails_not_ideal_fallback(self):
        c = runner.configurations()[2]
        net = runner.deck(c, self.models)
        self.assertIn("XR5 upstream en r5 EWC_RAIL PARAMS: LEVEL=5", net)
        self.assertIn("V5 r5 rail5 0", net)
        self.assertNotIn("V5 rail5 0 5", net)
        self.assertNotIn("RZERO", net)
        self.assertNotIn("uic", net.lower())
        self.assertNotIn(".ic ", net.lower())

    def test_time_and_vector_contract(self):
        configs = runner.configurations()
        self.assertEqual(len(configs), 3)
        self.assertEqual(configs[0]["states"], [0, 1, 2, 3, 4, 5, 6, 7, 0])
        self.assertEqual(len(set(runner.NAMES)), len(runner.NAMES))
        self.assertEqual(len(runner.NAMES), len(runner.VECTORS) + 1)
        self.assertAlmostEqual(configs[0]["duration_s"], 1.8e-6, places=18)
        self.assertEqual(configs[0]["feedback_resistance_ohm"], 32750)


if __name__ == "__main__":
    unittest.main(verbosity=2)
