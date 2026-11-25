"""Finite-width sources, fixed physical interfaces, and FDTD convergence tests.

All field advancement uses the unmodified flaport/fdtd package.
"""

import argparse
import json
from pathlib import Path
import time

import fdtd
import numpy as np
from scipy.optimize import brentq

from run_fdtd import COURANT, F0, OP, write_csv


def simulate(dx_mm, amplitudes, phases=(0, 0, 0), time_refinement=1,
             frequencies=(F0, F0, F0), epsilon_r=2.1):
    dx = dx_mm * 1e-3
    nx, ny = round(160 / dx_mm) + 1, round(100 / dx_mm) + 1
    x, y = np.arange(nx) * dx_mm, np.arange(ny) * dx_mm
    # Arithmetic averaging is appropriate for Ez tangential to these xy interfaces.
    fx = np.clip((np.minimum(x + dx_mm / 2, 100)
                  - np.maximum(x - dx_mm / 2, 60)) / dx_mm, 0, 1)
    fy = np.clip((np.minimum(y + dx_mm / 2, 65)
                  - np.maximum(y - dx_mm / 2, 35)) / dx_mm, 0, 1)
    eps = np.ones((nx, ny, 1, 3))
    eps[:, :, 0, :] += (epsilon_r - 1) * (fx[:, None] * fy[None, :])[:, :, None]
    eps[[0, -1], :, :, :] = np.inf
    eps[:, [0, -1], :, :] = np.inf
    grid = fdtd.Grid((nx, ny, 1), grid_spacing=dx,
                     permittivity=eps, courant_number=COURANT / time_refinement)
    for index, ((cx, cy), amplitude) in enumerate(zip(
        ((40, 50), (120, 50), (80, 25)), amplitudes,
    )):
        period_steps = int(round(1 / frequencies[index] / grid.time_step))
        xx = np.arange(round((cx - 20) / dx_mm), round((cx + 20) / dx_mm) + 1)
        yy, zz = np.full_like(xx, round(cy / dx_mm)), np.zeros_like(xx)
        source = fdtd.LineSource(
            period=1 / frequencies[index], amplitude=1,
            phase_shift=phases[index] + np.pi / period_steps,
            name=f"src_{index}",
        )
        grid[xx.tolist(), yy.tolist(), zz.tolist()] = source
        weights = np.exp(-0.5 * ((xx * dx_mm - cx) / 5) ** 2)
        weights[[0, -1]] *= 0.5
        # Fixed total line-current scaling: each injection is proportional to dt/dx^2.
        source.profile = (amplitude * 5 / dx_mm / time_refinement) * weights / weights.sum()
    rx, ry = round(80 / dx_mm), round(75 / dx_mm)
    grid[rx:rx + 1, ry:ry + 1, 0:1] = fdtd.LineDetector(name="receiver")
    started = time.perf_counter()
    grid.run(5e-9, progress_bar=False)
    elapsed = time.perf_counter() - started
    field = np.r_[0, np.asarray(grid.receiver.E)[:, 0, 2]]
    times = np.arange(len(field)) * grid.time_step
    if not np.all(np.isfinite(field)) or np.max(np.abs(grid.E[[0, -1], :, :, 2])) != 0:
        raise RuntimeError("Invalid FDTD field or perimeter condition.")
    coefficient = 2 / times[-1] * np.trapz(field * np.exp(-2j * np.pi * F0 * times), times)
    return {
        "time": times, "field": field, "coefficient": complex(coefficient),
        "rms": float(np.sqrt(np.trapz(field ** 2, times) / times[-1])),
        "runtime_s": elapsed, "dt_s": grid.time_step,
        "actual_frequency_Hz": 1 / (grid.src_0.period * grid.time_step),
        "actual_source_frequencies_Hz": [
            1 / (getattr(grid, f"src_{index}").period * grid.time_step)
            for index in range(3)
        ],
        "grid_shape": grid.shape,
    }


def richardson(values, spacings):
    rows = []
    for i in range(len(values) - 2):
        coarse, medium, fine = values[i:i + 3]
        d1, d2 = coarse - medium, medium - fine
        monotonic = d1 * d2 > 0
        p = float(np.log(abs(d1 / d2)) / np.log(2)) if d1 and d2 else None
        eligible = bool(monotonic and p is not None and 0 < p < 5)
        extrapolated = fine + (fine - medium) / (2 ** p - 1) if eligible else None
        gci = 1.25 * abs((fine - medium) / fine) / (2 ** p - 1) if eligible else None
        rows.append({
            "coarse_mm": spacings[i], "medium_mm": spacings[i + 1], "fine_mm": spacings[i + 2],
            "monotonic": monotonic, "observed_order": p, "eligible": eligible,
            "extrapolated_magnitude": extrapolated, "fine_GCI_fraction": gci,
            "meaning": "Conditional Richardson estimate, not a proven error bound.",
        })
    return rows


def main(args):
    args.output.mkdir(parents=True, exist_ok=True)
    spacings = [5, 2.5, 1.25, 0.625]
    results, rows, arrays = {}, [], {}
    for spacing in spacings:
        for label, amplitudes in (("A", (1, 0, 0)), ("B", (0, 1, 0)), ("OP", (0, 0, 1))):
            print(f"Finite-source FDTD dx={spacing} mm, basis={label}", flush=True)
            result = simulate(spacing, amplitudes)
            results[(spacing, label)] = result
            arrays[f"dx_{spacing}_{label}"] = result["field"]
            arrays[f"dx_{spacing}_time"] = result["time"]
            rows.append({
                "dx_mm": spacing, "source": label, "dt_s": result["dt_s"],
                "coefficient_real": result["coefficient"].real,
                "coefficient_imag": result["coefficient"].imag,
                "coefficient_magnitude": abs(result["coefficient"]),
                "receiver_rms": result["rms"], "runtime_s": result["runtime_s"],
                "actual_source_frequency_Hz": result["actual_frequency_Hz"],
            })
    write_csv(args.output / "finite_source_grid_responses.csv", rows)
    convergence = richardson([abs(results[(dx, "A")]["coefficient"]) for dx in spacings], spacings)
    write_csv(args.output / "richardson_grid_estimates.csv", convergence)
    fine = results[(spacings[-1], "A")]
    changes = []
    for coarse_dx, fine_dx in zip(spacings[:-1], spacings[1:]):
        coarse, finer = results[(coarse_dx, "A")], results[(fine_dx, "A")]
        aligned = np.interp(finer["time"], coarse["time"], coarse["field"])
        changes.append({
            "coarse_mm": coarse_dx, "fine_mm": fine_dx,
            "relative_full_waveform_change": float(np.linalg.norm(aligned - finer["field"])
                                                   / np.linalg.norm(finer["field"])),
            "relative_complex_coefficient_change": float(abs(coarse["coefficient"] - finer["coefficient"])
                                                          / abs(finer["coefficient"])),
        })
    write_csv(args.output / "grid_pair_changes.csv", changes)

    # Independent time refinement at fixed physical grid, with midpoint forcing.
    print("FDTD fixed-grid time-step refinement", flush=True)
    time_fine = simulate(1.25, (1, 0, 0), time_refinement=2)
    fixed = results[(1.25, "A")]
    time_change = float(abs(time_fine["coefficient"] - fixed["coefficient"])
                        / abs(time_fine["coefficient"]))
    arrays["time_refined_A"], arrays["time_refined_time"] = time_fine["field"], time_fine["time"]

    # Direct combinations test the same source distribution, not an injected answer.
    direct = []
    r1 = fine["field"] / 15
    ro = results[(spacings[-1], "OP")]["field"]
    for operation, phase in (("add", 0), ("subtract", np.pi)):
        for a, b in ((15, 15), (5, 3)):
            print(f"FDTD finest-grid direct {operation} {a},{b}", flush=True)
            case = simulate(spacings[-1], (a / 15, b / 15, OP[operation]), phases=(0, phase, 0))
            centered = case["field"] - OP[operation] * ro
            coordinate = float(np.dot(r1, centered) / np.dot(r1, r1))
            target = a + b if operation == "add" else a - b
            direct.append({"operation": operation, "A": a, "B": b,
                           "coordinate": coordinate, "target": target,
                           "absolute_error": abs(coordinate - target),
                           "correct": int(np.rint(coordinate)) == target})
            arrays[f"fine_{operation}_{a}_{b}"] = case["field"]
    write_csv(args.output / "finite_source_direct_arithmetic.csv", direct)

    print("FDTD quadrature response for exact phase-error boundary", flush=True)
    quadrature = simulate(1.25, (0, 1, 0), phases=(0, np.pi / 2, 0))
    r = results[(1.25, "A")]["field"]
    eta = float(np.dot(r, quadrature["field"]) / np.dot(r, r))
    boundaries = {}
    for sign, label in ((1, "positive"), (-1, "negative")):
        def margin(angle):
            return abs(np.cos(sign * angle) + eta * np.sin(sign * angle) - 1) - 1 / 30
        angles = np.linspace(0, np.pi, 2001)
        crossing = next(index for index in range(1, len(angles)) if margin(angles[index]) >= 0)
        root = brentq(margin, angles[crossing - 1], angles[crossing], xtol=1e-13)
        boundaries[label + "_phase_boundary_degrees"] = float(sign * np.rad2deg(root))
    summary = {
        "solver": "flaport/fdtd 0.3.5", "new_direct_runs": 12 + 1 + 4 + 1,
        "scope": "New finite-Gaussian-line-source 2D model; not identical to old point-source model.",
        "source_sigma_mm": 5, "source_half_extent_mm": 20,
        "material_rectangle_mm": [60, 100, 35, 65], "epsilon_r": 2.1,
        "field_units": "Normalized solver fields, not calibrated V/m or watts",
        "source_sampling": "Physical current sampled at time midpoints; recorded E at completed time steps.",
        "grids_mm": spacings, "richardson": convergence, "grid_changes": changes,
        "fixed_grid_time_refinement_relative_complex_change": time_change,
        "direct_arithmetic": direct, "matched_phase_quadrature_eta": eta,
        **boundaries,
        "ideal_quadrature_zero_phase_bound_degrees": float(np.rad2deg(np.arccos(29 / 30))),
        "only_B_gain_strict_bound_percent": 100 / 30,
        "common_A_B_gain_strict_bound_percent": 100 / 60,
    }
    np.savez_compressed(args.output / "precision_fdtd_waveforms.npz", **arrays)
    (args.output / "precision_fdtd_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(11, 4), constrained_layout=True)
    for dx in spacings:
        result = results[(dx, "A")]
        axes[0].plot(result["time"] * 1e9, result["field"], label=f"{dx:g} mm", lw=1)
    axes[0].set(xlabel="Time (ns)", ylabel="Ez (normalized)", title="Finite-width source: mesh refinement")
    axes[0].legend()
    axes[1].loglog([row["fine_mm"] for row in changes],
                  [row["relative_full_waveform_change"] for row in changes], "o-", label="Waveform change")
    axes[1].loglog([row["fine_mm"] for row in changes],
                  [row["relative_complex_coefficient_change"] for row in changes], "s-", label="Complex coefficient")
    axes[1].set(xlabel="Fine grid spacing (mm)", ylabel="Relative adjacent-grid change")
    axes[1].legend()
    for ax in axes:
        ax.grid(alpha=0.25)
    fig.savefig(args.output / "grid_convergence.png", dpi=150)
    plt.close(fig)
    if not all(row["correct"] for row in direct):
        raise RuntimeError("Finite-source direct arithmetic test failed.")
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "第三轮结果")
    main(parser.parse_args())
