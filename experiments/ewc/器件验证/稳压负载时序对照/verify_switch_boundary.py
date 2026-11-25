"""Independent point/KCL check and expanded-switch evidence, no vendor edits."""
import hashlib
import json
from pathlib import Path
import re

import numpy as np

ROOT = Path(__file__).resolve().parent
RESULT = ROOT / "switch_boundary"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit():
    summary = json.loads((RESULT / "summary.json").read_text(encoding="utf-8"))
    configs = json.loads((RESULT / "declared_configurations.json").read_text(encoding="utf-8"))
    assert len(configs) == len(summary["runs"]) == 5
    assert sha(ROOT / "run_switch_boundary.py") == summary["runner_sha256"]
    assert sha(RESULT / "declared_configurations.json") == summary["declared_configurations_sha256"]
    for group in ("original_files_sha256", "previous_evidence_sha256"):
        for path, digest in summary[group].items():
            assert sha(Path(path)) == digest, path
    records = []
    for config, run in zip(configs, summary["runs"]):
        assert all(run[key] == value for key, value in config.items())
        assert run["status"] == "observed" and run["returncode"] == 0
        for path, digest in run["files_sha256"].items():
            assert sha(RESULT / path) == digest, path
        folder = RESULT / run["id"]
        data = np.loadtxt(folder / "point.csv", skiprows=1, delimiter=",", ndmin=2)
        assert data.shape == (1, 4) and np.isfinite(data).all()
        _, control, voltage, supply_current = data[0]
        assert abs(control - run["control_V"]) < 1e-12
        assert abs(voltage - run["output_V"]) < 1e-12
        load_residual = float(abs(-supply_current - voltage / 1000))
        assert load_residual < 1e-9
        resistance = float((3.3 - voltage) / -supply_current)
        assert abs(resistance - run["inferred_switch_resistance_ohm"]) < 1e-8
        log = (folder / "solver.log").read_text(encoding="utf-8")
        assert re.search(r"\.model xs:a_s2 pswitch\(\s*r_off=1e-6 r_on=1e6 cntl_off=0 cntl_on=1m log=true\)", log)
        records.append(dict(id=run["id"], control_V=float(control), output_V=float(voltage),
                            inferred_switch_resistance_ohm=resistance, load_kcl_residual_A=load_residual,
                            expanded_model="XSPICE pswitch", simulator_default_temperature_C=27,
                            physical_device_or_supported_pspice_claim=False))
    zero = next(r for r in records if r["id"] == "zero")
    negative = next(r for r in records if r["id"] == "negative")
    output = dict(runs=records, evidence_integrity_passed=True,
                  zero_control_high_resistance_observed=zero["inferred_switch_resistance_ohm"] > 9e5,
                  negative_control_low_resistance_observed=negative["inferred_switch_resistance_ohm"] < 2e-6,
                  scope="isolated_switch_endpoint_in_ngspice47_ps_mode_not_full_LDO_root_cause",
                  supported_pspice_verified=False, full_power_passed=False, full_joint_passed=False,
                  summary_sha256=sha(RESULT / "summary.json"), verifier_sha256=sha(Path(__file__)))
    (RESULT / "verification.json").write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    for record in records:
        print(record["id"], record["control_V"], record["inferred_switch_resistance_ohm"])
    return output


if __name__ == "__main__":
    audit()
