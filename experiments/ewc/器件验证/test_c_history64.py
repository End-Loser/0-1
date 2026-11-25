"""Preflight coverage, observation-only topology and lossless vector archives."""
from collections import Counter
import tempfile
from pathlib import Path
import unittest

import numpy as np

import run_c_history64 as runner


class History64(unittest.TestCase):
    def test_exactly_all_ordered_pairs_once(self):
        states = runner.history()
        pairs = Counter(zip(states, states[1:]))
        self.assertEqual(len(states), 65)
        self.assertEqual(pairs, Counter({(a, b): 1 for a in range(8) for b in range(8)}))
        self.assertEqual(states[0], states[-1])
        self.assertTrue(all((a, a) in pairs for a in range(8)))

    def test_full_duration_and_unchanged_gates(self):
        c = runner.configurations()
        self.assertEqual(len(c), 3)
        self.assertAlmostEqual(c[1]["duration_s"], 13e-6, places=18)
        self.assertEqual(c[1]["states"], c[2]["states"])
        self.assertEqual([r["step_s"] for r in c], [10e-12, 10e-12, 5e-12])
        self.assertTrue(all(not r["reset_between_transitions"] for r in c))
        self.assertTrue(all(r["amplitude_gate_V"] == .001 for r in c))

    def test_probes_do_not_change_physical_network(self):
        original = (runner.PRIOR / "actual_C_10ps_diagnostic_rails" /
                    "test.cir").read_text(encoding="ascii")
        net = runner.deck(original, runner.configurations()[0])
        for stage in (1, 2):
            net = net.replace(f"VCM{stage} cm cm{stage} 0\n", "")
            net = net.replace(
                f"om{stage} cm{stage} vs{stage} 0 EWC_FDA",
                f"om{stage} cm vs{stage} 0 EWC_FDA")
        net = net.replace("VROUT refout vref 0\n", "")
        net = net.replace("trimref refout EWC_REF", "trimref vref EWC_REF")
        def physical(text):
            return [line for line in text.splitlines()
                    if not line.startswith(("VA ", "VB ", "VCI ", "tran ", "wrdata "))]
        self.assertEqual(physical(net), physical(original))
        self.assertIn("RCFEED cfeed im2 32750", net)

    def test_stimuli_are_independent_of_carry(self):
        for bit in (0, 1, 2):
            text = runner.stimulus(runner.history(), bit)
            values = list(map(float, text.split()))
            self.assertEqual(set(values[1::2]), {0., .3})
            self.assertTrue(all(a <= b for a, b in zip(values[::2], values[2::2])))
            self.assertAlmostEqual(values[-2], 13e-6, places=18)

    def test_archive_preserves_every_row(self):
        with tempfile.TemporaryDirectory(prefix="ewc-history64-test-") as temp:
            root = Path(temp)
            source = root / "raw.txt"
            source.write_text("time voltage\n0 1\n1e-9 2\n2e-9 3\n", encoding="ascii")
            output = root / "vectors"
            output.mkdir()
            record = runner.archive_rows(source, output, ["time", "voltage"])
            self.assertEqual(record["rows"], 3)
            self.assertTrue(record["time_strictly_monotonic"])
            with np.load(output / record["chunks"][0]["file"], allow_pickle=False) as ar:
                np.testing.assert_array_equal(ar["data"], [[0, 1], [1e-9, 2], [2e-9, 3]])
            with self.assertRaises(AssertionError):
                runner.archive_rows(source, output, ["time", "voltage"])

    def test_duplicate_times_retained_but_not_approved(self):
        with tempfile.TemporaryDirectory(prefix="ewc-history64-time-test-") as temp:
            root = Path(temp)
            source = root / "raw.txt"
            source.write_text("time voltage\n0 1\n0 2\n1e-9 3\n", encoding="ascii")
            output = root / "vectors"
            output.mkdir()
            record = runner.archive_rows(source, output, ["time", "voltage"])
            self.assertEqual(record["rows"], 3)
            self.assertFalse(record["time_strictly_monotonic"])
            self.assertNotIn("local_passed", record)


if __name__ == "__main__":
    unittest.main(verbosity=2)
