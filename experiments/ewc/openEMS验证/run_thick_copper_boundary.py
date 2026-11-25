"""One-sided conductor FEM and a passive Foster surface-impedance candidate.

This is a NEW standalone 1-D frequency-domain FEM, not openEMS FDTD. The
Foster model is an equivalent boundary kernel, not a purchasable component BOM.
It is NOT coupled into a cavity or accepted as a native openEMS boundary.
"""

import argparse
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

import numpy as np
from scipy.linalg import solve_banded

MU0 = 4e-7 * np.pi
EPS0 = 1 / (MU0 * 299792458.0**2)
FREQUENCIES = np.linspace(2.5e9, 3.5e9, 101)
SIGMA_TYPICAL = 58.58e6


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def halfspace_reference(frequency, sigma):
    s = 2j * np.pi * np.asarray(frequency)
    return np.sqrt(s * MU0 / (sigma + s * EPS0))


def finite_depth_reference(frequency, sigma, depth):
    s = 2j * np.pi * np.asarray(frequency)
    gamma = np.sqrt(s * MU0 * (sigma + s * EPS0))
    return halfspace_reference(frequency, sigma) * np.tanh(gamma * depth)


def solve_conductor(frequency, sigma, depth, cells):
    """Solve E''=s*mu*(sigma+s*eps)*E; -E'(0)=s*mu*H0, E(depth)=0.

    Linear elements with consistent mass. H0=1 A/m is boundary data; the
    analytic impedance is never used in assembly or solution.
    """
    if sigma <= 0 or depth <= 0 or cells < 2:
        raise ValueError("Positive sigma/depth and at least two cells required.")
    h = depth / cells
    fields, impedances, loss, surface = [], [], [], []
    for f in frequency:
        s = 2j * np.pi * f
        coefficient = s * MU0 * (sigma + s * EPS0)
        local_diag = 1 / h + coefficient * h / 3
        local_off = -1 / h + coefficient * h / 6
        diagonal = np.full(cells, 2 * local_diag, dtype=complex)
        diagonal[0] = local_diag
        matrix = np.zeros((3, cells), complex)
        matrix[1] = diagonal
        matrix[0, 1:] = local_off
        matrix[2, :-1] = local_off
        rhs = np.zeros(cells, complex)
        rhs[0] = s * MU0
        field = np.r_[solve_banded((1, 1), matrix, rhs), 0j]
        fields.append(field)
        impedances.append(field[0])
        left, right = field[:-1], field[1:]
        integral = h / 3 * np.sum(
            abs(left)**2 + np.real(left * np.conj(right)) + abs(right)**2)
        loss.append(.5 * sigma * integral)
        surface.append(.5 * field[0].real)
    return np.asarray(impedances), np.asarray(fields), np.asarray(loss), np.asarray(surface)


def foster_parameters(sigma, terms, low_frequency=.5e9, high_frequency=6e9):
    """Positive log-quadrature for sqrt(s) = integral s/(s+p)/sqrt(p) dp/pi."""
    if sigma <= 0 or terms < 8:
        raise ValueError("Positive sigma and at least eight Foster terms required.")
    logp = np.linspace(np.log(2 * np.pi * low_frequency * 1e-8),
                       np.log(2 * np.pi * high_frequency * 1e8), terms)
    step = logp[1] - logp[0]
    weights = np.full(terms, step)
    weights[[0, -1]] *= .5
    poles = np.exp(logp)
    resistance = np.sqrt(MU0 / sigma) / np.pi * np.sqrt(poles) * weights
    inductance = resistance / poles
    return poles, resistance, inductance


def foster_impedance(frequency, poles, resistance, dt=None):
    omega = 2 * np.pi * np.asarray(frequency)
    if np.any(omega <= 0) or np.any(np.asarray(poles) <= 0) or np.any(np.asarray(resistance) <= 0):
        raise ValueError("Positive frequencies, poles and resistance required.")
    if dt is not None:
        if not np.isfinite(dt) or dt <= 0:
            raise ValueError("Positive finite timestep required.")
        if np.max(omega * dt) >= np.pi:
            raise ValueError("Frequency reaches discrete-time Nyquist.")
        s = 2j * np.tan(omega * dt / 2) / dt
    else:
        s = 1j * omega
    return np.sum(resistance[None, :] * s[:, None] / (s[:, None] + poles[None, :]), axis=1)


def write_spice(path, resistance, inductance):
    lines = [
        "* Passive Foster impedance per square; boundary model, NOT a hardware BOM",
        "Iprobe 0 port dc 0 ac 1",
    ]
    for i, (r, l) in enumerate(zip(resistance, inductance)):
        a = "port" if i == 0 else f"n{i}"
        b = "0" if i == len(resistance) - 1 else f"n{i+1}"
        lines += [f"R{i+1} {a} {b} {r:.17g}", f"L{i+1} {a} {b} {l:.17g}"]
    lines += [
        ".control", "set noaskquit", "set numdgt=17",
        "set wr_singlescale", "set wr_vecnames",
        "ac lin 101 2.5G 3.5G", "wrdata impedance.dat v(port)", "quit",
        ".endc", ".end",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


def error_metrics(observed, expected):
    return {
        "max_relative_complex_error": float(np.max(abs(observed / expected - 1))),
        "max_relative_amplitude_error": float(np.max(abs(abs(observed) / abs(expected) - 1))),
        "max_phase_error_deg": float(np.max(abs(np.angle(observed / expected, deg=True)))),
        "positive_real": bool(np.all(observed.real >= 0)),
    }


def write_csv(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main(args):
    args.output.mkdir(parents=True, exist_ok=True)
    args.raw_dir.mkdir(parents=True, exist_ok=True)
    fem_cases = []
    for sigma in (58e6, SIGMA_TYPICAL):
        for depth_um in (2, 8, 35):
            previous = None
            for cells in (64, 128, 256):
                case = f"sigma{sigma:g}_depth{depth_um}um_n{cells}"
                depth = depth_um * 1e-6
                z, fields, loss, incoming = solve_conductor(
                    FREQUENCIES, sigma, depth, cells)
                finite = finite_depth_reference(FREQUENCIES, sigma, depth)
                half = halfspace_reference(FREQUENCIES, sigma)
                metrics = error_metrics(z, finite)
                half_metrics = error_metrics(z, half)
                closure = float(np.max(abs(loss / incoming - 1)))
                row = {
                    "case": case, "sigma_S_per_m": sigma, "depth_um": depth_um,
                    "cells": cells, "mesh_nm": depth / cells * 1e9,
                    "finite_depth_complex_error": metrics["max_relative_complex_error"],
                    "halfspace_complex_error": half_metrics["max_relative_complex_error"],
                    "halfspace_amplitude_error": half_metrics["max_relative_amplitude_error"],
                    "halfspace_phase_error_deg": half_metrics["max_phase_error_deg"],
                    "relative_power_closure_error": closure,
                    "refinement_change_relative": None if previous is None else
                        float(np.max(abs(z - previous) / abs(z))),
                    "finite_depth_1percent_pass": metrics["max_relative_complex_error"] < .01,
                    "halfspace_1percent_pass": half_metrics["max_relative_complex_error"] < .01,
                }
                fem_cases.append(row)
                previous = z
                np.savez_compressed(
                    args.output / (case + ".npz"), frequency_Hz=FREQUENCIES,
                    depth_m=np.linspace(0, depth, cells + 1), E_V_per_m=fields,
                    impedance_ohm=z, finite_depth_reference=finite,
                    halfspace_reference=half, joule_power_W_per_m2=loss,
                    surface_power_W_per_m2=incoming)
                print(json.dumps(row), flush=True)
    write_csv(args.output / "fem_cases.csv", fem_cases)
    model_cases, time_cases, spice_cases, models = [], [], [], []
    guard_frequency = np.geomspace(.5e9, 6e9, 401)
    for terms in (16, 32, 64):
        poles, resistance, inductance = foster_parameters(SIGMA_TYPICAL, terms)
        z = foster_impedance(FREQUENCIES, poles, resistance)
        metrics = error_metrics(z, halfspace_reference(FREQUENCIES, SIGMA_TYPICAL))
        guard = error_metrics(foster_impedance(guard_frequency, poles, resistance),
                              halfspace_reference(guard_frequency, SIGMA_TYPICAL))
        model_cases.append({"terms": terms, **metrics,
                            "guard_band_complex_error": guard["max_relative_complex_error"]})
        models.append({
            "terms": terms, "sigma_S_per_m": SIGMA_TYPICAL,
            "pole_rad_per_s": poles.tolist(), "R_ohm": resistance.tolist(),
            "L_H": inductance.tolist(),
            "positive_R_and_L": bool(np.all(resistance > 0) and np.all(inductance > 0)),
            "native_openems_boundary_verified": False, "physical_BOM": False,
        })
        for dt_ps in (1, 5, 20, 50):
            digital = foster_impedance(FREQUENCIES, poles, resistance, dt_ps * 1e-12)
            time_cases.append({
                "terms": terms, "timestep_ps": dt_ps,
                **error_metrics(digital, halfspace_reference(FREQUENCIES, SIGMA_TYPICAL)),
            })
        folder = args.raw_dir / f"foster_{terms}"
        folder.mkdir(parents=True, exist_ok=True)
        netlist = folder / "surface_impedance.cir"
        write_spice(netlist, resistance, inductance)
        process = subprocess.run(
            [str(args.ngspice), "-b", str(netlist)], cwd=folder,
            capture_output=True, timeout=60)
        (folder / "ngspice.log").write_bytes(process.stdout + process.stderr)
        if process.returncode:
            raise RuntimeError(f"ngspice failed ({process.returncode}): {folder}")
        data = np.loadtxt(folder / "impedance.dat", skiprows=1)
        if data.shape != (101, 3) or not np.allclose(data[:, 0], FREQUENCIES, rtol=1e-12):
            raise RuntimeError("Unexpected ngspice frequency/complex output format.")
        zs = data[:, 1] + 1j * data[:, 2]
        spice_row = {
            "terms": terms,
            "ngspice_vs_foster_max_relative_error": float(np.max(abs(zs / z - 1))),
            "ngspice_vs_halfspace": error_metrics(zs, halfspace_reference(
                FREQUENCIES, SIGMA_TYPICAL)),
            "raw_folder": str(folder),
            "raw_manifest": [{"name": p.name, "sha256": digest(p)}
                             for p in sorted(folder.iterdir()) if p.is_file()],
        }
        spice_cases.append(spice_row)
        np.savez_compressed(args.output / f"foster_{terms}.npz",
                            frequency_Hz=FREQUENCIES, impedance=z, ngspice_impedance=zs,
                            guard_frequency_Hz=guard_frequency,
                            guard_impedance=foster_impedance(guard_frequency, poles, resistance))
        print(json.dumps({"model": model_cases[-1], "spice": spice_row}), flush=True)
    (args.output / "passive_surface_models.json").write_text(
        json.dumps({"models": models, "scope": "one-sided local boundary candidate only"},
                   indent=2, allow_nan=False), encoding="utf-8")
    result = {
        "method": "standalone 1-D linear FEM plus passive Foster model and ngspice AC",
        "not_openems_FDTD": True, "frequency_band_Hz": [2.5e9, 3.5e9],
        "fem_case_count": len(fem_cases), "fem_cases": fem_cases,
        "foster_cases": model_cases, "bilinear_frequency_checks": time_cases,
        "ngspice_cases": spice_cases, "script_sha256": digest(Path(__file__)),
        "ngspice_sha256": digest(args.ngspice),
        "3d_boundary_coupling_complete": False,
        "transient_closed_loop_complete": False, "full_joint_simulation_complete": False,
    }
    (args.output / "summary.json").write_text(
        json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, default=Path(tempfile.gettempdir()) / "ewc-openems/thick-boundary")
    parser.add_argument("--ngspice", type=Path, default=Path(tempfile.gettempdir()) /
                        "ewc-openems/ngspice47/Spice64/bin/ngspice_con.exe")
    main(parser.parse_args())
