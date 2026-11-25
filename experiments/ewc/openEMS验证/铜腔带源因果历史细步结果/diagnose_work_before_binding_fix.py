"""Preserve strict work mismatch; alternate arithmetic is diagnostic only."""
import hashlib
import json
from pathlib import Path

import numpy as np
from threadpoolctl import threadpool_limits

from verify_copper_driven_history import gaussian_reference, seed_011, seed_basis, source_coefficients
from verify_copper_driven_state import check_step, equations_pass
from verify_finite_copper_cavity import geometry

ROOT = Path(__file__).resolve().parent
FOLDER = ROOT / "\u94dc\u8154\u5e26\u6e90\u56e0\u679c\u5386\u53f2\u7ec6\u6b65\u7ed3\u679c"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    summary_bytes = (FOLDER / "summary.json").read_bytes()
    summary = json.loads(summary_bytes)
    row = next(x for x in summary["cases"] if x["case"] == "mixed_history" and x["dt_s"] == 1e-12)
    path = FOLDER / row["file"]
    assert sha(path) == row["sha256"]
    modelpath = ROOT / "\u539a\u94dc\u8fb9\u754c\u57fa\u51c6\u7ed3\u679c/passive_surface_models.json"
    assert sha(modelpath) == summary["model_sha256"]
    model = next(x for x in json.loads(modelpath.read_bytes())["models"] if x["terms"] == 64)
    with np.load(path) as archive:
        data = {key: archive[key] for key in archive.files}
    m, _, _, _, _, _ = geometry(row["shape"], row["lengths_m"])
    reference = gaussian_reference(np.array(row["lengths_m"]), model["sigma_S_per_m"])
    seeds = np.column_stack((seed_011(row["shape"], row["lengths_m"], m),
        seed_basis(row["shape"], np.array(row["lengths_m"]), data["physical_loss_basis"], m)))
    freqs = np.array([299792458. / 2 * np.sqrt((1 / .10)**2 + (1 / .06)**2),
                     reference["frequency_Hz"], reference["frequency_Hz"]])
    records = []
    with threadpool_limits(limits=1):
        for index, step in enumerate(data["snapshot_step"]):
            before = tuple(data["snapshot_" + key][index] for key in ("e_before", "b_before", "i_before"))
            after = tuple(data["snapshot_" + key][index] for key in ("e_after", "b_after", "i_after"))
            coeff = source_coefficients((step + .5) * row["dt_s"], freqs, row["pulses"])
            source = m * (seeds @ coeff)
            metric = check_step(row["shape"], row["lengths_m"], row["dt_s"],
                               data["R_ohm"], data["L_H"], before, after,
                               data["snapshot_j_mid"][index], source)
            stored = float(data["per_step_source_work_J"][step])
            difference = abs(metric["source_work_J"] - stored)
            emid = (before[0] + after[0]) / 2
            recorded_source = m * (data["seeds"] @ data["snapshot_source_coefficients"][index])
            recorded_work = float(row["dt_s"] * (emid @ recorded_source))
            sum_abs = float(row["dt_s"] * np.sum(abs(emid * source)))
            records.append(dict(step=int(step), midpoint_time_s=(step + .5) * row["dt_s"],
                independent_work_J=metric["source_work_J"], stored_work_J=stored,
                absolute_difference_J=difference,
                relative_difference=difference / max(abs(stored), 1e-25),
                original_work_match=difference <= 1e-25 + 1e-11 * abs(stored),
                equations_passed=equations_pass(metric), metrics=metric,
                recorded_source_reconstructed_work_J=recorded_work,
                recorded_source_work_difference_J=abs(recorded_work - stored),
                sum_abs_edge_work_J=sum_abs,
                sum_abs_to_net_work_ratio=sum_abs / max(abs(stored), 1e-25),
                source_difference_norm=float(np.linalg.norm(source - recorded_source))))
    result = dict(scope="strict_source_work_failure_diagnosis_not_acceptance",
                  raw_file=row["file"], raw_sha256=row["sha256"],
                  summary_snapshot=summary, summary_read_sha256=hashlib.sha256(summary_bytes).hexdigest(),
                  model_sha256=summary["model_sha256"],
                  original_rtol=1e-11, original_atol_J=1e-25, thresholds_changed=False,
                  anchors=records, any_strict_work_mismatch=any(not x["original_work_match"] for x in records),
                  raw_1ps_case_approved=False, pair_approved=False,
                  physical_cause_proven=False, full_system_gates_closed=0,
                  diagnostic_source_sha256=sha(Path(__file__)))
    content = (json.dumps(result, indent=2) + "\n").encode("ascii")
    output = FOLDER / ("source_work_failure_" + hashlib.sha256(content).hexdigest()[:12] + ".json")
    if output.exists():
        assert output.read_bytes() == content
    else:
        output.write_bytes(content)
    print("Saved", output.name)
    for item in records:
        if not item["original_work_match"]:
            print(json.dumps(item, indent=2))


if __name__ == "__main__":
    main()
