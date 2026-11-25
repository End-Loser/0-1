"""Verifier regressions use small full-state fixtures, not 8 us evidence."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from threadpoolctl import threadpool_limits

from copper_midpoint_cg import MidpointCG
from finite_copper_cavity import CopperCavity
from run_fine_copper_longtime import advance_chunk
import verify_fine_copper_longtime as verifier


class VerifierTests(unittest.TestCase):
    def setUp(self):
        self.grid = CopperCavity((8, 5, 3), (.16, .10, .06))
        r, l = np.array([.01, .1]), np.array([1e-10, 1e-11])
        self.solver = MidpointCG(self.grid, 2e-12, r, l)
        self.seed = self.grid.pec.lift(self.grid.pec.sample_mode((0, 1, 1)))
        self.m, self.q, self.edges, scale, _, _ = verifier.geometry(
            self.grid.shape, self.grid.lengths)
        self.rr, self.ll = scale[:, None] * r, scale[:, None] * l
        with threadpool_limits(limits=1):
            self.data = advance_chunk(
                self.solver, self.seed, self.seed, np.zeros(self.grid.nf),
                np.zeros_like(self.rr), 0., 0, 40, 10)

    def check(self, data):
        return verifier.check_anchor(data, self.grid.shape, 2e-12, self.m,
                                     self.q, self.edges, self.rr, self.ll)

    def test_independent_011_seed(self):
        observed = verifier.seed_011(self.grid.shape, self.grid.lengths, self.m)
        np.testing.assert_allclose(observed, self.seed, rtol=1e-13, atol=1e-10)
        self.assertAlmostEqual(float(self.m @ observed**2), 1.)

    def test_independent_equations_and_energy(self):
        result = self.check(self.data)
        self.assertLess(result["energy_step"], 1e-9)
        self.assertLess(result["electric"], 1e-7)

    def test_changed_wall_current_rejected(self):
        data = dict(self.data)
        data["j_mid"] = self.data["j_mid"] + 1.
        with self.assertRaises(AssertionError):
            self.check(data)

    def test_changed_full_state_rejected(self):
        for key in ("e_after", "b_after", "i_after"):
            data = dict(self.data)
            data[key] = self.data[key].copy()
            data[key].flat[0] += 1.
            with self.subTest(key=key), self.assertRaises(AssertionError):
                self.check(data)

    def test_wrong_shape_and_nonfinite_rejected(self):
        for replacement in (self.data["e_before"][:-1],
                            np.full_like(self.data["e_before"], np.nan)):
            data = dict(self.data, e_before=replacement)
            with self.assertRaises(AssertionError):
                self.check(data)

    def test_manifest_hash_matches_parsed_snapshot(self):
        with tempfile.TemporaryDirectory(prefix="ewc-longtime-verifier-") as folder:
            path = Path(folder) / "progress.json"
            raw = json.dumps({"completed_steps": 50000}).encode("utf-8")
            path.write_bytes(raw)
            manifest, digest = verifier.load_manifest(path)
            path.write_text('{"completed_steps": 100000}', encoding="utf-8")
            self.assertEqual(manifest["completed_steps"], 50000)
            self.assertEqual(digest, hashlib.sha256(raw).hexdigest())
            self.assertNotEqual(digest, verifier.sha(path))

    def test_partial_and_failed_physics_never_pass(self):
        good = dict(relative_frequency_error=.002, relative_Q_error=.009,
                    fit_window_Q_difference=.0001)
        self.assertTrue(verifier.physical_pass(True, good))
        self.assertFalse(verifier.physical_pass(False, good))
        self.assertFalse(verifier.physical_pass(True, None))
        for key in good:
            for value in (.01, .01003765, np.nan, np.inf, -.001):
                with self.subTest(key=key, value=value):
                    self.assertFalse(verifier.physical_pass(True, dict(good, **{key: value})))

    def test_unfinished_manifest_rejected_in_full_mode(self):
        manifest, digest = verifier.load_manifest(verifier.OUT / "progress.json")
        manifest = dict(manifest, status="running", completed_steps=0, chunks=[])
        with patch.object(verifier, "load_manifest", return_value=(manifest, digest)):
            with self.assertRaisesRegex(AssertionError, "not yet finished"):
                verifier.audit()
            with self.assertRaisesRegex(AssertionError, "No completed state chunk"):
                verifier.audit(partial=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
