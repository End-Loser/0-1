"""Modal reduced-order 8 us window from measured 111 complex poles.

Not a time-domain full-wave advance and not openEMS. The two mode fields and
the two complex poles come from the existing A-level six-wall frequency solves
in 铜腔111双极化结果/; this script only samples the closed-form exponential
decay of those measured poles and asks what an 8 us record can resolve.
"""
import hashlib
import json
from pathlib import Path

import numpy as np
from threadpoolctl import threadpool_limits

from finite_copper_cavity import CopperCavity

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "模态长窗降阶结果"
SOURCES = ROOT / "铜腔111双极化结果"
SHAPES = ((24, 15, 9), (36, 23, 14))
LENGTHS = (.16, .10, .06)
DT = 2e-12
DURATION = 8e-6
STEPS = int(round(DURATION / DT))
SAMPLE_EVERY = 1000
WINDOWS = ((1e-6, 5e-6), (1e-6, 8e-6))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fit_rate(times, energy):
    """Exponential rate beta from a linear fit of log energy."""
    log = np.log(energy)
    matrix = np.column_stack((times, np.ones(len(times))))
    slope, _ = np.linalg.lstsq(matrix, log, rcond=None)[0]
    residual = np.linalg.norm(matrix @ np.linalg.lstsq(matrix, log, rcond=None)[0] - log)
    return -float(slope), float(residual / np.linalg.norm(log))


def main():
    assert not OUT.exists(), "New evidence only; do not overwrite a prior result"
    OUT.mkdir()
    config = dict(dt_s=DT, duration_s=DURATION, steps=STEPS, sample_every=SAMPLE_EVERY,
                  windows_s=[list(w) for w in WINDOWS], shapes=[list(s) for s in SHAPES],
                  lengths_m=list(LENGTHS),
                  method="closed-form exponential sampling of measured complex poles",
                  initial_amplitude="equal 1/sqrt(2) coherent two-mode, idealised mixed drive")
    config_path = OUT / "configurations.json"
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    summary = dict(
        scope="8 us modal reduced-order window from existing 111 complex poles and mode fields",
        evidence_level="B (analytic reduced order; A-level poles and mode fields as input)",
        limitation="two-mode diagonal decay only; no inter-mode coupling, no full-wave advance, "
                   "no drive port, initial amplitudes idealised; G1 stays open",
        configuration_sha256=sha(config_path),
        source_sha256={name: sha(ROOT / name) for name in (
            "run_modal_long_window.py", "finite_copper_cavity.py", "finite_pec_cavity.py")},
        cases=[])
    config_check = sha(config_path)

    with threadpool_limits(limits=1):
        for shape in SHAPES:
            grid = CopperCavity(shape, LENGTHS)
            stem = "111_" + "x".join(map(str, shape))
            peak_path = SOURCES / (stem + "_peaks.npz")
            data = np.load(peak_path, allow_pickle=True)
            e_peak, vectors = data["e_V"], data["fitted_vectors"]
            poles, q_input = data["fitted_poles_Hz"], data["fitted_Q"]
            fields = np.stack([e_peak[k][:, :2] @ vectors[:, k] for k in range(2)], axis=1)
            mass = grid.m[:, None]
            gram = fields.conj().T @ (mass * fields)
            norms = np.sqrt(np.abs(np.diag(gram).real))
            fields = fields / norms
            gram = fields.conj().T @ (mass * fields)
            diagonals = np.sqrt(np.abs(np.diag(gram).real))
            offdiag = float(np.abs(gram[0, 1]) / np.sqrt(diagonals[0] * diagonals[1]))

            rate = 2 * np.pi * np.abs(poles.imag)
            omega = 2 * np.pi * np.abs(poles.real)
            s = -rate + 1j * omega
            amplitude0 = np.full(2, 1 / np.sqrt(2), complex)

            indices = np.arange(0, STEPS + 1, SAMPLE_EVERY)
            times = indices * DT
            amplitude = amplitude0 * np.exp(np.outer(times, s))
            energy_mode = np.abs(amplitude) ** 2
            energy_total = energy_mode.sum(axis=1)

            probe_index = int(np.argmax(np.abs(fields[:, 0]) + np.abs(fields[:, 1])))
            probe = np.real(amplitude @ fields[probe_index, :])
            np.savez_compressed(OUT / (stem + "_window.npz"), shape=shape, lengths_m=LENGTHS,
                                time_s=times, energy_total=energy_total, energy_mode=energy_mode,
                                probe_field_V=probe, poles_Hz=poles, input_Q=q_input)

            fits = []
            for low, high in WINDOWS:
                pick = (times >= low) & (times <= high)
                beta_total, total_residual = fit_rate(times[pick], energy_total[pick])
                beta_mode, mode_residual = fit_rate(times[pick], energy_mode[pick, 1])
                omega_mean = float(np.mean(omega))
                fits.append(dict(window_s=[low, high], points=int(pick.sum()),
                                 mixed_Q=omega_mean / beta_total,
                                 mixed_log_residual=total_residual,
                                 high_Q_mode=omega_mean / beta_mode,
                                 high_Q_mode_log_residual=mode_residual,
                                 high_Q_mode_input=float(q_input[1]),
                                 high_Q_mode_relative_error=float(abs(omega_mean / beta_mode / q_input[1] - 1))))
            difference = abs(fits[0]["mixed_Q"] / fits[1]["mixed_Q"] - 1)
            amplitude_tau = 1 / rate
            remaining = float(energy_total[-1] / energy_total[0])
            row = dict(
                shape=list(shape), peaks_file=peak_path.name, peaks_sha256=sha(peak_path),
                dt_s=DT, steps=STEPS, duration_s=DURATION, sample_interval_s=SAMPLE_EVERY * DT,
                poles_Hz=[dict(real=float(p.real), imag=float(p.imag)) for p in poles],
                input_Q=[float(q) for q in q_input],
                gram_offdiag_relative=offdiag,
                decay_time_amplitude_us=(amplitude_tau * 1e6).tolist(),
                duration_in_amplitude_decay_times=(DURATION / amplitude_tau).tolist(),
                beat_period_us=float(1e6 / abs(poles[0].real - poles[1].real)),
                beats_in_window=float(DURATION * abs(poles[0].real - poles[1].real)),
                energy_fraction_at_8us=dict(total=remaining,
                                            mode=[float(v) for v in energy_mode[-1] / energy_mode[0].sum()]),
                fits=fits, mixed_window_Q_difference=difference,
                window_Q_difference_below_one_percent=bool(difference < .01),
                probe_edge_full_index=probe_index,
                probe_peak_abs_V=float(np.max(np.abs(probe))),
                status="complete")
            summary["cases"].append(row)
            print(shape, "offdiag", offdiag, "Q windows", [f["mixed_Q"] for f in fits],
                  "diff", difference, flush=True)
            assert sha(config_path) == config_check
            (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
