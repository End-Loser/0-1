"""Negative checks on archived TI data, not new RF simulation evidence."""
import json
import unittest

import numpy as np

import verify_adl_dc_holdout as verifier


class DCEvaluation(unittest.TestCase):
    def fixture(self):
        folder = verifier.BASE / "probe_equivalence_10ps"
        run = json.loads((folder / "run.json").read_bytes())
        for chunk in run["chunks"]:
            if chunk["first_time_s"] <= 150e-9 <= chunk["last_time_s"]:
                with np.load(folder / chunk["file"], allow_pickle=False) as ar:
                    values = ar["data"]
                    names = ar["columns"].tolist()
                    row = values[np.argmin(abs(values[:, 0] - 150e-9))]
                d = dict(zip(names, row))
                break
        else:
            self.fail("No actual archived nominal window")
        net = (folder / "test.cir").read_text(encoding="ascii")
        return d, net, dict(state=0, common_mode_V=3.2)

    def test_nominal_archived_window_is_not_ADL_certification(self):
        result = verifier.evaluate(*self.fixture())
        self.assertTrue(result["checks"]["amplitude"])
        self.assertTrue(result["checks"]["logic"])
        # Actual transient capacitors still carry current at this sample.
        self.assertFalse(result["checks"]["external_DC_kcl"])
        self.assertFalse(result["local_fixed_chain_dc_passed"])
        self.assertFalse(result["ADL_actual_loading_passed"])
        self.assertFalse(result["real_RF_passed"])
        self.assertTrue(result["no_time_integral_or_energy_claim"])

    def test_numpy_sample_serializes_without_losing_scope(self):
        encoded = json.dumps(verifier.evaluate(*self.fixture()))
        self.assertFalse(json.loads(encoded)["real_RF_passed"])

    def test_two_millivolt_sum_error_rejected(self):
        d, net, config = self.fixture()
        d["cp2"] += .002
        self.assertFalse(verifier.evaluate(d, net, config)["checks"]["amplitude"])

    def test_wrong_supply_sign_rejected(self):
        d, net, config = self.fixture()
        d["i_5"] *= -1
        self.assertFalse(verifier.evaluate(d, net, config)["checks"]["external_DC_kcl"])

    def test_missing_reference_current_rejected(self):
        d, net, config = self.fixture()
        d["i_refout"] = 0
        self.assertFalse(verifier.evaluate(d, net, config)["checks"]["external_DC_kcl"])

    def test_peak_current_is_only_typical_screen(self):
        d, net, config = self.fixture()
        d["i_sp1"] = .013
        result = verifier.evaluate(d, net, config)
        self.assertFalse(result["typical_current_screen_passed"])
        self.assertFalse(result["ADL_actual_loading_passed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
