"""Does probe placement alone reach critical coupling to 50 ohm?

The single-edge probe at the 111 field maximum gave Re(Z) of tens of kohm.
For a fixed 1 A filament the coupled power scales with the square of the modal
field at the probe, so Re(Z) should scale with (normalized field amplitude)^2.
If that holds, a placement at a prescribed fraction of the peak field reaches
Re(Z) = 50 ohm, and a matched port is a design lever rather than an impossibility.

Each position is one full six-wall solve at the measured 111 resonance.
"""

import hashlib
import json
import time
from pathlib import Path

import numpy as np
from scipy.sparse import diags
from scipy.sparse.linalg import splu
from threadpoolctl import threadpool_limits

from finite_copper_cavity import CopperCavity

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "端口匹配校核结果"
EVIDENCE = ROOT / "铜腔111双极化结果"
MODEL_PATH = ROOT / "厚铜边界基准结果" / "passive_surface_models.json"
LENGTHS = np.array([.16, .10, .06])
SHAPES = ((24, 15, 9), (36, 23, 14))
Z0 = 50.
TARGETS = (1., .5, .25, .125, .0625)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def input_impedance(grid, frequency, resistance, inductance, drive):
    _, per_record, total = grid.admittance(frequency, resistance, inductance)
    s = 2j * np.pi * frequency
    operator = (grid.stiffness + diags(s * s * grid.m + s * total)).tocsc()
    started = time.perf_counter()
    factor = splu(operator, permc_spec="MMD_AT_PLUS_A")
    e = factor.solve(s * drive)
    seconds = time.perf_counter() - started
    wall = .5 * float(np.sum(per_record.real * np.abs(e[grid.wall_edges]) ** 2))
    source = .5 * float(np.real(np.vdot(e, drive)))
    return dict(z_in=complex(np.vdot(e, drive)), wall_power_W=wall,
                source_power_W=source, solve_seconds=seconds)


def main():
    assert not OUT.exists(), "New evidence only; do not overwrite a prior audit"
    OUT.mkdir()
    model = next(row for row in json.loads(MODEL_PATH.read_text(encoding="utf-8"))["models"]
                 if row["terms"] == 64)
    resistance = np.asarray(model["R_ohm"])
    inductance = np.asarray(model["L_H"])
    configurations = [dict(shape=list(shape), lengths_m=[float(x) for x in LENGTHS],
                           mode=[1, 1, 1], reference_impedance_ohm=Z0,
                           amplitude_targets=list(TARGETS), terms=64)
                      for shape in SHAPES]
    config_path = OUT / "configurations.json"
    config_path.write_text(json.dumps(configurations, indent=2) + "\n", encoding="utf-8")
    report = dict(
        scope="Re(Z_in) versus normalized modal field amplitude at the probe",
        limitation="single-edge delta-gap filament; probe cross-section is not mesh resolved",
        evidence_level="A (full six-wall frequency solve)",
        configuration_sha256=sha(config_path),
        model_sha256=sha(MODEL_PATH),
        source_sha256={name: sha(ROOT / name) for name in (
            "run_port_matching.py", "finite_copper_cavity.py", "finite_pec_cavity.py")},
        cases=[])
    (OUT / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    started = time.perf_counter()
    try:
        with threadpool_limits(limits=1):
            for shape in SHAPES:
                stem = "111_" + "x".join(map(str, shape))
                peak = np.load(EVIDENCE / (stem + "_peaks.npz"))
                center = float(np.mean(peak["frequency_Hz"].real))
                grid = CopperCavity(shape, LENGTHS)
                seed = grid.pec.lift(grid.pec.sample_mode([1, 1, 1]))
                wall = set(int(i) for i in grid.wall_edges)
                candidates = np.array([int(i) for i in grid.pec.active if int(i) not in wall])
                amplitude = np.abs(seed[candidates])
                peak_amplitude = float(np.max(amplitude))

                rows = []
                for target in TARGETS:
                    pick = int(np.argmin(np.abs(amplitude - target * peak_amplitude)))
                    edge = int(candidates[pick])
                    ratio = float(amplitude[pick] / peak_amplitude)
                    drive = np.zeros(grid.ne, complex)
                    drive[edge] = 1.
                    result = input_impedance(grid, center, resistance, inductance, drive)
                    z = result["z_in"]
                    rows.append(dict(target=float(target), achieved_ratio=ratio,
                                     probe_edge_full_index=edge,
                                     z_real_ohm=float(z.real), z_imag_ohm=float(z.imag),
                                     abs_s11=float(abs((z - Z0) / (z + Z0))),
                                     power_balance_error=float(
                                         abs(result["wall_power_W"] - result["source_power_W"])
                                         / max(result["source_power_W"], 1e-30)),
                                     solve_seconds=result["solve_seconds"]))
                    print(shape, "target", target, "ratio", ratio, "ReZ", z.real, flush=True)

                ratios = np.array([row["achieved_ratio"] for row in rows])
                resistances = np.array([row["z_real_ohm"] for row in rows])
                valid = (ratios > 0) & (resistances > 0)
                slope, intercept = np.polyfit(np.log10(ratios[valid]),
                                              np.log10(resistances[valid]), 1)
                needed = float(10 ** ((np.log10(Z0) - intercept) / slope)) if slope else None
                report["cases"].append(dict(
                    shape=list(shape), center_frequency_Hz=center,
                    peak_amplitude=peak_amplitude,
                    log_log_slope=float(slope), log_log_intercept=float(intercept),
                    required_ratio_for_50_ohm=needed,
                    reachable_50_ohm=bool(needed is not None and needed <= 1.),
                    best_abs_s11=float(min(row["abs_s11"] for row in rows)),
                    passive=bool(all(row["abs_s11"] <= 1 + 1e-9 for row in rows)),
                    positions=rows))
                report["seconds"] = time.perf_counter() - started
                (OUT / "summary.json").write_text(json.dumps(report, indent=2) + "\n",
                                                  encoding="utf-8")
                print(shape, "slope", slope, "needed ratio", needed, flush=True)
        report["status"] = "complete"
        report["seconds"] = time.perf_counter() - started
        (OUT / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    except Exception as error:
        report["status"] = "failed_preserve_partial"
        report["failure"] = repr(error)
        report["seconds"] = time.perf_counter() - started
        (OUT / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        raise


if __name__ == "__main__":
    main()
