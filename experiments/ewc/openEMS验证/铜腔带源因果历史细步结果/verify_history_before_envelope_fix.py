"""Independent declared sources, full-state anchors and paired short-history audit."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from verify_copper_doublet import gaussian_reference, seed_basis
from verify_copper_driven_state import check_step, equations_pass, stored_energy
from verify_fine_copper_longtime import seed_011
from verify_fine_copper_ringdown import decay
from verify_finite_copper_cavity import geometry

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "\u94dc\u8154\u5e26\u6e90\u56e0\u679c\u5386\u53f2\u7ed3\u679c"
REFINED_OUT = ROOT / "\u94dc\u8154\u5e26\u6e90\u56e0\u679c\u5386\u53f2\u7ec6\u6b65\u7ed3\u679c"
PULSES = [
    dict(mode=0, start_s=.5e-9, end_s=2.5e-9, amplitude=1e9, phase_rad=0.),
    dict(mode=1, start_s=2e-9, end_s=4e-9, amplitude=1e9, phase_rad=.3),
    dict(mode=2, start_s=3.5e-9, end_s=5.5e-9, amplitude=1e9, phase_rad=1.1),
    dict(mode=0, start_s=6e-9, end_s=8e-9, amplitude=-.8e9, phase_rad=.7),
    dict(mode=1, start_s=7.5e-9, end_s=10e-9, amplitude=.9e9, phase_rad=2.),
    dict(mode=2, start_s=9.5e-9, end_s=12e-9, amplitude=-1e9, phase_rad=.2),
]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path):
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def source_coefficients(t, frequencies, pulses):
    c = np.zeros(3)
    for p in pulses:
        if p["start_s"] < t < p["end_s"]:
            fraction = (t - p["start_s"]) / (p["end_s"] - p["start_s"])
            c[p["mode"]] += (p["amplitude"] * (1 - np.cos(2 * np.pi * fraction)) / 2 *
                             np.cos(2 * np.pi * frequencies[p["mode"]] * t + p["phase_rad"]))
    return c


def short_physical_pass(qerror, ferror, window_error):
    return bool(all(np.isfinite(x) and 0 <= x < .01 for x in (qerror, ferror, window_error)))


def pair_pass(error):
    return bool(np.isfinite(error) and 0 <= error < .01)


def audit(folder=OUT, partial=False, refinement=False):
    summary, summary_sha = read_json(folder / "summary.json")
    assert summary["status"] in ("running", "failed_preserve_partial", "solver_complete_unverified")
    if not partial:
        assert summary["status"] == "solver_complete_unverified", "All six cases must finish"
    declaration, config_sha = read_json(folder / "configurations.json")
    assert config_sha == summary["configuration_sha256"]
    time_steps = (1e-12, .5e-12) if refinement else (5e-12, 2e-12)
    configs = [dict(case=name, initial_seed=seed, shape=[36, 23, 14], lengths_m=[.16, .10, .06],
                    dt_s=dt, duration_s=16e-9, sample_interval_s=20e-12, snapshots=9,
                    pulses=PULSES if name == "mixed_history" else [])
               for name, seed in (("111_loss0", 1), ("111_loss1", 2), ("mixed_history", None))
               for dt in time_steps]
    assert declaration["configurations"] == configs
    assert declaration["thresholds"] == dict(
        Q_continuum=.01, frequency_continuum=.01, fit_window_Q=.01,
        paired_complete_state_energy_norm=.01, sampled_source_energy_closure=1e-7)
    assert 1 <= len(summary["cases"]) <= 6, "No completed raw case to check"
    if not partial:
        assert len(summary["cases"]) == 6
    required_sources = {
        "run_copper_driven_history.py", "copper_driven_midpoint.py",
        "copper_midpoint_cg.py", "copper_doublet.py",
        "finite_copper_cavity.py", "finite_pec_cavity.py"}
    if refinement:
        required_sources.add("run_copper_driven_refinement.py")
    assert set(summary["source_sha256"]) == required_sources
    for name, digest in summary["source_sha256"].items():
        assert sha(ROOT / name) == digest
    for key in ("full_long_history_passed", "actual_feed_passed",
                "manufactured_cavity_passed", "full_joint_simulation_passed"):
        assert summary[key] is False
    assert summary["full_system_gates_closed"] == 0
    modelpath = ROOT / "\u539a\u94dc\u8fb9\u754c\u57fa\u51c6\u7ed3\u679c/passive_surface_models.json"
    assert sha(modelpath) == summary["model_sha256"]
    model = next(row for row in json.loads(modelpath.read_text(encoding="utf-8"))["models"]
                 if row["terms"] == 64)
    r, l = np.array(model["R_ohm"]), np.array(model["L_H"])
    m, q, edges, scales, _, _ = geometry([36, 23, 14], [.16, .10, .06])
    ll = scales[:, None] * l
    continuous = gaussian_reference(np.array([.16, .10, .06]), model["sigma_S_per_m"])
    records, pairs = [], []
    for config, row in zip(configs, summary["cases"]):
        assert all(row[key] == value for key, value in config.items())
        assert row["file"] == f"{config['case']}_{config['dt_s'] * 1e12:g}ps.npz"
        path = folder / row["file"]
        assert sha(path) == row["sha256"]
        with np.load(path) as archive:
            data = {key: archive[key] for key in archive.files}
        for key in ("shape", "lengths_m", "dt_s", "duration_s"):
            np.testing.assert_array_equal(data[key], config[key])
        np.testing.assert_array_equal(data["R_ohm"], r)
        np.testing.assert_array_equal(data["L_H"], l)
        assert all(np.isfinite(value).all() and not np.iscomplexobj(value) for value in data.values())
        physical = data["physical_loss_basis"]
        gref = gaussian_reference(np.array(config["lengths_m"]), model["sigma_S_per_m"], physical)
        loss = np.linalg.solve(gref["mass"], gref["surface"])
        assert np.linalg.norm(loss - np.diag(np.diag(loss))) / np.linalg.norm(loss) < 1e-12
        np.testing.assert_allclose(np.linalg.norm(physical, axis=0), 1., atol=1e-13)
        omega = 2 * np.pi * continuous["frequency_Hz"]
        rs = np.sqrt(omega * (4e-7 * np.pi) / (2 * model["sigma_S_per_m"]))
        directional_q = omega / (rs * np.diag(loss))
        np.testing.assert_allclose(np.sort(directional_q), continuous["Q"], rtol=1e-12)
        seeds = np.column_stack((seed_011(config["shape"], config["lengths_m"], m),
                                 seed_basis(config["shape"], np.array(config["lengths_m"]), physical, m)))
        np.testing.assert_allclose(data["seeds"], seeds, rtol=1e-11, atol=1e-12)
        np.testing.assert_allclose(seeds.T @ (m[:, None] * seeds), np.eye(3), atol=1e-12)
        f011 = 299792458. / 2 * np.sqrt((1 / .10)**2 + (1 / .06)**2)
        freqs = np.array([f011, continuous["frequency_Hz"], continuous["frequency_Hz"]])
        np.testing.assert_allclose(data["source_frequency_Hz"], freqs, rtol=1e-14)
        dt = config["dt_s"]
        steps = int(round(16e-9 / dt))
        stride = int(round(20e-12 / dt))
        indices = np.linspace(0, steps - 1, 9).astype(int)
        np.testing.assert_array_equal(data["snapshot_step"], indices)
        for key in ("per_step_heat_J", "per_step_source_work_J", "scaled_residual", "CG_iterations"):
            assert data[key].shape == (steps,)
        assert np.all(data["per_step_heat_J"] >= 0)
        assert np.all((data["scaled_residual"] >= 0) & (data["scaled_residual"] <= 5e-13))
        assert np.all((data["CG_iterations"] >= 0) & (data["CG_iterations"] <= 80))
        np.testing.assert_array_equal(data["CG_iterations"], np.round(data["CG_iterations"]))
        samples = data["samples"]
        assert samples.shape == (801, 7)
        np.testing.assert_allclose(samples[:, 0], np.arange(801) * 20e-12, atol=1e-23, rtol=0.)
        np.testing.assert_allclose(samples[1:, 2], np.cumsum(data["per_step_heat_J"])[stride-1::stride],
                                   rtol=1e-12, atol=1e-25)
        np.testing.assert_allclose(samples[1:, 3], np.cumsum(data["per_step_source_work_J"])[stride-1::stride],
                                   rtol=1e-12, atol=1e-25)
        if config["case"] != "mixed_history":
            np.testing.assert_array_equal(data["per_step_source_work_J"], np.zeros(steps))
        else:
            quiet = (np.arange(steps) + .5) * dt >= 12e-9
            np.testing.assert_array_equal(data["per_step_source_work_J"][quiet], np.zeros(quiet.sum()))
            assert samples[600, 1] > 0 and samples[-1, 1] > 0, "Memory must persist after pulse termination"
        initial_seed = config["initial_seed"]
        expected_e = np.zeros(len(m)) if initial_seed is None else seeds[:, initial_seed]
        np.testing.assert_allclose(data["snapshot_e_before"][0], expected_e, rtol=1e-11, atol=1e-12)
        np.testing.assert_array_equal(data["snapshot_b_before"][0], np.zeros(len(q)))
        np.testing.assert_array_equal(data["snapshot_i_before"][0], np.zeros(ll.shape))
        initial_energy = 0. if initial_seed is None else .5
        assert abs(samples[0, 1] - initial_energy) < 1e-12
        assert np.all(samples[:, 1] >= 0)
        closure = float(np.max(abs(samples[:, 1] + samples[:, 2] - samples[:, 3] - initial_energy)) /
                        max(np.max(samples[:, 1]), abs(samples[-1, 3]), 1e-25))
        assert closure < 1e-7
        checks = []
        for index, step in enumerate(indices):
            before = tuple(data["snapshot_" + key][index] for key in ("e_before", "b_before", "i_before"))
            after = tuple(data["snapshot_" + key][index] for key in ("e_after", "b_after", "i_after"))
            coeff = source_coefficients((step + .5) * dt, freqs, config["pulses"])
            np.testing.assert_allclose(data["snapshot_source_coefficients"][index], coeff, rtol=1e-12, atol=1e-6)
            source = m * (seeds @ coeff)
            metrics = check_step(config["shape"], config["lengths_m"], dt, r, l,
                                 before, after, data["snapshot_j_mid"][index], source)
            assert equations_pass(metrics), metrics
            np.testing.assert_allclose(metrics["heat_J"], data["per_step_heat_J"][step], rtol=1e-11, atol=1e-25)
            np.testing.assert_allclose(metrics["source_work_J"], data["per_step_source_work_J"][step],
                                       rtol=1e-11, atol=1e-25)
            # All noninitial after-anchors coincide with 2 ns sampling endpoints.
            if index > 0:
                sample_index = int(round((step + 1) * dt / 20e-12))
                assert abs(samples[sample_index, 0] - (step + 1) * dt) < 1e-23
                np.testing.assert_allclose(metrics["after_energy_J"], samples[sample_index, 1], rtol=1e-12, atol=1e-25)
                np.testing.assert_allclose(seeds.T @ (m * after[0]), samples[sample_index, 4:], rtol=1e-11, atol=1e-12)
            checks.append(metrics)
        record = dict(case=config["case"], dt_s=dt, raw_sha256=row["sha256"], anchors_checked=9,
                      source_energy_closure=closure,
                      maximum_equation_residuals={key: max(c[key] for c in checks)
                                                 for key in ("electric", "magnetic", "RL", "wall", "charge", "energy")},
                      final_energy_J=float(samples[-1, 1]), cumulative_heat_J=float(samples[-1, 2]),
                      cumulative_source_work_J=float(samples[-1, 3]), decay_metrics=None)
        if initial_seed is not None:
            frequency, fits = decay(samples[:, 0], samples[:, 1], samples[:, 4 + initial_seed])
            qerror = abs(fits[-1]["Q_decay"] / directional_q[initial_seed - 1] - 1)
            ferror = abs(frequency / continuous["frequency_Hz"] - 1)
            window_error = abs(fits[-1]["Q_decay"] / fits[0]["Q_decay"] - 1)
            record["decay_metrics"] = dict(
                frequency_Hz=frequency, fits=fits, directional_reference_Q=float(directional_q[initial_seed - 1]),
                Q_continuum_error=float(qerror),
                frequency_continuum_error=float(ferror), window_Q_difference=float(window_error),
                selected_short_decay_passed=short_physical_pass(qerror, ferror, window_error))
        records.append(record)
        if dt == time_steps[0]:
            first = {key: data[key][1:].copy() for key in ("snapshot_e_after", "snapshot_b_after", "snapshot_i_after")}
        else:
            errors = []
            for index in range(8):
                actual = tuple(data[key][index + 1] for key in first)
                expected = tuple(first[key][index] for key in first)
                difference = tuple(a - b for a, b in zip(actual, expected))
                norm = np.sqrt(stored_energy(*difference, m, q, ll) /
                               max(stored_energy(*actual, m, q, ll), 1e-25))
                errors.append(float(norm))
            pairs.append(dict(case=config["case"], common_after_anchor_times_s=list(np.arange(1, 9) * 2e-9),
                              complete_state_energy_norm_errors=errors,
                              paired_short_history_passed=pair_pass(max(errors))))
            del first
        del data
    return dict(scope="partial_saved_cases_only" if partial else
                "six declared 16 ns full-state cases; no actual feeds or long-history approval",
                paired_time_steps_s=list(time_steps),
                independent_equation_source_energy_checks_passed=True, cases=records, pairs=pairs,
                all_six_declared_cases_present=not partial and len(records) == 6,
                all_three_paired_short_histories_passed=bool(
                    not partial and len(pairs) == 3 and all(p["paired_short_history_passed"] for p in pairs)),
                summary_sha256=summary_sha, summary_snapshot=summary, configuration_sha256=config_sha,
                verifier_sha256=sha(Path(__file__)),
                helper_sha256={name: sha(ROOT / name) for name in (
                    "verify_copper_doublet.py", "verify_copper_driven_state.py", "verify_fine_copper_longtime.py",
                    "verify_fine_copper_ringdown.py", "verify_finite_copper_cavity.py", "verify_finite_pec_cavity.py")},
                full_long_history_passed=False, actual_feed_passed=False,
                manufactured_cavity_passed=False, full_joint_simulation_passed=False, full_system_gates_closed=0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-pair", action="store_true")
    parser.add_argument("--partial", action="store_true")
    parser.add_argument("--refinement", action="store_true")
    args = parser.parse_args()
    folder = REFINED_OUT if args.refinement else OUT
    result = audit(folder=folder, partial=args.partial, refinement=args.refinement)
    raw = (json.dumps(result, indent=2) + "\n").encode("utf-8")
    prefix = "partial_verification_" if args.partial else "verification_"
    path = folder / (prefix + hashlib.sha256(raw).hexdigest()[:12] + ".json")
    if path.exists():
        assert path.read_bytes() == raw, "Refuse to overwrite conflicting verification"
    else:
        path.write_bytes(raw)
    print(json.dumps(result, indent=2))
    if args.require_pair and not result["all_three_paired_short_histories_passed"]:
        raise SystemExit(2)
