"""Independent arrays, source/heat budgets and common-time complete-state pairs."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from threadpoolctl import threadpool_limits

from verify_copper_doublet import gaussian_reference, seed_basis
from verify_copper_driven_history import PULSES, source_coefficients
from verify_copper_driven_state import stored_energy
from verify_copper_gauss_state import check_collocation, equations_pass
from verify_fine_copper_longtime import seed_011
from verify_finite_copper_cavity import geometry

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "\u94dc\u8154\u56db\u9636\u53cc\u6781\u5316\u4e0e\u591a\u7d22\u5f158us\u7ed3\u679c"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_chunk(d, config, seeds, freqs, previous=None):
    shape, lengths, dt = config["shape"], config["lengths_m"], config["dt_s"]
    m, q, _, scale, _, _ = geometry(shape, lengths)
    ll = scale[:, None] * d["L_H"]
    assert all(np.isfinite(x).all() and not np.iscomplexobj(x) for x in d.values())
    np.testing.assert_array_equal(d["shape"], shape)
    np.testing.assert_array_equal(d["lengths_m"], lengths)
    assert float(d["dt_s"]) == dt
    np.testing.assert_allclose(d["seeds"], seeds, rtol=1e-10, atol=1e-6)
    np.testing.assert_allclose(d["source_frequency_Hz"], freqs, rtol=1e-14, atol=0)
    start, end = int(d["start_step"]), int(d["end_step"])
    stride = round(config["sample_interval_s"] / dt)
    assert start >= 0 and end > start and int(d["sample_stride"]) == stride
    s = d["samples"]
    np.testing.assert_array_equal(s[:, 0], np.r_[start, np.arange(start + stride, end + 1, stride)] * dt)
    assert s.shape[1] == 7
    starts = tuple(d[key] for key in ("e_start", "b_start", "i_start"))
    ends = tuple(d[key] for key in ("e_after", "b_after", "i_after"))
    if previous is not None:
        for actual, expected in zip(starts, previous[0]):
            np.testing.assert_array_equal(actual, expected)
        np.testing.assert_array_equal(s[0, 1:], previous[1][1:])
    else:
        assert start == 0 and d["heat_start_J"] == d["work_start_J"] == 0
        assert np.all(starts[1] == 0) and np.all(starts[2] == 0)
        if config["initial_seed"] is None:
            assert np.all(starts[0] == 0)
        else:
            np.testing.assert_allclose(starts[0], seeds[:, config["initial_seed"]], rtol=1e-10, atol=1e-6)
    for state, row in ((starts, s[0]), (ends, s[-1])):
        np.testing.assert_allclose(stored_energy(*state, m, q, ll), row[1], rtol=1e-11, atol=0)
        np.testing.assert_allclose(seeds.T @ (m * state[0]), row[4:], rtol=1e-11, atol=1e-13)
    for key in ("per_step_heat_J", "per_step_source_work_J", "GMRES_iterations", "scaled_residual"):
        assert len(d[key]) == end - start
    assert np.all(d["per_step_heat_J"] >= 0)
    assert np.all((d["scaled_residual"] >= 0) & (d["scaled_residual"] <= 5e-13))
    assert np.all((d["GMRES_iterations"] >= 0) & (d["GMRES_iterations"] <= 80))
    assert np.all(d["GMRES_iterations"] == np.floor(d["GMRES_iterations"]))
    np.testing.assert_allclose(s[:, 2], d["heat_start_J"] + np.r_[0, np.cumsum(d["per_step_heat_J"])[stride-1::stride]],
                               rtol=1e-11, atol=1e-20)
    np.testing.assert_allclose(s[:, 3], d["work_start_J"] + np.r_[0, np.cumsum(d["per_step_source_work_J"])[stride-1::stride]],
                               rtol=1e-11, atol=1e-20)
    assert d["heat_start_J"] == s[0, 2] and d["work_start_J"] == s[0, 3]
    initial_energy = 0. if config["initial_seed"] is None else .5
    closure = float(np.max(abs(s[:, 1] + s[:, 2] - s[:, 3] - initial_energy)) /
                    max(initial_energy, float(np.max(abs(s[:, 3]))), 1e-25))
    assert closure < 1e-7
    if not config["pulses"] or start * dt >= 12e-9:
        assert np.all(d["per_step_source_work_J"] == 0)
    nodes = .5 + np.array([-1., 1.]) * np.sqrt(3) / 6
    chosen = {start, end - 1}
    chosen.update(round(t / dt) - 1 for t in np.arange(2, 18, 2) * 1e-9
                  if start <= round(t / dt) - 1 < end)
    np.testing.assert_array_equal(d["snapshot_step"], sorted(chosen))
    checks = []
    for index, step in enumerate(d["snapshot_step"]):
        before = tuple(d["snapshot_" + key][index] for key in ("e_before", "b_before", "i_before"))
        after = tuple(d["snapshot_" + key][index] for key in ("e_after", "b_after", "i_after"))
        stages = tuple(d["snapshot_stage_" + key][index] for key in ("e", "b", "i", "j"))
        coeff = np.array([source_coefficients((step + c) * dt, freqs, config["pulses"]) for c in nodes])
        current = (coeff @ seeds.T) * m
        np.testing.assert_allclose(d["snapshot_source_coefficients"][index], coeff, rtol=1e-12, atol=1e-6)
        np.testing.assert_allclose(d["snapshot_source_currents"][index], current, rtol=1e-12, atol=1e-15)
        metric = check_collocation(shape, lengths, dt, d["R_ohm"], d["L_H"], before, after, stages, current)
        assert equations_pass(metric), metric
        np.testing.assert_allclose(metric["source_work_J"], d["per_step_source_work_J"][step-start], rtol=1e-11, atol=1e-25)
        np.testing.assert_allclose(metric["heat_J"], d["per_step_heat_J"][step-start], rtol=1e-11, atol=0)
        if step == start:
            for a, b in zip(before, starts):
                np.testing.assert_array_equal(a, b)
        if step == end - 1:
            for a, b in zip(after, ends):
                np.testing.assert_array_equal(a, b)
        checks.append(dict(step=int(step), **metric))
    return dict(energy_closure=closure, anchors=checks), (ends, s[-1])


def full_metrics(samples, config, ref):
    t, u = samples[:, 0], samples[:, 1]
    assert abs(t[-1] - 8e-6) < 1e-20
    if config["initial_seed"] is None:
        return dict(single_mode_Q_claimed=False)
    index = config["initial_seed"]
    probe = samples[:, 4 + index]
    crossings = np.flatnonzero((probe[:-1] <= 0) & (probe[1:] > 0) & (t[:-1] >= 1e-6))
    tc = t[crossings] - probe[crossings] * (t[crossings+1] - t[crossings]) / (probe[crossings+1] - probe[crossings])
    assert len(tc) > 10000
    frequency = float((len(tc) - 1) / (tc[-1] - tc[0]))
    fits = []
    for limit in (5e-6, 8e-6):
        keep = (t >= 1e-6) & (t <= limit)
        x, y = t[keep], np.log(u[keep])
        x, y = x - x.mean(), y - y.mean()
        slope = x @ y / (x @ x)
        assert slope < 0
        measured = -2 * np.pi * frequency / slope
        fits.append(dict(end_s=limit, Q=float(measured),
                         Q_relative_error=float(abs(measured / ref["directional_Q"][index-1] - 1))))
    ferror = abs(frequency / ref["frequency_Hz"] - 1)
    return dict(frequency_Hz=frequency, frequency_relative_error=ferror, fits=fits,
                physical_passed=ferror < .01 and all(row["Q_relative_error"] < .01 for row in fits))


def audit(partial=False):
    raw = (OUT / "progress.json").read_bytes()
    manifest = json.loads(raw)
    declaration = json.loads((OUT / "configurations.json").read_bytes())
    assert sha(OUT / "configurations.json") == manifest["configuration_sha256"]
    assert declaration["terms"] == 64 and declaration["cg_rtol"] == 1e-13 and declaration["maxiter"] == 80
    assert declaration["thresholds"] == dict(frequency=.01, Q=.01, complete_state_pair=.01, energy_closure=1e-7)
    configs = declaration["configurations"]
    assert len(configs) == 6
    expected = [(name, seed, dt) for name, seed in (("111_loss0", 1), ("111_loss1", 2), ("mixed_history", None))
                for dt in (2.5e-12, 1.25e-12)]
    for config, (name, seed, dt) in zip(configs, expected):
        assert config == dict(id=f"{name}_{dt * 1e12:g}ps", case=name, initial_seed=seed,
            shape=[36, 23, 14], lengths_m=[.16, .10, .06], dt_s=dt, duration_s=8e-6,
            chunk_duration_s=100e-9, sample_interval_s=20e-12, pulses=PULSES if seed is None else [])
    assert set(manifest["source_sha256"]) == {
        "run_copper_gauss_longtime.py", "copper_gauss_legendre.py", "copper_doublet.py",
        "run_copper_driven_history.py", "finite_copper_cavity.py", "finite_pec_cavity.py"}
    for name, digest in manifest["source_sha256"].items():
        assert sha(ROOT / name) == digest
    modelpath = ROOT / "\u539a\u94dc\u8fb9\u754c\u57fa\u51c6\u7ed3\u679c/passive_surface_models.json"
    assert sha(modelpath) == manifest["model_sha256"]
    model = next(x for x in json.loads(modelpath.read_bytes())["models"] if x["terms"] == 64)
    approval = ROOT / "\u94dc\u8154\u56db\u9636\u5b8c\u6574\u72b6\u6001\u5bf9\u7167\u7ed3\u679c/verification_27617c5451c5.json"
    assert sha(approval) == manifest["approval_sha256"]
    assert not manifest["general_initial_state_approved"] and not manifest["full_long_history_passed"]
    assert manifest["actual_feed_passed"] is False and manifest["full_joint_simulation_passed"] is False
    assert manifest["full_system_gates_closed"] == 0
    if not partial:
        assert manifest["status"] == "solver_complete_unverified" and len(manifest["cases"]) == 6
    m, q, _, scale, _, _ = geometry([36, 23, 14], [.16, .10, .06])
    ll = scale[:, None] * model["L_H"]
    ref = gaussian_reference(np.array([.16, .10, .06]), model["sigma_S_per_m"])
    rates, vectors = np.linalg.eig(np.linalg.solve(ref["mass"], ref["surface"]))
    expected_basis = ref["basis"] @ vectors[:, np.argsort(rates.real)]
    expected_basis /= np.linalg.norm(expected_basis, axis=0)
    cases, metrics, source_files = [], [], {}
    with threadpool_limits(limits=1):
        for config in configs:
            case = next((x for x in manifest["cases"] if x["id"] == config["id"]), None)
            if case is None or not case["chunks"]:
                assert partial
                continue
            chunks, last, samples, records = case["chunks"], None, [], []
            end = 0
            for row in chunks:
                path = OUT / config["id"] / row["file"]
                assert sha(path) == row["sha256"]
                source_files[str(path)] = row["sha256"]
                with np.load(path) as archive:
                    d = {key: archive[key] for key in archive.files}
                stepcount = round(100e-9 / config["dt_s"])
                assert row["start_step"] == int(d["start_step"]) == end
                end += stepcount
                assert row["end_step"] == int(d["end_step"]) == end <= round(8e-6 / config["dt_s"])
                assert row["file"] == f"steps_{end-stepcount:07d}_{end:07d}.npz"
                np.testing.assert_array_equal(d["R_ohm"], model["R_ohm"])
                np.testing.assert_array_equal(d["L_H"], model["L_H"])
                basis = d["physical_loss_basis"]
                np.testing.assert_allclose(basis.T @ basis, np.eye(2), rtol=1e-12, atol=1e-12)
                np.testing.assert_allclose(abs(basis.T @ expected_basis), np.eye(2), rtol=1e-10, atol=1e-10)
                directional = gaussian_reference(np.array(config["lengths_m"]), model["sigma_S_per_m"], basis)
                omega = 2 * np.pi * ref["frequency_Hz"]
                rs = np.sqrt(omega * 4e-7 * np.pi / (2 * model["sigma_S_per_m"]))
                directional["directional_Q"] = omega * np.diag(directional["mass"]) / (rs * np.diag(directional["surface"]))
                seeds = np.column_stack((seed_011(config["shape"], config["lengths_m"], m),
                                        seed_basis(config["shape"], np.array(config["lengths_m"]), basis, m)))
                freqs = np.array([299792458./2*np.sqrt((1/.10)**2+(1/.06)**2), ref["frequency_Hz"], ref["frequency_Hz"]])
                record, last = check_chunk(d, config, seeds, freqs, last)
                records.append(dict(file=row["file"], **record))
                samples.extend(d["samples"][0 if not samples else 1:])
            assert end == case["completed_steps"]
            complete = end == round(8e-6 / config["dt_s"]) and len(chunks) == 80
            assert partial or complete
            measured = full_metrics(np.array(samples), config, directional) if complete else None
            metrics.append(measured)
            cases.append(dict(id=config["id"], completed_steps=end, completed_s=end*config["dt_s"],
                              complete=complete, chunks=records, metrics=measured))
        pairs = []
        for coarse, fine in zip(configs[::2], configs[1::2]):
            rows = [next((x for x in manifest["cases"] if x["id"] == c["id"]), None) for c in (coarse, fine)]
            if any(r is None or not r["chunks"] for r in rows):
                continue
            errors = []
            for a, b in zip(rows[0]["chunks"], rows[1]["chunks"]):
                with np.load(OUT / coarse["id"] / a["file"]) as x, np.load(OUT / fine["id"] / b["file"]) as y:
                    tx, ty = (x["snapshot_step"]+1)*coarse["dt_s"], (y["snapshot_step"]+1)*fine["dt_s"]
                    for i, t in enumerate(tx):
                        matches = np.flatnonzero(abs(ty-t) < 1e-20)
                        if not len(matches):
                            continue
                        j = int(matches[0])
                        sx = tuple(x["snapshot_" + key][i] for key in ("e_after", "b_after", "i_after"))
                        sy = tuple(y["snapshot_" + key][j] for key in ("e_after", "b_after", "i_after"))
                        delta = tuple(u-v for u, v in zip(sx, sy))
                        error = np.sqrt(stored_energy(*delta, m, q, ll) /
                            max(stored_energy(*sx, m, q, ll), stored_energy(*sy, m, q, ll), 1e-25))
                        errors.append(dict(time_s=float(t), energy_norm_relative_error=float(error)))
            complete = all(r["completed_steps"]*c["dt_s"] >= 8e-6-1e-20 for r, c in zip(rows, (coarse, fine)))
            assert errors
            passed = max(x["energy_norm_relative_error"] for x in errors) < .01
            pairs.append(dict(case=coarse["case"], complete=complete, samples=errors,
                              limited_available_pair_passed=passed, full_pair_passed=complete and passed))
    full = not partial and len(cases) == 6 and all(x["complete"] for x in cases)
    passed = full and len(pairs) == 3 and all(x["full_pair_passed"] for x in pairs) and all(
        x["metrics"].get("physical_passed", True) for x in cases)
    result = dict(partial_mode=partial, source_manifest_read_sha256=hashlib.sha256(raw).hexdigest(),
                  manifest_snapshot=manifest, sources_sha256=source_files, cases=cases, pairs=pairs,
                  all_cases_complete=full, specified_8us_histories_passed=passed,
                  general_initial_state_approved=False, actual_feed_passed=False,
                  full_joint_simulation_passed=False, full_system_gates_closed=0,
                  verifier_sha256=sha(Path(__file__)))
    content = (json.dumps(result, indent=2) + "\n").encode("ascii")
    path = OUT / ("verification_" + hashlib.sha256(content).hexdigest()[:12] + ".json")
    if path.exists():
        assert path.read_bytes() == content
    else:
        path.write_bytes(content)
    print("Saved", path.name, "cases", len(cases), "8us passed", passed)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--partial", action="store_true")
    args = parser.parse_args()
    result = audit(args.partial)
    raise SystemExit(0 if args.partial or result["specified_8us_histories_passed"] else 2)
