"""High-resolution sweep of the k x k wall-fed post on the fine grid.

Not openEMS. Same chain as run_probe_cross_section.py. The 10 MHz sweep in
run_probe_resonance_scan.py is far coarser than the 111 linewidth
(0.187 MHz), so it can neither confirm nor exclude a narrow mode-coupling
feature near 3.05536 GHz. This run samples 3.050-3.062 GHz in 0.5 MHz steps
for k = 1, 2, 3 on the 36x23x14 grid only, which is the only grid where the
narrow-scan k = 3 anomaly appeared.

Decisive test: a feature within +-1 linewidth of the mode centre that exists
for k = 3 and not for k = 1, 2 is mode coupling through the distributed port.
A feature far from the centre is a port/structure self-resonance.
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
OUT = ROOT / "探针高分辨扫频结果"
SOURCES = ROOT / "铜腔111双极化结果"
SHAPE = (36, 23, 14)
TARGET_HEIGHT_M = .03
SIDES = (1, 2, 3)
BAND_HZ = (3.050e9, 3.062e9)
STEP_HZ = .5e6


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    assert not OUT.exists(), "New evidence only; do not overwrite a prior result"
    model_path = ROOT / "厚铜边界基准结果/passive_surface_models.json"
    model = next(row for row in json.loads(model_path.read_text(encoding="utf-8"))["models"]
                 if row["terms"] == 64)
    resistance, inductance = np.asarray(model["R_ohm"]), np.asarray(model["L_H"])
    OUT.mkdir()
    frequencies = np.arange(BAND_HZ[0], BAND_HZ[1] + STEP_HZ / 2, STEP_HZ)
    configurations = [dict(shape=list(SHAPE), lengths_m=list(LENGTHS), sides=list(SIDES),
                           post_target_height_m=TARGET_HEIGHT_M, terms=64,
                           reference_impedance_ohm=Z0, band_Hz=list(BAND_HZ),
                           step_Hz=STEP_HZ, points=int(frequencies.size))]
    config_path = OUT / "configurations.json"
    config_path.write_text(json.dumps(configurations, indent=2) + "\n", encoding="utf-8")
    summary = dict(
        scope="0.5 MHz steps over 3.050-3.062 GHz, fine grid only, k = 1, 2, 3, "
              "full six-wall frequency solve",
        evidence_level="A (full six-wall frequency solve)",
        limitation="post is a bundle of k^2 thin wires with E_axial forced to zero; "
                   "no coaxial/SMA geometry; port defined as the mean wall-gap "
                   "voltage at 1 A total drive current",
        configuration_sha256=sha(config_path), model_sha256=sha(model_path),
        source_sha256={name: sha(ROOT / name) for name in (
            "run_probe_resonance_fine.py", "run_probe_cross_section.py",
            "finite_copper_cavity.py", "finite_pec_cavity.py")},
        cases=[], status="running")

    def checkpoint():
        (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    checkpoint()
    try:
        with threadpool_limits(limits=1):
            grid = CopperCavity(SHAPE, LENGTHS)
            stem = "111_" + "x".join(map(str, SHAPE))
            peaks = np.load(SOURCES / (stem + "_peaks.npz"), allow_pickle=True)
            scan = np.load(SOURCES / (stem + "_scan.npz"), allow_pickle=True)
            seed = scan["seeds"][:, 0]
            poles, q_poles = peaks["fitted_poles_Hz"], peaks["fitted_Q"]
            center = float(np.mean(poles.real))
            width = center / float(np.min(q_poles))
            for side in SIDES:
                gap, post, height, location, cells = probe_pattern(
                    grid, seed, side, TARGET_HEIGHT_M)
                keep = np.setdiff1d(np.arange(grid.ne), post)
                position = -np.ones(grid.ne, dtype=int)
                position[keep] = np.arange(len(keep))
                assert np.all(position[gap] >= 0)
                wall_position = position[grid.wall_edges[grid.wall_edges < grid.ne]]
                assert np.all(wall_position >= 0)
                drive = np.zeros(len(keep), complex)
                drive[position[gap]] = 1. / len(gap)
                grid.m_keep = grid.m[keep]
                grid.stiffness_keep = (grid.curl[:, keep].T @ diags(grid.q)
                                       @ grid.curl[:, keep]).tocsc()
                rows = []
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
                    voltages = e[position[gap]]
                    phase = np.angle(voltages * np.exp(-1j * np.angle(voltages.mean())))
                    rows.append(dict(frequency_Hz=float(f), z_real_ohm=float(z.real),
                                     z_imag_ohm=float(z.imag),
                                     abs_s11=float(abs((z - Z0) / (z + Z0))),
                                     power_balance_error=float(
                                         abs(source_power - wall_power) / source_power),
                                     gap_voltage_uniformity=float(
                                         np.abs(voltages).std() / np.abs(voltages).mean()),
                                     gap_phase_spread_rad=float(np.ptp(phase)),
                                     solve_seconds=seconds))
                    print(SHAPE, "k", side, f, rows[-1]["z_real_ohm"], rows[-1]["z_imag_ohm"],
                          rows[-1]["abs_s11"], rows[-1]["gap_voltage_uniformity"],
                          rows[-1]["gap_phase_spread_rad"], flush=True)
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
                    shape=list(SHAPE), side=side, post_height_m=height, post_cells=cells,
                    post_location_ij=[int(v) for v in location],
                    gap_edges=int(len(gap)), removed_edges=int(len(post)),
                    mode_center_Hz=center, mode_width_Hz=width,
                    peak_re_frequency_Hz=float(frequencies[peak]),
                    peak_re_ohm=float(re[peak]),
                    peak_offset_in_linewidths=float((frequencies[peak] - center) / width),
                    im_zero_crossing_Hz=zero,
                    min_abs_s11=float(min(r["abs_s11"] for r in rows)),
                    min_s11_frequency_Hz=float(min(rows, key=lambda r: r["abs_s11"])["frequency_Hz"]),
                    max_gap_voltage_uniformity=float(
                        max(r["gap_voltage_uniformity"] for r in rows)),
                    max_gap_phase_spread_rad=float(max(r["gap_phase_spread_rad"] for r in rows)),
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
