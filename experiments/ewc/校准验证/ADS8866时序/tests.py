import copy
import unittest

from model import generate, scenarios, validate
from oracle import check


class TimingTests(unittest.TestCase):
    def reject_both(self, trace):
        before = copy.deepcopy(trace)
        for validator in (validate, check):
            with self.assertRaises(ValueError):
                validator(trace)
            self.assertEqual(trace, before)

    def test_conversion_outside_min_max(self):
        for duration in (499_999, 8_800_001):
            self.reject_both(generate([0xA53C], conv=duration))

    def test_read_overlaps_next_conversion(self):
        self.reject_both(generate([1, 2], interval=10_000_000, period=100_000))

    def test_quiet_too_short(self):
        self.reject_both(generate([1, 2], interval=11_000_000, period=137_250))

    def test_duty_cycle(self):
        tr = generate([0xA53C])
        for e in tr["events"]:
            if (e["signal"] == "SCLK" and e["value"] == 1) or e["signal"] == "sample":
                e["t_ps"] += 10_000
        tr["events"].sort(key=lambda e: e["t_ps"])
        self.reject_both(tr)

    def test_output_enable_hold_valid_and_tristate(self):
        base = generate([0xA53C])
        for value in (1, "X", "Z"):
            for index, e in enumerate(base["events"]):
                if e["signal"] == "DOUT" and e["value"] == value:
                    tr = copy.deepcopy(base)
                    tr["events"][index]["t_ps"] += 1
                    tr["events"].sort(key=lambda e: e["t_ps"])
                    self.reject_both(tr)
                    break

    def test_each_bit_corruption(self):
        base = generate([0xA53C])
        for i, e in enumerate(base["events"]):
            if e["signal"] == "sample":
                tr = copy.deepcopy(base)
                tr["events"][i]["value"] ^= 1
                self.reject_both(tr)

    def test_missing_duplicate_clock(self):
        base = generate([0xA53C])
        index = next(i for i, e in enumerate(base["events"]) if e["signal"] == "SCLK")
        tr = copy.deepcopy(base)
        tr["events"].pop(index)
        self.reject_both(tr)
        tr = copy.deepcopy(base)
        tr["events"].insert(index, copy.deepcopy(tr["events"][index]))
        self.reject_both(tr)

    def test_CNV_short_pulse(self):
        tr = generate([1])
        next(e for e in tr["events"] if e["signal"] == "CNV" and e["value"] == 0)["t_ps"] = 1_209_999
        tr["events"].sort(key=lambda e: e["t_ps"])
        self.reject_both(tr)

    def test_wrong_mode_and_supply_temperature_load(self):
        base = generate([1])
        tr = copy.deepcopy(base)
        tr["mode"] = "daisy_chain"
        self.reject_both(tr)
        for key, value in (("AVDD_V", 3.3), ("VREF_V", 2.5),
                           ("temperature_C", 86), ("DOUT_load_pF", 21)):
            tr = copy.deepcopy(base)
            tr["supplies"][key] = value
            self.reject_both(tr)

    def test_time_and_completion(self):
        tr = generate([1])
        tr["events"][1]["t_ps"] = -1
        self.reject_both(tr)
        tr = generate([1])
        tr["events"].pop()
        self.reject_both(tr)

    def test_nominal_all_words_and_rates(self):
        for word in (0, 1, 0x8000, 0xA53C, 0xFFFF):
            for period in (40_000, 50_000, 60_000):
                tr = generate([word]*8, period=period)
                self.assertEqual(validate(tr), check(tr))


def add_case(case):
    def test(self):
        if case["expected_accept"]:
            self.assertEqual(validate(case["trace"]), check(case["trace"]))
        else:
            self.reject_both(case["trace"])
    setattr(TimingTests, "test_scenario_"+case["name"], test)


for case in scenarios():
    add_case(case)


if __name__ == "__main__":
    unittest.main(verbosity=2)
