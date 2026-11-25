"""Independent chunk states, energy trace and actual 8 us acceptance boundary."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from verify_finite_copper_cavity import geometry, node_divergence, quadrature_q
from verify_finite_pec_cavity import curl_array, transpose_array, layouts, mask

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "\u7ec6\u7f51\u683c\u94dc\u81548us\u957f\u65f6\u7ed3\u679c"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_manifest(path):
    # Hash exactly the bytes parsed, even if the live runner advances afterwards.
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def seed_011(shape, lengths, m):
    es, _ = layouts(shape)
    h = np.asarray(lengths) / shape
    index = np.indices(es[0])
    ex = h[0] * np.sin(np.pi * h[1] * index[1] / lengths[1]) * np.sin(
        np.pi * h[2] * index[2] / lengths[2])
    field = np.concatenate([ex.ravel(), np.zeros(np.prod(es[1])),
                            np.zeros(np.prod(es[2]))])
    field[~mask(shape)] = 0.
    return field / np.sqrt(m @ field**2)


def check_anchor(data, shape, dt, m, q, edges, rr, ll):
    e0, b0, i0 = (data[key] for key in ("e_before", "b_before", "i_before"))
    e1, b1, i1 = (data[key] for key in ("e_after", "b_after", "i_after"))
    for e, b, state in ((e0, b0, i0), (e1, b1, i1)):
        assert e.shape == m.shape and b.shape == q.shape and state.shape == ll.shape
        assert all(np.all(np.isfinite(a)) for a in (e, b, state))
    em, bm, im = (e0 + e1) / 2, (b0 + b1) / 2, (i0 + i1) / 2
    j = data["j_mid"]
    assert j.shape == edges.shape and np.all(np.isfinite(j))
    total_j = np.bincount(edges, weights=j, minlength=len(m))
    ctqb = transpose_array(q * bm, shape)
    electric = np.linalg.norm(m * (e1 - e0) / dt - ctqb + total_j) / max(
        np.linalg.norm(ctqb) + np.linalg.norm(total_j), 1e-25)
    ce = curl_array(em, shape)
    magnetic = np.linalg.norm((b1 - b0) / dt + ce) / max(np.linalg.norm(ce), 1e-25)
    drho = node_divergence(m * (e1 - e0), shape) / dt
    expected = -node_divergence(total_j, shape)
    charge = np.linalg.norm(drho - expected) / max(
        np.linalg.norm(drho), np.linalg.norm(expected), 1e-25)
    left, right = ll * (i1 - i0) / dt, rr * (j[:, None] - im)
    rl = np.linalg.norm(left - right) / max(np.linalg.norm(left), np.linalg.norm(right), 1e-25)
    wall = np.linalg.norm(em[edges] - right.sum(axis=1)) / max(np.linalg.norm(em[edges]), 1e-25)
    u0 = .5 * (m @ e0**2 + q @ b0**2 + np.sum(ll * i0**2))
    u1 = .5 * (m @ e1**2 + q @ b1**2 + np.sum(ll * i1**2))
    loss = dt * np.sum(rr * (j[:, None] - im)**2)
    energy = abs(u1 - u0 + loss) / max(u0, u1, 1e-25)
    assert electric < 1e-7 and magnetic < 1e-7 and charge < 1e-6
    assert rl < 1e-5 and wall < 1e-5 and energy < 1e-9
    return dict(electric=float(electric), magnetic=float(magnetic), charge=float(charge),
                RL=float(rl), wall=float(wall), energy_step=float(energy))


def physical_pass(full_window, metrics):
    return bool(full_window and metrics and all(
        np.isfinite(metrics[key]) and 0 <= metrics[key] < .01 for key in (
            "relative_frequency_error", "relative_Q_error", "fit_window_Q_difference")))


def audit(partial=False):
    manifest, manifest_digest = load_manifest(OUT / "progress.json")
    config = manifest["configuration"]
    assert config["shape"] == [36, 23, 14] and config["mode"] == [0, 1, 1]
    assert config["lengths_m"] == [.16, .10, .06]
    assert config["steps"] == 4000000 and config["dt_s"] == 2e-12 and config["duration_s"] == 8e-6
    assert config["cg_rtol"] == 1e-13 and config["maxiter"] == 80 and config["terms"] == 64
    assert config["sample_stride"] == 10 and config["chunk_steps"] == 50000
    assert manifest["status"] in ("running", "failed_preserve_partial", "solver_complete_unverified")
    for key in ("full_long_time_passed", "manufactured_cavity_passed",
                "full_joint_simulation_passed"):
        assert manifest[key] is False, "Runner is not an acceptance authority"
    assert set(manifest["source_sha256"]) == {
        "run_fine_copper_longtime.py", "copper_midpoint_cg.py",
        "finite_copper_cavity.py", "finite_pec_cavity.py"}
    for name, digest in manifest["source_sha256"].items():
        assert sha(ROOT / name) == digest
    modelpath = ROOT / "\u539a\u94dc\u8fb9\u754c\u57fa\u51c6\u7ed3\u679c/passive_surface_models.json"
    approval = ROOT / "\u94dc\u8154CG\u5b8c\u6574\u72b6\u6001\u5bf9\u7167/verification.json"
    assert sha(modelpath) == manifest["model_sha256"]
    assert sha(approval) == manifest["approval_sha256"]
    model = next(row for row in json.loads(modelpath.read_text(encoding="utf-8"))["models"]
                 if row["terms"] == 64)
    if not partial:
        assert manifest["status"] == "solver_complete_unverified", "Long run not yet finished"
        assert manifest["completed_steps"] == 4000000 and len(manifest["chunks"]) == 80
    assert manifest["chunks"], "No completed state chunk yet"
    shape, dt = tuple(config["shape"]), config["dt_s"]
    m, q, edges, scale, _, _ = geometry(shape, config["lengths_m"])
    rr, ll = scale[:, None] * model["R_ohm"], scale[:, None] * model["L_H"]
    assert rr.shape[1] == ll.shape[1] == 64
    assert np.all(np.isfinite(rr)) and np.all(rr > 0)
    assert np.all(np.isfinite(ll)) and np.all(ll > 0)
    seed = seed_011(shape, config["lengths_m"], m)
    times, energies, heats, probes, checks = [], [], [], [], []
    previous, end = None, 0
    for row in manifest["chunks"]:
        assert row["file"] == f"steps_{end:07d}_{end + 50000:07d}.npz"
        path = OUT / row["file"]
        assert sha(path) == row["sha256"]
        with np.load(path) as data:
            assert int(data["start_step"]) == row["start_step"] == end
            end = int(data["end_step"])
            assert end == row["end_step"] and end - row["start_step"] == 50000
            assert end <= config["steps"]
            np.testing.assert_array_equal(data["shape"], shape)
            np.testing.assert_array_equal(data["lengths_m"], config["lengths_m"])
            assert float(data["dt_s"]) == dt
            assert len(data["scaled_residual"]) == len(data["cg_iterations"]) == 50000
            assert np.all(np.isfinite(data["scaled_residual"])) and max(data["scaled_residual"]) <= 5e-13
            assert np.all((data["cg_iterations"] >= 0) & (data["cg_iterations"] <= 80))
            assert np.all(data["cg_iterations"] == np.floor(data["cg_iterations"]))
            assert float(max(data["scaled_residual"])) == row["max_scaled_residual"]
            assert int(max(data["cg_iterations"])) == row["max_CG_iterations"]
            assert float(data["stepping_seconds"]) == row["stepping_seconds"] > 0
            t, u, heat, probe = (data[key] for key in (
                "sample_time_s", "total_energy_J", "cumulative_wall_heat_J", "modal_e_probe"))
            np.testing.assert_array_equal(t, np.arange(row["start_step"], end + 1, 10) * dt)
            assert np.all(np.isfinite(u)) and np.all(u > 0) and np.all(np.isfinite(heat))
            assert np.all(np.isfinite(probe)) and np.all(np.diff(heat) >= 0)
            assert np.max(np.diff(u)) / .5 < 1e-10
            assert np.max(abs(u + heat - .5)) / .5 < 1e-7
            starts = [data[name] for name in ("e_start", "b_start", "i_start")]
            if previous is None:
                assert t[0] == 0 and abs(u[0] - .5) < 1e-12 and heat[0] == 0
                assert np.all(starts[1] == 0) and np.all(starts[2] == 0)
                np.testing.assert_allclose(starts[0], seed, rtol=1e-13, atol=1e-10)
            else:
                for actual, expected in zip(starts, previous[:3]):
                    np.testing.assert_array_equal(actual, expected)
                np.testing.assert_array_equal([u[0], heat[0], probe[0]], previous[3:])
            e1, b1, i1 = (data[key] for key in ("e_after", "b_after", "i_after"))
            for e, b, state, observed in (
                    (*starts, u[0]), (e1, b1, i1, u[-1])):
                assert e.shape == m.shape and b.shape == q.shape and state.shape == ll.shape
                assert np.all(np.isfinite(e)) and np.all(np.isfinite(b)) and np.all(np.isfinite(state))
                stored = .5 * (m @ e**2 + q @ b**2 + np.sum(ll * state**2))
                np.testing.assert_allclose(stored, observed, rtol=1e-11, atol=0.)
            for e, observed in ((starts[0], probe[0]), (e1, probe[-1])):
                np.testing.assert_allclose(seed @ (m * e), observed, rtol=1e-11, atol=1e-13)
            np.testing.assert_allclose(row["global_energy_closure"],
                                       np.max(abs(u + heat - .5)) / .5, atol=1e-14)
            np.testing.assert_allclose(row["final_energy_fraction"], u[-1] / .5, rtol=1e-13)
            checks.append(dict(end_step=end, **check_anchor(data, shape, dt, m, q, edges, rr, ll)))
            previous = [e1.copy(), b1.copy(), i1.copy(), u[-1], heat[-1], probe[-1]]
            first = 0 if not times else 1
            for target, values in zip((times, energies, heats, probes), (t, u, heat, probe)):
                target.extend(values[first:])
    assert end == manifest["completed_steps"]
    full_window = not partial
    metrics = None
    if full_window:
        t, u, probe = map(np.asarray, (times, energies, probes))
        assert abs(t[-1] - 8e-6) < 1e-20 and len(t) == 400001
        crossings = np.flatnonzero((probe[:-1] <= 0) & (probe[1:] > 0) & (t[:-1] >= 1e-6))
        crossing_times = t[crossings] - probe[crossings] * (
            t[crossings + 1] - t[crossings]) / (probe[crossings + 1] - probe[crossings])
        assert len(crossings) > 10000
        frequency = (len(crossing_times) - 1) / (crossing_times[-1] - crossing_times[0])
        qs = []
        for limit in (5e-6, 8e-6):
            keep = (t >= 1e-6) & (t <= limit)
            tx, logu = t[keep], np.log(u[keep])
            x = tx - tx.mean()
            slope = x @ (logu - logu.mean()) / (x @ x)
            assert slope < 0
            qs.append(float(-2 * np.pi * frequency / slope))
        fref, qref = quadrature_q(np.array([0, 1, 1]), np.array(config["lengths_m"]), model["sigma_S_per_m"])
        metrics = dict(frequency_Hz=float(frequency), decay_Q=qs,
                       relative_frequency_error=float(abs(frequency / fref - 1)),
                       relative_Q_error=float(abs(qs[-1] / qref - 1)),
                       fit_window_Q_difference=float(abs(qs[-1] / qs[0] - 1)))
    physical = physical_pass(full_window, metrics)
    return dict(scope="partial_chunks_only" if partial else "selected_011_actual_8us",
                completed_steps=end, chunks_checked=len(checks), anchor_equations=checks,
                full_8us_window_present=full_window, decay_metrics=metrics,
                selected_011_8us_relative_Q_frequency_passed=physical,
                manifest_sha256=manifest_digest, manifest_snapshot=manifest,
                verifier_sha256=sha(Path(__file__)),
                helper_sha256={name: sha(ROOT / name) for name in (
                    "verify_finite_copper_cavity.py", "verify_finite_pec_cavity.py")},
                equation_coverage="one final-step full-state anchor per chunk; per-step CG residuals",
                independent_chunk_evidence_passed=True,
                arbitrary_modes_passed=False, manufactured_cavity_passed=False,
                full_joint_simulation_passed=False, full_system_gates_closed=0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--partial", action="store_true")
    args = parser.parse_args()
    result = audit(args.partial)
    prefix = "partial_verification" if args.partial else "verification"
    target = OUT / f"{prefix}_{result['manifest_sha256']}_{result['verifier_sha256'][:12]}.json"
    text = json.dumps(result, indent=2) + "\n"
    if target.exists():
        assert target.read_text(encoding="utf-8") == text, "Existing verification differs"
    else:
        with target.open("x", encoding="utf-8") as stream:
            stream.write(text)
    print(json.dumps(result, indent=2))
    if not args.partial and not result["selected_011_8us_relative_Q_frequency_passed"]:
        raise SystemExit(2)
