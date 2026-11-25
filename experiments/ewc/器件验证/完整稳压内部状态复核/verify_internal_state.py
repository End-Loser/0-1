"""Independent full-LDO terminal KCL and observation-only equivalence checks."""
import hashlib
import json
from pathlib import Path
import re

import numpy as np

ROOT = Path(__file__).resolve().parent
PRIOR = ROOT.parent / "\u7a33\u538b\u4f5c\u7528\u57df\u5bf9\u7167"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def physical_lines(net):
    return net.split(".control\n")[0] + net.split(".endc\n")[1]


def metrics(data, columns, window):
    assert data.ndim == 2 and data.shape[1] == len(columns) and np.isfinite(data).all()
    assert np.all(np.diff(data[:, 0]) > 0)
    v = {key: data[:, n] for n, key in enumerate(columns)}
    mask = (v["time"] >= window[0]) & (v["time"] <= window[1])
    assert mask.sum() > 10
    switch_current = v["output_current"] + v["shunt_current"]
    # V2 and V3 leave the switch output terminal. V4 supplies the internal side.
    kcl_out = np.max(np.abs(v["output_current"] - v["i_load"] -
                            (v["out"] - v["cap"]) / .05))
    rbottom = 3e6 / (3.3 / .8 - 1)
    kcl_internal = np.max(np.abs(v["feedback_current"] - switch_current -
                                 v["vout_int"] / (3e6 + rbottom)))
    assert kcl_out < 1e-9 and kcl_internal < 1e-9
    assert np.max(np.abs(v["out"] - v["switch_terminal"])) < 1e-12
    assert np.all(np.abs(switch_current[mask]) > 1e-12)
    resistance = (v["vout_int"][mask] - v["switch_terminal"][mask]) / switch_current[mask]
    error = float(np.max(np.abs(v["out"][mask] - 3.3)))
    control = v["switch_control"][mask]
    expected_resistance = np.where(control < 0, 1e-6, 1e6)
    endpoint_error = float(np.max(np.abs(resistance / expected_resistance - 1)))
    assert endpoint_error < 1e-6
    return dict(window_rows=int(mask.sum()),
                last_time_s=float(v["time"][-1]),
                output_error_V=error, selected_output_passed=error < .015 * 3.3,
                output_KCL_max_A=float(kcl_out), internal_KCL_max_A=float(kcl_internal),
                control_min_V=float(control.min()), control_max_V=float(control.max()),
                exact_zero_control_rows=int(np.count_nonzero(control == 0)),
                negative_control_rows=int(np.count_nonzero(control < 0)),
                endpoint_by_control_sign_relative_error=endpoint_error,
                inferred_resistance_min_ohm=float(resistance.min()),
                inferred_resistance_max_ohm=float(resistance.max()),
                million_ohm_path_residual_A=float(np.max(np.abs(
                    switch_current[mask] -
                    (v["vout_int"][mask] - v["switch_terminal"][mask]) / 1e6))),
                window_output_min_V=float(v["out"][mask].min()),
                window_output_max_V=float(v["out"][mask].max()),
                window_internal_min_V=float(v["vout_int"][mask].min()),
                window_internal_max_V=float(v["vout_int"][mask].max()))


def audit():
    summary = json.loads((ROOT / "summary.json").read_bytes())
    configs = json.loads((ROOT / "declared_configurations.json").read_bytes())
    assert len(configs) == len(summary["runs"]) == 2
    assert sha(ROOT / "run_internal_state.py") == summary["runner_sha256"]
    assert sha(ROOT / "declared_configurations.json") == summary["declared_configurations_sha256"]
    for path, digest in summary["protected_files_sha256"].items():
        assert sha(Path(path)) == digest, path
    for field in ("vendor_library_edits", "full_power_passed",
                  "supported_pspice_verified", "full_joint_passed"):
        assert summary[field] is False
    records = []
    for config, run in zip(configs, summary["runs"]):
        assert all(run[key] == value for key, value in config.items())
        assert run["returncode"] == 0
        for path, digest in run["files_sha256"].items():
            assert sha(ROOT / path) == digest, path
        folder = ROOT / "results" / run["id"]
        base = PRIOR / "results" / run["id"]
        net = (folder / "test.cir").read_text(encoding="ascii")
        oldnet = (base / "test.cir").read_text(encoding="ascii")
        assert sha(base / "test.cir") == run["base_net_sha256"]
        assert physical_lines(net) == physical_lines(oldnet)
        assert re.findall(r"^tran .+$", net, re.M) == re.findall(r"^tran .+$", oldnet, re.M)
        assert ".ic " not in net and "uic" not in net.lower()
        assert (folder / "spiceinit.txt").read_text(encoding="ascii") == "set ngbehavior=ps\n"
        data = np.loadtxt(folder / "raw_wave.txt", skiprows=1, ndmin=2)
        assert abs(data[-1, 0] - config["finish_s"]) < 1e-12
        old = np.loadtxt(base / "wave.csv", delimiter=",", skiprows=1, ndmin=2)
        assert data[:, :old.shape[1]].shape == old.shape
        assert np.max(np.abs(data[:, :old.shape[1]] - old)) < 1e-10
        log = (folder / "solver.log").read_text(encoding="utf-8")
        assert "device       a.xu.x_s2.as_s2" in log
        assert re.search(r"a\.xu\.x_s2\.as_s2 %gd xu\.load_dis 0 %gd xu\.n15528889 xu\.vout_int", log)
        assert re.search(r"\.model xu\.x_s2:a_s2 pswitch\(\s*r_off=1e-6 r_on=1e6 cntl_off=0 cntl_on=1m log=true\)", log)
        record = metrics(data, run["columns"], config["window_s"])
        record.update(id=run["id"], original_columns_max_difference=float(
            np.max(np.abs(data[:, :old.shape[1]] - old))),
            matched_full_LDO_switch_instance=True,
            exported_switch_locdata_available=False,
            showmod_query_matched=False)
        records.append(record)
    failed = records[1]
    successful = records[0]
    assert successful["selected_output_passed"]
    assert 0 < successful["negative_control_rows"] < successful["window_rows"]
    assert max(abs(successful["control_min_V"]), abs(successful["control_max_V"])) < 1e-14
    assert not failed["selected_output_passed"]
    assert failed["exact_zero_control_rows"] == failed["window_rows"]
    assert failed["million_ohm_path_residual_A"] < 1e-9
    assert abs(failed["inferred_resistance_min_ohm"] / 1e6 - 1) < 1e-6
    output = dict(runs=records, evidence_integrity_passed=True,
                  observed_full_LDO_zero_control_high_resistance=True,
                  original_success_has_roundoff_scale_endpoint_switching=True,
                  scope="full_LDO_output_path_observation_not_repaired_supply_or_chip_validation",
                  complete_LDO_root_cause_proven=False, supported_pspice_verified=False,
                  full_power_passed=False, full_joint_passed=False,
                  summary_sha256=sha(ROOT / "summary.json"),
                  verifier_sha256=sha(Path(__file__)))
    (ROOT / "verification.json").write_text(json.dumps(output, indent=2) + "\n", encoding="ascii")
    print(json.dumps(output, indent=2))
    return output


if __name__ == "__main__":
    audit()
