"""Offset topology and nominal-gate tests, not physical receiver acceptance."""
import json
import unittest

import numpy as np

import run_adl_offset_holdout as runner
from verify_adl_offset_holdout import window_metrics


class OffsetHoldout(unittest.TestCase):
    def test_four_independent_sign_combinations(self):
        configs = runner.configurations()
        self.assertEqual({tuple(c["offsets_V"]) for c in configs},
                         {(-.01, -.01), (-.01, .01), (.01, -.01), (.01, .01)})
        self.assertTrue(all(not c["calibration_retuned"] and not c["actual_ADL_model"]
                            and c["amplitude_gate_V"] == .001
                            and abs(c["duration_s"] - 1.8e-6) < 1e-18 for c in configs))

    def test_only_fixed_input_offsets_added(self):
        original = (runner.BASE / "probe_equivalence_10ps" / "test.cir").read_text(
            encoding="ascii")
        for config in runner.configurations():
            candidate = runner.deck(original, config)
            self.assertEqual([s for s in original.splitlines() if not s.startswith(("BP", "BN"))],
                             [s for s in candidate.splitlines() if not s.startswith(("BP", "BN"))])
            self.assertNotIn("carry", "".join(s for s in candidate.splitlines()
                                             if s.startswith(("BP", "BN"))))

    def fixture(self, state, offsets=(0, 0)):
        count = state.bit_count()
        gain = .5 * 1000 / 1044
        carry, summation = count // 2, count % 2
        diffs = [gain * (.1 * count - .15 + offsets[0]),
                 gain * (.1 * count - .05 - .2 * carry + offsets[1])]
        d = {f"c{p}{s}": np.full(2, 1.65 + sign * diffs[s - 1] / 2)
             for s in (1, 2) for p, sign in (("p", 1), ("n", -1))}
        d.update(carry=np.full(2, 3.3 * carry), sum=np.full(2, 3.3 * summation))
        return d, np.ones(2, dtype=bool), state

    def test_nominal_eight_states_pass_fixed_gate(self):
        for state in range(8):
            self.assertTrue(window_metrics(*self.fixture(state))["nominal_amplitude_passed"])

    def test_typical_offset_does_not_shift_correct_answer(self):
        for state in range(8):
            result = window_metrics(*self.fixture(state, (.01, -.01)))
            self.assertFalse(result["nominal_amplitude_passed"])
            self.assertTrue(result["logic_passed"])
            self.assertGreater(max(result["amplitude_error_V"]), .004)

    def test_two_millivolt_error_rejected(self):
        d, mask, state = self.fixture(0)
        d["cp2"] += .002
        self.assertFalse(window_metrics(d, mask, state)["nominal_amplitude_passed"])

    def test_column_names_bound_to_frozen_metadata_and_NPZ(self):
        source = json.loads((runner.BASE / "running_source.json").read_bytes())
        run = json.loads((runner.BASE / "probe_equivalence_10ps" / "run.json").read_bytes())
        self.assertNotIn("columns", run)
        self.assertEqual(len(runner.column_names(source, run)), 83)
        changed = dict(source, columns=list(reversed(source["columns"])))
        with self.assertRaises(AssertionError):
            runner.column_names(changed, run)
        with self.assertRaises(KeyError):
            runner.column_names({}, run)


if __name__ == "__main__":
    unittest.main(verbosity=2)
