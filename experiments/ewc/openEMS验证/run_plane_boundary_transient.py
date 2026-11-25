"""Standalone 1-D TEM LC line + passive RL wall, implicit midpoint.

NOT openEMS/Yee FDTD, NOT a 3-D boundary extension or a hardware BOM.
Voltage/current are for a square parallel-plate TEM normalization (w/h=1).
"""

import argparse
import hashlib
import json
from pathlib import Path
import tempfile

import numpy as np
from scipy.sparse import diags, lil_matrix, eye
from scipy.sparse.linalg import splu

MU = 4e-7 * np.pi
C0 = 299792458.
EPS = 1 / (MU * C0**2)
Z0 = np.sqrt(MU / EPS)
LENGTH = .12
FREQ = np.linspace(2.5e9, 3.5e9, 101)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source(time, cutoff=2.4e-9):
    t = np.asarray(time)
    value = np.exp(-.5 * ((t - 1.2e-9) / .2e-9)**2) * np.cos(2 * np.pi * 3e9 * (t - 1.2e-9))
    return np.where(t < cutoff, value, 0.)


def dft(time, value, frequency):
    # Midpoint samples are integrated as equal-width bins, not endpoint samples.
    return (time[1] - time[0]) * np.exp(-2j * np.pi * frequency[:, None] * time) @ value


def make_line(cells):
    if cells < 2:
        raise ValueError("At least two propagation cells required.")
    dx = LENGTH / cells
    size = 2 * cells + 1
    rate = C0 / dx
    k = lil_matrix((size, size))
    for edge in range(cells):
        current = cells + 1 + edge
        k[edge, current] -= rate * (2 if edge == 0 else 1)
        k[edge + 1, current] += rate * (2 if edge + 1 == cells else 1)
        k[current, edge] += rate
        k[current, edge + 1] -= rate
    k[0, 0] -= 2 * rate  # Matched source resistance remains after drive cutoff.
    weights = np.full(size, EPS * dx)
    weights[[0, cells]] *= .5
    return k.tocsc(), weights, rate


def simulate(cells, dt, duration, resistance, inductance, termination="copper"):
    if not np.isfinite(dt) or dt <= 0 or duration <= dt:
        raise ValueError("Positive finite timestep and duration required.")
    if termination not in ("copper", "pec", "matched"):
        raise ValueError("Unknown termination.")
    r, l = np.asarray(resistance), np.asarray(inductance)
    if termination == "copper" and (len(r) == 0 or np.any(r <= 0) or np.any(l <= 0)):
        raise ValueError("Positive RL parameters required.")
    k, weights, rate = make_line(cells)
    steps = int(round(duration / dt))
    if not np.isclose(steps * dt, duration, rtol=1e-10, atol=1e-18):
        raise ValueError("Duration must be an integer number of steps.")
    time = (np.arange(steps) + .5) * dt
    drive = source(time)
    if termination == "copper":
        p = r / l
        b = 1 / (1 + dt * p / 2)
        a = 1 - b
        rb = r * b / Z0
        effective = float(np.sum(rb))
    else:
        b = a = rb = np.zeros(0)
        effective = 1. if termination == "matched" else 0.
        r = l = np.zeros(0)
    dynamic = k.copy().tolil()
    if termination != "pec":
        dynamic[cells, cells] -= 2 * rate / effective
    dynamic = dynamic.tocsc()
    lhs = eye(k.shape[0], format="csc") - dt / 2 * dynamic
    rhs_matrix = eye(k.shape[0], format="csc") + dt / 2 * dynamic
    if termination == "pec":
        lhs = lhs.tolil()
        lhs[cells, :] = 0
        lhs[cells, cells] = 1
        lhs = lhs.tocsc()
    factor = splu(lhs)
    wave = np.zeros(k.shape[0])
    state = np.zeros(len(r))
    waves = np.zeros((steps + 1, len(wave)))
    states = np.zeros((steps + 1, len(state)))
    wall_current = np.zeros(steps)
    copper_power = np.zeros(steps)
    source_work = np.zeros(steps)
    left_power = np.zeros(steps)
    load_power = np.zeros(steps)
    for n in range(steps):
        memory = float(rb @ state)
        rhs = rhs_matrix @ wave
        rhs[0] += dt * 4 * rate * drive[n]
        if termination != "pec":
            rhs[cells] -= dt * 2 * rate * memory / effective
        else:
            rhs[cells] = 0.
        new_wave = factor.solve(rhs)
        midpoint = (wave + new_wave) / 2
        if termination == "pec":
            j = midpoint[-1]  # Last line current; wall half capacitor is clamped.
        else:
            j = (midpoint[cells] + memory) / effective
        if termination == "copper":
            mid_state = b * state + a * j
            new_state = 2 * mid_state - state
            copper_power[n] = np.sum(r * ((j - mid_state) / Z0)**2)
        else:
            new_state = state
        wall_current[n] = j / Z0
        source_work[n] = 2 * drive[n] * midpoint[0] / Z0
        left_power[n] = midpoint[0]**2 / Z0
        load_power[n] = midpoint[cells] * j / Z0
        wave, state = new_wave, new_state
        waves[n + 1] = wave
        states[n + 1] = state
    wave_energy = .5 * (waves**2) @ weights
    boundary_energy = .5 * (states**2) @ (l / Z0**2)
    energy = wave_energy + boundary_energy
    wall_dissipation = copper_power if termination == "copper" else load_power
    defect = np.diff(energy) - dt * (source_work - left_power - wall_dissipation)
    input_work = dt * np.sum(source_work)
    normalized = max(abs(input_work), np.max(energy), 1e-30)
    mids = (waves[:-1] + waves[1:]) / 2
    off = np.flatnonzero(time >= 2.4e-9)
    return {
        "time_mid_s": time, "source_V": drive, "left_V": mids[:, 0],
        "wall_V": mids[:, cells], "wall_I_A": wall_current,
        "wave_energy_J": wave_energy, "boundary_energy_J": boundary_energy,
        "energy_J": energy, "source_work_W": source_work,
        "left_resistor_power_W": left_power, "copper_power_W": copper_power,
        "load_power_W": load_power, "wave_states": waves, "RL_states_scaled": states,
        "R_ohm": r, "L_H": l, "mass_weights": weights,
        "metrics": {
            "relative_accumulated_energy_defect": float(np.max(abs(np.cumsum(defect))) / normalized),
            "relative_max_step_energy_defect": float(np.max(abs(defect)) / normalized),
            "source_off_max_relative_energy_increase": float(max(0., np.max(np.diff(energy)[off])) / normalized),
            "source_off_end_to_start_energy_ratio": float(energy[-1] / max(energy[off[0]], 1e-30)),
            "input_work_J": float(input_work),
            "left_resistor_loss_J": float(dt * np.sum(left_power)),
            "copper_resistor_loss_J": float(dt * np.sum(copper_power)),
            "terminal_work_J": float(dt * np.sum(load_power)),
            "final_stored_energy_J": float(energy[-1]),
        },
    }


def analyze(data, dt, sigma, termination):
    t = data["time_mid_s"]
    source_f = dft(t, data["source_V"], FREQ)
    left = dft(t, data["left_V"], FREQ)
    voltage = dft(t, data["wall_V"], FREQ)
    current = dft(t, data["wall_I_A"], FREQ)
    reflection = (left - source_f) / source_f
    s = 2j * np.pi * FREQ
    target = np.sqrt(s * MU / (sigma + s * EPS)) if termination == "copper" else (
        np.zeros(len(FREQ), complex) if termination == "pec" else np.full(len(FREQ), Z0, complex))
    gamma = (target - Z0) / (target + Z0)
    expected = gamma * np.exp(-2j * 2 * np.pi * FREQ * LENGTH / C0)
    row = {"reflection_absolute_error": float(np.max(abs(reflection - expected))),
           "source_band_min_relative_amplitude": float(np.min(abs(source_f)) / np.max(abs(source_f))),
           **data["metrics"]}
    spectra = {"frequency_Hz": FREQ, "input_spectrum_V_s": source_f,
               "left_reflection": reflection, "expected_left_reflection": expected,
               "wall_voltage_spectrum_V_s": voltage, "wall_current_spectrum_A_s": current}
    if termination == "copper":
        z = voltage / current
        sd = 2j / dt * np.tan(np.pi * FREQ * dt)
        r, l = data["R_ohm"], data["L_H"]
        digital = np.sum(1 / (1 / r + 1 / (sd[:, None] * l)), axis=1)
        boundary_error = float(np.max(abs(z / target - 1)))
        row.update({
            "wall_complex_impedance_error": boundary_error,
            "wall_phase_error_deg": float(np.max(abs(np.angle(z / target, deg=True)))),
            "wall_vs_discrete_RL_error": float(np.max(abs(z / digital - 1))),
            "wall_real_impedance_error": float(np.max(abs(z.real / target.real - 1))),
            "wall_impedance_1percent_pass": boundary_error < .01,
        })
        spectra.update(wall_impedance_ohm=z, target_impedance_ohm=target,
                       digital_RL_impedance_ohm=digital)
    row["reflection_1percent_absolute_pass"] = row["reflection_absolute_error"] < .01
    row["energy_consistency_pass"] = (row["relative_accumulated_energy_defect"] < 1e-8 and
                                      row["source_off_max_relative_energy_increase"] < 1e-10)
    return row, spectra


def main(args):
    args.output.mkdir(parents=True, exist_ok=True)
    args.raw_dir.mkdir(parents=True, exist_ok=True)
    models = json.loads(args.models.read_text(encoding="utf-8"))["models"]
    definitions = [(dx, dt, 64, 8, "copper")
                   for dx in (2, 1, .5) for dt in (1, 5, 20, 50)]
    definitions += [(.5, 1, 16, 8, "copper"), (.5, 1, 32, 8, "copper"),
                    (.5, 1, 64, 16, "copper"),
                    (.5, 1, 0, 8, "pec"), (.5, 1, 0, 8, "matched")]
    rows = []
    for dx_mm, dt_ps, terms, duration_ns, termination in definitions:
        model = next(m for m in models if m["terms"] == (terms or 64))
        cells = int(round(LENGTH / (dx_mm * 1e-3)))
        case = f"{termination}_dx{dx_mm:g}mm_dt{dt_ps:g}ps_m{terms}_t{duration_ns}ns"
        result = simulate(cells, dt_ps * 1e-12, duration_ns * 1e-9,
                          model["R_ohm"], model["L_H"], termination)
        metrics, spectra = analyze(result, dt_ps * 1e-12, model["sigma_S_per_m"], termination)
        raw = args.raw_dir / (case + ".npz")
        np.savez_compressed(raw, wave_states=result.pop("wave_states"),
                            RL_states_scaled=result.pop("RL_states_scaled"),
                            mass_weights=result.pop("mass_weights"))
        result.pop("metrics")
        data_path = args.output / (case + ".npz")
        np.savez_compressed(data_path, **result, **spectra)
        row = {"case": case, "cells": cells, "dx_mm": dx_mm, "dt_ps": dt_ps,
               "duration_ns": duration_ns, "terms": terms, "termination": termination,
               "sigma_S_per_m": model["sigma_S_per_m"], **metrics,
               "data_file": data_path.name, "data_sha256": sha(data_path),
               "raw_file": str(raw.resolve()), "raw_sha256": sha(raw)}
        rows.append(row)
        print(json.dumps(row), flush=True)
    summary = {
        "method": "standalone 1-D LC TEM plus passive boundary; implicit midpoint",
        "not_openems_FDTD": True, "case_count": len(rows), "cases": rows,
        "line_length_m": LENGTH, "TEM_Z0_ohm": Z0, "source_off_s": 2.4e-9,
        "normalization": "square parallel-plate TEM w/h=1, line V/A; NOT EWC energy",
        "models_file": str(args.models.resolve()), "models_sha256": sha(args.models),
        "runner_sha256": sha(Path(__file__)),
        "1d_transient_coupling_implemented": True,
        "openems_boundary_extension_complete": False, "3d_boundary_coupling_complete": False,
        "actual_connector_complete": False, "full_joint_simulation_complete": False,
    }
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--models", type=Path, default=Path(__file__).parent /
                        "\u539a\u94dc\u8fb9\u754c\u57fa\u51c6\u7ed3\u679c/passive_surface_models.json")
    parser.add_argument("--raw-dir", type=Path, default=Path(tempfile.gettempdir()) /
                        "ewc-openems/plane-transient")
    main(parser.parse_args())
