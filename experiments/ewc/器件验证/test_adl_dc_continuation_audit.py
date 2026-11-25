"""Negative tests for completed coverage, fixed circuits and DC classification."""
from copy import deepcopy
import unittest

from run_adl_dc_holdout import BASE, configurations, deck
from verify_adl_dc_continuation import check_deck, classify, validate_boundary


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.configs = configurations()
        self.pending = self.configs[4:]
        self.stop = dict(completed_runs=[dict(id=c["id"]) for c in self.configs[:4]],
                         configurations_completed=4, next_configuration=self.configs[4],
                         no_solver_call_for_next_case=True)
        self.continuation = dict(original_completed_ids=[c["id"] for c in self.configs[:4]],
                                 only_never_called_ids=[c["id"] for c in self.pending],
                                 original_cases_reexecuted=False, library_or_bias_modified=False)

    def test_exact_boundary_and_all_24(self):
        validate_boundary(self.configs, self.pending, self.stop, self.continuation)
        for pending in (self.pending[:-1], self.pending[1:], self.configs):
            with self.assertRaises(ValueError):
                validate_boundary(self.configs, pending, self.stop, self.continuation)

    def test_rerun_or_modified_source_is_rejected(self):
        for name in ("original_cases_reexecuted", "library_or_bias_modified"):
            changed = deepcopy(self.continuation)
            changed[name] = True
            with self.assertRaises(ValueError):
                validate_boundary(self.configs, self.pending, self.stop, changed)

    def test_original_deck_all_24_and_tampering(self):
        baseline = (BASE / "probe_equivalence_10ps" / "test.cir").read_text(encoding="ascii")
        for config in self.configs:
            net = deck(baseline, config)
            check_deck(net, baseline, config)
        config = self.configs[4]
        net = deck(baseline, config)
        for changed in (net.replace("RCFEED cfeed im2 32750", "RCFEED cfeed im2 32751"),
                        net.replace("\nop\n", "\ntran 10p 1.8u\n"),
                        net.replace("VA a 0 0.3", "VA a 0 0.0"),
                        net.replace("\nop\n", "\noption reltol=1e-2\nop\n"),
                        net.replace("wrdata dc.txt ", "wrdata dc.txt v(extra) "),
                        net.replace("VA a 0 0.3", "VA a 0 0.3\nVA a 0 0.0")):
            with self.assertRaises(ValueError):
                check_deck(changed, baseline, config)

    def test_zero_exit_without_vector_is_not_success(self):
        record = dict(returncode=0, status="dc_operating_point_observed_unverified")
        self.assertEqual(classify(record, "", False), "no_valid_DC_vector")
        for log in ("Error: no such vector cp1", "op simulation(s) aborted",
                    "doAnalyses: OP: failed", "DC solution failed"):
            self.assertEqual(classify(record, log, True), "solver_aborted")
        self.assertEqual(classify(record, "No. of Data Rows : 1", True), "DC_observed")

    def test_unknown_exit_and_failed_record_are_not_success(self):
        record = dict(status="failed_preserve_output")
        self.assertEqual(classify(record, "", True), "timeout_or_nonzero_or_unknown_exit")
        record["returncode"] = 0
        self.assertEqual(classify(record, "", True), "record_not_valid_DC")


if __name__ == "__main__":
    unittest.main(verbosity=2)
