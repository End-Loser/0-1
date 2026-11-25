"""Independent CSV/hash reconstruction; no runner imports or simulator required."""

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import sys
import unittest

SOURCE = Path(__file__).resolve().parent
ROOT = SOURCE
EXPECTED = "4e4345dc2929ee91a99a0b5437369c32b3817e9c8673a7ae90310d5485a81ba9"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def crossing(rows, column, level, up, start, stop):
    found = []
    for left, right in zip(rows, rows[1:]):
        if not start <= right[0] <= stop:
            continue
        a, b = column(left) - level, column(right) - level
        if (a < 0 <= b) if up else (a > 0 >= b):
            found.append(left[0] - a * (right[0] - left[0]) / (b - a))
    return found


def reconstruct(c, rows):
    events = []
    for ns, up in [(200, True), (320, False), (440, True), (560, False)]:
        start = ns * 1e-9
        ins = crossing(rows, lambda row: row[1] - row[2], 0, up,
                       start, start + c["ramp_s"] + 1e-9)
        outs = crossing(rows, lambda row: row[5], c["supply_V"] / 2, up,
                        start, start + 110e-9)
        stable = [row[5] for row in rows if start + 70e-9 <= row[0] <= start + 100e-9]
        low, high = min(stable), max(stable)
        delay = outs[0] - ins[0] if len(ins) == len(outs) == 1 else None
        level_ok = low > .7 * c["supply_V"] if up else high < .3 * c["supply_V"]
        events.append(dict(input_crossings_s=ins, output_crossings_s=outs, delay_s=delay,
                           plateau_min_V=low, plateau_max_V=high, level_ok=level_ok,
                           delay_ok=delay is not None and 0 < delay < 30e-9))
    pin_min = min(min(row[3], row[4]) for row in rows)
    pin_max = max(max(row[3], row[4]) for row in rows)
    guard = c["overdrive_V"] - .0095
    in_range = pin_min >= -.2 and pin_max <= c["supply_V"] - .2
    function = all(e["level_ok"] and e["delay_ok"] for e in events)
    return dict(events=events, pin_min_V=pin_min, pin_max_V=pin_max, range_ok=in_range,
                planning_margin_V=guard, function_pass=function,
                status="pass" if in_range and guard > 0 and function else "fail")


class EvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.summary = json.loads((ROOT / "summary.json").read_text(encoding="utf-8"))
        cls.runs = {r["id"]: r for r in cls.summary["runs"]}
        cls.rows, cls.check = {}, {}
        for key, run in cls.runs.items():
            path = ROOT / "results" / key / "wave.csv"
            if path.is_file():
                with path.open(newline="", encoding="ascii") as f:
                    reader = csv.reader(f)
                    if next(reader) != ["time_s", "raw_V", "threshold_V", "inp_V",
                                        "inn_V", "out_V", "supply_current_A"]:
                        raise ValueError("Unexpected CSV header")
                    cls.rows[key] = [tuple(map(float, row)) for row in reader]
                cls.check[key] = reconstruct(run, cls.rows[key])

    def test_01_configuration_and_actual_counts(self):
        s = self.summary
        self.assertEqual(len(self.runs), 11)
        self.assertEqual(s["counts"]["configurations"], 11)
        self.assertEqual(s["counts"]["solver_attempts"], sum(r["attempted"] for r in self.runs.values()))
        self.assertEqual(s["counts"]["datasets"], len(self.rows))
        for status in ("pass", "fail", "blocked"):
            self.assertEqual(s["counts"][status], sum(r["status"] == status for r in self.runs.values()))
        self.assertEqual(s["counts"]["function_pass"], sum(r["function_pass"] for r in self.check.values()))

    def test_02_all_evidence_hashes(self):
        for run in self.runs.values():
            for path, expected in run.get("files", {}).items():
                target = (ROOT / path).resolve()
                self.assertIn(ROOT, target.parents)
                self.assertEqual(sha(target), expected, path)

    def test_03_script_hashes(self):
        for name, expected in self.summary["scripts_sha256"].items():
            self.assertEqual(sha(SOURCE / name), expected, name)
        self.assertIn("run_screening.py", self.summary["scripts_sha256"])
        self.assertIn("verify_offline.py", self.summary["scripts_sha256"])

    def test_04_previous_batch_unchanged(self):
        self.assertTrue(self.summary["first_batch_sha256"])
        for path, expected in self.summary["first_batch_sha256"].items():
            self.assertEqual(sha(SOURCE.parent / path), expected, path)

    def test_05_original_model_identity_record(self):
        self.assertEqual(self.summary["expected_model_sha256"], EXPECTED)
        self.assertFalse(self.summary["model_redistributed"])
        if self.summary["counts"]["solver_attempts"]:
            self.assertEqual(self.summary["model_sha256_before"], EXPECTED)
            self.assertEqual(self.summary["model_sha256_after"], EXPECTED)
        else:
            self.assertTrue(self.summary["blockers"])

    def test_06_finite_strict_time_and_window(self):
        for key, rows in self.rows.items():
            self.assertTrue(all(len(r) == 7 and all(math.isfinite(v) for v in r) for r in rows))
            self.assertTrue(all(b[0] > a[0] for a, b in zip(rows, rows[1:])))
            self.assertLessEqual(rows[0][0], 1e-12)
            self.assertGreaterEqual(rows[-1][0], 800e-9 - 1e-12)
            self.assertEqual(len(rows), self.runs[key]["rows"])

    def test_07_ideal_threshold_and_differential_identity(self):
        for key, rows in self.rows.items():
            c = self.runs[key]
            for row in rows:
                self.assertAlmostEqual(row[2], c["threshold_V"], delta=1e-12)
                self.assertAlmostEqual(row[3] - row[4], row[1] - row[2], delta=1e-12)
                self.assertAlmostEqual((row[3] + row[4]) / 2, c["common_mode_V"], delta=1e-12)
            self.assertAlmostEqual(min(r[1] for r in rows), c["threshold_V"] - c["overdrive_V"], delta=1e-12)
            self.assertAlmostEqual(max(r[1] for r in rows), c["threshold_V"] + c["overdrive_V"], delta=1e-12)

    def test_08_independent_crossings_and_delays(self):
        for key, result in self.check.items():
            saved = self.runs[key]["measurements"]["events"]
            self.assertEqual(len(saved), 4)
            for got, old in zip(result["events"], saved):
                for name in ("input_crossings_s", "output_crossings_s"):
                    self.assertEqual(len(got[name]), len(old[name]))
                    for a, b in zip(got[name], old[name]):
                        self.assertAlmostEqual(a, b, delta=1e-15)
                if got["delay_s"] is None:
                    self.assertIsNone(old["delay_s"])
                else:
                    self.assertAlmostEqual(got["delay_s"], old["delay_s"], delta=1e-15)
                self.assertEqual(got["delay_ok"], old["delay_ok"])

    def test_09_independent_full_plateau_limits(self):
        for key, result in self.check.items():
            for got, old in zip(result["events"], self.runs[key]["measurements"]["events"]):
                for name in ("plateau_min_V", "plateau_max_V"):
                    self.assertAlmostEqual(got[name], old[name], delta=1e-10)
                self.assertEqual(got["level_ok"], old["level_ok"])

    def test_10_independent_range_margin_status(self):
        for key, result in self.check.items():
            old = self.runs[key]["measurements"]
            self.assertEqual(result["status"], self.runs[key]["status"])
            for name in ("pin_min_V", "pin_max_V", "planning_margin_V"):
                self.assertAlmostEqual(result[name], old[name], delta=1e-12)
            self.assertEqual(result["range_ok"], old["range_ok"])
            self.assertEqual(result["function_pass"], old["function_pass"])

    def test_11_supply_common_mode_threshold_coverage(self):
        self.assertEqual({r["supply_V"] for r in self.runs.values()}, {3.3, 5.0})
        self.assertGreaterEqual(len({r["common_mode_V"] for r in self.runs.values()}), 5)
        self.assertTrue(all(r["threshold_V"] != 0 for r in self.runs.values()))
        self.assertTrue(any(r["threshold_V"] < 0 for r in self.runs.values()))

    def test_12_conservative_common_mode_failure_preserved(self):
        r = self.runs["c06_3p3_cm_conflict"]
        if r["status"] == "blocked":
            self.assertTrue(r["reason"])
            return
        self.assertEqual(r["status"], "fail")
        self.assertFalse(self.check[r["id"]]["range_ok"])
        self.assertAlmostEqual(self.check[r["id"]]["pin_max_V"], 3.7, delta=1e-12)

    def test_13_low_overdrive_failure_preserved(self):
        r = self.runs["c07_3p3_2mV"]
        if r["status"] == "blocked":
            self.assertTrue(r["reason"])
            return
        self.assertEqual(r["status"], "fail")
        self.assertLess(self.check[r["id"]]["planning_margin_V"], 0)

    def test_14_heavy_load_failure_preserved(self):
        r = self.runs["c09_3p3_1000pF"]
        if r["status"] == "blocked":
            self.assertTrue(r["reason"])
            return
        self.assertEqual(r["status"], "fail")
        self.assertFalse(all(e["delay_ok"] for e in self.check[r["id"]]["events"]))

    def test_15_real_load_effect(self):
        if all(k in self.check for k in ("c01_3p3_mid", "c08_3p3_20pF")):
            a = self.check["c01_3p3_mid"]["events"][0]["delay_s"]
            b = self.check["c08_3p3_20pF"]["events"][0]["delay_s"]
            self.assertIsNotNone(a)
            self.assertIsNotNone(b)
            self.assertGreater(b - a, .5e-9)

    def test_16_timestep_refinement(self):
        if "refinement" not in self.summary:
            self.assertGreater(self.summary["counts"]["blocked"], 0)
            return
        a, b = self.check["c01_3p3_mid"], self.check["c11_3p3_refined"]
        differences = [abs(x["delay_s"] - y["delay_s"]) for x, y in zip(a["events"], b["events"])]
        self.assertTrue(max(differences) < 20e-12)
        self.assertTrue(self.summary["refinement"]["pass_check"])
        for got, old in zip(differences, self.summary["refinement"]["event_differences_s"]):
            self.assertAlmostEqual(got, old, delta=1e-15)

    def test_17_slow_slew_measured_input_not_schedule(self):
        key = "c10_3p3_slow"
        if key in self.check:
            for event in self.check[key]["events"]:
                self.assertAlmostEqual(event["input_crossings_s"][0],
                                       self.runs[key]["measurements"]["events"][
                                           self.check[key]["events"].index(event)]["start_s"] + 10e-9,
                                       delta=1e-15)
        self.assertEqual(self.runs[key]["ramp_s"], 20e-9)

    def test_18_model_scope_and_no_system_claim(self):
        self.assertEqual(self.summary["temperature_C"], 25)
        self.assertTrue(self.summary["ideal_conditioner"])
        self.assertEqual(self.summary["scope"], "typical_macro_interface_screen_only")
        for field in ("rf_model_simulations", "physical_tests", "procurement"):
            self.assertEqual(self.summary[field], 0)
        self.assertGreaterEqual(len(self.summary["limitations"]), 6)

    def test_19_no_redistributed_model_and_solver_init(self):
        self.assertFalse(list(ROOT.rglob("*.lib")))
        for r in self.runs.values():
            if not r["attempted"]:
                continue
            base = ROOT / "results" / r["id"]
            deck = (base / "test.cir").read_text(encoding="ascii")
            self.assertIn('.include "', deck)
            self.assertNotIn(".SUBCKT", deck.upper())
            self.assertIn("XDUT inp inn shdn vcc 0 out TLV3501", deck)
            self.assertIn("NOT ADL5380", deck)
            self.assertEqual((base / "spiceinit.txt").read_text(encoding="ascii"), "set ngbehavior=ps\n")

    def test_20_crossing_helper_and_hash_tamper_sensitivity(self):
        rows = [(0., -1.), (1., 1.), (2., -1.)]
        self.assertEqual(crossing(rows, lambda r: r[1], 0, True, 0, 2), [.5])
        self.assertEqual(crossing(rows, lambda r: r[1], 0, False, 0, 2), [1.5])
        self.assertNotEqual(hashlib.sha256(b"wave").hexdigest(),
                            hashlib.sha256(b"wavE").hexdigest())


def main():
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(EvidenceTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    verification = dict(scope="independent_offline_CSV_and_hash_reconstruction_not_resimulation",
                        summary_sha256=sha(ROOT / "summary.json"),
                        verifier_sha256=sha(Path(__file__)),
                        tests_run=result.testsRun, failures=len(result.failures),
                        errors=len(result.errors), passed=result.wasSuccessful(),
                        datasets_reconstructed=len(getattr(EvidenceTests, "check", {})),
                        events_reconstructed=4 * len(getattr(EvidenceTests, "check", {})),
                        independent_measurements=getattr(EvidenceTests, "check", {}),
                        failure_details=[str(test) + ": " + detail
                                         for test, detail in result.failures + result.errors])
    (ROOT / "verification.json").write_text(
        json.dumps(verification, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=SOURCE)
    args = parser.parse_args()
    ROOT = args.input.resolve()
    if ROOT != SOURCE and SOURCE not in ROOT.parents:
        parser.error("--input must stay inside the second-batch directory")
    sys.exit(main())
