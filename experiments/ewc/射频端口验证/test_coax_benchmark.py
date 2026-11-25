"""Focused normalization tests and evidence-tampering negative controls."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

import numpy as np

import run_coax_benchmark as runner
import verify_coax_benchmark as verifier

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "结果"


class CoaxTests(unittest.TestCase):
    def case(self, load=50, z0=50, length=0.075):
        return runner.line_case(z0, 299792458, 3e9, load,
                                np.array([0, length/2, length]), length)

    def rejected_mutation(self, mutate):
        with tempfile.TemporaryDirectory(prefix="ewc-coax-test-") as temp:
            folder = Path(temp)/"evidence"
            shutil.copytree(RESULTS, folder)
            summary = json.loads((folder/"summary.json").read_text(encoding="utf-8"))
            mutate(summary, folder)
            (folder/"summary.json").write_text(
                json.dumps(summary, allow_nan=False), encoding="utf-8")
            with self.assertRaises(AssertionError):
                verifier.verify(folder)

    def test_design_geometry_50ohm(self):
        a, b = runner.geometry()
        c, l = runner.reference(a, b)
        self.assertAlmostEqual(np.sqrt(l/c), 50, places=10)

    def test_invalid_geometry(self):
        for a, b in ((0, 1), (1, 1), (2, 1)):
            with self.assertRaises(ValueError):
                runner.reference(a, b)

    def test_invalid_mesh(self):
        a, b = runner.geometry()
        for n, mesh in ((1, "uniform"), (8, "unknown")):
            with self.assertRaises(ValueError):
                runner.radial_fem(a, b, n, mesh)

    def test_radial_voltage_energy_and_refinement(self):
        a, b = runner.geometry()
        cref, _ = runner.reference(a, b)
        coarse = runner.radial_fem(a, b, 2, "uniform")
        fine = runner.radial_fem(a, b, 256, "uniform")
        self.assertGreater(float(coarse["c_f_per_m"])/cref-1, 0.01)
        self.assertLess(float(fine["c_f_per_m"])/cref-1, 2e-6)
        np.testing.assert_allclose(np.dot(fine["e_r"], np.diff(fine["r"])), 1)
        np.testing.assert_allclose(2*fine["electric_energy_j_per_m"], fine["c_f_per_m"])

    def test_ampere_circles(self):
        a, b = runner.geometry()
        d = runner.radial_fem(a, b, 32, "inner_graded")
        np.testing.assert_allclose(2*np.pi*d["r"]*d["h_phi"], 1)

    def test_peak_power_normalization(self):
        a, b = runner.power_waves(np.array([1+0j]), np.array([0.02+0j]), 50)
        self.assertAlmostEqual(float(abs(a[0])**2), 0.01)
        self.assertAlmostEqual(float(abs(b[0])**2), 0)

    def test_invalid_wave_reference(self):
        for ref in (0, -50, 50+1j):
            with self.assertRaises(ValueError):
                runner.power_waves(1, 0.02, ref)

    def test_matched_source_load(self):
        d = self.case()
        np.testing.assert_allclose(d["power_w"], 0.0025, atol=1e-14)
        np.testing.assert_allclose(d["waves"][:, 1], 0, atol=1e-14)
        np.testing.assert_allclose(d["source_power_w"], 0.005)
        np.testing.assert_allclose(d["source_resistor_w"], 0.0025)

    def test_terminal_resistor_not_shunt(self):
        d = self.case(25)
        gl = d["waves"][-1, 1]/d["waves"][-1, 0]
        np.testing.assert_allclose(gl, -1/3)
        self.assertGreater(abs(gl-(-50/(2*25+50))), 0.1)

    def test_open_and_short(self):
        for load, gamma in (("open", 1), ("short", -1)):
            d = self.case(load)
            np.testing.assert_allclose(d["power_w"], 0, atol=1e-13)
            np.testing.assert_allclose(d["waves"][-1, 1]/d["waves"][-1, 0], gamma)

    def test_complex_load_absorption(self):
        d = self.case(50+25j)
        v, i = d["vi"][-1]
        np.testing.assert_allclose(v, (50+25j)*i)
        self.assertAlmostEqual(d["power_w"][-1], 25*abs(i)**2)

    def test_reference_roundtrip_phase(self):
        d = self.case(100)
        gamma = d["waves"][:, 1]/d["waves"][:, 0]
        beta = 2*np.pi*3e9/299792458
        np.testing.assert_allclose(gamma[0], gamma[-1]*np.exp(-2j*beta*0.075))
        self.assertGreater(abs(gamma[0]-gamma[-1]*np.exp(-1j*beta*0.075)), 0.1)

    def test_50ohm_renormalization_not_pure_phase_for_non50_line(self):
        d = self.case(100, z0=75)
        w = d["waves_50"]
        gamma = w[:, 1]/w[:, 0]
        beta = 2*np.pi*3e9/299792458
        self.assertGreater(abs(gamma[0]-gamma[-1]*np.exp(-2j*beta*0.075)), 0.01)
        np.testing.assert_allclose(abs(w[:, 0])**2-abs(w[:, 1])**2, d["power_w"])

    def test_transfer_matrix_composition_and_reciprocity(self):
        beta = 2*np.pi*3e9/299792458
        m = runner.abcd(50, beta, 0.075)
        np.testing.assert_allclose(m, runner.abcd(50, beta, 0.02) @
                                   runner.abcd(50, beta, 0.055), atol=1e-13)
        np.testing.assert_allclose(np.linalg.det(m), 1, atol=1e-13)
        np.testing.assert_allclose(m@runner.abcd(50, beta, -0.075), np.eye(2), atol=1e-13)

    def test_zero_length_line(self):
        d = self.case(100, length=0)
        np.testing.assert_allclose(d["vi"][:, 0], 2/3)
        np.testing.assert_allclose(d["vi"][:, 1], 1/150)

    def test_invalid_reference_plane(self):
        with self.assertRaises(ValueError):
            runner.line_case(50, 3e8, 3e9, 50, [-0.01, 0.1])

    def test_independent_complete_evidence(self):
        report = verifier.verify(RESULTS)
        self.assertTrue(report["independent_verification_pass"])
        self.assertEqual(len(report["retained_physical_failures"]), 2)

    def test_reject_forged_coarse_pass(self):
        self.rejected_mutation(lambda s, _: s["electrostatic"][0].update(
            physical_1percent_pass=True))

    def test_reject_changed_threshold(self):
        self.rejected_mutation(lambda s, _: s["thresholds"].update(
            relative_physical_error=0.1))

    def test_reject_false_full_port_claim(self):
        self.rejected_mutation(lambda s, _: s.update(complete_physical_port_pass=True))

    def test_reject_missing_case(self):
        self.rejected_mutation(lambda s, _: s["transmission"].pop())

    def test_reject_raw_hash_change(self):
        def mutate(s, folder):
            p = folder/s["electrostatic"][0]["archive"]
            with p.open("ab") as handle:
                handle.write(b"tampering")
        self.rejected_mutation(mutate)

    def test_reject_wrong_current_even_with_updated_hash(self):
        def mutate(s, folder):
            row = s["transmission"][0]
            p = folder/row["archive"]
            with np.load(p, allow_pickle=False) as data:
                changed = {k: data[k] for k in data.files}
            changed["vi"][:, 1] *= -1
            np.savez(p, **changed)
            row["sha256"] = hashlib.sha256(p.read_bytes()).hexdigest()
        self.rejected_mutation(mutate)

    def test_reject_wrong_peak_factor_even_with_updated_hash(self):
        def mutate(s, folder):
            row = s["transmission"][0]
            p = folder/row["archive"]
            with np.load(p, allow_pickle=False) as data:
                changed = {k: data[k] for k in data.files}
            changed["waves"] *= np.sqrt(2)
            np.savez(p, **changed)
            row["sha256"] = hashlib.sha256(p.read_bytes()).hexdigest()
        self.rejected_mutation(mutate)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(CoaxTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = dict(tests_run=result.testsRun, failures=len(result.failures),
                  errors=len(result.errors), skipped=len(result.skipped),
                  all_pass=result.wasSuccessful(),
                  tests_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  summary_sha256=hashlib.sha256((RESULTS/"summary.json").read_bytes()).hexdigest())
    (RESULTS/"tests.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    raise SystemExit(0 if result.wasSuccessful() else 1)
