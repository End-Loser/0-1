import json
import copy
import importlib.util
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]


def contract(task):
    return json.loads((ROOT / "schemas/output_contracts" / f"{task}.json").read_text(encoding="utf-8"))


def test_causal_classifier_declares_all_required_consumer_fields():
    data = contract("P10")
    fields = set(data["conditional_fields"]["CAUSAL_INTERACTION_CONFIRMED"])
    assert set(data["causal_requirements"]) - {"evidence_refs"} <= fields
    assert "effect_node_refs" in fields
    assert "seq is local" in data["temporal_rule"]


def test_asset_source_declares_phase_targets_before_portfolio():
    data = contract("P12")
    assert set(data["conditional_fields"]["CREATE_ASSET"]) == {
        "introduce_chapter", "reveal_chapter", "payoff_chapter"}


def portfolio_validator():
    spec = importlib.util.spec_from_file_location("portfolio_logic", ROOT / "tools/validate_narrative_logic.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.validate_portfolio


def portfolio_fixture():
    sources = [{"asset_id": sid, "introduce_chapter": 1, "reveal_chapter": 2, "payoff_chapter": 5}
               for sid in ("A", "B", "C")]
    plan = {"selected_assets": [{"asset_id": "A", "source_asset_ids": ["A"],
                                "phase_chapters": {"SETUP": 1, "REVEAL": 2, "PAYOFF": 5}}],
            "deferred_assets": [{"asset_id": "B"}], "rejected_assets": [{"asset_id": "C"}],
            "merged_groups": []}
    return plan, sources


def test_portfolio_valid_partition_preserves_input():
    plan, sources = portfolio_fixture()
    original = copy.deepcopy((plan, sources))
    assert portfolio_validator()(plan, sources) == []
    assert (plan, sources) == original


def test_portfolio_reports_specific_duplicate_missing_and_unknown():
    plan, sources = portfolio_fixture()
    plan["deferred_assets"] = [{"asset_id": "A"}]
    plan["rejected_assets"] = [{"asset_id": "PLACEHOLDER"}]
    errors = portfolio_validator()(plan, sources)
    assert "PORTFOLIO_DUPLICATE_DISPOSITION:A" in errors
    assert "PORTFOLIO_MISSING_DISPOSITION:B" in errors
    assert "PORTFOLIO_MISSING_DISPOSITION:C" in errors
    assert "PORTFOLIO_UNKNOWN_DISPOSITION:PLACEHOLDER" in errors


def test_portfolio_phase_drift_and_missing_source_phase_block():
    plan, sources = portfolio_fixture()
    plan["selected_assets"][0]["phase_chapters"]["REVEAL"] = 3
    del sources[0]["payoff_chapter"]
    errors = portfolio_validator()(plan, sources)
    assert "PORTFOLIO_PHASE_DRIFT:A:REVEAL" in errors
    assert "PORTFOLIO_PHASE_DRIFT:A:PAYOFF" in errors


def test_portfolio_valid_merge_requires_exact_selected_sources():
    plan, sources = portfolio_fixture()
    plan["deferred_assets"] = []
    plan["selected_assets"][0]["source_asset_ids"] = ["A", "B"]
    assert "PORTFOLIO_MERGE_GROUP_MISSING:A" in portfolio_validator()(plan, sources)
    plan["merged_groups"] = [{"canonical_asset_id": "A", "source_asset_ids": ["A", "B"]}]
    assert portfolio_validator()(plan, sources) == []
    plan["merged_groups"][0]["source_asset_ids"] = ["A", "C"]
    assert "PORTFOLIO_MERGE_MAPPING_MISMATCH:A" in portfolio_validator()(plan, sources)


def test_portfolio_incompatible_source_phases_cannot_merge():
    plan, sources = portfolio_fixture()
    plan["deferred_assets"] = []
    plan["selected_assets"][0]["source_asset_ids"] = ["A", "B"]
    plan["merged_groups"] = [{"canonical_asset_id": "A", "source_asset_ids": ["A", "B"]}]
    sources[1]["introduce_chapter"] = 4
    assert "PORTFOLIO_PHASE_DRIFT:B:SETUP" in portfolio_validator()(plan, sources)


def test_portfolio_malformed_rows_block_without_exception():
    validate = portfolio_validator()
    assert validate([], []) == ["PORTFOLIO_OR_CANDIDATES_INVALID"]
    plan, sources = portfolio_fixture()
    plan["selected_assets"][0]["source_asset_ids"] = [{}]
    plan["merged_groups"] = [{"canonical_asset_id": "A", "source_asset_ids": [{}]}]
    assert validate(plan, sources)


def test_portfolio_duplicate_canonical_and_merge_block():
    plan, sources = portfolio_fixture()
    plan["selected_assets"].append(copy.deepcopy(plan["selected_assets"][0]))
    plan["merged_groups"] = [{"canonical_asset_id": "A", "source_asset_ids": ["A", "B"]}] * 2
    errors = portfolio_validator()(plan, sources)
    assert "PORTFOLIO_DUPLICATE_CANONICAL:A" in errors
    assert "PORTFOLIO_DUPLICATE_MERGE:A" in errors


def real_host():
    spec = importlib.util.spec_from_file_location("real_test_host", ROOT / "tools/real_novel_test.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_real_host_immutable_writes(tmp_path):
    host = real_host()
    path = tmp_path / "artifact.json"
    host.write(path, {"state": "original"})
    host.write(path, {"state": "original"})
    with pytest.raises(RuntimeError, match="IMMUTABLE_OUTPUT_EXISTS"):
        host.write(path, {"state": "changed"})
    assert host.read(path) == {"state": "original"}


def test_real_host_scopes_snapshots_without_losing_zero_magnitude_payload(tmp_path):
    host = real_host()
    host.WORK = tmp_path
    lines = [{"line": {"artifact_id": "BASE-" + eid, "content": {
        "id": eid + "-goal", "entity_id": eid, "dimension": "goal",
        "change_nodes": [{"chapter": 1, "seq": 0, "state": "start"},
                         {"chapter": 2, "seq": 0, "state": "end", "change_magnitude": 0,
                          "action_seed": "meaningful action"}]}}}
             for eid in ("A", "B")]
    host.write(tmp_path / "bases.json", {"lines": lines})
    before = host.snapshots(2, True, {"A"})
    after = host.snapshots(2, False, {"A"})
    assert len(before) == len(after) == 1
    assert before[0]["node"]["state"] == "start"
    assert after[0]["node"]["action_seed"] == "meaningful action"
    assert after[0]["source_node_ref"] == "A-goal::ch2.seq0"


def test_real_host_budget_blocks_before_request_archive(tmp_path, monkeypatch):
    host = real_host()
    host.WORK, host.BUDGET = tmp_path, 1
    host.ROWS = {"P13C": {"prompt_file": "prompts/P13C_EVENT_ACTOR_DECISION_PLAN.md",
                          "output_contract_file": "schemas/output_contracts/P13C.json"}}
    monkeypatch.setattr(host.PREFLIGHT, "evaluate", lambda *args: {"decision": "NEEDS_HOST_CHECK"})
    monkeypatch.setattr(host, "config", lambda: {
        "LLM_PRIMARY_MODEL": "test", "LLM_BASE_URL": "https://example.invalid", "LLM_API_KEY": "not-a-real-key"})
    with pytest.raises(RuntimeError, match="HOST_API_BUDGET_LIMIT"):
        host.call("P13C", "BLOCKED", {}, max_tokens=100)
    assert host.RESERVED == 0
    assert not (tmp_path / "requests").exists()


def test_real_host_does_not_freeze_failed_semantic_gate(tmp_path):
    host = real_host()
    host.WORK = tmp_path
    artifact = {"artifact_id": "CONDITIONAL", "decision": "CONDITIONAL", "status": "DRAFT"}
    with pytest.raises(RuntimeError, match="SEMANTIC_GATE"):
        host.require(artifact, {"READY"})
    assert host.read(tmp_path / "blocked/CONDITIONAL.json") == artifact
    assert not (tmp_path / "frozen").exists()


def test_call_limit_includes_inflight_reservations(tmp_path, monkeypatch):
    host = real_host()
    host.WORK, host.BUDGET, host.CALL_LIMIT, host.INFLIGHT_CALLS = tmp_path, 1000000, 1, 1
    host.ROWS = {"P13C": {"prompt_file": "prompts/P13C_EVENT_ACTOR_DECISION_PLAN.md",
                         "output_contract_file": "schemas/output_contracts/P13C.json"}}
    monkeypatch.setattr(host.PREFLIGHT, "evaluate", lambda *args: {"decision": "NEEDS_HOST_CHECK"})
    monkeypatch.setattr(host, "config", lambda: {
        "LLM_PRIMARY_MODEL": "test", "LLM_BASE_URL": "https://example.invalid", "LLM_API_KEY": "test-only"})
    with pytest.raises(RuntimeError, match="HOST_API_BUDGET_LIMIT"):
        host.call("P13C", "CALL-LIMIT", {})
    assert host.INFLIGHT_CALLS == 1
    assert host.RESERVED == 0
    assert not (tmp_path / "requests").exists()


def test_report_only_object_decision_does_not_crash_contract_validation():
    host = real_host()
    data = {"chapter": 3, "decision": {"result": "NO_TIME_CONFLICT"}}
    original = copy.deepcopy(data)
    assert host.validate_contract(data, {"required_fields": ["chapter", "decision"]}) == []
    assert data == original
    errors = host.validate_contract(data, {
        "required_fields": ["decision"], "decision_values": ["READY"],
        "conditional_fields": {"READY": ["conditions"]}})
    assert "DECISION_ENUM" in errors
    assert "DECISION_SHAPE" in errors


def test_explicit_call_limit_is_recorded_in_stage_receipts(tmp_path, monkeypatch):
    host = real_host()
    monkeypatch.setattr(sys, "argv", ["real_novel_test.py", "status", "--work-dir", str(tmp_path),
                                    "--token-budget", "8000000", "--call-limit", "550"])
    assert host.main() == 0
    assert host.read(tmp_path / "stage_runs/status.001.started.json")["call_limit"] == 550
    assert host.read(tmp_path / "stage_runs/status.001.finished.json")["call_limit"] == 550


def test_prose_reviews_keep_scene_samples_and_location_targets_separate(tmp_path, monkeypatch):
    host = real_host()
    host.WORK = tmp_path
    plans = [{"chapter": n, "outline": {"beats": []}, "events": [{"id": f"E{n}"}]}
             for n in range(1, 6)]
    texts = {n: ("opening " * n) + f"\n\nfinal scene {n}" for n in range(1, 6)}
    host.write(tmp_path / "chapters-r2.json", {"chapters": plans})
    host.write(tmp_path / "writers-r2.json", {"writers": [
        {"chapter": n, "content": {"text": text}} for n, text in texts.items()]})
    monkeypatch.setattr(host, "setup_data", lambda: {"seed": {
        "theme_seed": "test", "venue": "test venue", "clock": "same day"},
        "facts": {"facts": [], "unknowns": []}, "rule": {"rule": "test rule"}})
    monkeypatch.setattr(host, "snapshots", lambda *args: [])
    monkeypatch.setattr(host, "transition_nodes", lambda *args: [])
    captured = []

    def fake_call(task, key, inputs, **kwargs):
        captured.append((task, inputs))
        return {"artifact_id": key, "task_id": task}

    monkeypatch.setattr(host, "call", fake_call)
    host.reviews()
    assert len(captured) == 25
    samples = [data for task, data in captured if task == "P19A2"]
    for n, data in enumerate(samples, 1):
        assert "chapter_text" not in data
        assert data["scene_ref"] == f"CH{n}-FINAL-PARAGRAPH"
        assert data["scene_text"] == f"final scene {n}"
        assert texts[n][data["source_text_start"]:] == data["scene_text"]
    assert len([data for task, data in captured if task == "P19C3"]) == 5
    assert all("WorldConstants" in data and "boundary_candidates" in data
               for task, data in captured if task == "P19D2")
    assert "whole-book semantic consistency" in host.read(tmp_path / "reviews-r2.json")["scope"]["not_proven"]


def test_real_host_preserves_same_chapter_acquisition_sequence(tmp_path):
    host = real_host()
    host.WORK = tmp_path
    nodes = [{"chapter": 1, "seq": 0, "state": "unknown", "causal_status": "established"},
             {"chapter": 1, "seq": 1, "state": "heard phrase", "causal_status": "proposed"},
             {"chapter": 1, "seq": 2, "state": "noticed sound", "causal_status": "proposed"},
             {"chapter": 2, "seq": 0, "state": "later", "causal_status": "proposed"}]
    host.write(tmp_path / "bases.json", {"lines": [{"line": {"artifact_id": "BASE-A", "content": {
        "id": "A-cognition", "entity_id": "A", "dimension": "cognition", "change_nodes": nodes}}}]})
    transitions = host.transition_nodes(1, {"A"})
    assert [row["node"]["state"] for row in transitions] == ["heard phrase", "noticed sound"]
    assert host.snapshots(1, True, {"A"})[0]["node"]["state"] == "unknown"
    assert host.transition_nodes(1, {"B"}) == []


def test_real_host_conditional_plan_keeps_original_decision(tmp_path):
    host = real_host()
    host.WORK = tmp_path
    artifact = {"artifact_id": "DECISION-A", "decision": "CONDITIONAL",
                "unresolved_gaps": ["specific constraint"], "status": "DRAFT"}
    host.require(artifact, {"READY", "CONDITIONAL"})
    frozen = host.freeze(artifact, "conditional proposal, not unconditional readiness")
    assert frozen["decision"] == "CONDITIONAL"
    assert frozen["unresolved_gaps"] == artifact["unresolved_gaps"]
    assert artifact["status"] == "DRAFT"


def test_real_host_receipt_preserves_conditions_and_source_hash(tmp_path):
    host = real_host()
    host.WORK = tmp_path
    artifact = {"artifact_id": "AUDIT", "status": "DRAFT", "decision": "FEASIBLE_WITH_CONDITIONS",
                "blocked_reasons": [{"blocks_event": False, "description": "do not invent unknown names"}],
                "condition_checks": [{"condition_id": "A", "result": "PASS"},
                                     {"condition_id": "B", "result": "PASS_WITH_CONDITION"}]}
    frozen = host.freeze(artifact, "test-only receipt")
    receipt = host.audit_receipt(frozen)
    assert receipt["passed_condition_checks"] == 1
    assert receipt["condition_checks"] == [{"condition_id": "B", "result": "PASS_WITH_CONDITION"}]
    assert receipt["blocked_reasons"] == frozen["blocked_reasons"]
    assert receipt["source_sha256"] == host.sha(tmp_path / "frozen/AUDIT.json")
    assert receipt["decision"] == "FEASIBLE_WITH_CONDITIONS"


def base_target_fixture():
    plan = {"event_clusters": [{"cluster_id": "C2", "target_chapter": 2,
                               "expected_state_changes": [
                                   {"target_kind": "BASE_LINE", "target_ref": "A-relation::ch2.seq1"}]}]}
    nodes = [{"source_node_ref": "A-relation::ch2.seq1",
              "node": {"chapter": 2, "seq": 1, "causal_status": "proposed"}}]
    return plan, nodes


def test_base_target_exact_source_binding_preserves_inputs():
    host = real_host()
    plan, nodes = base_target_fixture()
    original = copy.deepcopy((plan, nodes))
    assert host.LOGIC.validate_base_targets(plan, nodes) == []
    assert (plan, nodes) == original


def test_base_target_future_node_cannot_be_consumed_early():
    host = real_host()
    plan, nodes = base_target_fixture()
    nodes[0]["source_node_ref"] = "CEN-relation::ch3.seq3"
    nodes[0]["node"]["chapter"] = 3
    plan["event_clusters"][0]["expected_state_changes"][0]["target_ref"] = nodes[0]["source_node_ref"]
    assert host.LOGIC.validate_base_targets(plan, nodes) == [
        "C2:BASE_TARGET_CHAPTER_DRIFT:CEN-relation::ch3.seq3:3!=2"]


def test_base_target_unknown_and_established_are_rejected():
    host = real_host()
    plan, nodes = base_target_fixture()
    assert "C2:BASE_TARGET_UNKNOWN:A-relation::ch2.seq1" in host.LOGIC.validate_base_targets(plan, [])
    nodes[0]["node"]["causal_status"] = "established"
    assert "C2:BASE_TARGET_NOT_PROPOSED:A-relation::ch2.seq1" in host.LOGIC.validate_base_targets(plan, nodes)


def test_base_target_malformed_or_duplicate_source_blocks():
    host = real_host()
    plan, nodes = base_target_fixture()
    assert host.LOGIC.validate_base_targets(plan, {}) == ["BASE_TARGET_INPUT_INVALID"]
    assert "BASE_SOURCE_NODE_INVALID" in host.LOGIC.validate_base_targets(plan, [None])
    assert "BASE_SOURCE_NODE_DUPLICATE:A-relation::ch2.seq1" in host.LOGIC.validate_base_targets(plan, nodes * 2)


def test_base_target_reader_obligation_is_not_a_base_state_change():
    host = real_host()
    plan, nodes = base_target_fixture()
    plan["event_clusters"][0]["expected_state_changes"][0]["target_kind"] = "READER_COGNITION"
    assert host.LOGIC.validate_base_targets(plan, []) == []


def test_real_host_rejects_bad_opportunity_before_any_paid_call(tmp_path, monkeypatch):
    host = real_host()
    host.WORK = tmp_path
    plan, nodes = base_target_fixture()
    nodes[0]["node"]["chapter"] = 3
    artifact = {"artifact_id": "P13B-R3", "status": "FROZEN", "content": plan}
    host.write(tmp_path / "frozen/P13B-R3.json", artifact)
    host.write(tmp_path / "relations.json", {"decisions": []})
    monkeypatch.setattr(host, "setup_data", lambda: {})
    monkeypatch.setattr(host, "source_assets", lambda: ({}, {"selected_assets": []}))
    monkeypatch.setattr(host, "base_source_nodes", lambda: nodes)
    monkeypatch.setattr(host, "call", lambda *args, **kwargs: pytest.fail("paid call must not start"))
    with pytest.raises(RuntimeError, match="BASE_TARGET_CHAPTER_DRIFT"):
        host.events()
    receipt = host.read(tmp_path / "blocked/P13B-R3.source-binding.json")
    assert receipt["decision"] == "BLOCKED_SOURCE_BINDING"
    assert receipt["source_sha256"] == host.sha(tmp_path / "frozen/P13B-R3.json")
    assert host.read(tmp_path / "frozen/P13B-R3.json") == artifact


def test_causal_audit_contract_separates_design_from_prose():
    boundary = contract("P19D")["planning_boundary"]
    assert "Absence of prose alone" in boundary
    assert "missing carriers" in boundary
    assert "never relabel old CONDITIONAL_PASS" in boundary


def test_real_host_causal_conditional_is_not_silently_passed(tmp_path):
    host = real_host()
    host.WORK = tmp_path
    artifact = {"artifact_id": "P19D-02-R3", "status": "DRAFT", "decision": "CONDITIONAL_PASS"}
    with pytest.raises(RuntimeError, match="SEMANTIC_GATE"):
        host.require(artifact, {"PASS"})
    assert host.read(tmp_path / "blocked/P19D-02-R3.json")["decision"] == "CONDITIONAL_PASS"
    assert not (tmp_path / "frozen").exists()


def test_real_host_stage_receipts_preserve_each_resume(tmp_path, monkeypatch):
    host = real_host()
    monkeypatch.setattr(sys, "argv", ["real_novel_test.py", "status",
                                    "--work-dir", str(tmp_path), "--token-budget", "4000000"])
    assert host.main() == 0
    assert host.main() == 0
    assert len(list((tmp_path / "stage_runs").glob("*.started.json"))) == 2
    assert len(list((tmp_path / "stage_runs").glob("*.finished.json"))) == 2
    receipt = host.read(tmp_path / "stage_runs/status.001.finished.json")
    assert receipt["outcome"] == "SUCCESS"
    assert receipt["recorded_new_calls"] == receipt["recorded_new_tokens"] == 0
    assert receipt["token_budget"] == 4000000


def test_real_host_stage_failure_is_recorded_without_approval(tmp_path, monkeypatch):
    host = real_host()
    monkeypatch.setattr(sys, "argv", ["real_novel_test.py", "events",
                                    "--work-dir", str(tmp_path), "--token-budget", "4000000"])

    def stop():
        raise RuntimeError("EXPECTED_GATE")

    monkeypatch.setattr(host, "events", stop)
    assert host.main() == 1
    receipt = host.read(tmp_path / "stage_runs/events.001.finished.json")
    assert receipt["outcome"] == "STOPPED"
    assert receipt["failure"] == "EXPECTED_GATE"
    assert not (tmp_path / "frozen").exists()


def causal_render_fixture():
    event = {"actions": ["Record the observed limit"], "causal_links": [
        {"target_kind": "READER_COGNITION", "target_ref": "READER-LIMIT"}]}
    audit = {"decision": "CONDITIONAL_PASS", "causal_findings": [
        {"finding_id": "F1", "result": "CONDITIONAL", "blocking": False}],
        "conditional_items": [{"condition_id": "C1", "finding_id": "F1",
            "condition_type": "READER_BEAT_EMBODIMENT", "owner_stage": "P15A",
            "target_ref": "READER-LIMIT", "event_action_indices": [0],
            "required_action": "Render the existing observation in a beat"}]}
    return audit, event


def test_causal_render_only_condition_keeps_original_decision():
    host = real_host()
    audit, event = causal_render_fixture()
    original = copy.deepcopy((audit, event))
    assert host.LOGIC.validate_causal_continuation(audit, event) == []
    assert (audit, event) == original


@pytest.mark.parametrize("mutation", ["blocking", "revise", "resource", "unknown_ref", "bad_index", "missing_item"])
def test_causal_continuation_rejects_nonrender_or_unbound_conditions(mutation):
    host = real_host()
    audit, event = causal_render_fixture()
    if mutation == "blocking":
        audit["causal_findings"][0]["blocking"] = True
    elif mutation == "revise":
        audit["causal_findings"][0]["result"] = "REVISE"
    elif mutation == "resource":
        audit["conditional_items"][0]["condition_type"] = "MISSING_RESOURCE"
    elif mutation == "unknown_ref":
        audit["conditional_items"][0]["target_ref"] = "INVENTED"
    elif mutation == "bad_index":
        audit["conditional_items"][0]["event_action_indices"] = [True, 99]
    else:
        audit["conditional_items"] = []
    assert host.LOGIC.validate_causal_continuation(audit, event)


def test_render_obligation_requires_exactly_one_payload_beat():
    host = real_host()
    audit, _ = causal_render_fixture()
    conditions = audit["conditional_items"]
    outline = {"beats": [{"beat_id": "B1", "payload": True,
                         "fulfilled_condition_ids": ["C1"],
                         "source_reader_obligation_refs": ["READER-LIMIT"],
                         "required_elements": ["Record the observed limit"]}]}
    assert host.LOGIC.validate_render_obligations(outline, conditions) == []
    outline["beats"] *= 2
    assert "RENDER_OBLIGATION_BEAT_COUNT:C1:2" in host.LOGIC.validate_render_obligations(outline, conditions)
    outline["beats"] = []
    assert "RENDER_OBLIGATION_BEAT_COUNT:C1:0" in host.LOGIC.validate_render_obligations(outline, conditions)


def test_render_obligation_cannot_be_cleared_by_an_unbound_beat():
    host = real_host()
    audit, _ = causal_render_fixture()
    outline = {"beats": [{"beat_id": "B1", "payload": False, "fulfilled_condition_ids": ["C1"],
                         "source_reader_obligation_refs": ["OTHER"], "required_elements": []}]}
    assert host.LOGIC.validate_render_obligations(outline, audit["conditional_items"]) == [
        "RENDER_OBLIGATION_BEAT_UNBOUND:C1"]


def ownership_source_fixture():
    record = {"event": {"id": "E1", "chapter": 1, "outcome": "Observed limit"},
              "decision_plan_refs": ["D1"], "asset_phase_refs": [],
              "source_relationship_ids": ["R1"]}
    unit = {"source_event_ids": ["E1"], "canonical_event": copy.deepcopy(record["event"]),
            "decision_plan_refs": ["D1"], "asset_phase_refs": [], "source_relationship_ids": ["R1"]}
    return {"event_units": [unit], "excluded_source_events": [], "required_state_nodes": ["N1"]}, [record]


def test_single_event_ownership_preserves_source_and_provenance():
    host = real_host()
    plan, records = ownership_source_fixture()
    original = copy.deepcopy((plan, records))
    assert host.LOGIC.validate_single_event_ownership(plan, records, ["N1"]) == []
    assert (plan, records) == original


def test_single_event_ownership_cannot_rewrite_or_drop_source():
    host = real_host()
    plan, records = ownership_source_fixture()
    plan["event_units"][0]["canonical_event"]["outcome"] = "Invented verdict"
    plan["event_units"][0]["source_relationship_ids"] = []
    errors = host.LOGIC.validate_single_event_ownership(plan, records, ["N1"])
    assert "OWNERSHIP_CANONICAL_EVENT_CHANGED:E1" in errors
    assert "OWNERSHIP_PROVENANCE_CHANGED:source_relationship_ids" in errors
    plan["excluded_source_events"] = [{"source_event_id": "E1", "approved": True}]
    plan["event_units"] = []
    errors = host.LOGIC.validate_single_event_ownership(plan, records, ["N1"])
    assert "OWNERSHIP_EXCLUSION_NOT_AUTHORIZED" in errors
    assert "OWNERSHIP_SOURCE_COVERAGE" in errors


def test_chapter_sequence_requires_exact_source_ids():
    host = real_host()
    events = [{"id": "E1"}, {"id": "E2"}]
    sequence = {"event_order": [{"event_id": "E2"}, {"event_id": "E1"}],
                "unresolved_dependencies": []}
    assert host.LOGIC.validate_event_sequence(sequence, events) == []
    sequence["event_order"][0]["event_id"] = "INVENTED"
    assert "SEQUENCE_APPROVED_EVENT_COVERAGE" in host.LOGIC.validate_event_sequence(sequence, events)
    sequence["unresolved_dependencies"] = [{"blocking": True}]
    assert "SEQUENCE_UNRESOLVED_BLOCKER" in host.LOGIC.validate_event_sequence(sequence, events)


def test_outline_cannot_summarize_event_or_drop_beat_binding():
    host = real_host()
    events = [{"id": "E1", "outcome": "Recorded observation"}]
    outline = {"event_records": copy.deepcopy(events), "beats": [{"event_id": "E1"}]}
    assert host.LOGIC.validate_outline_sources(outline, events) == []
    outline["event_records"][0]["outcome"] = "Changed conclusion"
    assert "OUTLINE_CANONICAL_EVENTS_CHANGED" in host.LOGIC.validate_outline_sources(outline, events)
    outline["beats"][0]["event_id"] = "OTHER"
    errors = host.LOGIC.validate_outline_sources(outline, events)
    assert "OUTLINE_BEAT_EVENT_UNBOUND" in errors
    assert "OUTLINE_BEAT_EVENT_COVERAGE" in errors


def test_real_outline_request_requires_canonical_sources():
    host = real_host()
    source = Path(host.__file__).read_text(encoding="utf-8")
    assert '("event_records", "beats", "active_assets", "word_count_target")' in source
    schema = host.read(host.ROOT / "schemas/CHAPTER_OUTLINE_SCHEMA.json")
    assert schema["required"] == ["chapter"]


def test_outline_error_identifies_missing_canonical_field_without_repairing():
    host = real_host()
    events = [{"id": "E1", "variable_changes": [], "outcome": "Observed limit"}]
    outline = {"event_records": [{"id": "E1", "outcome": "Observed limit"}],
               "beats": [{"event_id": "E1"}]}
    original = copy.deepcopy(outline)
    errors = host.LOGIC.validate_outline_sources(outline, events)
    assert "OUTLINE_EVENT_MISSING_FIELD:0:variable_changes" in errors
    assert outline == original


def test_reader_error_supplies_exact_binding_without_creating_beat():
    host = real_host()
    audit, _ = causal_render_fixture()
    outline = {"beats": []}
    errors = host.LOGIC.validate_render_obligations(outline, audit["conditional_items"])
    assert ("RENDER_BIND_EXACTLY_ONE_PAYLOAD_BEAT:fulfilled_condition_ids=[C1]:"
            "source_reader_obligation_refs=[READER-LIMIT]") in errors
    assert outline["beats"] == []


def test_outline_request_schema_defines_reader_binding_fields(tmp_path, monkeypatch):
    from contextlib import contextmanager
    host = real_host()
    host.WORK, host.BUDGET = tmp_path, 1000000
    host.ROWS = {}
    import csv
    with (ROOT / "TASK_REGISTRY.csv").open(encoding="utf-8-sig") as stream:
        host.ROWS["P15A"] = next(row for row in csv.DictReader(stream) if row["task_id"] == "P15A")
    monkeypatch.setattr(host.PREFLIGHT, "evaluate", lambda *args: {"decision": "NEEDS_HOST_CHECK"})
    monkeypatch.setattr(host, "config", lambda: {
        "LLM_PRIMARY_MODEL": "test", "LLM_BASE_URL": "https://example.invalid", "LLM_API_KEY": "test-only"})
    monkeypatch.setattr(host, "validate_schema", lambda *args: [])

    @contextmanager
    def fake_response(request, timeout):
        sent = json.loads(json.loads(request.data)["messages"][1]["content"])
        schema = sent["output_schema"]
        assert "fulfilled_condition_ids" in schema["$defs"]["OutlineBeat"]["required"]
        constraint = schema["properties"]["beats"]["allOf"][0]
        assert constraint["maxContains"] == constraint["minContains"] == 1
        assert constraint["contains"]["properties"]["payload"] == {"const": True}
        assert constraint["contains"]["properties"]["fulfilled_condition_ids"]["contains"] == {"const": "C1"}
        class Response:
            def read(self):
                return json.dumps({"usage": {"total_tokens": 1}, "choices": [
                    {"finish_reason": "stop", "message": {"content": '{"chapter":1}'}}]}).encode()
        yield Response()

    monkeypatch.setattr(host.urllib.request, "urlopen", fake_response)
    host.call("P15A", "OUTLINE", {"explicit_conditional_constraints": [
        {"causal_render_obligations": [{"condition_id": "C1", "target_ref": "READER-LIMIT"}]}]})


def test_trace_resume_creates_new_snapshot_with_new_failed_attempt(tmp_path, monkeypatch):
    host = real_host()
    host.WORK = tmp_path
    (tmp_path / "usage.jsonl").write_text("", encoding="utf-8")
    host.write(tmp_path / "requests/T.attempt-1.json",
               {"input_pack": {"pack_id": "T", "task_id": "P19D", "inputs": {}}})
    monkeypatch.setattr(host, "run_tool", lambda *args: None)
    host.trace()
    host.write(tmp_path / "requests/T.attempt-2.json",
               {"input_pack": {"pack_id": "T", "task_id": "P19D", "inputs": {"retry": True}}})
    host.trace()
    assert host.read(tmp_path / "trace_snapshots/001/snapshot_receipt.json")["request_count"] == 1
    assert host.read(tmp_path / "trace_snapshots/002/snapshot_receipt.json")["request_count"] == 2
    assert not (tmp_path / "trace_snapshots/002/outputs/T.attempt-2.json").exists()


def test_full_planned_node_coverage_preserves_intermediate_changes():
    host = real_host()
    nodes = [{"source_node_ref": "L::ch1.seq1", "node": {"chapter": 1, "causal_status": "proposed"}},
             {"source_node_ref": "L::ch1.seq2", "node": {"chapter": 1, "causal_status": "proposed"}}]
    change = {"target_kind": "BASE_LINE", "target_ref": "L::ch1.seq2"}
    plan = {"event_clusters": [{"cluster_id": "C1", "target_chapter": 1, "expected_state_changes": [change]}]}
    assert "PLANNED_NODE_OWNER_COUNT:L::ch1.seq1:0" in host.LOGIC.validate_planned_node_coverage(plan, nodes)
    plan["event_clusters"][0]["expected_state_changes"].append({**change, "target_ref": "L::ch1.seq1"})
    assert host.LOGIC.validate_planned_node_coverage(plan, nodes) == []


def test_event_must_keep_reader_target_and_exact_drivers():
    host = real_host()
    drivers = [{"kind": "ASSET_PHASE", "ref": "A:REVEAL"}]
    change = {"target_kind": "READER_COGNITION", "target_ref": "READER-LIMIT", "cause_driver_refs": drivers}
    cluster = {"expected_state_changes": [change]}
    event = {"causal_links": []}
    assert host.LOGIC.validate_event_target_coverage(event, cluster) == ["EVENT_TARGET_COVERAGE:READER-LIMIT"]
    event["causal_links"] = [{"target_kind": "READER_COGNITION", "target_ref": "READER-LIMIT",
                              "driver_refs": copy.deepcopy(drivers)}]
    assert host.LOGIC.validate_event_target_coverage(event, cluster) == []
    event["causal_links"][0]["driver_refs"] = []
    assert host.LOGIC.validate_event_target_coverage(event, cluster) == ["EVENT_TARGET_DRIVERS_CHANGED:READER-LIMIT"]


def test_retry_carries_nearest_valid_failed_output_without_paid_api(tmp_path, monkeypatch):
    from contextlib import contextmanager
    host = real_host()
    host.WORK, host.BUDGET = tmp_path, 1000000
    host.ROWS = {"P13C": {"prompt_file": "prompts/P13C_EVENT_ACTOR_DECISION_PLAN.md",
                          "output_contract_file": "schemas/output_contracts/P13C.json"}}
    monkeypatch.setattr(host.PREFLIGHT, "evaluate", lambda *args: {"decision": "NEEDS_HOST_CHECK"})
    monkeypatch.setattr(host, "config", lambda: {
        "LLM_PRIMARY_MODEL": "test", "LLM_BASE_URL": "https://example.invalid", "LLM_API_KEY": "test-only"})
    def current_validation(data, schema_or_contract):
        return ([] if data.get("fixed") else
                (["CURRENT_FIELD_ERROR"] if data.get("kept_nodes") == 67 else ["A", "B"]))

    monkeypatch.setattr(host, "validate_schema", current_validation)
    monkeypatch.setattr(host, "validate_contract", current_validation)
    for index, errors, output in ((1, ["ONE_ERROR"], {"kept_nodes": 67}),
                                  (2, ["A", "B", "C"], {"kept_nodes": 24})):
        host.write(tmp_path / f"requests/T.attempt-{index}.json", {"input_pack": {"inputs": {}}})
        host.write(tmp_path / f"errors/T.structure-{index}.json", {"errors": errors})
        host.write(tmp_path / f"responses/T.attempt-{index}.json",
                   {"choices": [{"message": {"content": json.dumps(output)}}]})

    @contextmanager
    def fake_response(request, timeout):
        body = json.loads(request.data)
        sent = json.loads(body["messages"][1]["content"])
        assert sent["prior_failed_output_for_local_correction"] == {"kept_nodes": 67}
        assert sent["structural_errors_to_correct"] == ["CURRENT_FIELD_ERROR"]
        class Response:
            def read(self):
                return json.dumps({"model": "test", "usage": {"total_tokens": 1},
                    "choices": [{"finish_reason": "stop", "message": {"content": '{"kept_nodes":67,"fixed":true}'}}]}).encode()
        yield Response()

    monkeypatch.setattr(host.urllib.request, "urlopen", fake_response)
    bound = host.call("P13C", "T", {}, max_tokens=100, preview=True)
    assert bound > 100
    assert not (tmp_path / "usage.jsonl").exists()
    assert not (tmp_path / "requests/T.attempt-3.json").exists()
    result = host.call("P13C", "T", {}, max_tokens=100)
    assert host.payload(result)["kept_nodes"] == 67
    assert host.read(tmp_path / "responses/T.attempt-1.json")["choices"][0]["message"]["content"] == '{"kept_nodes": 67}'


def test_export_rejects_missing_novel_without_creating_delivery(tmp_path, monkeypatch):
    host = real_host()
    host.WORK = tmp_path
    monkeypatch.setattr(host, "ROOT", tmp_path)
    with pytest.raises(RuntimeError, match="EXPORT_REQUIRES_FIVE_VALID_WRITERS"):
        host.export()
    assert not (tmp_path / "真实测试_五章_V160_20261105").exists()


def test_host_single_source_ownership_copies_without_api_or_source_changes(tmp_path):
    host = real_host()
    host.WORK = tmp_path
    _, records = ownership_source_fixture()
    records[0]["event"]["causal_links"] = [{"target_kind": "BASE_LINE", "target_ref": "N1"}]
    original = copy.deepcopy(records)
    result = host.single_source_ownership(1, records, [{"source_node_ref": "N1"}])
    assert result["event_units"][0]["canonical_event"] == original[0]["event"]
    assert result["required_state_nodes"] == ["N1"]
    assert records == original
    assert not (tmp_path / "requests").exists()
    receipt = host.read(tmp_path / "normalization_receipts/P14C0-CH1-HOST1.json")
    assert receipt["paid_api_call"] is False


def test_host_single_source_ownership_does_not_claim_omitted_nodes(tmp_path):
    host = real_host()
    host.WORK = tmp_path
    _, records = ownership_source_fixture()
    with pytest.raises(RuntimeError, match="SINGLE_SOURCE_EVENT_NODE_COVERAGE"):
        host.single_source_ownership(1, records, [{"source_node_ref": "N1"}])
    assert not (tmp_path / "validated").exists()


def test_active_event_package_prefers_immutable_settlement_revision(tmp_path):
    host = real_host()
    host.WORK = tmp_path
    assert host.active_event_path() == tmp_path / "events-r2.json"
    host.write(tmp_path / "events-r3.json", {"events": []})
    assert host.active_event_path() == tmp_path / "events-r3.json"


def test_settlement_cannot_change_completed_chapter_package(tmp_path):
    host = real_host()
    host.WORK = tmp_path
    host.write(tmp_path / "chapters-r2.json", {})
    with pytest.raises(RuntimeError, match="SETTLEMENT_REQUIRES_UNFROZEN_CHAPTER_PACKAGE"):
        host.settlement()
    assert not (tmp_path / "events-r3.json").exists()


def test_texture_condition_does_not_approve_future_setting_conditions():
    host = real_host()
    hook = {"decision": "CONDITIONAL_ACCEPT", "hook_type": "TEXTURE_HOOK",
            "payload": {"texture_only": True},
            "reveal_condition": {"condition_type": "TEXTURE", "texture_only": True}}
    assert host.texture_hook_conditions_met(hook) is True
    hook["reveal_condition"]["condition_type"] = "FUTURE_EVIDENCE"
    assert host.texture_hook_conditions_met(hook) is False
    hook["reveal_condition"]["condition_type"] = "TEXTURE"
    hook["blockers"] = [{"blocking": True}]
    assert host.texture_hook_conditions_met(hook) is False
