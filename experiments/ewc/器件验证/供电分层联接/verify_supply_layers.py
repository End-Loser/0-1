"""Independent CSV/node equations; interface success is not system acceptance."""
import hashlib
import json
from pathlib import Path
import re

import numpy as np
from scipy.integrate import cumulative_trapezoid

ROOT = Path(__file__).resolve().parent
CURRENT_LIMIT_A = 1e-6
CHARGE_LIMIT = .01


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def component(net, name):
    match = re.search(rf"^{re.escape(name)}\s+\S+\s+\S+\s+(\S+)\s*$", net, re.M | re.I)
    assert match, name
    token = match[1].lower()
    number = re.fullmatch(r"([+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?)(meg|[unpfmk])?", token)
    assert number, token
    return float(number[1]) * {None: 1, "meg": 1e6, "m": 1e-3, "k": 1e3,
                              "u": 1e-6, "n": 1e-9, "p": 1e-12, "f": 1e-15}[number[2]]


def capacitor(time, voltage, current, cap, window):
    residual = current - cap * np.gradient(voltage, time, edge_order=2)
    integral = cumulative_trapezoid(current, time, initial=0)
    charge_error = integral - cap * (voltage - voltage[0])
    scale = max(cap * float(np.max(np.abs(voltage))), 1e-30)
    window_error = float(np.max(np.abs(residual[window])))
    relative = float(np.max(np.abs(charge_error))) / scale
    return dict(window_kcl_A=window_error, charge_relative_error=relative,
                accounting_passed=window_error < CURRENT_LIMIT_A and relative < CHARGE_LIMIT,
                initial_energy_J=float(.5 * cap * voltage[0] ** 2),
                final_energy_J=float(.5 * cap * voltage[-1] ** 2),
                energy_change_J=float(.5 * cap * (voltage[-1] ** 2 - voltage[0] ** 2)),
                charge_scale_C=scale)


def buffer_range(values, window):
    supply = values["r3"]
    pins = [values[f"{node}{i}"] for i in range(1, 4) for node in ("b", "o")]
    recommended = (supply >= 1.8) & (supply <= 5.5)
    absolute = (supply >= 0) & (supply <= 6)
    for pin in pins:
        recommended &= (pin >= -.1) & (pin <= supply + .1)
        absolute &= (pin >= -.5) & (pin <= supply + .5)
    return dict(datasheet_url="https://www.ti.com/lit/gpn/opa320-q1",
                datasheet_revision="SLOS884B", pages=[4, 5],
                checked_scope="buffer_supply_and_input_pins_only_not_all_ratings",
                supply_min_V=float(supply.min()), supply_max_V=float(supply.max()),
                terminal_supply_min_V=float(supply[window].min()),
                recommended_all_samples=bool(recommended.all()),
                recommended_terminal_window=bool(recommended[window].all()),
                checked_absolute_limits_all_samples=bool(absolute.all()),
                checked_absolute_limits_terminal_window=bool(absolute[window].all()),
                invalid_recommended_samples=int((~recommended).sum()),
                real_chip_failure_inferred=False)


def recalculate(run):
    folder = ROOT / "results" / run["id"]
    net = (folder / "test.cir").read_text(encoding="ascii")
    path = folder / "wave.csv"
    with path.open(encoding="ascii") as stream:
        header = stream.readline().strip().split(",")
    assert header == run["columns"]
    data = np.loadtxt(path, delimiter=",", skiprows=1)
    assert data.ndim == 2 and data.shape == (run["rows"], len(header))
    assert np.isfinite(data).all() and np.all(np.diff(data[:, 0]) > 0)
    v = dict(zip(header, data.T))
    t = v["time"]
    assert t[0] >= 0 and t[-1] >= run["finish_s"] - 1e-12
    assert t[-1] <= run["finish_s"] + 1e-12
    window = (t >= run["finish_s"] - .0003) & (t <= run["finish_s"] - .0001)
    assert window.sum() > 100
    targets = {"r5": (5, .075), "r3": (3.3, .0495)}
    if run["reference"]:
        targets["vref"] = (5, .005)
    if run["buffers"]:
        targets.update(o1=(1.65, .005), o2=(1.6, .001), o3=(1.7, .001))
    errors = {node: float(abs(v[node][window] - target).max())
              for node, (target, _) in targets.items()}
    checks = {{"r5": "rail5", "r3": "rail3", "vref": "reference"}.get(node, node):
              errors[node] < limit for node, (_, limit) in targets.items()}
    checks["headroom"] = bool(v["vin"][window].min() > 5.3)
    if run["buffers"]:
        errors["threshold"] = float(abs(v["o3"][window] - v["o2"][window] - .1).max())
        checks["threshold"] = errors["threshold"] < .002
    if run["enable_cycle"]:
        off = (t >= .0045) & (t <= .0049)
        assert off.sum() > 100
        for suffix in ("5", "3"):
            errors[f"off{suffix}"] = float(abs(v[f"r{suffix}"][off]).max())
            checks[f"off{suffix}"] = errors[f"off{suffix}"] < .05
    assert checks == run["measurements"]["checks"]
    assert errors.keys() == run["measurements"]["errors_V"].keys()
    assert all(abs(value - run["measurements"]["errors_V"][node]) < 1e-12
               for node, value in errors.items())
    interface = all(checks.values())
    assert run["status"] == ("pass" if interface else "fail")
    cap_records, algebra = {}, {}
    for suffix in ("5", "3"):
        current = (v[f"r{suffix}"] - v[f"c{suffix}"]) / component(net, f"RESR{suffix}")
        load = v[f"r{suffix}"] / component(net, f"RLD{suffix}")
        if run["load_step"]:
            ctl = np.interp(t, [0, .001, .001002, .002], [0, 0, 1, 1])
            load *= 1 + ctl
        algebra[f"load{suffix}"] = float(abs(v[f"i_load{suffix}"] - load).max())
        rail_load = v[f"i_load{suffix}"].copy()
        if suffix == "3" and run["buffers"]:
            rail_load += sum(v[f"i_brail{i}"] for i in range(1, 4))
        algebra[f"rail{suffix}"] = float(abs(v[f"i_reg{suffix}out"] - rail_load - current).max())
        cap_records[f"c{suffix}"] = capacitor(t, v[f"c{suffix}"], current,
                                             component(net, f"C{suffix}"), window)
    upstream_cap_current = -v["i_up"] - v["i_reg5in"] - v["i_reg3in"]
    if run["reference"]:
        upstream_cap_current -= v["i_refin"]
    cap_records["cin"] = capacitor(t, v["vin"], upstream_cap_current, component(net, "CIN"), window)
    algebra["upstream_resistor"] = float(abs(
        (v["upstream"] - v["vin"]) / component(net, "RS") + v["i_up"]).max())
    if run["reference"]:
        reference_cap_current = (v["vref"] - v["cref"]) / component(net, "RREFESR")
        if run["buffers"]:
            ref_load = sum((v["vref"] - v[f"b{i}"]) / component(net, f"RDT{i}")
                           for i in range(1, 4))
        else:
            ref_load = v["vref"] / component(net, "RREFLOAD")
        algebra["reference"] = float(abs(v["i_refout"] - ref_load - reference_cap_current).max())
        cap_records["cref"] = capacitor(t, v["cref"], reference_cap_current,
                                        component(net, "CREF"), window)
    if run["buffers"]:
        for i in range(1, 4):
            input_current = ((v["vref"] - v[f"b{i}"]) / component(net, f"RDT{i}")
                             - v[f"b{i}"] / component(net, f"RDB{i}") - v[f"i_bin{i}"])
            if i == 1:
                load = v["o1"] / component(net, "RCM")
            else:
                source, resistor = ("srcp", "RBACKP") if i == 2 else ("srcn", "RBACKN")
                load = (v[f"o{i}"] - v[source]) / component(net, resistor)
            output_current = v[f"i_bout{i}"] - v[f"i_fb{i}"] - load
            cap_records[f"bin{i}"] = capacitor(t, v[f"b{i}"], input_current,
                                               component(net, f"CBIN{i}"), window)
            cap_records[f"bout{i}"] = capacitor(t, v[f"o{i}"], output_current,
                                                component(net, f"CBOUT{i}"), window)
    energy = {"upstream_source_J": float(np.trapz(-v["upstream"] * v["i_up"], t))}
    if run["ideal_rails"]:
        for suffix in ("5", "3"):
            energy[f"ideal_rail{suffix}_source_J"] = float(
                np.trapz(v[f"r{suffix}"] * v[f"i_reg{suffix}out"], t))
    return dict(id=run["id"], waveform_complete=True, interface_passed=interface,
                checks=checks, errors_V=errors,
                terminal_node_ranges_V={node: [float(v[node][window].min()), float(v[node][window].max())]
                                        for node in targets},
                capacitors=cap_records, algebraic_max_residual_A=algebra,
                algebraic_passed=all(value < CURRENT_LIMIT_A for value in algebra.values()),
                capacitor_accounting_passed=all(r["accounting_passed"] for r in cap_records.values()),
                modeled_source_energy=energy,
                energy_scope="individual_sources_not_summed; ideal_signal_sources_not_measured; no_full_power_claim",
                buffer_range=buffer_range(v, window) if run["buffers"] else None)


def audit():
    summary = json.loads((ROOT / "summary.json").read_text(encoding="utf-8"))
    configs = json.loads((ROOT / "declared_configurations.json").read_text(encoding="utf-8"))
    assert len(configs) == len(summary["runs"]) == 9
    assert len({c["id"] for c in configs}) == 9
    assert sha(ROOT / "declared_configurations.json") == summary["declared_configurations_sha256"]
    assert sha(ROOT / "run_supply_layers.py") == summary["runner_sha256"]
    for group in ("original_files_sha256", "previous_evidence_sha256"):
        for path, digest in summary[group].items():
            assert sha(Path(path)) == digest, path
    for config, run in zip(configs, summary["runs"]):
        assert all(run[key] == value for key, value in config.items())
        for path, digest in run["files"].items():
            assert sha(ROOT / path) == digest, path
    records = [recalculate(run) for run in summary["runs"]]
    result = dict(scope="independent_supply_fixture_audit_not_system_acceptance",
                  runs=records, evidence_integrity_passed=True,
                  interface_pass_count=sum(r["interface_passed"] for r in records),
                  full_G4_G5_passed=False, startup_safety_passed=False,
                  full_power_passed=False, full_joint_passed=False,
                  limits=dict(window_kcl_A=CURRENT_LIMIT_A, charge_relative_error=CHARGE_LIMIT,
                              charge_normalization="C_times_max_absolute_node_voltage"),
                  summary_sha256=sha(ROOT / "summary.json"),
                  verifier_sha256=sha(Path(__file__)))
    (ROOT / "verification.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    for record in records:
        print(record["id"], "interface", record["interface_passed"],
              "algebra", record["algebraic_passed"], "capacitors", record["capacitor_accounting_passed"],
              "buffer_range", record["buffer_range"] and record["buffer_range"]["recommended_terminal_window"])
    return result


if __name__ == "__main__":
    audit()
