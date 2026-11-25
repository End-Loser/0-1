"""Independent full-array/exponential and declared physical-history candidate audit."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.linalg import expm
from threadpoolctl import threadpool_limits

from verify_copper_doublet import gaussian_reference, seed_basis
from verify_copper_driven_history import PULSES, source_coefficients, pair_pass, short_physical_pass
from verify_copper_driven_state import stored_energy
from verify_copper_gauss_state import check_collocation, equations_pass
from verify_fine_copper_longtime import seed_011
from verify_fine_copper_ringdown import decay
from verify_finite_copper_cavity import geometry
from verify_finite_pec_cavity import curl_array, transpose_array

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "\u94dc\u8154\u56db\u9636\u5b8c\u6574\u72b6\u6001\u5bf9\u7167\u7ed3\u679c"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path):
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def array_generator(shape, lengths, r, l):
    m, q, edges, scales, _, _ = geometry(shape, lengths)
    rr, ll = scales[:, None] * r, scales[:, None] * l
    ne, nf = len(m), len(q)
    weight = np.sqrt(np.concatenate((m, q, ll.ravel())))
    matrix = np.empty((len(weight), len(weight)))
    for col in range(len(weight)):
        x = np.zeros(len(weight)); x[col] = 1 / weight[col]
        e, b, i = x[:ne], x[ne:ne+nf], x[ne+nf:].reshape(ll.shape)
        j = (e[edges] + np.sum(rr * i, axis=1)) / rr.sum(axis=1)
        wall = np.bincount(edges, weights=j, minlength=ne)
        edot = (transpose_array(q * b, shape) - wall) / m
        bdot = -curl_array(e, shape)
        idot = rr * (j[:, None] - i) / ll
        matrix[:, col] = weight * np.concatenate((edot, bdot, idot.ravel()))
    return matrix, weight


def preflight_audit(rows, folder):
    configs = [dict(duration_s=t, steps=n) for t, counts in
               ((80e-12, (8, 16, 32)), (1e-14, (2, 4, 8))) for n in counts]
    assert len(rows) == 6
    r, l = np.array([.01, .1]), np.array([1e-10, 1e-11])
    shape, lengths = [3, 2, 2], [.16, .10, .06]
    generator, weight = array_generator(shape, lengths, r, l)
    m, q, _, scales, _, _ = geometry(shape, lengths)
    rng = np.random.default_rng(423)
    initial = (rng.normal(size=len(m)), rng.normal(size=len(q)) * 1e-9,
               rng.normal(size=(len(scales), 2)) * .01)
    vector = np.concatenate([a.ravel() for a in initial])
    records = []
    for index, (row, config) in enumerate(zip(rows, configs)):
        assert all(row[k] == v for k, v in config.items())
        assert row["file"] == f"preflight_{index}.npz" and sha(folder / row["file"]) == row["sha256"]
        with np.load(folder / row["file"]) as archive:
            data = {key: archive[key] for key in archive.files}
        for key, expected in (("shape", shape), ("lengths_m", lengths), ("R_ohm", r), ("L_H", l)):
            np.testing.assert_array_equal(data[key], expected)
        assert all(np.isfinite(a).all() and not np.iscomplexobj(a) for a in data.values())
        np.testing.assert_allclose(data["energy_weight"], weight**2, rtol=1e-14, atol=0.)
        np.testing.assert_array_equal(data["initial_full_state"], vector)
        dt, steps = config["duration_s"] / config["steps"], config["steps"]
        assert float(data["dt_s"]) == dt and float(data["duration_s"]) == config["duration_s"]
        exact = expm(config["duration_s"] * generator) @ (weight * vector)
        assert np.linalg.norm(exact - data["scaled_exact_state"]) / np.linalg.norm(exact) < 1e-10
        checks = []
        for n in range(steps):
            before = tuple(data[key][n] for key in ("e_before", "b_before", "i_before"))
            after = tuple(data[key][n] for key in ("e_after", "b_after", "i_after"))
            wanted = initial if n == 0 else tuple(data[key][n-1] for key in ("e_after", "b_after", "i_after"))
            for a, b in zip(before, wanted):
                np.testing.assert_array_equal(a, b)
            stages = tuple(data["stage_" + key][n] for key in ("e", "b", "i", "j"))
            metrics = check_collocation(shape, lengths, dt, r, l, before, after, stages, np.zeros((2, len(m))))
            assert equations_pass(metrics), metrics
            checks.append(metrics)
        final = np.concatenate([data[key][-1].ravel() for key in ("e_after", "b_after", "i_after")])
        np.testing.assert_array_equal(final, data["final_full_state"])
        error = float(np.linalg.norm(weight * final - exact) / np.linalg.norm(exact))
        assert abs(error - float(data["complete_state_energy_norm_error"])) < 1e-10
        assert abs(error - row["reported_full_state_error"]) < 1e-10
        assert data["scaled_residual"].shape == (steps,) and np.max(data["scaled_residual"]) <= 5e-13
        assert np.all((data["GMRES_iterations"] >= 0) & (data["GMRES_iterations"] <= 80))
        records.append(dict(config, file=row["file"], raw_sha256=row["sha256"], full_steps_checked=steps,
                            complete_state_energy_norm_error=error, one_percent_accuracy_passed=pair_pass(error),
                            maximum_equation_residuals={key: max(c[key] for c in checks)
                                                       for key in ("electric", "magnetic", "RL", "wall", "charge", "energy")}))
    assert all(not row["one_percent_accuracy_passed"] for row in records[:3])
    ratios = [records[i]["complete_state_energy_norm_error"] /
              records[i+1]["complete_state_energy_norm_error"] for i in (3, 4)]
    assert all(8 < ratio < 24 for ratio in ratios)
    return dict(cases=records, resolved_formal_order_error_ratios=ratios,
                unresolved_initial_state_failed=True, general_initial_state_approved=False)


def _audit(partial):
    summary, digest = read_json(OUT / "summary.json")
    assert summary["status"] in ("running", "failed_preserve_partial", "solver_complete_unverified")
    if not partial:
        assert summary["status"] == "solver_complete_unverified"
    declaration, config_digest = read_json(OUT / "configurations.json")
    assert config_digest == summary["configuration_sha256"]
    configs = [dict(case=case, initial_seed=seed, dt_s=dt, duration_s=16e-9, sample_interval_s=20e-12,
                    pulses=PULSES if case == "mixed_history" else [])
               for case, seed in (("111_loss0", 1), ("111_loss1", 2), ("mixed_history", None))
               for dt in (20e-12, 10e-12, 5e-12)]
    assert declaration["configurations"] == configs
    assert declaration["preflight"] == [dict(duration_s=t, steps=n) for t, counts in
        ((80e-12, (8, 16, 32)), (1e-14, (2, 4, 8))) for n in counts]
    assert declaration["shape"] == [36, 23, 14] and declaration["lengths_m"] == [.16, .10, .06]
    assert declaration["thresholds"] == dict(Q=.01, frequency=.01, fit_window_Q=.01,
        paired_complete_state_energy_norm=.01, source_energy_closure=1e-7)
    assert 0 <= len(summary["cases"]) <= 9
    if not partial:
        assert len(summary["cases"]) == 9
    assert set(summary["source_sha256"]) == {
        "run_copper_gauss_validation.py", "copper_gauss_legendre.py", "test_copper_gauss_legendre.py",
        "run_copper_driven_history.py", "copper_doublet.py", "finite_copper_cavity.py", "finite_pec_cavity.py"}
    for name, value in summary["source_sha256"].items():
        assert sha(ROOT / name) == value
    for key in ("general_initial_state_approved", "full_long_history_passed",
                "actual_feed_passed", "full_joint_simulation_passed"):
        assert summary[key] is False
    assert summary["full_system_gates_closed"] == 0
    preflight = preflight_audit(summary["preflight"], OUT)
    modelpath = ROOT / "\u539a\u94dc\u8fb9\u754c\u57fa\u51c6\u7ed3\u679c/passive_surface_models.json"
    assert sha(modelpath) == summary["model_sha256"]
    model = next(row for row in json.loads(modelpath.read_text(encoding="utf-8"))["models"] if row["terms"] == 64)
    r, l = np.array(model["R_ohm"]), np.array(model["L_H"])
    shape, lengths = [36, 23, 14], np.array([.16, .10, .06])
    m, q, edges, scale, _, _ = geometry(shape, lengths)
    ll = scale[:, None] * l
    continuous = gaussian_reference(lengths, model["sigma_S_per_m"])
    records, pairs = [], []
    previous = None
    reference_path = ROOT / "\u94dc\u8154\u5e26\u6e90\u56e0\u679c\u5386\u53f2\u7ec6\u6b65\u7ed3\u679c/summary.json"
    reference_summary, reference_digest = read_json(reference_path)
    for config, row in zip(configs, summary["cases"]):
        assert all(row[k] == v for k, v in config.items())
        assert row["file"] == f"{config['case']}_{config['dt_s'] * 1e12:g}ps.npz"
        assert sha(OUT / row["file"]) == row["sha256"]
        with np.load(OUT / row["file"]) as archive:
            data = {key: archive[key] for key in archive.files}
        assert all(np.isfinite(a).all() and not np.iscomplexobj(a) for a in data.values())
        for key, expected in (("shape", shape), ("lengths_m", lengths), ("R_ohm", r), ("L_H", l),
                              ("dt_s", config["dt_s"]), ("duration_s", config["duration_s"])):
            np.testing.assert_array_equal(data[key], expected)
        physical = data["physical_loss_basis"]
        np.testing.assert_allclose(np.linalg.norm(physical, axis=0), 1., atol=1e-13)
        gref = gaussian_reference(lengths, model["sigma_S_per_m"], physical)
        loss = np.linalg.solve(gref["mass"], gref["surface"])
        assert np.linalg.norm(loss - np.diag(np.diag(loss))) / np.linalg.norm(loss) < 1e-12
        omega = 2 * np.pi * continuous["frequency_Hz"]
        rs = np.sqrt(omega * (4e-7 * np.pi) / (2 * model["sigma_S_per_m"]))
        directional_q = omega / (rs * np.diag(loss))
        np.testing.assert_allclose(np.sort(directional_q), continuous["Q"], rtol=1e-12)
        seeds = np.column_stack((seed_011(shape, lengths, m), seed_basis(shape, lengths, physical, m)))
        np.testing.assert_allclose(data["seeds"], seeds, rtol=1e-11, atol=1e-12)
        frequencies = np.array([299792458. / 2 * np.sqrt(1 / .10**2 + 1 / .06**2),
                                continuous["frequency_Hz"], continuous["frequency_Hz"]])
        np.testing.assert_allclose(data["source_frequency_Hz"], frequencies, rtol=1e-14)
        dt, steps = config["dt_s"], round(16e-9 / config["dt_s"])
        stride = round(20e-12 / dt)
        indices = np.linspace(0, steps - 1, 9).astype(int)
        np.testing.assert_array_equal(data["snapshot_step"], indices)
        assert data["GMRES_iterations"].shape == (steps,) and data["scaled_residual"].shape == (steps,)
        assert np.all((data["scaled_residual"] >= 0) & (data["scaled_residual"] <= 5e-13))
        assert np.all((data["GMRES_iterations"] >= 0) & (data["GMRES_iterations"] <= 80))
        np.testing.assert_array_equal(data["GMRES_iterations"], np.round(data["GMRES_iterations"]))
        for key in ("per_step_heat_J", "per_step_source_work_J"):
            assert data[key].shape == (steps,)
        assert np.all(data["per_step_heat_J"] >= 0)
        samples = data["samples"]
        assert samples.shape == (801, 7) and np.all(samples[:, 1] >= 0)
        np.testing.assert_allclose(samples[:, 0], np.arange(801) * 20e-12, rtol=0., atol=1e-23)
        for col, key in ((2, "per_step_heat_J"), (3, "per_step_source_work_J")):
            np.testing.assert_allclose(samples[1:, col], np.cumsum(data[key])[stride-1::stride], rtol=1e-12, atol=1e-25)
        seed = config["initial_seed"]
        initial_energy = 0. if seed is None else .5
        np.testing.assert_allclose(data["snapshot_e_before"][0], np.zeros(len(m)) if seed is None else seeds[:, seed],
                                   rtol=1e-11, atol=1e-12)
        np.testing.assert_array_equal(data["snapshot_b_before"][0], np.zeros(len(q)))
        np.testing.assert_array_equal(data["snapshot_i_before"][0], np.zeros(ll.shape))
        np.testing.assert_allclose(samples[0], [0., initial_energy, 0., 0.,
            *(seeds.T @ (m * data["snapshot_e_before"][0]))], rtol=1e-12, atol=1e-12)
        if seed is not None:
            np.testing.assert_array_equal(data["per_step_source_work_J"], np.zeros(steps))
        else:
            quiet = (np.arange(steps) + 1.) * dt >= 12e-9 + dt
            np.testing.assert_array_equal(data["per_step_source_work_J"][quiet], np.zeros(quiet.sum()))
            assert samples[600, 1] > 0 and samples[-1, 1] > 0
        closure = float(np.max(abs(samples[:, 1] + samples[:, 2] - samples[:, 3] - initial_energy)) /
                        max(samples[:, 1].max(), abs(samples[-1, 3]), 1e-25))
        assert closure < 1e-7
        checks = []
        for i, step in enumerate(indices):
            before = tuple(data["snapshot_" + key][i] for key in ("e_before", "b_before", "i_before"))
            after = tuple(data["snapshot_" + key][i] for key in ("e_after", "b_after", "i_after"))
            stages = tuple(data["snapshot_stage_" + key][i] for key in ("e", "b", "i", "j"))
            coeff = np.array([source_coefficients((step + c) * dt, frequencies, config["pulses"])
                              for c in (.5 - np.sqrt(3) / 6, .5 + np.sqrt(3) / 6)])
            np.testing.assert_allclose(data["snapshot_source_coefficients"][i], coeff, rtol=1e-12, atol=1e-6)
            sources = (coeff @ seeds.T) * m
            assert np.linalg.norm(sources - data["snapshot_source_currents"][i]) / max(
                np.linalg.norm(sources), 1e-25) < 1e-10
            metrics = check_collocation(shape, lengths, dt, r, l, before, after, stages, sources)
            assert equations_pass(metrics), metrics
            for key, raw in (("heat_J", "per_step_heat_J"), ("source_work_J", "per_step_source_work_J")):
                np.testing.assert_allclose(metrics[key], data[raw][step], rtol=1e-11, atol=1e-25)
            if i:
                sample_index = round((step + 1) * dt / 20e-12)
                np.testing.assert_allclose(samples[sample_index, 0], (step + 1) * dt, rtol=0., atol=1e-23)
                np.testing.assert_allclose(samples[sample_index, 1], metrics["after_energy_J"], rtol=1e-12, atol=1e-25)
                np.testing.assert_allclose(samples[sample_index, 4:], seeds.T @ (m * after[0]), rtol=1e-11, atol=1e-12)
            checks.append(metrics)
        record = dict(case=config["case"], dt_s=dt, raw_sha256=row["sha256"], anchors_checked=9,
                      source_energy_closure=closure, final_energy_J=float(samples[-1, 1]),
                      cumulative_heat_J=float(samples[-1, 2]), cumulative_source_work_J=float(samples[-1, 3]),
                      maximum_equation_residuals={key: max(c[key] for c in checks)
                                                 for key in ("electric", "magnetic", "RL", "wall", "charge", "energy")})
        if seed is not None:
            f, fits = decay(samples[:, 0], samples[:, 1], samples[:, 4 + seed])
            qe = abs(fits[-1]["Q_decay"] / directional_q[seed - 1] - 1)
            fe = abs(f / continuous["frequency_Hz"] - 1)
            we = abs(fits[-1]["Q_decay"] / fits[0]["Q_decay"] - 1)
            record["decay_metrics"] = dict(frequency_Hz=f, fits=fits, reference_Q=float(directional_q[seed-1]),
                Q_continuum_error=float(qe), frequency_continuum_error=float(fe), fit_window_Q_difference=float(we),
                selected_short_decay_passed=short_physical_pass(qe, fe, we))
        state_keys = ("snapshot_e_after", "snapshot_b_after", "snapshot_i_after")

        def state_errors(other):
            result = []
            for i in range(1, 9):
                states = tuple(data[key][i] for key in state_keys)
                difference = tuple(data[key][i] - other[key][i] for key in state_keys)
                result.append(float(np.sqrt(stored_energy(*difference, m, q, ll) /
                                           max(stored_energy(*states, m, q, ll), 1e-25))))
            return result

        if previous is not None and previous["case"] == config["case"]:
            errors = state_errors(previous["fields"])
            pairs.append(dict(case=config["case"], paired_dt_s=[previous["dt_s"], dt],
                              common_after_times_s=list(np.arange(1, 9) * 2e-9),
                              complete_state_energy_norm_errors=errors,
                              selected_pair_passed=pair_pass(max(errors))))
        refrow = next((item for item in reference_summary["cases"]
                       if item["case"] == config["case"] and item["dt_s"] == 1e-12), None)
        if refrow is not None:
            refpath = reference_path.parent / refrow["file"]
            assert sha(refpath) == refrow["sha256"]
            with np.load(refpath) as archive:
                other = {key: archive[key] for key in state_keys}
            record["existing_midpoint_1ps_comparison"] = dict(reference_file=str(refpath.relative_to(ROOT)),
                reference_sha256=refrow["sha256"], complete_state_energy_norm_errors=state_errors(other),
                scope="Existing independent full-field solver; not generic or long-history approval")
        records.append(record)
        previous = dict(case=config["case"], dt_s=dt,
                        fields={key: data[key].copy() for key in state_keys})
        del data
    return dict(scope="partial_saved_cases_only" if partial else "nine declared 16 ns full-state Gauss candidates",
                independent_discrete_checks_passed=True, preflight=preflight, cases=records, pairs=pairs,
                all_nine_physical_cases_present=not partial and len(records) == 9,
                all_six_short_pairs_passed=bool(not partial and len(pairs) == 6 and all(p["selected_pair_passed"] for p in pairs)),
                summary_sha256=digest, summary_snapshot=summary, configuration_sha256=config_digest,
                midpoint_reference_summary_sha256=reference_digest, midpoint_reference_summary_snapshot=reference_summary,
                verifier_sha256=sha(Path(__file__)),
                helper_sha256={name: sha(ROOT / name) for name in (
                    "verify_copper_gauss_state.py", "verify_copper_doublet.py", "verify_copper_driven_history.py",
                    "verify_copper_driven_state.py", "verify_fine_copper_longtime.py", "verify_fine_copper_ringdown.py",
                    "verify_finite_copper_cavity.py", "verify_finite_pec_cavity.py")},
                general_initial_state_approved=False, full_long_history_passed=False, actual_feed_passed=False,
                full_joint_simulation_passed=False, full_system_gates_closed=0)


def audit(partial=False):
    with threadpool_limits(limits=1):
        return _audit(partial)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--partial", action="store_true")
    parser.add_argument("--require-pair", action="store_true")
    args = parser.parse_args()
    result = audit(partial=args.partial)
    raw = (json.dumps(result, indent=2) + "\n").encode("utf-8")
    prefix = "partial_verification_" if args.partial else "verification_"
    path = OUT / (prefix + hashlib.sha256(raw).hexdigest()[:12] + ".json")
    if path.exists():
        assert path.read_bytes() == raw
    else:
        path.write_bytes(raw)
    print(json.dumps(result, indent=2))
    if args.require_pair and not result["all_six_short_pairs_passed"]:
        raise SystemExit(2)
