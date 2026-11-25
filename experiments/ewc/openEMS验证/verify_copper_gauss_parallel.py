"""Independent audit of the parallel per-case 1.25 ps long histories.

Recomputes each chunk with the same equations as verify_copper_gauss_longtime.py, checks the
three-way hash chain and, once all three fine cases reach 8 us, judges frequency/Q against the
reference. It never overwrites an existing verification file and never edits run directories.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from threadpoolctl import threadpool_limits

from verify_copper_doublet import gaussian_reference, seed_basis
from verify_copper_gauss_longtime import check_chunk, full_metrics
from verify_fine_copper_longtime import seed_011
from verify_finite_copper_cavity import geometry

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "\u94dc\u8154\u56db\u9636\u5e76\u884c\u5355\u7b97\u4f8b\u7ed3\u679c"
MAIN = ROOT / "\u94dc\u8154\u56db\u9636\u53cc\u6781\u5316\u4e0e\u591a\u7d22\u5f158us\u7ed3\u679c"
CASES = ("111_loss0_1.25ps", "111_loss1_1.25ps", "mixed_history_1.25ps")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(partial=False):
    declaration_bytes = (MAIN / "configurations.json").read_bytes()
    declaration_sha = hashlib.sha256(declaration_bytes).hexdigest()
    declaration = json.loads(declaration_bytes)
    assert declaration["terms"] == 64 and declaration["cg_rtol"] == 1e-13 and declaration["maxiter"] == 80
    configs = {row["id"]: row for row in declaration["configurations"]}
    modelpath = ROOT / "\u539a\u94dc\u8fb9\u754c\u57fa\u51c6\u7ed3\u679c/passive_surface_models.json"
    model = next(x for x in json.loads(modelpath.read_bytes())["models"] if x["terms"] == 64)
    approval_path = ROOT / "\u94dc\u8154\u56db\u9636\u5b8c\u6574\u72b6\u6001\u5bf9\u7167\u7ed3\u679c/verification_27617c5451c5.json"
    m, q, _, scale, _, _ = geometry([36, 23, 14], [.16, .10, .06])
    ll = scale[:, None] * model["L_H"]
    ref = gaussian_reference(np.array([.16, .10, .06]), model["sigma_S_per_m"])
    rates, vectors = np.linalg.eig(np.linalg.solve(ref["mass"], ref["surface"]))
    expected_basis = ref["basis"] @ vectors[:, np.argsort(rates.real)]
    expected_basis /= np.linalg.norm(expected_basis, axis=0)
    cases, source_files = [], {}
    with threadpool_limits(limits=1):
        for case_id in CASES:
            case_dir = OUT / case_id
            if not case_dir.exists():
                assert partial, "Missing parallel case directory: " + case_id
                continue
            raw = (case_dir / "progress.json").read_bytes()
            manifest = json.loads(raw)
            config = configs[case_id]
            assert manifest["case"] == config
            assert sha(case_dir / "declaration.json") == manifest["declaration_sha256"] == declaration_sha
            assert set(manifest["source_sha256"]) == {
                "run_copper_gauss_longtime.py", "copper_gauss_legendre.py", "copper_doublet.py",
                "run_copper_driven_history.py", "finite_copper_cavity.py", "finite_pec_cavity.py"}
            for name, digest in manifest["source_sha256"].items():
                assert sha(ROOT / name) == digest
            assert sha(ROOT / "run_copper_gauss_case.py") == manifest["runner_sha256"]
            assert sha(modelpath) == manifest["model_sha256"]
            assert sha(approval_path) == manifest["approval_sha256"]
            assert manifest["general_initial_state_approved"] is False
            assert manifest["actual_feed_passed"] is False
            assert manifest["full_joint_simulation_passed"] is False
            assert manifest["full_system_gates_closed"] == 0
            assert manifest["status"] in ("running", "failed_preserve_partial", "solver_complete_unverified")
            chunks, last, samples, records = manifest["chunks"], None, [], []
            end = 0
            for row in chunks:
                path = case_dir / row["file"]
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
            assert end == manifest["completed_steps"]
            complete = end == round(8e-6 / config["dt_s"]) and len(chunks) == 80
            if not partial:
                assert manifest["status"] == "solver_complete_unverified" and complete, "Case not yet finished: " + case_id
            measured = full_metrics(np.array(samples), config, directional) if complete else None
            cases.append(dict(id=case_id, completed_steps=end, completed_s=end * config["dt_s"],
                              complete=complete, chunks=records, metrics=measured,
                              manifest_read_sha256=hashlib.sha256(raw).hexdigest()))
    complete_cases = [x for x in cases if x["complete"]]
    complete = not partial and len(cases) == len(CASES) and len(complete_cases) == len(CASES)
    passed = complete and all(x["metrics"].get("physical_passed", True) for x in cases)
    result = dict(partial_mode=partial, cases=cases, sources_sha256=source_files,
                  declaration_sha256=declaration_sha,
                  all_parallel_cases_complete=complete,
                  parallel_fine_histories_passed=passed,
                  cross_case_pairs_pending=True,
                  general_initial_state_approved=False, actual_feed_passed=False,
                  full_joint_simulation_passed=False, full_system_gates_closed=0,
                  verifier_sha256=sha(Path(__file__)))
    content = (json.dumps(result, indent=2) + "\n").encode("ascii")
    path = OUT / ("parallel_verification_" + hashlib.sha256(content).hexdigest()[:12] + ".json")
    if path.exists():
        assert path.read_bytes() == content, "Existing verification differs"
    else:
        path.write_bytes(content)
    print("Saved", path.name, "cases", len(cases), "fine 8us passed", passed)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--partial", action="store_true")
    args = parser.parse_args()
    result = audit(args.partial)
    raise SystemExit(0 if args.partial or result["parallel_fine_histories_passed"] else 2)
