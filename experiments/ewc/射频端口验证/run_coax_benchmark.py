"""Independent radial electrostatic FEM and lossless coax reference benchmark."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

import numpy as np
from scipy.linalg import solve_banded

ROOT = Path(__file__).resolve().parent
EPS0 = 8.8541878128e-12
MU0 = 1.25663706212e-6
TOL = 0.01


def geometry():
    eta = np.sqrt(MU0 / EPS0)
    a = 0.5e-3
    b = a * np.exp(2 * np.pi * 50.0 / eta)
    return a, b


def reference(a, b):
    if not 0 < a < b:
        raise ValueError("Require 0 < inner radius < outer radius")
    log_ratio = np.log(b / a)
    return (2 * np.pi * EPS0 / log_ratio,
            MU0 * log_ratio / (2 * np.pi))


def radial_fem(a, b, cells, mesh):
    """Solve integral r-weighted Laplace weak form, not analytic log potential."""
    if cells < 2 or not 0 < a < b or mesh not in ("uniform", "inner_graded"):
        raise ValueError("Invalid radial mesh")
    t = np.linspace(0, 1, cells + 1)
    r = a + (b - a) * (t if mesh == "uniform" else t**1.5)
    dr = np.diff(r)
    mid = (r[:-1] + r[1:]) / 2
    stiffness = 2 * np.pi * EPS0 * mid / dr
    diagonal = stiffness[:-1] + stiffness[1:]
    band = np.zeros((3, cells - 1))
    band[1] = diagonal
    band[0, 1:] = -stiffness[1:-1]
    band[2, :-1] = -stiffness[1:-1]
    rhs = np.zeros(cells - 1)
    rhs[0] = stiffness[0]  # Dirichlet inner=1 V, outer=0 V
    phi = np.concatenate(([1.0], solve_banded((1, 1), band, rhs), [0.0]))
    e_r = -np.diff(phi) / dr
    # Boundary reaction charge per metre; energy is separately evaluated.
    cap = stiffness[0] * (phi[0] - phi[1])
    energy_e = np.sum(np.pi * EPS0 * mid * dr * e_r**2)
    h_phi = 1 / (2 * np.pi * r)  # H for 1 A axial inner-conductor current
    # Independent endpoint quadrature of magnetic energy; no use of computed C.
    inductance = np.trapz(MU0 / (2 * np.pi * r), r)
    return dict(r=r, phi=phi, e_r=e_r, h_phi=h_phi,
                c_f_per_m=np.array(cap), l_h_per_m=np.array(inductance),
                electric_energy_j_per_m=np.array(energy_e),
                magnetic_energy_j_per_m=np.array(inductance / 2))


def abcd(z0, beta, distance):
    c, s = np.cos(beta * distance), np.sin(beta * distance)
    return np.array([[c, 1j * z0 * s], [1j * s / z0, c]], complex)


def power_waves(v, i, resistance):
    """Peak phasors -> power waves in sqrt(W), real positive reference only."""
    if not np.isreal(resistance) or resistance <= 0:
        raise ValueError("Real positive reference resistance required")
    scale = 2 * np.sqrt(2 * resistance)
    return (v + resistance * i) / scale, (v - resistance * i) / scale


def line_case(z0, speed, freq, load, positions, length=0.075):
    """1 V peak Thevenin source, Rs=50 ohm; current positive toward load."""
    positions = np.asarray(positions, dtype=float)
    if (not np.all(np.isfinite([z0, speed, freq, length])) or
            min(z0, speed, freq) <= 0 or length < 0 or
            positions.ndim != 1 or not len(positions) or
            not np.all(np.isfinite(positions)) or
            np.any(positions < 0) or np.any(positions > length)):
        raise ValueError("Invalid lossless line parameters/reference positions")
    beta = 2 * np.pi * freq / speed
    q = (np.array([1, 0], complex) if load == "open" else
         np.array([0, 1], complex) if load == "short" else
         np.array([complex(load), 1], complex))
    inp = abcd(z0, beta, length) @ q
    terminal = q / (inp[0] + 50 * inp[1])
    vi = np.array([abcd(z0, beta, length - x) @ terminal for x in positions])
    v, i = vi[:, 0], vi[:, 1]
    a, b = power_waves(v, i, z0)
    a50, b50 = power_waves(v, i, 50.0)
    power = 0.5 * np.real(v * np.conj(i))
    return dict(vi=vi, waves=np.column_stack((a, b)),
                waves_50=np.column_stack((a50, b50)), power_w=power,
                source_power_w=np.array(0.5 * np.real(np.conj(i[0]))),
                source_resistor_w=np.array(25 * abs(i[0])**2),
                available_source_w=np.array(1 / 400))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(output):
    output = Path(output).resolve()
    if not output.is_relative_to(ROOT):
        raise ValueError("Archive output must stay under this independent line")
    output.mkdir(parents=True, exist_ok=True)
    a, b = geometry()
    c_ref, l_ref = reference(a, b)
    z_ref = np.sqrt(l_ref / c_ref)
    v_ref = 1 / np.sqrt(l_ref * c_ref)
    summary = dict(schema=1, scope="mathematical_air_coax_not_SMA_not_3D_feed",
                   constants=dict(epsilon_f_per_m=EPS0, mu_h_per_m=MU0),
                   geometry=dict(inner_radius_m=a, outer_inner_radius_m=b,
                                 length_m=0.075, dielectric="uniform eps_r=mu_r=1",
                                 conductors="ideal PEC, no conductor loss"),
                   analytic=dict(c_f_per_m=c_ref, l_h_per_m=l_ref,
                                 z0_ohm=z_ref, speed_m_per_s=v_ref),
                   thresholds=dict(relative_physical_error=TOL,
                                   consistency=1e-9),
                   source=dict(v_peak_v=1.0, resistance_ohm=50.0),
                   runner_sha256=digest(Path(__file__)),
                   electrostatic=[], transmission=[])
    # Solver intermediates are outside the project; reproducible evidence copied in.
    with tempfile.TemporaryDirectory(prefix="ewc-coax-") as scratch:
        scratch = Path(scratch)
        finest = None
        for mesh in ("uniform", "inner_graded"):
            for n in (2, 4, 8, 16, 32, 64, 128, 256):
                data = radial_fem(a, b, n, mesh)
                c, l = float(data["c_f_per_m"]), float(data["l_h_per_m"])
                z, speed = np.sqrt(l / c), 1 / np.sqrt(l * c)
                errors = dict(c=abs(c / c_ref - 1), l=abs(l / l_ref - 1),
                              z0=abs(z / z_ref - 1), speed=abs(speed / v_ref - 1),
                              potential=float(np.max(np.abs(
                                  data["phi"] - np.log(b / data["r"]) / np.log(b / a)))))
                passed = all(errors[k] < TOL for k in ("c", "l", "z0", "speed"))
                name = f"radial_{mesh}_{n}.npz"
                np.savez(scratch / name, **data)
                shutil.copy2(scratch / name, output / name)
                summary["electrostatic"].append(dict(
                    id=f"{mesh}_{n}", cells=n, mesh=mesh, archive=name,
                    sha256=digest(output / name), c_f_per_m=c, l_h_per_m=l,
                    z0_ohm=z, speed_m_per_s=speed, errors=errors,
                    physical_1percent_pass=passed))
                if mesh == "uniform" and n == 256:
                    finest = (z, speed)
                    finest_fields = data
        z, speed = finest
        positions = np.array([0.0, 0.015, 0.045, 0.075])
        summary["line"] = dict(z0_ohm=z, speed_m_per_s=speed,
                               parameter_source="uniform_256",
                               reference_positions_m=positions.tolist(),
                               characteristic_reference_ohm=z,
                               instrument_reference_ohm=50.0)
        loads = [("R25", 25), ("R50", 50), ("R100", 100),
                 ("R50_X25", 50+25j), ("R5_X100", 5+100j),
                 ("open", "open"), ("short", "short")]
        for load_id, load in loads:
            for index, freq in enumerate(np.linspace(2.5e9, 3.5e9, 41)):
                data = line_case(z, speed, freq, load, positions)
                vi, waves = data["vi"], data["waves"]
                radii = finest_fields["r"]
                midpoint = (radii[:-1] + radii[1:]) / 2
                transverse_e = vi[:, 0, None] * finest_fields["e_r"][None, :]
                transverse_h = vi[:, 1, None] / (2 * np.pi * midpoint[None, :])
                data["poynting_w"] = 0.5 * np.real(np.sum(
                    transverse_e * np.conj(transverse_h) *
                    (2*np.pi*midpoint*np.diff(radii))[None, :], axis=1))
                gamma = waves[:, 1] / waves[:, 0]
                expected = gamma[-1] * np.exp(
                    -2j * 2 * np.pi * freq / speed * (0.075 - positions))
                power_error = float(np.max(np.abs(
                    abs(waves[:, 0])**2 - abs(waves[:, 1])**2 - data["power_w"])))
                source_error = float(abs(data["source_power_w"] -
                                         data["source_resistor_w"] - data["power_w"][0]))
                name = f"line_{load_id}_{index:02d}.npz"
                np.savez(scratch / name, positions_m=positions, **data)
                shutil.copy2(scratch / name, output / name)
                summary["transmission"].append(dict(
                    id=f"{load_id}_{index:02d}", frequency_hz=float(freq),
                    load_id=load_id,
                    load_ohm=load if isinstance(load, str) else
                    [float(complex(load).real), float(complex(load).imag)],
                    archive=name, sha256=digest(output / name),
                    reflection_move_error=float(np.max(abs(gamma - expected))),
                    power_wave_identity_error_w=power_error,
                    source_power_balance_error_w=source_error,
                    load_power_w=float(data["power_w"][-1]),
                    consistency_pass=bool(max(power_error, source_error,
                                              np.max(abs(gamma - expected))) < 1e-9)))
    summary["counts"] = dict(
        radial_fem_solves=len(summary["electrostatic"]),
        radial_physical_pass=sum(x["physical_1percent_pass"] for x in summary["electrostatic"]),
        radial_physical_fail=sum(not x["physical_1percent_pass"] for x in summary["electrostatic"]),
        transmission_cases=len(summary["transmission"]),
        reference_planes=len(positions),
        fullwave_3ghz_feed_solves=0, copper_loss_solves=0, actual_sma_models=0)
    summary["failures"] = [x["id"] for x in summary["electrostatic"]
                           if not x["physical_1percent_pass"]]
    summary["finest_radial_pass"] = all(
        x["physical_1percent_pass"] for x in summary["electrostatic"] if x["cells"] == 256)
    summary["all_transmission_consistency_pass"] = all(
        x["consistency_pass"] for x in summary["transmission"])
    summary["complete_physical_port_pass"] = False
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps(summary["counts"], ensure_ascii=False))
    print("Historical/coarse physical failures:", ", ".join(summary["failures"]))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "结果")
    run(parser.parse_args().output)
