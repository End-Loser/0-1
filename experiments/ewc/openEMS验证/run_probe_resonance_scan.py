"""Wideband scan of the k x k wall-fed post, six-wall frequency solve.

Not openEMS. Same chain as run_probe_cross_section.py, but the frequency band
is 3.00-3.11 GHz in 10 MHz steps instead of 5 points inside +-2 linewidths.
Purpose: decide where Re(Z) peaks and where Im(Z) crosses zero, so that the
k = 3 resonance seen at the top of the narrow scan can be attributed either to
the 111 mode (peak at 3.0553 GHz) or to a port/structure resonance elsewhere.
"""
import hashlib
import json
from pathlib import Path
import time

import numpy as np
from scipy.sparse import diags
from scipy.sparse.linalg import splu
from threadpoolctl import threadpool_limits

from finite_copper_cavity import CopperCavity
from run_probe_cross_section import LENGTHS, Z0, probe_pattern

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "探针宽带扫频结果"
SOURCES = ROOT / "铜腔111双极化结果"
TARGET_HEIGHT_M = .03
SHAPES = ((24, 15, 9), (36, 23, 14))
BAND_HZ = (2.98e9, 3.13e9)
POINTS = 16


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    assert not OUT.exists(), "New evidence only; do not overwrite a prior result"
    model_path = ROOT / "厚铜边界基准结果/passive_surface_models.json"
    model = next(row for row in json.loads(model_path.read_text(encoding="utf-8"))["models"]
                 if row["terms"] == 64)
    resistance, inductance = np.asarray(model["R_ohm"]), np.asarray(model["L_H"])
    OUT.mkdir()
    frequencies = np.linspace(BAND_HZ[0], BAND_HZ[1], POINTS)
    configurations = [dict(shape=list(shape), lengths_m=list(LENGTHS), sides=[1, 2, 3],
                           post_target_height_m=TARGET_HEIGHT_M, terms=64,
                           reference_impedance_ohm=Z0,
                           band_Hz=list(BAND_HZ), points=POINTS)
                      for shape in SHAPES]
    config_path = OUT / "configurations.json"
    config_path.write_text(json.dumps(configurations, indent=2) + "\n", encoding="utf-8")
    summary = dict(
        scope="16-point 2.98-3.13 GHz sweep of the k x k wall-fed post, "
              "full six-wall frequency solve",
        evidence_level="A (full six-wall frequency solve)",
        limitation="post is a bundle of k^2 thin wires with E_axial forced to zero; "
                   "no coaxial/SMA geometry; port defined as the mean wall-gap "
                   "voltage at 1 A total drive current",
        configuration_sha256=sha(config_path), model_sha256=sha(model_path),
        source_sha256={name: sha(ROOT / name) for name in (
            "run_probe_resonance_scan.py", "run_probe_cross_section.py",
            "finite_copper_cavity.py", "finite_pec_cavity.py")},
        cases=[], status="running")

    def checkpoint():
        (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    checkpoint()
    try:
        with threadpool_limits(limits=1):
            for shape in SHAPES:
                grid = CopperCavity(shape, LENGTHS)
                stem = "111_" + "x".join(map(str, shape))
                peaks = np.load(SOURCES / (stem + "_peaks.npz"), allow_pickle=True)
                scan = np.load(SOURCES / (stem + "_scan.npz"), allow_pickle=True)
                seed = scan["seeds"][:, 0]
                poles, q_poles = peaks["fitted_poles_Hz"], peaks["fitted_Q"]
                center = float(np.mean(poles.real))
                width = center / float(np.min(q_poles))
                for side in (1, 2, 3):
                    gap, post, height, location, cells = probe_pattern(
                        grid, seed, side, TARGET_HEIGHT_M)
                    keep = np.setdiff1d(np.arange(grid.ne), post)
                    position = -np.ones(grid.ne, dtype=int)
                    position[keep] = np.arange(len(keep))
                    wall_position = position[grid.wall_edges[grid.wall_edges < grid.ne]]
                    drive = np.zeros(len(keep), complex)
                    drive[position[gap]] = 1. / len(gap)
                    grid.m_keep = grid.m[keep]
                    grid.stiffness_keep = (grid.curl[:, keep].T @ diags(grid.q)
                                           @ grid.curl[:, keep]).tocsc()
                    rows, uniformity = [], []
                    for f in frequencies:
                        _, per_record, total = grid.admittance(f, resistance, inductance)
                        s = 2j * np.pi * f
                        operator = (grid.stiffness_keep
                                    + diags(s * s * grid.m_keep + s * total[keep])).tocsc()
                        started = time.perf_counter()
                        e = splu(operator, permc_spec="MMD_AT_PLUS_A").solve(s * drive)
                        seconds = time.perf_counter() - started
                        z = np.vdot(e, drive)
                        wall_power = .5 * float(np.sum(per_record.real
                                                       * np.abs(e[wall_position]) ** 2))
                        source_power = .5 * float(z.real)
                        reflection = abs((z - Z0) / (z + Z0))
                        rows.append(dict(frequency_Hz=float(f), z_real_ohm=float(z.real),
                                         z_imag_ohm=float(z.imag),
                                         abs_s11=float(reflection),
                                         power_balance_error=float(
                                             abs(source_power - wall_power) / source_power),
                                         gap_voltage_uniformity=float(
                                             np.abs(e[position[gap]]).std()
                                             / np.abs(e[position[gap]]).mean()),
                                         solve_seconds=seconds))
                        print(shape, "k", side, f, rows[-1]["z_real_ohm"],
                              rows[-1]["z_imag_ohm"], rows[-1]["abs_s11"], flush=True)
                    del grid.m_keep, grid.stiffness_keep
                    re = np.array([r["z_real_ohm"] for r in rows])
                    im = np.array([r["z_imag_ohm"] for r in rows])
                    peak = int(np.argmax(re))
                    zero = None
                    for n in range(len(im) - 1):
                        if im[n] * im[n + 1] < 0:
                            zero = float(frequencies[n] - im[n] * (frequencies[n + 1]
                                                                   - frequencies[n])
                                         / (im[n + 1] - im[n]))
                            break
                    summary["cases"].append(dict(
                        shape=list(shape), side=side, post_height_m=height,
                        post_cells=cells, post_location_ij=[int(v) for v in location],
                        gap_edges=int(len(gap)), removed_edges=int(len(post)),
                        mode_center_Hz=center, mode_width_Hz=width,
                        peak_re_frequency_Hz=float(frequencies[peak]),
                        peak_re_ohm=float(re[peak]),
                        peak_offset_in_linewidths=float((frequencies[peak] - center) / width),
                        im_zero_crossing_Hz=zero,
                        im_zero_offset_in_linewidths=(float((zero - center) / width)
                                                      if zero else None),
                        min_abs_s11=float(min(r["abs_s11"] for r in rows)),
                        max_gap_voltage_uniformity=float(
                            max(r["gap_voltage_uniformity"] for r in rows)),
                        max_power_balance_error=float(
                            max(r["power_balance_error"] for r in rows)),
                        scan=rows, status="complete"))
                    checkpoint()
        summary["status"] = "complete"
        checkpoint()
    except Exception as error:
        summary["status"] = "failed_preserve_partial"
        summary["failure"] = repr(error)
        checkpoint()
        raise


if __name__ == "__main__":
    main()
