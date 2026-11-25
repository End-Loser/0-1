"""Independent regulator controls; matched pairs do not prove a universal cause."""
import hashlib
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(run):
    path = ROOT / "results" / run["id"] / "wave.csv"
    with path.open(encoding="ascii") as stream:
        assert stream.readline().strip().split(",") == run["columns"]
    rows = np.loadtxt(path, skiprows=1, delimiter=",")
    assert rows.shape == (run["rows"], len(run["columns"]))
    assert np.isfinite(rows).all() and np.all(np.diff(rows[:, 0]) > 0)
    assert abs(rows[-1, 0] - run["finish_s"]) < 1e-12
    return rows


def audit():
    source = json.loads((ROOT / "summary.json").read_text(encoding="utf-8"))
    configs = json.loads((ROOT / "declared_configurations.json").read_text(encoding="utf-8"))
    assert len(configs) == len(source["runs"]) == 7
    assert len({r["id"] for r in source["runs"]}) == 7
    assert sha(ROOT / "run_regulator_controls.py") == source["runner_sha256"]
    assert sha(ROOT / "declared_configurations.json") == source["declared_configurations_sha256"]
    for group in ("original_files_sha256", "previous_evidence_sha256"):
        for path, digest in source[group].items():
            assert sha(Path(path)) == digest, path
    records, waves = [], {}
    for config, run in zip(configs, source["runs"]):
        assert all(run[k] == value for k, value in config.items())
        for path, digest in run["files_sha256"].items():
            assert sha(ROOT / path) == digest, path
        rows = read(run)
        waves[run["id"]] = rows
        t = rows[:, 0]
        window = (t >= run["finish_s"] - .0003) & (t <= run["finish_s"] - .0001)
        # The unchanged baseline uses 2 us steps; the other controls use 100 ns.
        assert window.sum() >= (50 if config["baseline"] else 100)
        error = float(abs(rows[window, 2] - run["target_V"]).max())
        assert abs(error - run["output_error_V"]) < 1e-12
        status = "pass" if error < .015 * run["target_V"] else "fail"
        assert status == run["status"]
        net = (ROOT / "results" / run["id"] / "test.cir").read_text(encoding="ascii")
        record = dict(id=run["id"], reproduced_status=status, output_error_V=error,
                      output_terminal_range_V=[float(rows[window, 2].min()), float(rows[window, 2].max())],
                      waveform_complete=True)
        if config["baseline"]:
            original = ROOT.parent / "\u516c\u5f00\u6a21\u578b\u8865\u9f50" / "supply_instrumented" / "supply_results" / "ldo_5V_50mA" / "test.cir"
            assert sha(ROOT / "results" / run["id"] / "test.cir") == sha(original)
            record["original_netlist_unchanged"] = True
        else:
            upstream_error = float(abs((run["vin_V"] - rows[:, 1]) / .05 + rows[:, 5]).max())
            resistance = 330 if run["target_V"] == 3.3 else 1000
            load_error = float(abs(rows[:, 6] - rows[:, 2] / resistance).max())
            assert upstream_error < 1e-6 and load_error < 1e-9
            assert ".ic" not in net.lower() and "uic" not in net.lower()
            # At a stationary external capacitor, this is a diagnostic, not a model edit.
            coupling = (rows[window, 10] - rows[window, 2]) / rows[window, 6]
            record.update(upstream_resistor_error_A=upstream_error, load_error_A=load_error,
                          internal_vout_terminal_range_V=[float(rows[window, 10].min()), float(rows[window, 10].max())],
                          discharge_control_terminal_range_V=[float(rows[window, 13].min()), float(rows[window, 13].max())],
                          inferred_stationary_output_coupling_ohm=[float(coupling.min()), float(coupling.max())],
                          coupling_scope="diagnostic_only_no_supported_solver_root_cause_claim")
        records.append(record)
    pairs = []
    for vin, target in ((5, 3.3), (6, 3.3), (6, 5)):
        first, second = f"direct_{vin:g}Vin_{target:g}Vout", f"scoped_{vin:g}Vin_{target:g}Vout"
        a, b = waves[first], waves[second]
        assert a.shape == b.shape
        difference = float(abs(a - b).max())
        pairs.append(dict(first=first, second=second, maximum_all_column_difference=difference,
                          identical_within_1e_12=difference < 1e-12,
                          scope="only_this_single_regulator_resistive_fixture"))
    result = dict(runs=records, scope_pairs=pairs, evidence_integrity_passed=True,
                  pass_count=sum(r["reproduced_status"] == "pass" for r in records),
                  scope_change_did_not_resolve_observed_failure=all(p["identical_within_1e_12"] for p in pairs),
                  root_cause_proven=False, supported_pspice_verified=False,
                  full_supply_acceptance_passed=False,
                  summary_sha256=sha(ROOT / "summary.json"), verifier_sha256=sha(Path(__file__)))
    (ROOT / "verification.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    for record in records:
        print(record["id"], record["reproduced_status"],
              record.get("inferred_stationary_output_coupling_ohm"))
    print("Scope pairs:", pairs)
    return result


if __name__ == "__main__":
    audit()
