"""Complete two-step comparisons at frozen anchors, never long-run admission."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import time

import numpy as np
from threadpoolctl import threadpool_limits

from copper_gauss_legendre import GaussLegendre
from copper_gauss_precomputed import PrecomputedGaussLegendre
from finite_copper_cavity import CopperCavity
from run_copper_driven_history import coefficients, PULSES
from verify_copper_driven_history import source_coefficients
from verify_copper_gauss_state import check_collocation, equations_pass

ROOT = Path(__file__).resolve().parent
FROZEN = ROOT / "\u94dc\u8154\u56db\u9636\u5b8c\u6574\u72b6\u6001\u5bf9\u7167\u7ed3\u679c"
OUT = ROOT / "gauss_precomputed_driven_anchors"
MODEL = ROOT / "\u539a\u94dc\u8fb9\u754c\u57fa\u51c6\u7ed3\u679c" / "passive_surface_models.json"
CASES = ("111_loss0", "111_loss1", "mixed_history")
DTS = (2.5e-12, 1.25e-12)
PAIR_GATE = 1e-10
KEYS = ("e_before", "b_before", "i_before", "e_after", "b_after",
        "i_after", "stage_e", "stage_b", "stage_i", "stage_j")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def equal(a, b):
    np.testing.assert_array_equal(a, b)


def pair_errors(original, candidate):
    errors = []
    for a, b in zip(original, candidate):
        if a.shape != b.shape or not np.isfinite(a).all() or not np.isfinite(b).all():
            raise ValueError("Matching finite complete state and stage arrays required")
        errors.append(float(np.linalg.norm(a - b) / max(np.linalg.norm(a), 1e-300)))
    return errors


def check_pair(original, candidate):
    errors = pair_errors(original, candidate)
    if len(errors) != 7 or max(errors) >= PAIR_GATE:
        raise ValueError("Complete state/stage pair exceeds frozen local gate")
    return errors


def declaration():
    return dict(cases=list(CASES), dt_s=list(DTS), anchors_per_case=9, steps_per_anchor=2,
                anchor_dt_s=5e-12, shape=[36, 23, 14], lengths_m=[.16, .10, .06],
                terms=64, pair_gate=PAIR_GATE, gmres_rtol=1e-13, maxiter=80,
                residual_gate=5e-13, pulses=PULSES,
                scope="Restarted two-step complete-state segments at all frozen 5 ps anchors",
                continuous_16ns_history=False, full_long_history_passed=False,
                general_initial_state_approved=False, actual_feed_passed=False,
                frozen_long_solver_replaced=False, full_system_gates_closed=0)


def evidence():
    approval = FROZEN / "verification_27617c5451c5.json"
    prior = json.loads(approval.read_bytes())
    assert prior["independent_discrete_checks_passed"]
    assert prior["all_nine_physical_cases_present"] and prior["all_six_short_pairs_passed"]
    assert prior["general_initial_state_approved"] is False
    snapshot = prior["summary_snapshot"]
    for name, digest in snapshot["source_sha256"].items():
        assert sha(ROOT / name) == digest
    assert sha(MODEL) == snapshot["model_sha256"]
    rows = [next(row for row in snapshot["cases"]
                 if row["case"] == case and row["dt_s"] == 5e-12) for case in CASES]
    for row in rows:
        assert sha(FROZEN / row["file"]) == row["sha256"]
    paths = [MODEL, approval, Path(__file__)]
    paths += [FROZEN / row["file"] for row in rows]
    paths += [ROOT / name for name in (
        "copper_gauss_legendre.py", "copper_gauss_precomputed.py",
        "finite_copper_cavity.py", "finite_pec_cavity.py", "copper_doublet.py",
        "run_copper_driven_history.py", "verify_copper_driven_history.py",
        "verify_copper_gauss_state.py", "verify_copper_driven_state.py",
        "verify_finite_copper_cavity.py", "verify_finite_pec_cavity.py")]
    return {str(path): sha(path) for path in paths}


def run():
    assert not OUT.exists(), "Preserve all existing evidence; do not rerun this batch"
    bound = evidence()
    assert shutil.disk_usage(ROOT).free >= 10 * 1024**3
    OUT.mkdir()
    (OUT / "declaration.json").write_text(json.dumps(declaration(), indent=2) + "\n",
                                         encoding="ascii")
    bound[str(OUT / "declaration.json")] = sha(OUT / "declaration.json")
    manifest = dict(status="running", evidence_sha256=bound, segments=[])

    def save():
        (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n",
                                          encoding="ascii")

    save()
    model = next(m for m in json.loads(MODEL.read_bytes())["models"] if m["terms"] == 64)
    try:
        with threadpool_limits(limits=1):
            for case in CASES:
                with np.load(FROZEN / f"{case}_5ps.npz", allow_pickle=False) as raw:
                    steps = raw["snapshot_step"].copy()
                    starts = [raw[f"snapshot_{k}"].copy() for k in KEYS[:3]]
                    seeds, freqs = raw["seeds"].copy(), raw["source_frequency_Hz"].copy()
                assert len(steps) == 9
                for dt in DTS:
                    grid = CopperCavity((36, 23, 14), (.16, .10, .06))
                    solvers = [cls(grid, dt, model["R_ohm"], model["L_H"])
                               for cls in (GaussLegendre, PrecomputedGaussLegendre)]
                    assert (solvers[0].operator != solvers[1].operator).nnz == 0
                    for anchor, step in enumerate(steps):
                        assert shutil.disk_usage(OUT).free >= 8 * 1024**3
                        t0 = int(step) * 5e-12
                        states = [tuple(x[anchor].copy() for x in starts) for _ in solvers]
                        arrays = {f"{label}_{key}": [] for label in ("original", "candidate")
                                  for key in KEYS}
                        stage_sources, stage_coeffs, timings = [], [], [0., 0.]
                        residuals = []
                        for offset in range(2):
                            coeff = np.array([coefficients(t0 + (offset + c) * dt, freqs,
                                             PULSES if case == "mixed_history" else [])
                                              for c in solvers[0].nodes])
                            source = (coeff @ seeds.T) * grid.m
                            stage_sources.append(source.copy())
                            stage_coeffs.append(coeff)
                            results = [None, None]
                            for index in (range(2) if offset % 2 == 0 else reversed(range(2))):
                                before = states[index]
                                started = time.perf_counter()
                                result = solvers[index].step(*before, source)
                                timings[index] += time.perf_counter() - started
                                label = ("original", "candidate")[index]
                                for key, value in zip(KEYS, (*before, *result[:7])):
                                    arrays[f"{label}_{key}"].append(value.copy())
                                states[index], results[index] = result[:3], result
                            residuals.append([result[8] for result in results])
                        target = OUT / f"{case}_{dt * 1e12:g}ps_anchor{anchor}.npz"
                        assert not target.exists()
                        np.savez_compressed(target, **{k: np.array(v) for k, v in arrays.items()},
                                            sources=np.array(stage_sources),
                                            coefficients=np.array(stage_coeffs),
                                            residuals=np.array(residuals), seeds=seeds,
                                            source_frequency_Hz=freqs, t0_s=t0, dt_s=dt,
                                            anchor_step_5ps=int(step))
                        manifest["segments"].append(dict(case=case, dt_s=dt, anchor=anchor,
                            file=target.name, sha256=sha(target), stepping_seconds=timings))
                        save()
                        print("Saved", target.name, flush=True)
                        del arrays, states, results
                    del solvers, grid
        assert all(sha(Path(path)) == digest for path, digest in bound.items())
        manifest["status"] = "solver_complete_unverified"
        save()
    except Exception as error:
        manifest.update(status="failed_preserve_partial", failure_type=type(error).__name__,
                        failure_message=str(error))
        save()
        raise


def verify():
    raw_manifest = (OUT / "manifest.json").read_bytes()
    manifest = json.loads(raw_manifest)
    assert manifest["status"] == "solver_complete_unverified"
    assert json.loads((OUT / "declaration.json").read_bytes()) == declaration()
    for path, digest in manifest["evidence_sha256"].items():
        assert sha(Path(path)) == digest
    expected = [(case, dt, anchor) for case in CASES for dt in DTS for anchor in range(9)]
    assert [(r["case"], r["dt_s"], r["anchor"]) for r in manifest["segments"]] == expected
    model = next(m for m in json.loads(MODEL.read_bytes())["models"] if m["terms"] == 64)
    records, source_signs, maximum = [], set(), np.zeros(7)
    with threadpool_limits(limits=1):
        for row in manifest["segments"]:
            path = OUT / row["file"]
            digest = sha(path)
            assert digest == row["sha256"]
            with np.load(path, allow_pickle=False) as raw:
                data = {key: raw[key] for key in raw.files}
            with np.load(FROZEN / f"{row['case']}_5ps.npz", allow_pickle=False) as original:
                anchor, dt = row["anchor"], row["dt_s"]
                equal(data["seeds"], original["seeds"])
                equal(data["source_frequency_Hz"], original["source_frequency_Hz"])
                step = int(original["snapshot_step"][anchor])
                assert float(data["t0_s"]) == step * 5e-12
                assert int(data["anchor_step_5ps"]) == step and float(data["dt_s"]) == dt
                for label in ("original", "candidate"):
                    for key in KEYS[:3]:
                        equal(data[f"{label}_{key}"][0], original[f"snapshot_{key}"][anchor])
            assert data["sources"].shape[0] == 2 and data["residuals"].shape == (2, 2)
            assert np.isfinite(data["residuals"]).all()
            assert np.all((data["residuals"] >= 0) & (data["residuals"] <= 5e-13))
            grid = CopperCavity((36, 23, 14), (.16, .10, .06))
            checks, errors = [], []
            for offset in range(2):
                nodes = (.5 - np.sqrt(3) / 6, .5 + np.sqrt(3) / 6)
                coeff = np.array([source_coefficients(float(data["t0_s"]) + (offset + c) * dt,
                                  data["source_frequency_Hz"],
                                  PULSES if row["case"] == "mixed_history" else []) for c in nodes])
                np.testing.assert_allclose(data["coefficients"][offset], coeff, rtol=1e-14, atol=1e-8)
                source = (coeff @ data["seeds"].T) * grid.m
                np.testing.assert_allclose(data["sources"][offset], source, rtol=1e-13, atol=1e-15)
                results = []
                for label in ("original", "candidate"):
                    before = tuple(data[f"{label}_{key}"][offset] for key in KEYS[:3])
                    after = tuple(data[f"{label}_{key}"][offset] for key in KEYS[3:6])
                    stages = tuple(data[f"{label}_{key}"][offset] for key in KEYS[6:])
                    if offset:
                        for key, value in zip(KEYS[3:6], before):
                            equal(value, data[f"{label}_{key}"][offset - 1])
                    metrics = check_collocation((36, 23, 14), (.16, .10, .06), dt,
                                                np.array(model["R_ohm"]), np.array(model["L_H"]),
                                                before, after, stages, source)
                    assert equations_pass(metrics), metrics
                    checks.append(dict(offset=offset, solver=label, **metrics))
                    if abs(metrics["source_work_J"]) > 1e-25:
                        source_signs.add(int(np.sign(metrics["source_work_J"])))
                    results.append((*after, *stages))
                error = check_pair(*results)
                maximum = np.maximum(maximum, error)
                errors.append(error)
            assert sha(path) == digest
            records.append(dict(**row, independent_equations=checks,
                                relative_complete_state_stage_errors=errors))
            del data, grid
    assert source_signs == {-1, 1}, "Both supplied and absorbed source work required"
    assert (OUT / "manifest.json").read_bytes() == raw_manifest
    assert all(sha(Path(path)) == digest for path, digest in manifest["evidence_sha256"].items())
    content = dict(declaration=declaration(), manifest_sha256=hashlib.sha256(raw_manifest).hexdigest(),
                   evidence_sha256=manifest["evidence_sha256"], segments=records,
                   segment_count=len(records), compared_steps=2 * len(records),
                   maximum_relative_complete_state_stage_errors=maximum.tolist(),
                   observed_source_work_signs=sorted(source_signs),
                   all_saved_steps_independently_checked=True, local_anchor_pairs_passed=True,
                   continuous_16ns_history=False, full_long_history_passed=False,
                   general_initial_state_approved=False, actual_feed_passed=False,
                   full_system_gates_closed=0, goal_complete=False)
    tag = hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()[:12]
    report = OUT / f"verification_{tag}.json"
    assert not report.exists()
    report.write_text(json.dumps(content, indent=2) + "\n", encoding="ascii")
    print(json.dumps(dict(report=str(report), segments=len(records),
                         compared_steps=2 * len(records), maximum_errors=maximum.tolist(),
                         local_anchor_pairs_passed=True), indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    verify() if args.verify else run()
