"""Single-probe input impedance and matching of the copper 111 cavity.

G3 currently has no measured coupling number. This run adds the smallest
physical probe the existing solver can represent: a one-edge current filament
inside the cavity, driven at 1 A. It is a delta-gap source, not a connector.
The output is the input impedance, |S11| versus 50 ohm, and the coupling
coefficient beta = Re(Z_in)/Z0 at resonance.

Power balance is checked independently: the real power delivered by the source
must equal the copper wall loss, because nothing else dissipates.
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
OUT = ROOT / "端口耦合校核结果"
EVIDENCE = ROOT / "铜腔111双极化结果"
MODEL_PATH = ROOT / "厚铜边界基准结果" / "passive_surface_models.json"
LENGTHS = np.array([.16, .10, .06])
SHAPES = ((24, 15, 9), (36, 23, 14))
Z0 = 50.
SWEEP = 9


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def port_solve(grid, frequency, resistance, inductance, drive):
    _, per_record, total = grid.admittance(frequency, resistance, inductance)
    s = 2j * np.pi * frequency
    operator = (grid.stiffness + diags(s * s * grid.m + s * total)).tocsc()
    started = time.perf_counter()
    factor = splu(operator, permc_spec="MMD_AT_PLUS_A")
    e = factor.solve(s * drive)
    seconds = time.perf_counter() - started
    # |I| = 1 A, so the complex drive-dot-field is the input impedance itself.
    z_in = np.vdot(e, drive)
    wall_power = .5 * float(np.sum(per_record.real * np.abs(e[grid.wall_edges]) ** 2))
    source_power = .5 * float(np.real(z_in))
    return dict(z_in=complex(z_in), wall_power_W=wall_power,
                source_power_W=source_power, solve_seconds=seconds)


def main():
    assert not OUT.exists(), "New evidence only; do not overwrite a prior audit"
    OUT.mkdir()
    model = next(row for row in json.loads(MODEL_PATH.read_text(encoding="utf-8"))["models"]
                 if row["terms"] == 64)
    resistance = np.asarray(model["R_ohm"])
    inductance = np.asarray(model["L_H"])
    configurations = [dict(shape=list(shape), lengths_m=[float(x) for x in LENGTHS],
                           mode=[1, 1, 1],
                           port="single interior edge current filament, delta-gap",
                           drive_current_A=1., reference_impedance_ohm=Z0,
                           sweep_points=SWEEP, terms=64)
                      for shape in SHAPES]
    config_path = OUT / "configurations.json"
    config_path.write_text(json.dumps(configurations, indent=2) + "\n", encoding="utf-8")
    report = dict(
        scope="one-port input impedance, S11 and coupling coefficient of the 111 mode",
        limitation="delta-gap edge current, not a physical 50 ohm connector or coax geometry",
        evidence_level="A (full six-wall frequency solve, same solver as the doublet run)",
        configuration_sha256=sha(config_path),
        model_sha256=sha(MODEL_PATH),
        source_sha256={name: sha(ROOT / name) for name in (
            "run_port_coupling.py", "finite_copper_cavity.py", "finite_pec_cavity.py")},
        cases=[])
    (OUT / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    total_started = time.perf_counter()
    try:
        with threadpool_limits(limits=1):
            for shape in SHAPES:
                stem = "111_" + "x".join(map(str, shape))
                peak = np.load(EVIDENCE / (stem + "_peaks.npz"))
                center = float(np.mean(peak["frequency_Hz"].real))
                unloaded_q = float(np.mean(peak["fitted_Q"]))
                linewidth = center / unloaded_q

                grid = CopperCavity(shape, LENGTHS)
                seed = grid.pec.lift(grid.pec.sample_mode([1, 1, 1]))
                wall = set(int(i) for i in grid.wall_edges)
                candidates = np.array([int(i) for i in grid.pec.active if int(i) not in wall])
                edge = int(candidates[np.argmax(np.abs(seed[candidates]))])
                drive = np.zeros(grid.ne, complex)
                drive[edge] = 1.

                rows = []
                for offset in np.linspace(-2, 2, SWEEP):
                    frequency = center + offset * linewidth
                    result = port_solve(grid, frequency, resistance, inductance, drive)
                    z = result["z_in"]
                    rows.append(dict(
                        offset_in_linewidths=float(offset),
                        frequency_Hz=float(frequency),
                        z_real_ohm=float(z.real), z_imag_ohm=float(z.imag),
                        abs_s11=float(abs((z - Z0) / (z + Z0))),
                        wall_power_W=result["wall_power_W"],
                        source_power_W=result["source_power_W"],
                        power_balance_error=float(abs(result["wall_power_W"]
                                                      - result["source_power_W"])
                                                  / max(result["source_power_W"], 1e-30)),
                        solve_seconds=result["solve_seconds"]))
                    print(shape, "port", frequency, "Z", z, flush=True)

                best = max(rows, key=lambda row: row["z_real_ohm"])
                beta = best["z_real_ohm"] / Z0
                imaginary = [row["z_imag_ohm"] for row in rows]
                report["cases"].append(dict(
                    shape=list(shape), probe_edge_full_index=edge,
                    probe_on_wall=bool(edge in wall),
                    center_frequency_Hz=center, unloaded_Q=unloaded_q,
                    linewidth_Hz=linewidth,
                    resonance=best,
                    coupling_beta=float(beta),
                    loaded_Q_estimate=float(unloaded_q / (1 + beta)),
                    min_abs_s11=float(best["abs_s11"]),
                    max_abs_s11=float(max(row["abs_s11"] for row in rows)),
                    max_power_balance_error=float(max(row["power_balance_error"] for row in rows)),
                    imaginary_monotonic=bool(np.all(np.diff(imaginary) > 0)),
                    passive=bool(all(row["abs_s11"] <= 1 + 1e-9 for row in rows)),
                    sweep=rows))
                print(shape, "beta", beta, "min |S11|", best["abs_s11"],
                      "passive", report["cases"][-1]["passive"], flush=True)
                report["seconds"] = time.perf_counter() - total_started
                (OUT / "summary.json").write_text(json.dumps(report, indent=2) + "\n",
                                                  encoding="utf-8")
        report["status"] = "complete"
        report["seconds"] = time.perf_counter() - total_started
        (OUT / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    except Exception as error:
        report["status"] = "failed_preserve_partial"
        report["failure"] = repr(error)
        report["seconds"] = time.perf_counter() - total_started
        (OUT / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        raise


if __name__ == "__main__":
    main()
