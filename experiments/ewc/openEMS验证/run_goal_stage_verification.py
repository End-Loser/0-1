"""Independent stage checks; never claim closure of the seven system gates."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Lock

ROOT = Path(__file__).resolve().parent.parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def finished_longtime_manifest(path):
    if not path.is_file():
        return None
    try:
        raw = path.read_bytes()
        manifest = json.loads(raw)
    except json.JSONDecodeError:
        # The active runner writes a live progress file; retry on the next stage run.
        return None
    if manifest.get("status") != "solver_complete_unverified":
        return None
    return manifest, hashlib.sha256(raw).hexdigest()


def main():
    runner_start_digest = sha(Path(__file__))
    temp = Path(tempfile.mkdtemp(prefix="ewc-goal-stage-"))
    jobs = [
        ("ringdown", ["openEMS\u9a8c\u8bc1/verify_six_wall_ringdown.py"]),
        ("conditioner", ["\u5668\u4ef6\u9a8c\u8bc1/\u8c03\u7406\u65f6\u95f4\u6b65\u6536\u655b/verify_refinement.py"]),
        ("conditioner_fine", ["\u5668\u4ef6\u9a8c\u8bc1/\u8c03\u7406\u65f6\u95f4\u6b65\u6536\u655b/verify_fine_check.py"]),
        ("conditioner_tests", ["\u5668\u4ef6\u9a8c\u8bc1/\u8c03\u7406\u65f6\u95f4\u6b65\u6536\u655b/test_refinement.py"]),
        ("adc", ["\u6821\u51c6\u9a8c\u8bc1/ADS8866\u65f6\u5e8f/oracle.py", "\u6821\u51c6\u9a8c\u8bc1/ADS8866\u65f6\u5e8f/\u7ed3\u679c"]),
        ("adc_tests", ["\u6821\u51c6\u9a8c\u8bc1/ADS8866\u65f6\u5e8f/tests.py"]),
        ("sma", ["\u5c04\u9891\u7aef\u53e3\u9a8c\u8bc1/\u5b9e\u9645\u63a5\u5934\u6838\u67e5/verify_sources.py", "--require-temp"]),
        ("sma_tests", ["\u5c04\u9891\u7aef\u53e3\u9a8c\u8bc1/\u5b9e\u9645\u63a5\u5934\u6838\u67e5/test_verification.py"]),
        ("public_sources", ["\u5668\u4ef6\u9a8c\u8bc1/\u516c\u5f00\u6a21\u578b\u8865\u9f50/verify_public_sources_v2.py"]),
        ("public_supply", ["\u5668\u4ef6\u9a8c\u8bc1/\u516c\u5f00\u6a21\u578b\u8865\u9f50/verify_supply_screen.py"]),
        ("public_tests", ["\u5668\u4ef6\u9a8c\u8bc1/\u516c\u5f00\u6a21\u578b\u8865\u9f50/test_public_models.py"]),
        ("linked_initial", ["\u5668\u4ef6\u9a8c\u8bc1/\u771f\u5b9e\u53c2\u8003\u4f9b\u7535\u8054\u63a5/verify_linked_reference_v2.py"]),
        ("linked_scoped", ["\u5668\u4ef6\u9a8c\u8bc1/\u771f\u5b9e\u53c2\u8003\u4f9b\u7535\u8054\u63a5/verify_scoped_linkage_v2.py"]),
        ("linked_gear", ["\u5668\u4ef6\u9a8c\u8bc1/\u771f\u5b9e\u53c2\u8003\u4f9b\u7535\u8054\u63a5/verify_gear_linkage_v2.py"]),
        ("linked_nodeset", ["\u5668\u4ef6\u9a8c\u8bc1/\u771f\u5b9e\u53c2\u8003\u4f9b\u7535\u8054\u63a5/verify_nodeset_linkage_v2.py"]),
        ("linked_tests", ["\u5668\u4ef6\u9a8c\u8bc1/\u771f\u5b9e\u53c2\u8003\u4f9b\u7535\u8054\u63a5/test_linkage.py"]),
        ("linked_audit", ["\u5668\u4ef6\u9a8c\u8bc1/\u771f\u5b9e\u53c2\u8003\u4f9b\u7535\u8054\u63a5/audit_batches_v2.py"]),
        ("bias_decoupling", ["\u5668\u4ef6\u9a8c\u8bc1/\u53c2\u8003\u8282\u70b9\u53bb\u8026/verify_bias_decoupling.py"]),
        ("bias_decoupling_tests", ["\u5668\u4ef6\u9a8c\u8bc1/\u53c2\u8003\u8282\u70b9\u53bb\u8026/test_bias_decoupling.py"]),
        ("bias_feedback", ["\u5668\u4ef6\u9a8c\u8bc1/\u53c2\u8003\u53cd\u9988\u7535\u6d41\u590d\u6838/verify_feedback_audit.py"]),
        ("bias_feedback_tests", ["\u5668\u4ef6\u9a8c\u8bc1/\u53c2\u8003\u53cd\u9988\u7535\u6d41\u590d\u6838/test_feedback_audit.py"]),
        ("input_initial", ["\u5668\u4ef6\u9a8c\u8bc1/\u53c2\u8003\u8f93\u5165\u53bb\u8026/verify_current_revision.py"]),
        ("input_tests", ["\u5668\u4ef6\u9a8c\u8bc1/\u53c2\u8003\u8f93\u5165\u53bb\u8026/test_input_decoupling.py"]),
        ("input_fine", ["\u5668\u4ef6\u9a8c\u8bc1/\u53c2\u8003\u8f93\u5165\u65f6\u95f4\u6b65\u590d\u6838/verify_fine_steps.py"]),
        ("input_fine_tests", ["\u5668\u4ef6\u9a8c\u8bc1/\u53c2\u8003\u8f93\u5165\u65f6\u95f4\u6b65\u590d\u6838/test_fine_steps.py"]),
        ("supply_layers", ["\u5668\u4ef6\u9a8c\u8bc1/\u4f9b\u7535\u5206\u5c42\u8054\u63a5/verify_supply_layers.py"]),
        ("supply_tests", ["\u5668\u4ef6\u9a8c\u8bc1/\u4f9b\u7535\u5206\u5c42\u8054\u63a5/test_supply_layers.py"]),
        ("regulator_controls", ["\u5668\u4ef6\u9a8c\u8bc1/\u7a33\u538b\u4f5c\u7528\u57df\u5bf9\u7167/verify_regulator_controls.py"]),
        ("regulator_tests", ["\u5668\u4ef6\u9a8c\u8bc1/\u7a33\u538b\u4f5c\u7528\u57df\u5bf9\u7167/test_regulator_controls.py"]),
        ("internal_state", ["\u5668\u4ef6\u9a8c\u8bc1/\u5b8c\u6574\u7a33\u538b\u5185\u90e8\u72b6\u6001\u590d\u6838/verify_internal_state.py"]),
        ("internal_tests", ["\u5668\u4ef6\u9a8c\u8bc1/\u5b8c\u6574\u7a33\u538b\u5185\u90e8\u72b6\u6001\u590d\u6838/test_internal_state.py"]),
        ("load_history", ["\u5668\u4ef6\u9a8c\u8bc1/\u7a33\u538b\u8d1f\u8f7d\u65f6\u5e8f\u5bf9\u7167/verify_load_history.py"]),
        ("load_switch", ["\u5668\u4ef6\u9a8c\u8bc1/\u7a33\u538b\u8d1f\u8f7d\u65f6\u5e8f\u5bf9\u7167/verify_switch_boundary.py"]),
        ("load_tests", ["\u5668\u4ef6\u9a8c\u8bc1/\u7a33\u538b\u8d1f\u8f7d\u65f6\u5e8f\u5bf9\u7167/test_load_history.py"]),
        ("compatibility_source", ["\u5668\u4ef6\u9a8c\u8bc1/\u5f00\u5173\u517c\u5bb9\u6027\u6e90\u7801\u590d\u6838/verify_source.py"]),
        ("compatibility_tests", ["\u5668\u4ef6\u9a8c\u8bc1/\u5f00\u5173\u517c\u5bb9\u6027\u6e90\u7801\u590d\u6838/test_source.py"]),
        ("compatibility_build", ["\u5668\u4ef6\u9a8c\u8bc1/\u5f00\u5173\u517c\u5bb9\u6027\u6e90\u7801\u590d\u6838/verify_build_evidence.py"]),
        ("compatibility_buildtests", ["\u5668\u4ef6\u9a8c\u8bc1/\u5f00\u5173\u517c\u5bb9\u6027\u6e90\u7801\u590d\u6838/test_build_evidence.py"]),
        ("dac_specs", ["\u6821\u51c6\u9a8c\u8bc1/DAC80501\u9002\u7528\u6027/verify_dac.py"]),
        ("dac_tests", ["\u6821\u51c6\u9a8c\u8bc1/DAC80501\u9002\u7528\u6027/test_dac.py"]),
        ("dac_vendor_project", ["\u6821\u51c6\u9a8c\u8bc1/DAC80501\u539f\u5382\u5de5\u7a0b\u590d\u6838/verify_vendor_project.py"]),
        ("dac_vendor_tests", ["\u6821\u51c6\u9a8c\u8bc1/DAC80501\u539f\u5382\u5de5\u7a0b\u590d\u6838/test_vendor_project.py"]),
        ("orientation_fields", ["openEMS\u9a8c\u8bc1/verify_copper_orientation.py"]),
        ("orientation_tests", ["openEMS\u9a8c\u8bc1/test_copper_orientation.py"]),
        ("finecopper_states", ["openEMS\u9a8c\u8bc1/verify_fine_copper_ringdown.py"]),
        ("finecopper_tests", ["openEMS\u9a8c\u8bc1/test_fine_copper_ringdown.py"]),
        ("cg_equations", ["openEMS\u9a8c\u8bc1/verify_copper_cg_validation.py"]),
        ("cg_solver_tests", ["openEMS\u9a8c\u8bc1/test_copper_midpoint_cg.py"]),
        ("cg_archive_tests", ["openEMS\u9a8c\u8bc1/test_copper_cg_validation.py"]),
        ("longtime_preflight", ["openEMS\u9a8c\u8bc1/test_fine_copper_longtime.py"]),
        ("longtime_verifier_tests", ["openEMS\u9a8c\u8bc1/test_fine_copper_longtime_verifier.py"]),
        ("doublet_fields", ["openEMS\u9a8c\u8bc1/verify_copper_doublet.py"]),
        ("doublet_references", ["openEMS\u9a8c\u8bc1/test_copper_doublet.py"]),
        ("doublet_archives", ["openEMS\u9a8c\u8bc1/test_copper_doublet_archive.py"]),
        ("driven_solver_tests", ["openEMS\u9a8c\u8bc1/test_copper_driven_midpoint.py"]),
        ("driven_history_tests", ["openEMS\u9a8c\u8bc1/test_copper_driven_history.py"]),
        ("driven_verifier_tests", ["openEMS\u9a8c\u8bc1/test_copper_driven_history_verifier.py"]),
        ("source_envelope_tests", ["openEMS\u9a8c\u8bc1/test_copper_source_envelope.py"]),
        ("source_work_failure", ["openEMS\u9a8c\u8bc1/diagnose_copper_refinement_work.py"]),
        ("gauss_solver_tests", ["openEMS\u9a8c\u8bc1/test_copper_gauss_legendre.py"]),
        ("gauss_runner_tests", ["openEMS\u9a8c\u8bc1/test_copper_gauss_runner.py"]),
        ("gauss_archive_tests", ["openEMS\u9a8c\u8bc1/test_copper_gauss_archive.py"]),
        ("gauss_longtime_tests", ["openEMS\u9a8c\u8bc1/test_copper_gauss_longtime.py"]),
        ("gauss_longtime_preflight", ["openEMS\u9a8c\u8bc1/run_copper_longtime_preflight.py"]),
    ]
    longtime_root = ROOT / "openEMS\u9a8c\u8bc1/\u7ec6\u7f51\u683c\u94dc\u81548us\u957f\u65f6\u7ed3\u679c"
    longtime_finished = finished_longtime_manifest(longtime_root / "progress.json")
    if longtime_finished is not None:
        jobs.append(("longtime_8us", ["openEMS\u9a8c\u8bc1/verify_fine_copper_longtime.py"]))
    driven_root = ROOT / "openEMS\u9a8c\u8bc1/\u94dc\u8154\u5e26\u6e90\u56e0\u679c\u5386\u53f2\u7ed3\u679c"
    driven_finished = finished_longtime_manifest(driven_root / "summary.json")
    if driven_finished is not None:
        jobs.extend([
            ("driven_fields", ["openEMS\u9a8c\u8bc1/verify_copper_driven_history.py"]),
            ("driven_archive_tests", ["openEMS\u9a8c\u8bc1/test_copper_driven_history_archive.py"]),
        ])
    driven_refined_root = ROOT / "openEMS\u9a8c\u8bc1/\u94dc\u8154\u5e26\u6e90\u56e0\u679c\u5386\u53f2\u7ec6\u6b65\u7ed3\u679c"
    driven_refined_finished = finished_longtime_manifest(driven_refined_root / "summary.json")
    if driven_refined_finished is not None:
        jobs.append(("driven_refined_fields", [
            "openEMS\u9a8c\u8bc1/verify_copper_driven_history.py", "--refinement", "--require-pair"]))
    gauss_root = ROOT / "openEMS\u9a8c\u8bc1/\u94dc\u8154\u56db\u9636\u5b8c\u6574\u72b6\u6001\u5bf9\u7167\u7ed3\u679c"
    gauss_finished = finished_longtime_manifest(gauss_root / "summary.json")
    gauss_partial = False
    if gauss_finished is not None:
        jobs.append(("gauss_fields", ["openEMS\u9a8c\u8bc1/verify_copper_gauss_validation.py", "--require-pair"]))
    else:
        try:
            snapshot = json.loads((gauss_root / "summary.json").read_bytes())
            gauss_partial = len(snapshot.get("preflight", [])) == 6
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        if gauss_partial:
            jobs.append(("gauss_fields_partial", ["openEMS\u9a8c\u8bc1/verify_copper_gauss_validation.py", "--partial"]))
    gauss_long_root = ROOT / "openEMS\u9a8c\u8bc1/\u94dc\u8154\u56db\u9636\u53cc\u6781\u5316\u4e0e\u591a\u7d22\u5f158us\u7ed3\u679c"
    gauss_long_finished = finished_longtime_manifest(gauss_long_root / "progress.json")
    gauss_long_partial = False
    if gauss_long_finished is not None:
        jobs.append(("gauss_longtime_fields", ["openEMS\u9a8c\u8bc1/verify_copper_gauss_longtime.py"]))
    else:
        try:
            snapshot = json.loads((gauss_long_root / "progress.json").read_bytes())
            gauss_long_partial = any(c["chunks"] for c in snapshot["cases"])
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        if gauss_long_partial:
            jobs.append(("gauss_longtime_fields_partial", ["openEMS\u9a8c\u8bc1/verify_copper_gauss_longtime.py", "--partial"]))
    gauss_parallel_root = ROOT / "openEMS\u9a8c\u8bc1/\u94dc\u8154\u56db\u9636\u5e76\u884c\u5355\u7b97\u4f8b\u7ed3\u679c"
    parallel_verifier_ids = ("111_loss0_1.25ps", "111_loss1_1.25ps", "mixed_history_1.25ps")
    parallel_snapshots = {}
    for case_id in parallel_verifier_ids + ("111_loss1_2.5ps", "mixed_history_2.5ps"):
        try:
            parallel_snapshots[case_id] = json.loads((gauss_parallel_root / case_id / "progress.json").read_bytes())
        except (FileNotFoundError, json.JSONDecodeError):
            pass
    gauss_parallel_started = any(row.get("chunks") for row in parallel_snapshots.values())
    gauss_parallel_all_complete = all(
        parallel_snapshots.get(c, {}).get("status") == "solver_complete_unverified"
        for c in parallel_verifier_ids)
    if gauss_parallel_all_complete:
        jobs.append(("gauss_parallel_fields", ["openEMS\u9a8c\u8bc1/verify_copper_gauss_parallel.py"]))
    elif gauss_parallel_started:
        jobs.append(("gauss_parallel_fields_partial", ["openEMS\u9a8c\u8bc1/verify_copper_gauss_parallel.py", "--partial"]))
    main_case_status = None
    try:
        main_manifest = json.loads((gauss_long_root / "progress.json").read_bytes())
        main_case_status = next((c.get("status") for c in main_manifest.get("cases", [])
                                 if c.get("id") == "111_loss0_2.5ps"), None)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    all_histories_complete = main_case_status == "solver_complete_unverified" and all(
        parallel_snapshots.get(c, {}).get("status") == "solver_complete_unverified"
        for c in parallel_verifier_ids + ("111_loss1_2.5ps", "mixed_history_2.5ps"))
    if all_histories_complete:
        jobs.append(("gauss_all_histories_fields",
                     ["openEMS\u9a8c\u8bc1/verify_copper_gauss_all_histories.py"]))
    elif main_case_status is not None or gauss_parallel_started:
        jobs.append(("gauss_all_histories_fields_partial",
                     ["openEMS\u9a8c\u8bc1/verify_copper_gauss_all_histories.py", "--partial"]))
    cap_root = ROOT / "\u5668\u4ef6\u9a8c\u8bc1/\u4f9b\u7535\u7535\u5bb9\u7ec6\u6b65\u590d\u6838"
    cap_ready = (cap_root / "summary.json").is_file()
    if cap_ready:
        jobs.extend([
            ("supplycap_equations", ["\u5668\u4ef6\u9a8c\u8bc1/\u4f9b\u7535\u7535\u5bb9\u7ec6\u6b65\u590d\u6838/verify_capacitor_refinement.py"]),
            ("supplycap_tests", ["\u5668\u4ef6\u9a8c\u8bc1/\u4f9b\u7535\u7535\u5bb9\u7ec6\u6b65\u590d\u6838/test_capacitor_refinement.py"]),
        ])
    qready = (ROOT / "openEMS\u9a8c\u8bc1/\u94dcQ\u7ec6\u5316\u7ed3\u679c/summary.json").exists()
    if qready:
        jobs.append(("copper_q", ["openEMS\u9a8c\u8bc1/verify_copper_q_refinement.py"]))
    pec_root = ROOT / "openEMS\u9a8c\u8bc1/PEC\u8868\u9762\u7535\u8377\u8d26\u76ee\u7ed3\u679c"
    pec_ready = (pec_root / "summary.json").is_file() and (pec_root / "verification.json").is_file()
    if pec_ready:
        jobs.append(("pec_charge", [
            "openEMS\u9a8c\u8bc1/verify_pec_surface_charge.py",
            "openEMS\u9a8c\u8bc1/PEC\u8868\u9762\u7535\u8377\u8d26\u76ee\u7ed3\u679c", "--recheck"]))
    wrapper = ROOT / "\u5668\u4ef6\u9a8c\u8bc1/\u8c03\u7406\u65f6\u95f4\u6b65\u6536\u655b/verify_original_batch_v2.py"
    code = ("import importlib.util; from pathlib import Path; "
            f"s=importlib.util.spec_from_file_location('sealed',{str(wrapper)!r}); "
            "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
            f"m.ROOT=Path({str(temp)!r}); m.main()")
    jobs.append(("sealed_original", ["-c", code]))
    locks = {name.split("_")[0]: Lock() for name, _ in jobs}

    def execute(job):
        name, args = job
        source = ROOT / args[0] if args and args[0].endswith(".py") else None
        with locks[name.split("_")[0]]:
            source_digest = sha(source) if source is not None else None
            proc = subprocess.run([sys.executable, "-B"] + args, cwd=ROOT,
                                  capture_output=True, text=True, errors="replace",
                                  timeout=1200 if name.startswith("gauss_longtime_fields") or
                                  name.startswith("gauss_parallel_fields") or
                                  name.startswith("gauss_all_histories_fields") else
                                  600 if name == "longtime_8us" else 180)
            source_unchanged = source is None or sha(source) == source_digest
        log = temp / (name + ".log")
        log.write_text(proc.stdout + "\n" + proc.stderr, encoding="utf-8")
        return dict(name=name, returncode=proc.returncode, log=str(log), log_sha256=sha(log),
                    source_sha256=source_digest, source_revision_unchanged=source_unchanged)

    # Avoid concurrent writers to each evidence directory.
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(execute, jobs))
    runner_unchanged = sha(Path(__file__)) == runner_start_digest
    output = dict(scope="goal_stage_independent_checks_not_full_acceptance",
                  jobs=results, copper_q_summary_available=qready,
                  executable_checks_passed=all(r["returncode"] == 0 for r in results),
                  stage_checks_complete=qready and runner_unchanged and all(
                      r["returncode"] == 0 and r["source_revision_unchanged"] for r in results),
                  runner_revision_unchanged=runner_unchanged,
                  runner_start_sha256=runner_start_digest,
                  full_system_gates_closed=0, total_system_gates=7,
                  goal_complete=False, full_joint_simulation_passed=False,
                  actual_8us_verifier_scheduled=longtime_finished is not None,
                  driven_history_verifier_scheduled=driven_finished is not None,
                  driven_refined_verifier_scheduled=driven_refined_finished is not None,
                  gauss_complete_verifier_scheduled=gauss_finished is not None,
                  gauss_partial_verifier_scheduled=gauss_partial,
                  gauss_longtime_complete_verifier_scheduled=gauss_long_finished is not None,
                  gauss_longtime_partial_verifier_scheduled=gauss_long_partial,
                  gauss_parallel_partial_verifier_scheduled=gauss_parallel_started,
                  gauss_parallel_complete_verifier_scheduled=gauss_parallel_all_complete,
                  gauss_all_histories_complete_verifier_scheduled=all_histories_complete,
                  gauss_all_histories_partial_verifier_scheduled=main_case_status is not None,
                  supply_capacitor_refinement_available=cap_ready,
                  pec_surface_charge_verifier_scheduled=pec_ready,
                  logs_directory=str(temp), runner_sha256=sha(Path(__file__)))
    context_audit = temp / "original_batch_verification.json"
    if context_audit.exists():
        output["sealed_original_current_context"] = json.loads(context_audit.read_text(encoding="utf-8"))
    evidence = [
        "openEMS\u9a8c\u8bc1/\u94dcQ\u7ec6\u5316\u7ed3\u679c/summary.json",
        "openEMS\u9a8c\u8bc1/\u94dcQ\u7ec6\u5316\u7ed3\u679c/verification.json",
        "openEMS\u9a8c\u8bc1/\u516d\u58c1\u957f\u65f6\u8870\u51cf\u7ed3\u679c/summary.json",
        "openEMS\u9a8c\u8bc1/\u516d\u58c1\u957f\u65f6\u8870\u51cf\u7ed3\u679c/verification.json",
        "\u5668\u4ef6\u9a8c\u8bc1/\u8c03\u7406\u65f6\u95f4\u6b65\u6536\u655b/summary.json",
        "\u5668\u4ef6\u9a8c\u8bc1/\u8c03\u7406\u65f6\u95f4\u6b65\u6536\u655b/verification.json",
        "\u5668\u4ef6\u9a8c\u8bc1/\u8c03\u7406\u65f6\u95f4\u6b65\u6536\u655b/fine_check/summary.json",
        "\u5668\u4ef6\u9a8c\u8bc1/\u8c03\u7406\u65f6\u95f4\u6b65\u6536\u655b/fine_check/verification.json",
        "\u5c04\u9891\u7aef\u53e3\u9a8c\u8bc1/\u5b9e\u9645\u63a5\u5934\u6838\u67e5/verification.json",
        "EWC\u5168\u4efb\u52a1\u9a8c\u6536\u53f0\u8d26.md",
        "EWC\u5916\u90e8\u8bc1\u636e\u8865\u9f50\u6e05\u5355.md",
        "EWC\u771f\u5b9e\u6750\u6599\u4e0e\u5668\u4ef6\u7edf\u4e00\u89c4\u5212.md",
        "\u5668\u4ef6\u9a8c\u8bc1/\u516c\u5f00\u6a21\u578b\u8865\u9f50/source_verification.json",
        "\u5668\u4ef6\u9a8c\u8bc1/\u516c\u5f00\u6a21\u578b\u8865\u9f50/source_verification_env_missing.json",
        "\u5668\u4ef6\u9a8c\u8bc1/\u516c\u5f00\u6a21\u578b\u8865\u9f50/supply_instrumented/supply_screen.json",
        "\u5668\u4ef6\u9a8c\u8bc1/\u516c\u5f00\u6a21\u578b\u8865\u9f50/supply_verification.json",
        "EWC\u6388\u6743\u4e0e\u53d7\u9650\u8d44\u6599\u5f85\u529e.md",
        "\u5668\u4ef6\u9a8c\u8bc1/\u771f\u5b9e\u53c2\u8003\u4f9b\u7535\u8054\u63a5/summary.json",
        "\u5668\u4ef6\u9a8c\u8bc1/\u771f\u5b9e\u53c2\u8003\u4f9b\u7535\u8054\u63a5/verification.json",
        "\u5668\u4ef6\u9a8c\u8bc1/\u771f\u5b9e\u53c2\u8003\u4f9b\u7535\u8054\u63a5/scoped/summary.json",
        "\u5668\u4ef6\u9a8c\u8bc1/\u771f\u5b9e\u53c2\u8003\u4f9b\u7535\u8054\u63a5/scoped/verification.json",
        "\u5668\u4ef6\u9a8c\u8bc1/\u771f\u5b9e\u53c2\u8003\u4f9b\u7535\u8054\u63a5/gear/summary.json",
        "\u5668\u4ef6\u9a8c\u8bc1/\u771f\u5b9e\u53c2\u8003\u4f9b\u7535\u8054\u63a5/gear/verification.json",
        "\u5668\u4ef6\u9a8c\u8bc1/\u771f\u5b9e\u53c2\u8003\u4f9b\u7535\u8054\u63a5/nodeset/summary.json",
        "\u5668\u4ef6\u9a8c\u8bc1/\u771f\u5b9e\u53c2\u8003\u4f9b\u7535\u8054\u63a5/nodeset/verification.json",
        "\u5668\u4ef6\u9a8c\u8bc1/\u771f\u5b9e\u53c2\u8003\u4f9b\u7535\u8054\u63a5/verification_v2.json",
        "\u5668\u4ef6\u9a8c\u8bc1/\u771f\u5b9e\u53c2\u8003\u4f9b\u7535\u8054\u63a5/scoped/verification_v2.json",
        "\u5668\u4ef6\u9a8c\u8bc1/\u771f\u5b9e\u53c2\u8003\u4f9b\u7535\u8054\u63a5/gear/verification_v2.json",
        "\u5668\u4ef6\u9a8c\u8bc1/\u771f\u5b9e\u53c2\u8003\u4f9b\u7535\u8054\u63a5/nodeset/verification_v2.json",
        "\u5668\u4ef6\u9a8c\u8bc1/\u771f\u5b9e\u53c2\u8003\u4f9b\u7535\u8054\u63a5/batch_audit.json",
        "\u5668\u4ef6\u9a8c\u8bc1/\u771f\u5b9e\u53c2\u8003\u4f9b\u7535\u8054\u63a5/batch_audit_runs.json",
        "\u5668\u4ef6\u9a8c\u8bc1/\u53c2\u8003\u8282\u70b9\u53bb\u8026/summary.json",
        "\u5668\u4ef6\u9a8c\u8bc1/\u53c2\u8003\u8282\u70b9\u53bb\u8026/verification.json",
        "\u5668\u4ef6\u9a8c\u8bc1/\u53c2\u8003\u53cd\u9988\u7535\u6d41\u590d\u6838/summary.json",
        "\u5668\u4ef6\u9a8c\u8bc1/\u53c2\u8003\u53cd\u9988\u7535\u6d41\u590d\u6838/verification.json",
        "\u5668\u4ef6\u9a8c\u8bc1/\u53c2\u8003\u8282\u70b9\u53bb\u8026/\u53c2\u8003\u8282\u70b9\u53bb\u8026\u9a8c\u8bc1\u62a5\u544a.md",
        "\u5668\u4ef6\u9a8c\u8bc1/\u53c2\u8003\u53cd\u9988\u7535\u6d41\u590d\u6838/\u53c2\u8003\u53cd\u9988\u7535\u6d41\u590d\u6838\u62a5\u544a.md",
        "\u5668\u4ef6\u9a8c\u8bc1/\u53c2\u8003\u53cd\u9988\u7535\u6d41\u590d\u6838/run_feedback_audit.py",
        "\u5668\u4ef6\u9a8c\u8bc1/\u53c2\u8003\u53cd\u9988\u7535\u6d41\u590d\u6838/verify_feedback_audit.py",
        "\u5668\u4ef6\u9a8c\u8bc1/\u53c2\u8003\u53cd\u9988\u7535\u6d41\u590d\u6838/test_feedback_audit.py",
        "\u5668\u4ef6\u9a8c\u8bc1/\u53c2\u8003\u8f93\u5165\u53bb\u8026/summary.json",
        "\u5668\u4ef6\u9a8c\u8bc1/\u53c2\u8003\u8f93\u5165\u53bb\u8026/verification.json",
        "\u5668\u4ef6\u9a8c\u8bc1/\u53c2\u8003\u8f93\u5165\u53bb\u8026/current_verification.json",
        "\u5668\u4ef6\u9a8c\u8bc1/\u53c2\u8003\u8f93\u5165\u53bb\u8026/declared_configurations.json",
        "\u5668\u4ef6\u9a8c\u8bc1/\u53c2\u8003\u8f93\u5165\u53bb\u8026/run_input_decoupling.py",
        "\u5668\u4ef6\u9a8c\u8bc1/\u53c2\u8003\u8f93\u5165\u53bb\u8026/verify_input_decoupling.py",
        "\u5668\u4ef6\u9a8c\u8bc1/\u53c2\u8003\u8f93\u5165\u53bb\u8026/verify_current_revision.py",
        "\u5668\u4ef6\u9a8c\u8bc1/\u53c2\u8003\u8f93\u5165\u53bb\u8026/test_input_decoupling.py",
        "\u5668\u4ef6\u9a8c\u8bc1/\u53c2\u8003\u8f93\u5165\u53bb\u8026/\u53c2\u8003\u8f93\u5165\u53bb\u8026\u9a8c\u8bc1\u62a5\u544a.md",
        "\u5668\u4ef6\u9a8c\u8bc1/\u53c2\u8003\u8f93\u5165\u65f6\u95f4\u6b65\u590d\u6838/summary.json",
        "\u5668\u4ef6\u9a8c\u8bc1/\u53c2\u8003\u8f93\u5165\u65f6\u95f4\u6b65\u590d\u6838/verification.json",
        "\u5668\u4ef6\u9a8c\u8bc1/\u53c2\u8003\u8f93\u5165\u65f6\u95f4\u6b65\u590d\u6838/declared_configurations.json",
        "\u5668\u4ef6\u9a8c\u8bc1/\u53c2\u8003\u8f93\u5165\u65f6\u95f4\u6b65\u590d\u6838/run_fine_steps.py",
        "\u5668\u4ef6\u9a8c\u8bc1/\u53c2\u8003\u8f93\u5165\u65f6\u95f4\u6b65\u590d\u6838/verify_fine_steps.py",
        "\u5668\u4ef6\u9a8c\u8bc1/\u53c2\u8003\u8f93\u5165\u65f6\u95f4\u6b65\u590d\u6838/test_fine_steps.py",
        "\u5668\u4ef6\u9a8c\u8bc1/\u53c2\u8003\u8f93\u5165\u65f6\u95f4\u6b65\u590d\u6838/\u53c2\u8003\u8f93\u5165\u65f6\u95f4\u6b65\u590d\u6838\u62a5\u544a.md",
        "\u5668\u4ef6\u9a8c\u8bc1/\u4f9b\u7535\u5206\u5c42\u8054\u63a5/summary.json",
        "\u5668\u4ef6\u9a8c\u8bc1/\u4f9b\u7535\u5206\u5c42\u8054\u63a5/verification.json",
        "\u5668\u4ef6\u9a8c\u8bc1/\u4f9b\u7535\u5206\u5c42\u8054\u63a5/declared_configurations.json",
        "\u5668\u4ef6\u9a8c\u8bc1/\u4f9b\u7535\u5206\u5c42\u8054\u63a5/run_supply_layers.py",
        "\u5668\u4ef6\u9a8c\u8bc1/\u4f9b\u7535\u5206\u5c42\u8054\u63a5/verify_supply_layers.py",
        "\u5668\u4ef6\u9a8c\u8bc1/\u4f9b\u7535\u5206\u5c42\u8054\u63a5/test_supply_layers.py",
        "\u5668\u4ef6\u9a8c\u8bc1/\u4f9b\u7535\u5206\u5c42\u8054\u63a5/\u4f9b\u7535\u5206\u5c42\u8054\u63a5\u62a5\u544a.md",
        "\u5668\u4ef6\u9a8c\u8bc1/\u7a33\u538b\u4f5c\u7528\u57df\u5bf9\u7167/summary.json",
        "\u5668\u4ef6\u9a8c\u8bc1/\u7a33\u538b\u4f5c\u7528\u57df\u5bf9\u7167/verification.json",
        "\u5668\u4ef6\u9a8c\u8bc1/\u7a33\u538b\u4f5c\u7528\u57df\u5bf9\u7167/declared_configurations.json",
        "\u5668\u4ef6\u9a8c\u8bc1/\u7a33\u538b\u4f5c\u7528\u57df\u5bf9\u7167/run_regulator_controls.py",
        "\u5668\u4ef6\u9a8c\u8bc1/\u7a33\u538b\u4f5c\u7528\u57df\u5bf9\u7167/verify_regulator_controls.py",
        "\u5668\u4ef6\u9a8c\u8bc1/\u7a33\u538b\u4f5c\u7528\u57df\u5bf9\u7167/test_regulator_controls.py",
        "\u5668\u4ef6\u9a8c\u8bc1/\u7a33\u538b\u4f5c\u7528\u57df\u5bf9\u7167/\u7a33\u538b\u4f5c\u7528\u57df\u5bf9\u7167\u62a5\u544a.md",
        "\u5668\u4ef6\u9a8c\u8bc1/\u7a33\u538b\u8d1f\u8f7d\u65f6\u5e8f\u5bf9\u7167/summary.json",
        "\u5668\u4ef6\u9a8c\u8bc1/\u7a33\u538b\u8d1f\u8f7d\u65f6\u5e8f\u5bf9\u7167/declared_configurations.json",
        "\u5668\u4ef6\u9a8c\u8bc1/\u7a33\u538b\u8d1f\u8f7d\u65f6\u5e8f\u5bf9\u7167/verification.json",
        "\u5668\u4ef6\u9a8c\u8bc1/\u7a33\u538b\u8d1f\u8f7d\u65f6\u5e8f\u5bf9\u7167/run_load_history.py",
        "\u5668\u4ef6\u9a8c\u8bc1/\u7a33\u538b\u8d1f\u8f7d\u65f6\u5e8f\u5bf9\u7167/verify_load_history.py",
        "\u5668\u4ef6\u9a8c\u8bc1/\u7a33\u538b\u8d1f\u8f7d\u65f6\u5e8f\u5bf9\u7167/run_switch_boundary.py",
        "\u5668\u4ef6\u9a8c\u8bc1/\u7a33\u538b\u8d1f\u8f7d\u65f6\u5e8f\u5bf9\u7167/verify_switch_boundary.py",
        "\u5668\u4ef6\u9a8c\u8bc1/\u7a33\u538b\u8d1f\u8f7d\u65f6\u5e8f\u5bf9\u7167/test_load_history.py",
        "\u5668\u4ef6\u9a8c\u8bc1/\u7a33\u538b\u8d1f\u8f7d\u65f6\u5e8f\u5bf9\u7167/switch_boundary/summary.json",
        "\u5668\u4ef6\u9a8c\u8bc1/\u7a33\u538b\u8d1f\u8f7d\u65f6\u5e8f\u5bf9\u7167/switch_boundary/declared_configurations.json",
        "\u5668\u4ef6\u9a8c\u8bc1/\u7a33\u538b\u8d1f\u8f7d\u65f6\u5e8f\u5bf9\u7167/switch_boundary/verification.json",
        "\u5668\u4ef6\u9a8c\u8bc1/\u7a33\u538b\u8d1f\u8f7d\u65f6\u5e8f\u5bf9\u7167/\u7a33\u538b\u8d1f\u8f7d\u65f6\u5e8f\u4e0e\u5f00\u5173\u8fb9\u754c\u62a5\u544a.md",
    ]
    evidence.extend(
        "\u5668\u4ef6\u9a8c\u8bc1/\u5f00\u5173\u517c\u5bb9\u6027\u6e90\u7801\u590d\u6838/" + path
        for path in (
            "sources.json", "verification.json", "fetch_release_sources.py",
            "verify_source.py", "test_source.py", "pswitch-endpoint-candidate.patch",
            "original/cfunc.mod", "original/ifspec.ifs", "original/COPYING",
            "fixed/cfunc.mod", "fixed/ifspec.ifs", "fixed/COPYING",
            "capture_build_evidence.py", "verify_build_evidence.py", "test_build_evidence.py",
            "build_evidence.json", "build_verification.json",
            "\u4fbf\u643a\u6784\u5efa\u4e0e\u5b89\u5168\u9650\u5236\u62a5\u544a.md",
            "\u5f00\u5173\u6e90\u7801\u539f\u56e0\u4e0e\u5019\u9009\u4fee\u590d\u62a5\u544a.md",
        )
    )
    dac_root = ROOT / "\u6821\u51c6\u9a8c\u8bc1/DAC80501\u9002\u7528\u6027"
    dac_files = [
        dac_root / name for name in (
            "sources.json", "models.json", "spec_sources.json",
            "analog_configurations.json", "analog_summary.json",
            "spec_configurations.json", "spec_summary.json", "verification.json",
            "collect_sources.py", "collect_models.py", "prepare_spec_evidence.py",
            "evaluate_specs.py", "run_analog.py", "verify_dac.py", "test_dac.py",
            "DAC80501\u9002\u7528\u6027\u4e0e\u539f\u5e93\u5931\u8d25\u9a8c\u8bc1\u62a5\u544a.md",
        )
    ]
    assert all(path.is_file() for path in dac_files), "Missing DAC stage evidence"
    dac_files.extend(path for folder in ("analog_results", "spec_snapshots")
                     for path in (dac_root / folder).rglob("*") if path.is_file())
    evidence.extend(path.relative_to(ROOT).as_posix() for path in dac_files)
    copper_stage_files = [ROOT / "openEMS\u9a8c\u8bc1" / name for name in (
        "run_copper_orientation.py", "verify_copper_orientation.py", "test_copper_orientation.py",
        "run_fine_copper_ringdown.py", "verify_fine_copper_ringdown.py", "test_fine_copper_ringdown.py",
        "\u94dc\u8154\u7b2c\u4e09\u7535\u573a\u65b9\u5411\u9a8c\u8bc1\u62a5\u544a.md",
        "\u7ec6\u7f51\u683c\u94dc\u8154\u56e0\u679c\u77ed\u7a97\u9a8c\u8bc1\u62a5\u544a.md",
        "copper_midpoint_cg.py", "run_copper_cg_validation.py", "verify_copper_cg_validation.py",
        "test_copper_midpoint_cg.py", "test_copper_cg_validation.py",
        "run_fine_copper_longtime.py", "test_fine_copper_longtime.py",
        "verify_fine_copper_longtime.py", "test_fine_copper_longtime_verifier.py",
        "\u94dc\u8154\u5b8c\u6574\u72b6\u6001\u52a0\u901f\u5bf9\u7167\u62a5\u544a.md",
    )]
    copper_stage_files.append(ROOT / "EWC\u5168\u76ee\u6807\u6301\u7eed\u6267\u884c\u8bb0\u5f55.md")
    for folder in ("\u94dc\u8154\u7b2c\u4e09\u65b9\u5411\u7ed3\u679c",
                   "\u7ec6\u7f51\u683c\u94dc\u8154\u77ed\u7a97\u7ed3\u679c",
                   "\u94dc\u8154CG\u5b8c\u6574\u72b6\u6001\u5bf9\u7167"):
        base = ROOT / "openEMS\u9a8c\u8bc1" / folder
        copper_stage_files.extend(base / name for name in (
            "configurations.json", "summary.json", "verification.json"))
        copper_stage_files.extend(sorted(base.glob("*.npz")))
    assert all(path.is_file() for path in copper_stage_files), "Missing new copper stage evidence"
    evidence.extend(path.relative_to(ROOT).as_posix() for path in copper_stage_files)
    doublet_root = ROOT / "openEMS\u9a8c\u8bc1/\u94dc\u8154111\u53cc\u6781\u5316\u7ed3\u679c"
    doublet_files = [ROOT / "openEMS\u9a8c\u8bc1" / name for name in (
        "copper_doublet.py", "run_copper_doublet.py", "verify_copper_doublet.py",
        "test_copper_doublet.py", "test_copper_doublet_archive.py",
        "\u94dc\u8154111\u7b80\u5e76\u53cc\u6781\u5316\u9a8c\u8bc1\u62a5\u544a.md")]
    doublet_files.extend(doublet_root / name for name in ("configurations.json", "summary.json"))
    doublet_files.extend(doublet_root.glob("*.npz"))
    doublet_files.extend(doublet_root.glob("verification_*.json"))
    assert all(path.is_file() for path in doublet_files) and len(list(doublet_root.glob("*.npz"))) == 4
    evidence.extend(path.relative_to(ROOT).as_posix() for path in doublet_files)
    driven_files = [ROOT / "openEMS\u9a8c\u8bc1" / name for name in (
        "copper_driven_midpoint.py", "verify_copper_driven_state.py",
        "test_copper_driven_midpoint.py", "run_copper_driven_history.py",
        "test_copper_driven_history.py", "verify_copper_driven_history.py",
        "test_copper_driven_history_verifier.py", "test_copper_driven_history_archive.py",
        "run_copper_driven_refinement.py",
        "\u94dc\u8154\u5e26\u6e90\u56e0\u679c\u5386\u53f2\u9a8c\u8bc1\u62a5\u544a.md")]
    driven_files.append(driven_root / "configurations.json")
    for report in driven_root.glob("partial_verification_*.json"):
        snapshot = json.loads(report.read_text(encoding="utf-8"))["summary_snapshot"]
        driven_files.append(report)
        driven_files.extend(driven_root / row["file"] for row in snapshot["cases"])
    if driven_finished is not None:
        assert len(list(driven_root.glob("*.npz"))) == 6, "Missing completed driven raw states"
        driven_files.append(driven_root / "summary.json")
        driven_files.extend(driven_root.glob("*.npz"))
        driven_files.extend(driven_root.glob("verification_*.json"))
    assert all(path.is_file() for path in driven_files)
    evidence.extend(path.relative_to(ROOT).as_posix() for path in driven_files)
    refined_files = [driven_refined_root / "configurations.json"]
    for report in driven_refined_root.glob("partial_verification_*.json"):
        snapshot = json.loads(report.read_text(encoding="utf-8"))["summary_snapshot"]
        refined_files.append(report)
        refined_files.extend(driven_refined_root / row["file"] for row in snapshot["cases"])
    if driven_refined_finished is not None:
        assert len(list(driven_refined_root.glob("*.npz"))) == 6
        refined_files.append(driven_refined_root / "summary.json")
        refined_files.extend(driven_refined_root.glob("*.npz"))
        refined_files.extend(driven_refined_root.glob("verification_*.json"))
        refined_files.append(ROOT / "openEMS\u9a8c\u8bc1/\u94dc\u8154\u5e26\u6e90\u7ec6\u5316\u516d\u7ec4\u5b8c\u6574\u9a8c\u6536\u62a5\u544a.md")
    assert all(path.is_file() for path in refined_files)
    evidence.extend(path.relative_to(ROOT).as_posix() for path in refined_files)
    envelope_files = [ROOT / "openEMS\u9a8c\u8bc1" / name for name in (
        "test_copper_source_envelope.py", "diagnose_copper_refinement_work.py",
        "\u94dc\u8154\u6e90\u5305\u7edc\u6570\u503c\u6d88\u51cf\u4fee\u6b63\u62a5\u544a.md")]
    envelope_files.extend(driven_refined_root / name for name in (
        "verify_history_before_envelope_fix.py", "diagnose_work_before_binding_fix.py"))
    for report in driven_refined_root.glob("source_work_failure_*.json"):
        diagnostic = json.loads(report.read_bytes())
        assert diagnostic["any_strict_work_mismatch"] and not diagnostic["thresholds_changed"]
        assert diagnostic["raw_1ps_case_approved"] is False
        raw = driven_refined_root / diagnostic["raw_file"]
        assert sha(raw) == diagnostic["raw_sha256"]
        envelope_files.extend((report, raw))
    assert all(path.is_file() for path in envelope_files)
    evidence.extend(path.relative_to(ROOT).as_posix() for path in envelope_files)
    gauss_files = [ROOT / "openEMS\u9a8c\u8bc1" / name for name in (
        "copper_gauss_legendre.py", "verify_copper_gauss_state.py",
        "test_copper_gauss_legendre.py", "run_copper_gauss_validation.py",
        "test_copper_gauss_runner.py", "verify_copper_gauss_validation.py",
        "test_copper_gauss_archive.py",
        "\u94dc\u8154\u56db\u9636\u5b8c\u6574\u72b6\u6001\u5019\u9009\u9a8c\u8bc1\u62a5\u544a.md")]
    if gauss_finished is not None:
        gauss_files.append(ROOT / "openEMS\u9a8c\u8bc1/\u94dc\u8154\u56db\u9636\u4e5d\u7ec4\u5b8c\u6574\u9a8c\u6536\u62a5\u544a.md")
    gauss_files.append(gauss_root / "configurations.json")
    for report in gauss_root.glob("*verification_*.json"):
        data = json.loads(report.read_text(encoding="utf-8"))
        snapshot = data["summary_snapshot"]
        gauss_files.append(report)
        gauss_files.extend(gauss_root / row["file"] for key in ("preflight", "cases") for row in snapshot[key])
        gauss_files.extend(ROOT / "openEMS\u9a8c\u8bc1" / record["existing_midpoint_1ps_comparison"]["reference_file"]
                           for record in data["cases"] if "existing_midpoint_1ps_comparison" in record)
    if gauss_finished is not None:
        assert len(list(gauss_root.glob("*.npz"))) == 15
        gauss_files.append(gauss_root / "summary.json")
        gauss_files.extend(gauss_root.glob("*.npz"))
    assert all(path.is_file() for path in gauss_files), "Missing full-state candidate evidence"
    evidence.extend(path.relative_to(ROOT).as_posix() for path in gauss_files)
    material_followup = ROOT / "\u6750\u6599\u9a8c\u8bc1/\u516c\u5f00\u9891\u5e26\u7eed\u67e5"
    material_files = [material_followup / name for name in (
        "sources.json", "laminate_tool_webfetch.txt", "5880lz_webfetch.txt",
        "5880\u76ee\u6807\u9891\u5e26\u516c\u5f00\u7eed\u67e5\u62a5\u544a.md")]
    assert all(path.is_file() for path in material_files), "Missing material follow-up snapshots"
    source_manifest = json.loads(material_files[0].read_text(encoding="utf-8"))
    assert source_manifest["G2_closed"] is False
    assert source_manifest["target_5880_3GHz_batch_evidence_obtained"] is False
    for row in source_manifest["sources"]:
        assert sha(material_followup / row["snapshot"]) == row["snapshot_sha256"]
    evidence.extend(path.relative_to(ROOT).as_posix() for path in material_files)
    if cap_ready:
        cap_files = [cap_root / name for name in (
            "run_capacitor_refinement.py", "verify_capacitor_refinement.py",
            "test_capacitor_refinement.py", "declared_configurations.json",
            "summary.json", "verification.json",
            "\u4f9b\u7535\u7535\u5bb9\u7ec6\u65f6\u95f4\u6b65\u9a8c\u8bc1\u62a5\u544a.md")]
        cap_files.extend(p for p in (cap_root / "results").rglob("*") if p.is_file())
        assert all(p.is_file() for p in cap_files)
        evidence.extend(p.relative_to(ROOT).as_posix() for p in cap_files)
    if pec_ready:
        pec_files = [ROOT / "openEMS\u9a8c\u8bc1" / name for name in (
            "run_pec_surface_charge.py", "verify_pec_surface_charge.py")]
        pec_files.extend(pec_root / name for name in (
            "configurations.json", "summary.json", "verification.json"))
        pec_files.extend(sorted(pec_root.glob("*.npz")))
        assert all(p.is_file() for p in pec_files), "Missing PEC surface-charge evidence"
        evidence.extend(p.relative_to(ROOT).as_posix() for p in pec_files)
    internal_root = ROOT / "\u5668\u4ef6\u9a8c\u8bc1/\u5b8c\u6574\u7a33\u538b\u5185\u90e8\u72b6\u6001\u590d\u6838"
    internal_files = [internal_root / name for name in (
        "run_internal_state.py", "verify_internal_state.py", "test_internal_state.py",
        "declared_configurations.json", "summary.json", "verification.json",
        "\u5b8c\u6574\u7a33\u538b\u5185\u90e8\u72b6\u6001\u590d\u6838\u62a5\u544a.md")]
    internal_files.extend(p for p in (internal_root / "results").rglob("*") if p.is_file())
    assert all(p.is_file() for p in internal_files), "Missing full LDO observation evidence"
    evidence.extend(p.relative_to(ROOT).as_posix() for p in internal_files)
    vendor_root = ROOT / "\u6821\u51c6\u9a8c\u8bc1/DAC80501\u539f\u5382\u5de5\u7a0b\u590d\u6838"
    vendor_files = [vendor_root / name for name in (
        "verify_vendor_project.py", "test_vendor_project.py",
        "DAC80501\u539f\u5382\u5de5\u7a0b\u5931\u8d25\u4e0e\u9002\u7528\u8303\u56f4\u62a5\u544a.md")]
    vendor_reports = list(vendor_root.glob("verification_*.json"))
    assert vendor_reports, "Missing supplied PSpice artifact audit"
    vendor_files.extend(vendor_reports)
    for report in vendor_reports:
        vendor_data = json.loads(report.read_bytes())
        archive = Path(vendor_data["archive_path"])
        if not archive.is_file():
            # The original audit JSON still points at a temp folder that has been
            # cleaned up. Keep the historical record as-is and re-hash the
            # byte-identical permanent copy under 原件/.
            archive = vendor_root / "\u539f\u4ef6" / archive.name
        assert archive.is_file(), "Vendor archive unavailable for re-hash"
        assert sha(archive) == vendor_data["archive_sha256"]
        evidence.append(str(archive))
    assert all(p.is_file() for p in vendor_files)
    evidence.extend(p.relative_to(ROOT).as_posix() for p in vendor_files)
    long_gauss_files = [ROOT / "openEMS\u9a8c\u8bc1" / name for name in (
        "run_copper_gauss_longtime.py", "verify_copper_gauss_longtime.py",
        "test_copper_gauss_longtime.py", "run_copper_longtime_preflight.py",
        "\u94dc\u8154\u53cc\u6781\u5316\u4e0e\u591a\u7d22\u5f15\u5b8c\u6574\u957f\u7a97\u6267\u884c\u62a5\u544a.md")]
    long_gauss_files.append(gauss_long_root / "configurations.json")
    for folder in (ROOT / "openEMS\u9a8c\u8bc1").glob("\u94dc\u8154\u957f\u7a97\u5b8c\u6574\u72b6\u6001\u524d\u7f6e\u539f\u4ef6*"):
        long_gauss_files.extend(p for p in folder.rglob("*") if p.suffix in (".py", ".json", ".npz"))
    for report in gauss_long_root.glob("verification_*.json"):
        long_gauss_files.append(report)
        data = json.loads(report.read_bytes())
        for raw, digest in data["sources_sha256"].items():
            assert sha(Path(raw)) == digest
            long_gauss_files.append(Path(raw))
    if gauss_long_finished is not None:
        long_gauss_files.append(gauss_long_root / "progress.json")
    assert all(p.is_file() for p in long_gauss_files), "Missing long-Gauss evidence"
    evidence.extend(p.relative_to(ROOT).as_posix() for p in long_gauss_files)
    # Partial reports contain their parsed manifest snapshot; live progress is excluded.
    for report in sorted(longtime_root.glob("partial_verification_*.json")):
        snapshot = json.loads(report.read_text(encoding="utf-8"))["manifest_snapshot"]
        evidence.append(report.relative_to(ROOT).as_posix())
        evidence.extend((longtime_root / row["file"]).relative_to(ROOT).as_posix()
                        for row in snapshot["chunks"])
    if longtime_finished is not None:
        snapshot, digest = longtime_finished
        verifier_digest = sha(ROOT / "openEMS\u9a8c\u8bc1/verify_fine_copper_longtime.py")
        report = longtime_root / f"verification_{digest}_{verifier_digest[:12]}.json"
        if report.is_file():
            evidence.append(report.relative_to(ROOT).as_posix())
        evidence.append((longtime_root / "progress.json").relative_to(ROOT).as_posix())
        evidence.extend((longtime_root / row["file"]).relative_to(ROOT).as_posix()
                        for row in snapshot["chunks"])
    output["evidence_sha256"] = {p: sha(ROOT / p) for p in evidence if (ROOT / p).is_file()}
    (ROOT / "EWC\u5168\u76ee\u6807\u9636\u6bb5\u590d\u6838\u7ed3\u679c.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for row in results:
        print(row["name"], row["returncode"])
    print("Stage complete:", output["stage_checks_complete"], "system gates closed: 0/7")
    return 0 if output["stage_checks_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
