"""Independent sparse FEM assembly and traveling-wave evidence verification.

Does not import the runner or use its ABCD/normalization/analysis functions.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.sparse import lil_matrix
from scipy.sparse.linalg import spsolve

ROOT = Path(__file__).resolve().parent


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def close(actual, expected, label, rtol=1e-9, atol=1e-13):
    require(np.all(np.isfinite(actual)), f"{label}: nonfinite values")
    require(np.allclose(actual, expected, rtol=rtol, atol=atol), f"{label}: mismatch")


def read_archive(folder, row):
    path = (folder / row["archive"]).resolve()
    require(path.is_relative_to(folder.resolve()), "Archive escapes result directory")
    require(hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"],
            f"{row['id']}: archive hash mismatch")
    with np.load(path, allow_pickle=False) as npz:
        return {k: npz[k] for k in npz.files}


def verify(folder):
    folder = Path(folder)
    s = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    eps, mu = 8.8541878128e-12, 1.25663706212e-6
    close(s["constants"]["epsilon_f_per_m"], eps, "epsilon", atol=1e-24)
    close(s["constants"]["mu_h_per_m"], mu, "mu", atol=1e-20)
    require(s["schema"] == 1 and s["scope"] == "mathematical_air_coax_not_SMA_not_3D_feed",
            "Scope/schema changed")
    require(s["complete_physical_port_pass"] is False, "Unsupported full-port pass")
    require(s["thresholds"] == dict(relative_physical_error=0.01, consistency=1e-9),
            "Threshold changed")
    require(hashlib.sha256((ROOT / "run_coax_benchmark.py").read_bytes()).hexdigest()
            == s["runner_sha256"], "Runner provenance changed; rerun required")
    g = s["geometry"]
    inner, outer = g["inner_radius_m"], g["outer_inner_radius_m"]
    close(inner, 0.0005, "inner design radius")
    close(outer, inner * np.exp(100 * np.pi / np.sqrt(mu / eps)), "outer design radius")
    close(g["length_m"], 0.075, "length")
    require(g["dielectric"] == "uniform eps_r=mu_r=1" and
            g["conductors"] == "ideal PEC, no conductor loss", "Material scope changed")
    cref = 2 * np.pi * eps / np.log(outer / inner)
    lref = mu * np.log(outer / inner) / (2 * np.pi)
    for key, val in dict(c_f_per_m=cref, l_h_per_m=lref,
                         z0_ohm=np.sqrt(lref / cref),
                         speed_m_per_s=1 / np.sqrt(eps * mu)).items():
        close(s["analytic"][key], val, key, atol=abs(val) * 1e-12)
    expected_ids = {f"{mesh}_{n}" for mesh in ("uniform", "inner_graded")
                    for n in (2, 4, 8, 16, 32, 64, 128, 256)}
    require({r["id"] for r in s["electrostatic"]} == expected_ids and
            len(s["electrostatic"]) == 16, "Incomplete radial sweep")
    assembled_elements, checked_planes, failures, max_residual = 0, 0, [], 0.0
    finest = None
    by_mesh = {}
    for row in s["electrostatic"]:
        d = read_archive(folder, row)
        r, phi = d["r"], d["phi"]
        n = row["cells"]
        require(row["id"] == f"{row['mesh']}_{n}", "Mesh identity mismatch")
        t = np.arange(n + 1) / n
        expected_r = inner + (outer - inner) * (
            t if row["mesh"] == "uniform" else t**1.5)
        close(r, expected_r, "radial mesh", atol=1e-15)
        close(phi[[0, -1]], [1, 0], "Dirichlet voltage", atol=1e-12)
        require(np.all(np.diff(phi) < 0), "Potential is not monotone")
        # Assemble each element's two-node weak-form matrix independently.
        k = lil_matrix((n + 1, n + 1))
        energy, reactions = 0.0, []
        for j in range(n):
            width = r[j+1] - r[j]
            integral_r = (r[j+1]**2 - r[j]**2) / 2
            ke = 2 * np.pi * eps * integral_r / width**2
            k[j, j] += ke
            k[j+1, j+1] += ke
            k[j, j+1] -= ke
            k[j+1, j] -= ke
            energy += 0.5 * ke * (phi[j] - phi[j+1])**2
            reactions.append(ke * (phi[j] - phi[j+1]))
        k = k.tocsr()
        independent_phi = np.r_[1.0, spsolve(k[1:-1, 1:-1],
                                           -k[1:-1, 0].toarray().ravel()), 0.0]
        close(phi, independent_phi, "independent sparse FEM", atol=1e-10)
        q = k @ phi
        cap = float(q[0])
        residual = float(max(abs(q[1:-1])) / cap)
        max_residual = max(max_residual, residual)
        require(residual < 1e-8, "Discrete Gauss residual")
        close(q[-1], -cap, "opposite conductor charge", atol=cap*1e-9)
        close(reactions, cap, "constant radial flux", atol=cap*1e-9)
        close(d["c_f_per_m"], cap, "boundary capacitance", atol=cap*1e-9)
        close(2 * energy, cap, "electric energy capacitance", atol=cap*1e-9)
        close(d["electric_energy_j_per_m"], energy, "electric energy", atol=energy*1e-9)
        electric = -(phi[1:] - phi[:-1]) / (r[1:] - r[:-1])
        close(d["e_r"], electric, "radial electric field", atol=1e-8)
        close(np.dot(electric, np.diff(r)), 1, "voltage line integral")
        close(2*np.pi*r*d["h_phi"], np.ones(n+1), "Ampere loop at all radii")
        # Integrate H^2 * area with explicit endpoint weights, not runner trapz.
        densities = mu * abs(d["h_phi"])**2 * 2 * np.pi * r
        lind = sum((densities[j] + densities[j+1]) * (r[j+1]-r[j]) / 2
                   for j in range(n))
        close(d["l_h_per_m"], lind, "independent magnetic energy", atol=lind*1e-9)
        close(d["magnetic_energy_j_per_m"], lind/2, "stored magnetic energy",
              atol=lind*1e-9)
        z, speed = np.sqrt(lind / cap), 1 / np.sqrt(lind * cap)
        errors = dict(c=abs(cap/cref-1), l=abs(lind/lref-1),
                      z0=abs(z/50-1), speed=abs(speed*np.sqrt(eps*mu)-1),
                      potential=float(max(abs(phi-np.log(outer/r)/np.log(outer/inner)))))
        for key in errors:
            close(row["errors"][key], errors[key], f"error {key}", atol=2e-9)
        for key, val in dict(c_f_per_m=cap, l_h_per_m=lind, z0_ohm=z,
                             speed_m_per_s=speed).items():
            close(row[key], val, key, atol=abs(val)*1e-9)
        passed = all(errors[key] < 0.01 for key in ("c", "l", "z0", "speed"))
        require(row["physical_1percent_pass"] == passed, "False radial pass label")
        if not passed:
            failures.append(row["id"])
        by_mesh.setdefault(row["mesh"], []).append((n, errors))
        if row["id"] == "uniform_256":
            finest = (z, speed)
            finest_r, finest_e = r, electric
        assembled_elements += n
    for rows in by_mesh.values():
        rows.sort()
        for left, right in zip(rows, rows[1:]):
            require(all(right[1][k] < left[1][k] for k in ("c", "l", "z0", "speed")),
                    "Radial refinement did not improve")
    require(s["failures"] == failures, "Failure list changed")
    require(s["finest_radial_pass"] is True, "Finest physical status changed")
    z0, speed = finest
    close(s["line"]["z0_ohm"], z0, "line FEM impedance")
    close(s["line"]["speed_m_per_s"], speed, "line FEM speed")
    close(s["line"]["characteristic_reference_ohm"], z0, "wave reference")
    close(s["line"]["instrument_reference_ohm"], 50, "instrument reference")
    require(s["line"]["parameter_source"] == "uniform_256", "Line parameter source")
    close(s["source"]["v_peak_v"], 1, "source voltage")
    close(s["source"]["resistance_ohm"], 50, "source resistance")
    load_map = dict(R25=25, R50=50, R100=100, R50_X25=50+25j,
                    R5_X100=5+100j, open="open", short="short")
    expected_line_ids = {f"{name}_{i:02d}" for name in load_map for i in range(41)}
    require(len(s["transmission"]) == 287 and
            {r["id"] for r in s["transmission"]} == expected_line_ids,
            "Incomplete transmission sweep")
    max_vi_error, max_power_error, max_move_error = 0.0, 0.0, 0.0
    matched_powers = []
    for row in s["transmission"]:
        d = read_archive(folder, row)
        index = int(row["id"].rsplit("_", 1)[1])
        require(row["id"] == f"{row['load_id']}_{index:02d}", "Line identity mismatch")
        close(row["frequency_hz"], np.linspace(2.5e9, 3.5e9, 41)[index], "frequency")
        x = np.array([0, 0.015, 0.045, 0.075])
        close(d["positions_m"], x, "reference planes")
        close(s["line"]["reference_positions_m"], x, "summary reference planes")
        beta = 2 * np.pi * row["frequency_hz"] / speed
        load = load_map[row["load_id"]]
        encoded = load if isinstance(load, str) else [complex(load).real, complex(load).imag]
        require(row["load_ohm"] == encoded, "Load metadata changed")
        gl = (1 if load == "open" else -1 if load == "short" else
              (load-z0)/(load+z0))
        # Solve in forward/backward wave basis rather than using a transfer matrix.
        g0 = gl * np.exp(-2j * beta * 0.075)
        vp0 = 1 / ((1 + 50/z0) + (1 - 50/z0) * g0)
        plus = vp0 * np.exp(-1j*beta*x)
        minus = vp0 * gl * np.exp(-1j*beta*(0.15-x))
        expected_vi = np.column_stack((plus+minus, (plus-minus)/z0))
        close(d["vi"], expected_vi, "ABCD vs traveling-wave solve")
        max_vi_error = max(max_vi_error, float(np.max(abs(d["vi"]-expected_vi))))
        v, i = d["vi"][:, 0], d["vi"][:, 1]
        expected_waves = np.column_stack((plus, minus)) / np.sqrt(2*z0)
        close(d["waves"], expected_waves, "characteristic power normalization")
        # Fixed 50-ohm waves are distinct from characteristic waves for FEM Z0.
        w50 = np.column_stack((v+50*i, v-50*i)) / np.sqrt(400)
        close(d["waves_50"], w50, "50-ohm instrument normalization")
        for w in (d["waves"], d["waves_50"]):
            close(abs(w[:, 0])**2-abs(w[:, 1])**2, d["power_w"], "wave net power")
        p = 0.5 * np.real(expected_vi[:, 0] * np.conj(expected_vi[:, 1]))
        close(d["power_w"], p, "VI power")
        area_power = np.zeros(4)
        for j in range(len(finest_e)):
            rm = (finest_r[j]+finest_r[j+1])/2
            er = finest_e[j] * v
            hp = i / (2*np.pi*rm)
            area_power += 0.5*np.real(er*np.conj(hp))*2*np.pi*rm*(
                finest_r[j+1]-finest_r[j])
        close(d["poynting_w"], area_power, "independent E cross H area integral")
        close(area_power, p, "area integral vs VI power")
        close(p, np.repeat(p[-1], 4), "lossless line power at all planes")
        require(min(p) >= -1e-12 and max(p) <= 0.0025+1e-12, "Passive power bound")
        if isinstance(load, str):
            close(p[-1], 0, "reactive termination power")
            close(i[-1] if load == "open" else v[-1], 0, "open/short boundary")
        else:
            close(v[-1], load*i[-1], "load constitutive relation")
            close(p[-1], 0.5*load.real*abs(i[-1])**2, "resistive load power")
        close(v[0]+50*i[0], 1, "Thevenin source equation")
        psrc, prs = 0.5*np.real(np.conj(i[0])), 25*abs(i[0])**2
        close(d["source_power_w"], psrc, "source generated power")
        close(d["source_resistor_w"], prs, "source resistor power")
        close(d["available_source_w"], 0.0025, "available matched source power")
        close(psrc-prs, p[-1], "source resistor and load power balance")
        close(row["load_power_w"], p[-1], "summary load power")
        gamma = d["waves"][:, 1] / d["waves"][:, 0]
        expected_gamma = gl*np.exp(-2j*beta*(0.075-x))
        close(gamma, expected_gamma, "reference plane roundtrip phase")
        close(gamma*np.exp(2j*beta*(0.075-x)), gl, "load deembedding")
        close(abs(gamma), abs(gl)*np.ones(4), "reflection magnitude invariant")
        move_error = float(max(abs(gamma-expected_gamma)))
        wave_error = float(max(abs(abs(d["waves"][:, 0])**2 -
                                  abs(d["waves"][:, 1])**2-d["power_w"])))
        source_error = float(abs(float(d["source_power_w"]) -
                                 float(d["source_resistor_w"]) - d["power_w"][0]))
        close(row["reflection_move_error"], move_error, "summary reflection error",
              atol=1e-12)
        close(row["power_wave_identity_error_w"], wave_error, "summary wave error")
        close(row["source_power_balance_error_w"], source_error, "summary source error")
        require(row["consistency_pass"] is True and
                max(move_error, wave_error, source_error) < 1e-9, "Line consistency fail")
        max_power_error = max(max_power_error, wave_error, source_error)
        max_move_error = max(max_move_error, move_error)
        if row["load_id"] == "R50":
            matched_powers.append(float(p[-1]))
        checked_planes += len(x)
    expected_counts = dict(radial_fem_solves=16, radial_physical_pass=16-len(failures),
                           radial_physical_fail=len(failures), transmission_cases=287,
                           reference_planes=4, fullwave_3ghz_feed_solves=0,
                           copper_loss_solves=0, actual_sma_models=0)
    require(s["counts"] == expected_counts, "Misleading counts")
    require(s["all_transmission_consistency_pass"] is True, "Transmission status")
    return dict(independent_verification_pass=True, counts=expected_counts,
                independent_sparse_fem_solves=16,
                independently_assembled_radial_elements=assembled_elements,
                checked_transmission_reference_samples=checked_planes,
                checked_raw_archive_hashes=303,
                max_discrete_gauss_relative_residual=max_residual,
                max_abcd_vs_waves_vi_absolute_error=max_vi_error,
                max_power_balance_absolute_error_w=max_power_error,
                max_reflection_move_absolute_error=max_move_error,
                matched_R50_load_power_w_range=[min(matched_powers), max(matched_powers)],
                retained_physical_failures=failures,
                verifier_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                summary_sha256=hashlib.sha256((folder/"summary.json").read_bytes()).hexdigest(),
                note="Reference samples and hash checks are not extra physical solves.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", nargs="?", type=Path, default=ROOT/"结果")
    args = parser.parse_args()
    require(args.folder.resolve().is_relative_to(ROOT), "Write only under this line")
    result = verify(args.folder)
    (args.folder/"verification.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
