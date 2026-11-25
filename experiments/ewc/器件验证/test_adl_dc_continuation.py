"""Reject a missing or inconsistent no-call boundary before DC continuation."""
from copy import deepcopy
import unittest

from continue_adl_dc_holdout import pending_configurations
from run_adl_dc_holdout import configurations


class ContinuationTests(unittest.TestCase):
    def setUp(self):
        self.configs = configurations()
        self.stop = dict(completed_runs=[dict(id=c["id"]) for c in self.configs[:4]],
                         configurations_completed=4, next_configuration=self.configs[4],
                         no_solver_call_for_next_case=True)

    def test_exact_remaining_twenty(self):
        pending = pending_configurations(self.configs, self.stop)
        self.assertEqual(pending, self.configs[4:])
        self.assertEqual(len(pending), 20)

    def test_prior_order_count_and_boundary_rejected(self):
        for field, value in (
            ("completed_runs", list(reversed(self.stop["completed_runs"]))),
            ("configurations_completed", 3),
            ("next_configuration", self.configs[5]),
            ("no_solver_call_for_next_case", False)):
            changed = deepcopy(self.stop)
            changed[field] = value
            with self.assertRaises(ValueError):
                pending_configurations(self.configs, changed)

    def test_duplicate_prior_runs_rejected(self):
        changed = deepcopy(self.stop)
        changed["completed_runs"][1] = changed["completed_runs"][0]
        with self.assertRaises(ValueError):
            pending_configurations(self.configs, changed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
