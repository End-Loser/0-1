"""Audit the existing 160x100x60 mm / [1,1,1] cavity design with measured numbers.

Three questions, each answered only by computed values:
  1. Is the [1,1,1] mode isolated from every other mode by more than 3 linewidths?
  2. Can the two polarizations of [1,1,1] be addressed as two independent channels?
  3. How much of a 1 % Q budget do 10 um geometry and 0.5 K temperature consume?

The mode table is analytic (closed-form PEC box), the mode frequencies are also
checked by the finite-integral eigen solver, and the channel conditioning is read
from the previously stored full-Maxwell 2x2 responses.
"""

import hashlib
import json
import time
from pathlib import Path

import numpy as np
from threadpoolctl import threadpool_limits

from copper_doublet import reference
from finite_copper_cavity import continuum_q
from finite_pec_cavity import C0, FinitePEC

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "腔体设计校核结果"
EVIDENCE = ROOT / "铜腔111双极化结果"
MODEL_PATH = ROOT / "厚铜边界基准结果" / "passive_surface_models.json"
LENGTHS = np.array([.16, .10, .06])
SHAPES = ((24, 15, 9), (36, 23, 14))
Q_LIMIT = .01


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def to_json(value):
    if isinstance(value, dict):
        return {key: to_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_json(item) for item in value]
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    return value


def mode_frequency(indices, lengths):
    return C0 / 2 * float(np.sqrt(np.sum((np.asarray(indices, float) / lengths) ** 2)))


def mode_table(lengths, low, high):
    rows = []
    for a in range(0, 8):
        for b in range(0, 8):
            for c in range(0, 8):
                indices = np.array([a, b, c])
                if np.count_nonzero(indices) < 2:
                    continue
                f = mode_frequency(indices, lengths)
                if low <= f <= high:
                    rows.append((f, tuple(indices)))
    rows.sort()
    return rows


def solve_fem(shape, indices, lengths):
    started = time.perf_counter()
    grid = FinitePEC(shape, lengths)
    eigenvalue, e = grid.solve_mode(indices)
    frequency = float(np.sqrt(eigenvalue) / (2 * np.pi))
    resid = float(np.linalg.norm(grid.stiffness @ e - eigenvalue * (grid.m * e))
                  / abs(eigenvalue) / np.linalg.norm(grid.m * e))
    return dict(shape=list(shape), indices=list(map(int, indices)),
                frequency_Hz=frequency, analytic_Hz=mode_frequency(indices, lengths),
                relative_error=abs(frequency / mode_frequency(indices, lengths) - 1),
                eigen_residual=resid, dof=int(grid.ne),
                seconds=time.perf_counter() - started)


def main():
    assert not OUT.exists(), "New evidence only; do not overwrite a prior audit"
    model = next(row for row in json.loads(MODEL_PATH.read_text(encoding="utf-8"))["models"]
                 if row["terms"] == 64)
    sigma = float(model["sigma_S_per_m"])
    ref = reference(LENGTHS, sigma)
    q = np.sort(np.asarray(ref["Q"], float))
    f0 = float(ref["frequency_Hz"])
    OUT.mkdir()
    report = dict(
        scope="existing 160x100x60 mm [1,1,1] design audit; mode isolation, "
              "doublet channel conditioning, 1 % budget consumption",
        lengths_m=LENGTHS.tolist(), sigma_S_per_m=sigma, terms=int(model["terms"]),
        reference_111=dict(frequency_Hz=f0, Q=q.tolist(),
                           linewidth_Hz=(f0 / q).tolist(),
                           decay_time_s=(2 * q / (2 * np.pi * f0)).tolist()),
        source_sha256={name: sha(ROOT / name) for name in (
            "run_design_audit.py", "copper_doublet.py", "finite_copper_cavity.py",
            "finite_pec_cavity.py")})

    # ---- 1. analytic mode table around the operating point -------------------
    table = mode_table(LENGTHS, .9 * f0, 1.15 * f0)
    linewidth = float(f0 / q.min())
    rows = []
    for index, (f, indices) in enumerate(table):
        gap = f - f0
        rows.append(dict(indices=list(indices), frequency_Hz=f,
                         offset_Hz=float(gap),
                         operating=bool(tuple(indices) == (1, 1, 1)),
                         offset_in_linewidths=float(abs(gap) / linewidth)))
    neighbours = sorted((r for r in rows if r["offset_Hz"] > 0 and not r["operating"]),
                        key=lambda r: r["offset_Hz"])[:4]
    report["mode_table"] = rows
    report["nearest_higher_modes"] = neighbours
    report["mode_isolation"] = dict(
        linewidth_Hz=linewidth,
        required_gap_3_linewidths_Hz=3 * linewidth,
        nearest_gap_Hz=neighbours[0]["offset_Hz"],
        margin_factor=neighbours[0]["offset_Hz"] / (3 * linewidth),
        passed=bool(neighbours[0]["offset_Hz"] > 3 * linewidth))

    # ---- 2. finite-integral eigen check on real meshes -----------------------
    checked = [((1, 1, 1), (2, 0, 1), (1, 2, 0), (0, 1, 1)), ((1, 1, 1),)]
    fem = []
    with threadpool_limits(limits=1):
        for shape, modes in zip(SHAPES, checked):
            for indices in modes:
                if any(i >= n for i, n in zip(indices, shape)):
                    continue
                fem.append(solve_fem(shape, indices, LENGTHS))
                print("fem", shape, indices, fem[-1]["frequency_Hz"], flush=True)
    report["fem_modes"] = fem
    report["fem_max_relative_error"] = float(max(r["relative_error"] for r in fem))
    report["fem_max_eigen_residual"] = float(max(r["eigen_residual"] for r in fem))

    # ---- 3. doublet channel conditioning from stored full-Maxwell responses --
    channels = []
    for shape in SHAPES:
        stem = "111_" + "x".join(map(str, shape))
        scan = np.load(EVIDENCE / (stem + "_scan.npz"))
        response = scan["response"]                       # (nfreq, 2, 2)
        frequency = scan["frequency_Hz"]
        peaks = np.load(EVIDENCE / (stem + "_peaks.npz"))
        basis = scan["drives_A"][:, :2]
        peak_response = np.stack([basis.T @ peaks["e_V"][i][:, :2] for i in range(2)])
        for label, f, r in [("scan", frequency, response),
                            ("peak", peaks["frequency_Hz"].real, peak_response)]:
            singular = np.linalg.svd(r, compute_uv=False)
            channels.append(dict(
                shape=list(shape), kind=label,
                frequency_Hz=np.asarray(f).tolist(),
                singular_values=singular.tolist(),
                condition_numbers=(singular[:, 0] / singular[:, -1]).tolist(),
                min_condition=float(np.min(singular[:, 0] / singular[:, -1]))))
    report["doublet_channels"] = channels

    # ---- 4. 1 % budget consumption ------------------------------------------
    budget = []
    for name, lengths in [
            ("nominal", LENGTHS),
            ("x_plus_10um", LENGTHS + [10e-6, 0, 0]),
            ("y_plus_10um", LENGTHS + [0, 10e-6, 0]),
            ("z_plus_10um", LENGTHS + [0, 0, 10e-6]),
            ("all_plus_10um", LENGTHS + 10e-6),
            ("thermal_0p5K", LENGTHS * (1 + 16.5e-6 * .5)),
            ("thermal_1K", LENGTHS * (1 + 16.5e-6 * 1.))]:
        perturbed = reference(lengths, sigma)
        pq = np.sort(np.asarray(perturbed["Q"], float))
        budget.append(dict(case=name, frequency_Hz=float(perturbed["frequency_Hz"]),
                           Q=pq.tolist(),
                           frequency_shift_Hz=float(perturbed["frequency_Hz"] - f0),
                           Q_relative_shift=(pq / q - 1).tolist()))
    for name, temperature in [("sigma_at_20p5C", 20.5), ("sigma_at_21C", 21.)]:
        shifted = sigma / (1 + .00393 * (temperature - 20.))
        perturbed = reference(LENGTHS, shifted)
        pq = np.sort(np.asarray(perturbed["Q"], float))
        budget.append(dict(case=name, sigma_S_per_m=shifted,
                           frequency_Hz=float(perturbed["frequency_Hz"]),
                           Q=pq.tolist(),
                           frequency_shift_Hz=float(perturbed["frequency_Hz"] - f0),
                           Q_relative_shift=(pq / q - 1).tolist()))
    report["budget"] = budget
    worst = max(float(np.max(np.abs(row["Q_relative_shift"]))) for row in budget
                if row["case"] != "nominal")
    thermal = next(row for row in budget if row["case"] == "thermal_1K")
    report["budget_summary"] = dict(
        worst_single_case_Q_shift=worst,
        worst_case=str(max((row for row in budget if row["case"] != "nominal"),
                           key=lambda r: float(np.max(np.abs(r["Q_relative_shift"]))))["case"]),
        thermal_frequency_shift_per_K_Hz=float(thermal["frequency_shift_Hz"]),
        limit=Q_LIMIT)

    (OUT / "summary.json").write_text(json.dumps(to_json(report), indent=2) + "\n",
                                      encoding="utf-8")
    print(json.dumps(to_json({k: report[k] for k in (
        "mode_isolation", "fem_max_relative_error", "fem_max_eigen_residual",
        "doublet_channels", "budget_summary")}), indent=2), flush=True)


if __name__ == "__main__":
    main()
