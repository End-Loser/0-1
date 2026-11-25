"""Read-only adversarial checks for full-LDO current and endpoint evidence."""
import json
import unittest

import numpy as np

from verify_internal_state import ROOT, metrics, physical_lines


class StateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runs = json.loads((ROOT / "summary.json").read_bytes())["runs"]

    def load(self, index):
        run = self.runs[index]
        return run, np.loadtxt(ROOT / "results" / run["id"] / "raw_wave.txt", skiprows=1)

    def test_real_failure_and_roundoff_success_preserved(self):
        for index in range(2):
            run, data = self.load(index)
            result = metrics(data, run["columns"], run["window_s"])
            self.assertEqual(result["selected_output_passed"], index == 0)
            self.assertLess(result["endpoint_by_control_sign_relative_error"], 1e-6)

    def test_output_current_corruption_rejected(self):
        run, data = self.load(1)
        data[:, run["columns"].index("output_current")] += 1e-6
        with self.assertRaises(AssertionError):
            metrics(data, run["columns"], run["window_s"])

    def test_internal_feedback_omission_rejected(self):
        run, data = self.load(1)
        data[:, run["columns"].index("feedback_current")] = 0
        with self.assertRaises(AssertionError):
            metrics(data, run["columns"], run["window_s"])

    def test_zero_control_bias_rejected(self):
        run, data = self.load(1)
        data[:, run["columns"].index("switch_control")] = -1e-16
        with self.assertRaises(AssertionError):
            metrics(data, run["columns"], run["window_s"])

    def test_nonfinite_and_reversed_time_rejected(self):
        run, data = self.load(1)
        bad = data.copy()
        bad[0, 2] = np.nan
        for corrupted in (bad, data[::-1]):
            with self.assertRaises(AssertionError):
                metrics(corrupted, run["columns"], run["window_s"])

    def test_physical_network_change_visible(self):
        net = (ROOT / "results" / self.runs[1]["id"] / "test.cir").read_text(encoding="ascii")
        self.assertNotEqual(physical_lines(net), physical_lines(net.replace("RLOAD load 0 330", "RLOAD load 0 331")))
        self.assertEqual(physical_lines(net), physical_lines(net.replace("listing e\n", "")))


if __name__ == "__main__":
    unittest.main()
