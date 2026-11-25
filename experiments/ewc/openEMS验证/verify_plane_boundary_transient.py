"""Independent trace/energy/equation audit and LC ABCD frequency reference.

Does not import the transient runner or use its assembled operator.
"""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import numpy as np

MU = 4e-7 * np.pi
C0 = 299792458.
EPS = 1 / (MU * C0**2)
Z0 = np.sqrt(MU / EPS)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def spectrum(time, value, frequency):
    result = []
    for f in frequency:
        result.append(np.sum(value * np.exp(-2j * np.pi * f * time)) * (time[1] - time[0]))
    return np.asarray(result)


def ladder_reflection(frequency, dt, dx, cells, load):
    """Cascade independently derived shunt(C/2)-series(L)-shunt(C/2) cells."""
    observed = []
    for f, zload in zip(frequency, load):
        s = 2j * np.tan(np.pi * f * dt) / dt
        shunt = np.array([[1, 0], [s * EPS * dx / 2, 1]], complex)
        series = np.array([[1, s * MU * dx], [0, 1]], complex)
        abcd = np.linalg.matrix_power(shunt @ series @ shunt, cells)
        zin = (abcd[0, 0] * zload + abcd[0, 1]) / (abcd[1, 0] * zload + abcd[1, 1])
        observed.append((zin - Z0) / (zin + Z0))
    return np.asarray(observed)


def main(folder):
    summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    assert sha(Path(__file__).with_name("run_plane_boundary_transient.py")) == summary["runner_sha256"]
    assert sha(Path(summary["models_file"])) == summary["models_sha256"]
    assert summary["case_count"] == 17 and summary["not_openems_FDTD"]
    assert not summary["3d_boundary_coupling_complete"]
    fem = Path(summary["models_file"]).parent / "sigma5.858e+07_depth35um_n256.npz"
    checks = []
    for row in summary["cases"]:
        path, raw_path = folder / row["data_file"], Path(row["raw_file"])
        assert sha(path) == row["data_sha256"] and sha(raw_path) == row["raw_sha256"]
        with np.load(path, allow_pickle=False) as data, np.load(raw_path, allow_pickle=False) as raw:
            t = data["time_mid_s"]
            dt, n = row["dt_ps"] * 1e-12, row["cells"]
            dx = summary["line_length_m"] / n
            wave, states = raw["wave_states"], raw["RL_states_scaled"]
            assert wave.shape == (len(t) + 1, 2 * n + 1)
            assert np.all(np.isfinite(wave)) and np.all(np.isfinite(states))
            weights = np.full(2 * n + 1, EPS * dx)
            weights[[0, n]] /= 2
            np.testing.assert_allclose(weights, raw["mass_weights"], rtol=1e-14)
            mid = (wave[1:] + wave[:-1]) / 2
            mids = (states[1:] + states[:-1]) / 2
            r, l = data["R_ohm"], data["L_H"]
            energy = .5 * (wave * wave) @ weights + .5 * (states * states) @ (l / Z0**2)
            np.testing.assert_allclose(energy, data["energy_J"], rtol=1e-10, atol=1e-30)
            np.testing.assert_allclose(mid[:, 0], data["left_V"], rtol=1e-12, atol=1e-20)
            np.testing.assert_allclose(mid[:, n], data["wall_V"], rtol=1e-12, atol=1e-20)
            work = 2 * data["source_V"] * mid[:, 0] / Z0
            left_loss = mid[:, 0]**2 / Z0
            j = data["wall_I_A"]
            copper_loss = np.sum(r * (j[:, None] - mids / Z0)**2, axis=1)
            np.testing.assert_allclose(copper_loss, data["copper_power_W"], rtol=1e-8, atol=1e-24)
            wall_work = mid[:, n] * j
            dissipation = copper_loss if row["termination"] == "copper" else wall_work
            scale = max(abs(dt * np.sum(work)), np.max(energy), 1e-30)
            closure = np.max(abs(energy[1:] - energy[0] -
                                 dt * np.cumsum(work - left_loss - dissipation))) / scale
            assert closure < 1e-8
            off = t >= summary["source_off_s"]
            assert np.all(data["source_V"][off] == 0)
            increase = max(0., np.max(np.diff(energy)[off])) / scale
            assert increase < 1e-10
            np.testing.assert_allclose(closure, row["relative_accumulated_energy_defect"],
                                       rtol=1e-3, atol=1e-12)
            # Independent nodal charge balance, edge flux and RL equations.
            selected = np.unique(np.linspace(0, len(t) - 1, 65).astype(int))
            wm = mid[selected]
            derivative = (wave[selected + 1] - wave[selected]) / dt
            cap = weights[:n + 1]
            injected = np.zeros((len(selected), n + 1))
            currents = wm[:, n + 1:] / Z0
            injected[:, :-1] -= currents
            injected[:, 1:] += currents
            injected[:, 0] += (2 * data["source_V"][selected] - wm[:, 0]) / Z0
            injected[:, n] -= j[selected]
            charge_residual = np.max(abs(derivative[:, :n + 1] * cap - injected))
            charge_scale = max(np.max(abs(currents)), 1e-30)
            flux_residual = np.max(abs(MU * dx / Z0 * derivative[:, n + 1:] -
                                       (wm[:, :n] - wm[:, 1:n + 1])))
            flux_scale = max(np.max(abs(wm[:, :n + 1])), 1e-30)
            assert charge_residual / charge_scale < 1e-7
            assert flux_residual / flux_scale < 1e-7
            branch_residual = algebraic_residual = 0.
            if row["termination"] == "copper":
                branch_residual = np.max(abs(l * (states[1:] - states[:-1]) / (dt * Z0) -
                                            r * (j[:, None] - mids / Z0)))
                algebraic_residual = np.max(abs(mid[:, n] -
                                               np.sum(r * (j[:, None] - mids / Z0), axis=1)))
                assert branch_residual / max(np.max(abs(mid[:, n])), 1e-30) < 1e-6
                assert algebraic_residual / max(np.max(abs(mid[:, n])), 1e-30) < 1e-6
            f = data["frequency_Hz"]
            a = spectrum(t, data["source_V"], f)
            reflection = spectrum(t, mid[:, 0], f) / a - 1
            np.testing.assert_allclose(reflection, data["left_reflection"], rtol=1e-10, atol=1e-10)
            if row["termination"] == "copper":
                sd = 2j * np.tan(np.pi * f * dt) / dt
                load = np.sum(1 / (1 / r + 1 / (sd[:, None] * l)), axis=1)
                z = spectrum(t, mid[:, n], f) / spectrum(t, j, f)
                np.testing.assert_allclose(z, data["wall_impedance_ohm"], rtol=1e-9)
                s = 2j * np.pi * f
                physical_load = np.sqrt(s * MU / (row["sigma_S_per_m"] + s * EPS))
                wall_error = float(np.max(abs(z / physical_load - 1)))
                np.testing.assert_allclose(wall_error, row["wall_complex_impedance_error"], rtol=1e-7)
                assert (wall_error < .01) == row["wall_impedance_1percent_pass"]
                boundary_energy = .5 * (states * states) @ (l / Z0**2)
                wall_balance = np.max(abs(boundary_energy[1:] - boundary_energy[0] -
                                         dt * np.cumsum(wall_work - copper_loss)))
                wall_balance /= max(dt * np.sum(copper_loss), 1e-30)
                assert wall_balance < 1e-6
                phase = np.exp(-2j * np.pi * f[:, None] * t) * dt
                branch_spectra = phase @ mids / Z0
                j_spectrum = spectrum(t, j, f)
                absorbed_density = np.sum(r * abs(j_spectrum[:, None] - branch_spectra)**2, axis=1)
                expected_density = 4 * abs(a)**2 * physical_load.real / abs(Z0 + physical_load)**2
                absorbed_error = float(np.max(abs(absorbed_density / expected_density - 1)))
                with np.load(fem, allow_pickle=False) as fd:
                    np.testing.assert_allclose(fd["frequency_Hz"], f)
                    fem_error = float(np.max(abs(z / fd["impedance_ohm"] - 1)))
            else:
                load = np.full(len(f), 0 if row["termination"] == "pec" else Z0, complex)
                physical_load = load
                wall_balance = 0.
                absorbed_error = None
                fem_error = None
            continuum = ((physical_load - Z0) / (physical_load + Z0) *
                         np.exp(-4j * np.pi * f * summary["line_length_m"] / C0))
            physical_reflection_error = float(np.max(abs(reflection - continuum)))
            np.testing.assert_allclose(physical_reflection_error, row["reflection_absolute_error"], rtol=1e-7)
            assert (physical_reflection_error < .01) == row["reflection_1percent_absolute_pass"]
            discrete_gamma = ladder_reflection(f, dt, dx, n, load)
            ac_difference = float(np.max(abs(discrete_gamma - reflection)))
            # Finite records need not exactly equal an infinite steady-state solution.
            assert ac_difference < 1e-4, (row["case"], ac_difference)
            checks.append({
                "case": row["case"], "energy_closure_relative": float(closure),
                "source_off_energy_increase_relative": float(increase),
                "nodal_equation_residual_relative": float(charge_residual / charge_scale),
                "edge_equation_residual_relative": float(flux_residual / flux_scale),
                "RL_equation_residual_V": float(branch_residual),
                "wall_algebraic_residual_V": float(algebraic_residual),
                "transient_vs_independent_discrete_ABCD_error": ac_difference,
                "wall_vs_prior_explicit_depth_FEM_error": fem_error,
                "boundary_energy_balance_relative_to_copper_loss": float(wall_balance),
                "absorbed_energy_spectral_density_relative_error": absorbed_error,
            })
    lookup = {r["case"]: r for r in summary["cases"]}
    short = folder / lookup["copper_dx0.5mm_dt1ps_m64_t8ns"]["data_file"]
    long = folder / lookup["copper_dx0.5mm_dt1ps_m64_t16ns"]["data_file"]
    with np.load(short, allow_pickle=False) as a, np.load(long, allow_pickle=False) as b:
        window_reflection = float(np.max(abs(a["left_reflection"] - b["left_reflection"])))
        window_impedance = float(np.max(abs(a["wall_impedance_ohm"] / b["wall_impedance_ohm"] - 1)))
    assert window_reflection < 1e-5 and window_impedance < 1e-4
    finest = lookup["copper_dx0.5mm_dt1ps_m64_t8ns"]
    assert finest["wall_impedance_1percent_pass"] and finest["reflection_1percent_absolute_pass"]
    check_lookup = {row["case"]: row for row in checks}
    assert check_lookup[finest["case"]]["absorbed_energy_spectral_density_relative_error"] < .01
    assert check_lookup["copper_dx0.5mm_dt1ps_m16_t8ns"]["absorbed_energy_spectral_density_relative_error"] > .01
    assert not lookup["copper_dx0.5mm_dt1ps_m16_t8ns"]["wall_impedance_1percent_pass"]
    assert not lookup["copper_dx0.5mm_dt50ps_m64_t8ns"]["wall_impedance_1percent_pass"]
    assert not lookup["copper_dx1mm_dt5ps_m64_t8ns"]["reflection_1percent_absolute_pass"]
    with np.load(short, allow_pickle=False) as data:
        frequencies = np.fft.rfftfreq(len(data["source_V"]), d=1e-12)
        powers = abs(np.fft.rfft(data["source_V"]))**2
        guard = (frequencies >= .5e9) & (frequencies <= 6e9)
        main_band = (frequencies >= 2.5e9) & (frequencies <= 3.5e9)
        guard_fraction = float(np.sum(powers[~guard]) / np.sum(powers))
        outside_main = float(np.sum(powers[~main_band]) / np.sum(powers))
    test_path = Path(__file__).with_name("test_plane_boundary_transient.py")
    tests = subprocess.run([sys.executable, "-B", str(test_path)],
                           capture_output=True, text=True, timeout=60)
    assert tests.returncode == 0, tests.stdout + tests.stderr
    result = {
        "independent_verification_pass": True, "case_count": len(checks), "checks": checks,
        "window_8_vs_16ns_reflection_difference": window_reflection,
        "window_8_vs_16ns_impedance_relative_difference": window_impedance,
        "source_spectral_energy_outside_0p5_to_6GHz": guard_fraction,
        "source_spectral_energy_outside_2p5_to_3p5GHz": outside_main,
        "test_output": tests.stdout + tests.stderr,
        "explicit_depth_FEM_file": str(fem), "explicit_depth_FEM_sha256": sha(fem),
        "summary_sha256": sha(folder / "summary.json"),
        "verifier_sha256": sha(Path(__file__)), "test_sha256": sha(test_path),
        "all_physical_cases_pass": False, "openems_boundary_extension_complete": False,
        "3d_boundary_coupling_complete": False, "full_joint_simulation_complete": False,
    }
    (folder / "verification.json").write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", type=Path)
    main(parser.parse_args().folder)
