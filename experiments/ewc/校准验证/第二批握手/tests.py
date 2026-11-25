"""Focused regression tests. Child assertions are not extra test counts."""

import copy
from dataclasses import FrozenInstanceError, asdict, replace
import hashlib
import json
import math
from pathlib import Path
import tempfile
import unittest

from model import Config, Controller, TIMES, budget, nominal
from runner import counterexamples, fault_recoveries
from verify import independent_budget, initial, oracle, verify

RESULTS = None


class BudgetTests(unittest.TestCase):
    def test_first_batch_alignment(self):
        for p, expected in ((Config(), 161200), (Config(m=4), 439600),
                            (Config(m=16), 1553200), (Config(d=2), 346800)):
            with self.subTest(p=p):
                self.assertEqual(budget(p)["pause_ns"], expected)
                self.assertEqual(independent_budget(asdict(p))[0], expected)

    def test_eight_full_slots(self):
        p = replace(Config(), **dict.fromkeys(TIMES, 0))
        self.assertEqual(budget(p)["pause_ns"], 80000)
        self.assertEqual(Controller(p).config.slot_ns, 10000)

    def test_discards_count(self):
        b = budget(Config(m=4, d=2))
        self.assertEqual((b["conversions"], b["retained"], b["discarded"]), (48, 32, 16))

    def test_multiple_dac_updates(self):
        self.assertEqual(budget(Config(updates=3))["pause_ns"], 182000)

    def test_rate_rounds_up(self):
        self.assertEqual(Config(sample_rate_hz=33333).slot_ns, 30001)

    def test_slow_hardware_expands_budget(self):
        self.assertEqual(budget(Config(adc_ready_ns=20000))["pause_ns"], 241200)

    def test_unknown_inputs_block(self):
        for key in TIMES:
            p = replace(Config(), **{key: None})
            with self.subTest(key=key):
                self.assertIn(key, budget(p)["missing"])
                with self.assertRaises(ValueError):
                    Controller(p)

    def test_unknown_lower_bounds(self):
        p = replace(Config(), **dict.fromkeys(TIMES))
        self.assertEqual(budget(p)["pause_ns"], 80000)
        self.assertEqual(budget(replace(p, dac_settle_ns=5000))["pause_ns"], 85000)

    def test_invalid_configuration(self):
        for field, value in (("m", 0), ("d", -1), ("updates", 0),
                             ("sample_rate_hz", 100001), ("sample_rate_hz", True),
                             ("adc_ready_ns", float("nan")), ("dac_spi_ns", -1),
                             ("m", 1.5), ("m", 10001)):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                replace(Config(), **{field: value})

    def test_config_is_frozen(self):
        p = Config()
        with self.assertRaises(FrozenInstanceError):
            p.m = 2

    def test_one_percent_active_interval(self):
        self.assertEqual(budget(Config())["active_for_one_percent_ns"], 15958800)


class ProtocolTests(unittest.TestCase):
    def test_nominal_and_oracle(self):
        for p in (Config(), Config(m=4), Config(m=16), Config(d=2),
                  Config(m=4, d=2, updates=3), Config(sample_rate_hz=33333)):
            ctl, s = Controller(p), initial(asdict(p))
            for e in nominal(p):
                accepted, s = oracle(asdict(p), s, e)
                self.assertTrue(accepted)
                self.assertEqual(ctl.step(e), s)
            self.assertEqual(s["now"], budget(p)["pause_ns"])

    def test_kept_samples_are_per_point(self):
        ctl = Controller(Config(m=3, d=2))
        for e in nominal(ctl.config):
            ctl.step(e)
        self.assertEqual(ctl.s["kept"], [[1000 * i + j for j in (2, 3, 4)] for i in range(8)])
        self.assertEqual(ctl.s["discarded"], 16)

    def test_all_counterexamples_match_oracle(self):
        for row in counterexamples():
            with self.subTest(id=row["id"]):
                accepted, after = oracle(asdict(Config()), row["before"], row["event"])
                self.assertFalse(accepted)
                self.assertEqual(after, row["after"])

    def test_fault_recovery_all_phases(self):
        for row in fault_recoveries():
            s = initial(asdict(Config()))
            for step in row["trace"]:
                accepted, s = oracle(asdict(Config()), s, step["event"])
                self.assertTrue(accepted)
                self.assertEqual(s, step["after"])

    def test_two_cycles_and_freeze(self):
        p = Config(updates=3)
        ctl = Controller(p)
        for cycle in range(2):
            before = ctl.snapshot()
            for e in nominal(p, before["now"], before["token"]):
                after = ctl.step(e)
                if e["event"] != "resume" and e["event"] != "data":
                    self.assertEqual(after["parameters"], before["parameters"])
                    self.assertEqual(after["version"], before["version"])
            self.assertEqual(ctl.s["version"], cycle + 2)

    def test_snapshot_has_no_alias(self):
        ctl = Controller(Config())
        snapshot = ctl.snapshot()
        snapshot["parameters"][0] = 555
        snapshot["kept"][0].append(1)
        self.assertEqual(ctl.snapshot()["parameters"], [0])
        self.assertEqual(ctl.snapshot()["kept"][0], [])

    def test_nonfinite_time_and_read(self):
        ctl = Controller(Config())
        for value in (float("nan"), float("inf"), 0.0, True, "0", None):
            with self.subTest(value=value):
                before = ctl.snapshot()
                with self.assertRaises(ValueError):
                    ctl.step(dict(event="request", time_ns=value))
                self.assertEqual(ctl.snapshot(), before)

    def test_stale_token_after_reset_at_same_point(self):
        p = Config()
        ctl = Controller(p)
        events = nominal(p)
        req = next(i for i, e in enumerate(events) if e["event"] == "adc_request")
        for e in events[:req + 1]:
            ctl.step(e)
        t = ctl.s["now"]
        ctl.step(dict(event="fault", time_ns=t))
        recovery = nominal(p, t, ctl.s["token"])
        recovery[0]["event"] = "reset"
        for e in recovery[:req]:
            ctl.step(e)
        before = ctl.snapshot()
        with self.assertRaises(ValueError):
            ctl.step(dict(recovery[req], token=1))
        self.assertEqual(before, ctl.snapshot())

    def test_dac_settle_starts_after_spi(self):
        p = Config()
        events = nominal(p)
        index = next(i for i, e in enumerate(events) if e["event"] == "dac_done")
        ctl = Controller(p)
        for e in events[:index + 1]:
            ctl.step(e)
        self.assertEqual(ctl.s["due"], events[index]["time_ns"] + p.dac_settle_ns + p.gain_settle_ns)

    def test_first_conversion_cannot_skip_slot(self):
        ctl = Controller(Config())
        for e in nominal(ctl.config):
            ctl.step(e)
            if e["event"] == "adc_request":
                with self.assertRaises(ValueError):
                    ctl.step(dict(event="adc_done", time_ns=e["time_ns"] + 9999, point=0, token=1))
                break

    def test_long_spi_and_delayed_events(self):
        p = Config(adc_spi_ns=100000, dac_spi_ns=40000)
        events = nominal(p)
        index = next(i for i, e in enumerate(events) if e["event"] == "adc_done")
        for e in events[index:]:
            e["time_ns"] += 12345
        ctl = Controller(p)
        for e in events:
            ctl.step(e)
        self.assertEqual(ctl.s["now"], budget(p)["pause_ns"] + 12345)

    def test_fault_idempotent_and_no_direct_run(self):
        ctl = Controller(Config())
        ctl.step(dict(event="fault", time_ns=0))
        ctl.step(dict(event="fault", time_ns=0))
        with self.assertRaises(ValueError):
            ctl.step(dict(event="data", time_ns=0, input=0))
        with self.assertRaises(ValueError):
            ctl.step(dict(event="resume", time_ns=0))


class EvidenceTests(unittest.TestCase):
    def test_archive_verifies(self):
        self.assertTrue(verify(RESULTS, Path(__file__).parent)["passed"])

    def corrupt(self, change, rehash=True):
        with tempfile.TemporaryDirectory(prefix="ewc-handshake-tamper-") as temp:
            dest = Path(temp)
            for name in ("evidence.json", "summary.json", "provenance.json", "manifest.json"):
                (dest / name).write_bytes((Path(RESULTS) / name).read_bytes())
            evidence = json.loads((dest / "evidence.json").read_text(encoding="utf-8"))
            change(evidence)
            (dest / "evidence.json").write_text(json.dumps(evidence), encoding="utf-8")
            if rehash:
                manifest = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
                manifest["artifacts"]["evidence.json"] = hashlib.sha256((dest / "evidence.json").read_bytes()).hexdigest()
                (dest / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(AssertionError):
                verify(dest)

    def test_self_consistent_hash_budget_tamper(self):
        self.corrupt(lambda e: e["cases"][0]["budget"].update(pause_ns=1))

    def test_self_consistent_hash_sample_tamper(self):
        self.corrupt(lambda e: e["cases"][0]["trace"][-1]["after"]["kept"][0].append(99))

    def test_self_consistent_hash_hardware_claim_tamper(self):
        self.corrupt(lambda e: e["claims"].update(rtl_passed=True))

    def test_self_consistent_hash_rejection_tamper(self):
        self.corrupt(lambda e: e["counterexamples"][0].update(rejected=False))

    def test_raw_hash_tamper(self):
        self.corrupt(lambda e: e.update(unused="modified"), rehash=False)


if __name__ == "__main__":
    raise SystemExit("Use run_all.py so evidence lives in OS TEMP.")
