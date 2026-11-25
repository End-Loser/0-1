"""Negative controls for the evidence gate; no field or geometry calculation."""

import copy
import io
import json
from pathlib import Path
import unittest

from verify_sources import ROOT, digest, verify


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))

    def reject(self, mutate):
        changed = copy.deepcopy(self.manifest)
        mutate(changed)
        with self.assertRaises(ValueError):
            verify(changed)

    def test_archived_evidence_passes_without_physical_pass(self):
        result = verify(self.manifest)
        self.assertEqual(result["full_wave_missing_items"], 16)
        self.assertFalse(result["complete_physical_port_pass"])

    def test_reject_source_limit_change(self):
        self.reject(lambda m: m.update(source_limit=6))

    def test_reject_unclosed_budget(self):
        self.reject(lambda m: m.update(source_budget_closed=False))

    def test_reject_physical_pass(self):
        self.reject(lambda m: m.update(complete_physical_port_pass=True))

    def test_reject_fixture(self):
        self.reject(lambda m: m.update(fixture_created=True))

    def test_reject_fake_original(self):
        self.reject(lambda m: m.update(successful_original_files=1))

    def test_reject_fake_field_run(self):
        self.reject(lambda m: m.update(field_runs=1))

    def test_reject_ideal_air_substitution(self):
        self.reject(lambda m: m.update(ideal_air_coax_substitution=True))

    def test_reject_omitted_requirement(self):
        self.reject(lambda m: m["candidates"][0]["full_wave_requirements"].pop())

    def test_reject_false_availability(self):
        self.reject(lambda m: m["candidates"][0]["full_wave_requirements"][0].update(
            status="可用"))

    def test_reject_unresolved_conflict_claimed_resolved(self):
        self.reject(lambda m: m["candidates"][1]["conflicts"][0].update(resolved=True))

    def test_reject_text_hash_change(self):
        self.reject(lambda m: m["evidence"][0].update(sha256="0" * 64))

    def test_reject_evidence_path_escape(self):
        self.reject(lambda m: m["evidence"][0].update(file="../run_coax_benchmark.py"))

    def test_reject_wrong_candidate_evidence(self):
        self.reject(lambda m: m["candidates"][0].update(sources=["S1", "S4"]))


if __name__ == "__main__":
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(EvidenceTests))
    payload = {"status": "通过" if result.wasSuccessful() else "失败",
               "tests_run": result.testsRun,
               "failures": len(result.failures), "errors": len(result.errors),
               "skipped": len(result.skipped),
               "negative_controls": result.testsRun - 1,
               "manifest_sha256": digest(ROOT / "manifest.json"),
               "test_source_sha256": digest(Path(__file__)),
               "verifier_sha256": digest(ROOT / "verify_sources.py"),
               "detail": stream.getvalue(), "physical_validation": False}
    (ROOT / "tests.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result.wasSuccessful() else 1)
