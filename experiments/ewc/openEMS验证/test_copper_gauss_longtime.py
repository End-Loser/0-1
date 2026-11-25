"""Complete-state chunk boundaries, independent equations and failure rejection."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from threadpoolctl import threadpool_limits

from copper_gauss_legendre import GaussLegendre
from finite_copper_cavity import CopperCavity
from run_copper_gauss_longtime import advance, sources, configurations, FILES, ROOT, sha
import verify_copper_gauss_longtime as verifier
from verify_copper_gauss_longtime import check_chunk


class ChunkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.grid = CopperCavity((3, 2, 2), (.16, .10, .06))
        cls.model = dict(sigma_S_per_m=58.58e6, R_ohm=[.01, .1], L_H=[1e-10, 1e-11])
        cls.solver = GaussLegendre(cls.grid, 2.5e-12, cls.model["R_ohm"], cls.model["L_H"])
        cls.seeds, cls.freqs, _ = sources(cls.grid, cls.model)
        cls.config = dict(shape=[3, 2, 2], lengths_m=[.16, .10, .06], dt_s=2.5e-12,
                          sample_interval_s=5e-12, initial_seed=None, pulses=[
            dict(mode=0, start_s=0., end_s=8e-12, amplitude=1e9, phase_rad=0.),
            dict(mode=0, start_s=8e-12, end_s=20e-12, amplitude=-1e9, phase_rad=0.)])
        cls.initial = np.zeros(cls.grid.ne), np.zeros(cls.grid.nf), np.zeros_like(cls.solver.rr)
        with threadpool_limits(limits=1):
            cls.whole = advance(cls.solver, cls.seeds, cls.freqs, cls.initial, 0., 0., 0, 16, 2, cls.config["pulses"])
            cls.first = advance(cls.solver, cls.seeds, cls.freqs, cls.initial, 0., 0., 0, 8, 2, cls.config["pulses"])
            state = tuple(cls.first[key] for key in ("e_after", "b_after", "i_after"))
            heat, work = cls.first["samples"][-1, 2:4]
            cls.second = advance(cls.solver, cls.seeds, cls.freqs, state, heat, work, 8, 16, 2, cls.config["pulses"])
        for data in (cls.whole, cls.first, cls.second):
            data["R_ohm"], data["L_H"] = np.array(cls.model["R_ohm"]), np.array(cls.model["L_H"])

    def test_whole_and_split_full_states_exact(self):
        for key in ("e_after", "b_after", "i_after"):
            np.testing.assert_array_equal(self.whole[key], self.second[key])
        self.assertGreater(np.linalg.norm(self.first["i_after"]), 0)
        np.testing.assert_array_equal(self.whole["samples"][-1], self.second["samples"][-1])

    def test_independent_collocation_and_budgets(self):
        with threadpool_limits(limits=1):
            _, last = check_chunk(self.first, self.config, self.seeds, self.freqs)
            check_chunk(self.second, self.config, self.seeds, self.freqs, last)
        self.assertTrue(np.any(self.whole["per_step_source_work_J"] > 0))
        self.assertTrue(np.any(self.whole["per_step_source_work_J"] < 0))

    def test_state_reset_and_work_sign_tampering_rejected(self):
        with threadpool_limits(limits=1):
            _, last = check_chunk(self.first, self.config, self.seeds, self.freqs)
            reset = {key: np.array(value, copy=True) for key, value in self.second.items()}
            reset["i_start"][:] = 0
            with self.assertRaises(AssertionError):
                check_chunk(reset, self.config, self.seeds, self.freqs, last)
            wrong = {key: np.array(value, copy=True) for key, value in self.first.items()}
            wrong["per_step_source_work_J"] *= -1
            with self.assertRaises(AssertionError):
                check_chunk(wrong, self.config, self.seeds, self.freqs)

    def test_source_and_stage_tampering_rejected(self):
        for key in ("snapshot_source_currents", "snapshot_stage_e"):
            bad = {key_: np.array(value, copy=True) for key_, value in self.first.items()}
            bad[key] += 1
            with self.assertRaises(AssertionError):
                check_chunk(bad, self.config, self.seeds, self.freqs)

    def test_reverse_chunk_and_invalid_stride_rejected(self):
        for start, end, stride in ((1, 0, 2), (0, 1, 0), (0, 1, 1.5)):
            with self.assertRaises(ValueError):
                advance(self.solver, self.seeds, self.freqs, self.initial, 0., 0., start, end, stride, [])


class ManifestTests(unittest.TestCase):
    def fixture(self, path):
        config = dict(configurations=configurations(), terms=64, cg_rtol=1e-13, maxiter=80,
                      thresholds=dict(frequency=.01, Q=.01, complete_state_pair=.01, energy_closure=1e-7))
        (path / "configurations.json").write_text(json.dumps(config), encoding="ascii")
        model = ROOT / "\u539a\u94dc\u8fb9\u754c\u57fa\u51c6\u7ed3\u679c/passive_surface_models.json"
        approval = ROOT / "\u94dc\u8154\u56db\u9636\u5b8c\u6574\u72b6\u6001\u5bf9\u7167\u7ed3\u679c/verification_27617c5451c5.json"
        manifest = dict(status="running", cases=[], source_sha256={name: sha(ROOT / name) for name in FILES},
                        configuration_sha256=sha(path / "configurations.json"), model_sha256=sha(model),
                        approval_sha256=sha(approval), full_long_history_passed=False,
                        general_initial_state_approved=False, actual_feed_passed=False,
                        full_joint_simulation_passed=False, full_system_gates_closed=0)
        (path / "progress.json").write_text(json.dumps(manifest), encoding="ascii")
        return manifest

    def test_empty_partial_never_approves_long_window(self):
        with tempfile.TemporaryDirectory(prefix="ewc-long-gate-") as tmp:
            path = Path(tmp)
            self.fixture(path)
            with patch.object(verifier, "OUT", path):
                result = verifier.audit(partial=True)
            self.assertFalse(result["all_cases_complete"])
            self.assertFalse(result["specified_8us_histories_passed"])
            self.assertEqual(result["cases"], [])

    def test_complete_mode_rejects_empty_manifest(self):
        with tempfile.TemporaryDirectory(prefix="ewc-long-gate-") as tmp:
            path = Path(tmp)
            self.fixture(path)
            with patch.object(verifier, "OUT", path), self.assertRaises(AssertionError):
                verifier.audit(partial=False)

    def test_missing_source_version_rejected(self):
        with tempfile.TemporaryDirectory(prefix="ewc-long-gate-") as tmp:
            path = Path(tmp)
            manifest = self.fixture(path)
            manifest["source_sha256"] = {}
            (path / "progress.json").write_text(json.dumps(manifest), encoding="ascii")
            with patch.object(verifier, "OUT", path), self.assertRaises(AssertionError):
                verifier.audit(partial=True)


if __name__ == "__main__":
    unittest.main()
