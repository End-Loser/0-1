"""Independent offline evidence checks; never contacts manufacturers."""

import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent


def read_json(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def edge(rows, field, level, lo, hi, rising):
    pairs = zip(rows[:-1], rows[1:])
    hits = []
    for left, right in pairs:
        a, b = left[field], right[field]
        t0, t1 = left["time_s"], right["time_s"]
        if not lo <= t1 <= hi:
            continue
        if (a < level <= b) if rising else (a > level >= b):
            hits.append(t0 + (level - a) * (t1 - t0) / (b - a))
    if len(hits) != 1:
        raise AssertionError(f"Expected exactly one crossing, got {len(hits)}")
    return hits[0]


class EvidenceChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = read_json("source_manifest.json")
        cls.sources = {r["id"]: r for r in cls.manifest["sources"]}
        cls.model = read_json("model_inspection.json")
        cls.result = read_json("model_test_results.json")
        cls.runs = {r["id"]: r for r in cls.result["runs"]}
        cls.interfaces = read_json("interface_model_gaps.json")
        cls.waves = {}
        for run in cls.result["runs"]:
            if "wave" in run:
                with (ROOT / run["wave"]).open(encoding="utf-8", newline="") as stream:
                    cls.waves[run["id"]] = [
                        {key: float(value) for key, value in row.items()}
                        for row in csv.DictReader(stream)]

    def test_01_source_snapshots(self):
        self.assertEqual(len(self.sources), 12)
        for row in self.sources.values():
            self.assertEqual(row["result"], "retrieved")
            self.assertEqual(row["status"], 200)
            path = (ROOT / row["snapshot"]).resolve()
            self.assertTrue(path.is_relative_to(ROOT))
            self.assertEqual(sha(path), row["snapshot_sha256"])

    def test_02_official_urls(self):
        from urllib.parse import urlparse
        for row in self.sources.values():
            for key in ("url", "final_url"):
                self.assertIn(urlparse(row[key]).hostname, ("www.ti.com", "www.analog.com"))
            self.assertRegex(row["raw_sha256"], r"^[0-9a-f]{64}$")

    def test_03_hidden_model_links(self):
        links = self.sources["cmp_page"]["links"]
        for token in ("SBOMBU5", "SBOMBU6"):
            self.assertTrue(any(token in link["href"] for link in links))
        self.assertIn("calculator", (ROOT / self.sources["tx_page"]["snapshot"])
                      .read_text(encoding="utf-8").lower())

    def test_04_pdf_revisions(self):
        for source, revision in (("tx_pdf", "Rev. D"), ("rx_pdf", "Rev. C"),
                                 ("cmp_pdf", "SBOS321E")):
            text = (ROOT / self.sources[source]["snapshot"]).read_text(encoding="utf-8")
            self.assertIn(revision, text)
        rx = (ROOT / self.sources["rx_pdf"]["snapshot"]).read_text(encoding="utf-8")
        self.assertIn("11/2026", rx)

    def test_05_vendor_macro_identity(self):
        entry = next(r for r in self.sources["cmp_pspice_zip"]["entries"]
                     if r["name"] == "tlv3501.lib")
        self.assertTrue(entry["text_readable"])
        self.assertEqual(entry["sha256"], self.model["model_sha256"])
        self.assertEqual(entry["sha256"], self.result["model_sha256"])
        self.assertEqual(self.model["pin_order"], ["IN+", "IN-", "SHDN", "V+", "V-", "OUT"])
        self.assertTrue(self.model["unchanged"])
        self.assertFalse(self.model["redistributed"])
        self.assertIn("as is", self.model["header_notice"])

    def test_06_binary_resources_not_spice(self):
        tipd = self.sources["tipd_zip"]["entries"]
        self.assertEqual(len(tipd), 7)
        self.assertEqual(sum(r["name"].lower().endswith(".tsc") for r in tipd), 5)
        for row in self.sources["cmp_tina_zip"]["entries"]:
            self.assertTrue(row["header_hex"].startswith("4f425353"))
        self.assertIn("NO OTHER LICENSE", self.model["reference_design_notice"])

    def test_07_execution_and_scripts(self):
        self.assertEqual(len(self.runs), 7)
        self.assertEqual(self.result["counts"], {"pass": 6, "fail": 0, "blocked": 1})
        self.assertIn("ngspice-47", self.result["simulator_version"])
        self.assertEqual(self.result["script_sha256"], sha(ROOT / "run_model_checks.py"))
        self.assertEqual(self.manifest["collector_sha256"], sha(ROOT / "collect_evidence.py"))

    def test_08_raw_logs_and_wave_hashes(self):
        for row in self.runs.values():
            self.assertEqual(sha(ROOT / row["log"]), row["log_sha256"])
            if "wave" in row:
                self.assertEqual(sha(ROOT / row["wave"]), row["wave_sha256"])

    def test_09_native_blocker(self):
        row = self.runs["native_5V_20mV"]
        self.assertEqual(row["status"], "blocked")
        self.assertNotIn("wave", row)
        log = (ROOT / row["log"]).read_text(encoding="utf-8")
        self.assertIn("no such function 'if'", log)
        self.assertIn("model type mismatch", log)

    def test_10_wave_integrity_and_logic(self):
        for key, rows in self.waves.items():
            run = self.runs[key]
            self.assertEqual(len(rows), run["rows"])
            self.assertTrue(all(math.isfinite(x) for row in rows for x in row.values()))
            self.assertTrue(all(b["time_s"] > a["time_s"] for a, b in zip(rows, rows[1:])))
            self.assertGreater(rows[-1]["time_s"], 949e-9)
            if run["kind"] == "pulse":
                high = [r["out_V"] for r in rows if 260e-9 < r["time_s"] < 300e-9]
                low = [r["out_V"] for r in rows if 380e-9 < r["time_s"] < 410e-9]
                self.assertGreater(sum(high) / len(high), .7 * run["supply_V"])
                self.assertLess(sum(low) / len(low), .3 * run["supply_V"])

    def test_11_independent_delay_measurements(self):
        for key, rows in self.waves.items():
            run = self.runs[key]
            if run["kind"] != "pulse":
                continue
            adjusted = [dict(row, diff=row["inp_V"] - row["inn_V"]) for row in rows]
            for rising, in_window, out_window, metric in (
                (True, (195e-9, 210e-9), (200e-9, 230e-9), "rise_delay_s"),
                (False, (315e-9, 330e-9), (320e-9, 350e-9), "fall_delay_s")):
                tin = edge(adjusted, "diff", 0, *in_window, rising)
                tout = edge(rows, "out_V", run["supply_V"] / 2, *out_window, rising)
                self.assertAlmostEqual(tout - tin, run["measurements"][metric], delta=1e-15)

    def test_12_time_refinement(self):
        a = self.runs["ps_5V_20mV"]["measurements"]
        b = self.runs["ps_5V_refined"]["measurements"]
        for key in ("rise_delay_s", "fall_delay_s"):
            self.assertLess(abs(a[key] - b[key]), 20e-12)

    def test_13_load_dependence(self):
        a = self.runs["ps_5V_20mV"]["measurements"]
        b = self.runs["ps_5V_20pF"]["measurements"]
        self.assertGreater(b["rise_delay_s"] - a["rise_delay_s"], .5e-9)

    def test_14_low_overdrive_gap_preserved(self):
        self.assertTrue(self.model["fixed_delay_evidence"])
        a = self.runs["ps_5V_20mV"]["measurements"]["rise_delay_s"]
        b = self.runs["ps_5V_5mV"]["measurements"]["rise_delay_s"]
        self.assertLess(abs(a - b), 50e-12)
        expected = self.interfaces["candidates"][2]["interfaces"]["delay"]
        self.assertEqual(expected["5mV_overdrive"]["typ_25C_s"], 7.5e-9)
        self.assertGreater(abs(b - 7.5e-9), 2e-9)

    def test_15_shutdown_independent(self):
        rows = self.waves["ps_shutdown"]
        for lo, hi, high in ((400e-9, 450e-9, True), (640e-9, 680e-9, False),
                             (850e-9, 900e-9, True)):
            values = [r["out_V"] for r in rows if lo <= r["time_s"] <= hi]
            avg = sum(values) / len(values)
            if high:
                self.assertGreater(avg, 3.5)
            else:
                self.assertLess(avg, 1.5)

    def test_16_interface_blockers(self):
        candidates = self.interfaces["candidates"]
        self.assertEqual(len(candidates), 3)
        for candidate in candidates:
            self.assertTrue(candidate["gaps"])
            self.assertIn(candidate["datasheet"]["source_id"], self.sources)
        rx = candidates[1]["interfaces"]["baseband"]
        max_pin = rx["default_ADJ_open_common_mode_approx_V"] + rx["differential_swing_Vpp"] / 4
        self.assertAlmostEqual(max_pin, 3.7)
        self.assertGreater(max_pin, 3.3 + .2)
        self.assertLess(max_pin, 5 - .2)
        self.assertFalse(self.interfaces["candidate_freeze"])
        self.assertFalse(self.interfaces["full_joint_acceptance"])


class RecordingResult(unittest.TextTestResult):
    def startTest(self, test):
        super().startTest(test)
        self.names = getattr(self, "names", []) + [test.id()]


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(EvidenceChecks)
    result = unittest.TextTestRunner(verbosity=2, resultclass=RecordingResult,
                                    stream=sys.stdout).run(suite)
    output = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "offline provenance and typical macro checks, not real-device acceptance",
        "tests_run": result.testsRun,
        "passed": result.testsRun - len(result.failures) - len(result.errors),
        "failures": [(str(test), message) for test, message in result.failures],
        "errors": [(str(test), message) for test, message in result.errors],
        "test_names": result.names,
        "verifier_sha256": sha(Path(__file__)),
        "interface_table_sha256": sha(ROOT / "interface_model_gaps.json")}
    (ROOT / "offline_verification.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sys.exit(0 if result.wasSuccessful() else 1)
