import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


C = module("canon_context")
P = module("preflight_task_call")
G = module("writer_dependency_gate")


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(value, indent=2) + "\n").encode()
    path.write_bytes(raw)
    return C.sha(raw)


def read(path):
    return json.loads(path.read_text())


def line(ident="D1", scope="hero", dimension="vow", start=1, end=None):
    return {"id": ident, "dimension": dimension, "scope": scope, "layer": "DERIVED",
            "valid_from": start, "valid_to": end,
            "nodes": [{"chapter": start, "seq": 0, "state": "keeps promise",
                       "change_reason": "approved event", "change_magnitude": 1,
                       "causal_status": "proposed"}],
            "cause": {"kind": "event", "ref": "E1", "note": "approved event"},
            "purpose": {"setup": {"ref": "B1", "note": "observable setup"}}}


def base_line():
    return {"id": "BASE1", "dimension": "goal", "entity_id": "hero", "entity_name": "Hero",
            "constraint_level": "hard", "importance": 5, "valid_from": 1,
            "description": "full approved trajectory", "layer": "BASE",
            "change_nodes": [
                {"chapter": 1, "seq": 0, "state": "waits", "change_reason": "opening",
                 "change_magnitude": 0, "causal_status": "established"},
                {"chapter": 3, "seq": 0, "state": "asks", "change_reason": "E1",
                 "change_magnitude": 1, "causal_status": "proposed"},
                {"chapter": 3, "seq": 1, "state": "refuses", "change_reason": "E2",
                 "change_magnitude": 1, "causal_status": "proposed"},
                {"chapter": 100, "seq": 0, "state": "resolves", "change_reason": "future plan",
                 "change_magnitude": 1, "causal_status": "proposed"}]}


def ack(ident="A1", target=3, related=None):
    return {"id": ident, "type": "reveal", "status": "pre-registered", "target_chapter": target,
            "related_lines": ["D1"] if related is None else related,
            "ack_ref": "B1", "obligations": ["Hero shows the token."], "scene_pressure": "deadline"}


def canon():
    return {"derived_lines": [line(), line("D2", "other", "token"),
                              line("D3", "other", "token"), line("FUTURE", start=9)],
            "ack_nodes": [ack()]}


def fixture(path, mode="generator"):
    def artifact(ident, value):
        data = {"artifact_id": ident, "project_id": "TEST", "version": "V2", "status": "FROZEN",
                "content": value}
        return {"artifact_id": ident, "version": "V2", "path": ident + ".json",
                "sha256": save(path / (ident + ".json"), data)}

    canon_binding = artifact("CANON", canon())
    base_binding = artifact("BASE", base_line())
    outline_binding = artifact("OUTLINE", {
        "chapter": 3, "event_records": [{"id": "E1", "chapter": 3}],
        "beats": [{"beat_id": "B1"}], "active_assets": [{"asset_id": "AS1"}]})
    manifest = {"project_id": "TEST", "chapter": 3, "version": "V2", "mode": mode,
                "canon": canon_binding, "base_lines": [base_binding], "outline": outline_binding,
                "entity_ids": ["hero"], "required_line_ids": [],
                "current_index": {"path": "index.json"}}
    index = {"project_id": "TEST", "artifacts": {
        row["artifact_id"]: {"version": row["version"], "sha256": row["sha256"], "status": "FROZEN"}
        for row in (canon_binding, base_binding, outline_binding)}}
    save(path / "index.json", index)
    save(path / "manifest.json", manifest)
    return path / "manifest.json"


def compile(path):
    return C.compile_manifest(path, C.sha(path.read_bytes()))


def inputs(context=None):
    return {"chapter": 3, "entity_id": "hero", "dimension": "vow", "line_id": "D1",
            "canon_context": context if context is not None else
            C.retrieve(canon(), [base_line()], 3, "generator", ["hero"])["model_context"],
            "chapter_outline": {"artifact_id": "OUTLINE", "status": "FROZEN",
                                "chapter": 3, "event_records": [{"id": "E1", "chapter": 3}],
                                "beats": [{"beat_id": "B1"}], "active_assets": [{"asset_id": "AS1"}]},
            "obligation_candidate": {"source_ref": "B1", "obligation": "Hero shows the token."}}


def derived_candidate():
    value = line("NEW", start=3)
    value["nodes"].append({**value["nodes"][0], "seq": 1, "state": "new commitment"})
    return value


def lifecycle():
    return {"line_id": "D1", "decision": "TERMINATE", "effective_chapter": 3,
            "effective_seq": 1, "successor_line": None,
            "cause_ref": "E1", "previous_status": "active", "new_status": "terminated",
            "dependency_refs": ["D1", "E1", "B1"], "reason": "approved event triggers termination"}


def test_generator_preserves_full_lines_base_nodes_and_isolation_boundary():
    data = canon()
    bases = [base_line()]
    original = copy.deepcopy((data, bases))
    result = C.retrieve(data, bases, 3, "generator", ["hero"])
    assert result["selected_line_ids"] == ["D1"]
    assert result["model_context"]["base_lines"] == bases
    assert len(result["model_context"]["base_lines"][0]["change_nodes"]) == 4
    assert result["model_context"]["derived_lines"][0]["nodes"][0]["causal_status"] == "proposed"
    assert result["knowledge_permission"] == "DESIGN_CONTEXT_ONLY_NOT_POV_KNOWLEDGE"
    assert (data, bases) == original


def test_relevance_selects_whole_scope_dimension_cluster():
    result = C.retrieve(canon(), [], 3, "generator", required_line_ids=["D2"])
    assert {"D2", "D3"}.issubset(result["selected_line_ids"])


def test_due_and_overdue_acks_never_disappear():
    data = canon()
    data["ack_nodes"] += [ack("OLD", 2), ack("LATER", 8), {**ack("DONE"), "status": "executed"},
                         {**ack("CANCELLED"), "status": "cancelled"}]
    result = C.retrieve(data, [], 3, "writer", budget_lines=1)
    assert result["pending_ack_ids"] == ["A1", "OLD"]
    assert result["overdue_ack_ids"] == ["OLD"]
    assert result["selected_line_ids"] == ["D1"]
    assert result["decision"] != "BLOCKED"


def test_base_line_ack_references_are_retained():
    data = {"derived_lines": [], "ack_nodes": [ack(related=["BASE1"])]}
    result = C.retrieve(data, [base_line()], 3, "writer", budget_lines=1)
    assert result["model_context"]["base_lines"] == [base_line()]
    assert result["pending_ack_ids"] == ["A1"]


def test_shared_ack_line_is_counted_and_injected_once():
    data = canon()
    data["ack_nodes"].append(ack("A2"))
    result = C.retrieve(data, [], 3, "writer", ["hero"], budget_lines=1)
    assert result["selected_line_ids"] == ["D1"]
    assert len(result["model_context"]["ack_nodes"]) == 2
    assert result["decision"] == "CANON_CONTEXT_COMPILED"


def test_required_clusters_over_budget_block_without_loss():
    data = canon()
    data["derived_lines"].append(line("D4"))
    result = C.retrieve(data, [], 3, "writer", ["hero"], budget_lines=1)
    assert result["decision"] == "BLOCKED"
    assert {"D1", "D4"}.issubset(result["selected_line_ids"])
    assert result["pending_ack_ids"] == ["A1"]


def test_optional_clusters_trim_whole_and_source_stays_full():
    data = canon()
    result = C.retrieve(data, [], 3, "writer", ["hero"], budget_lines=2)
    assert result["selected_line_ids"] == ["D1"]
    assert result["trimmed_clusters"][0]["line_ids"] == ["D2", "D3"]
    assert len(data["derived_lines"]) == 4


def test_valid_to_is_half_open_with_explicit_termination_chapter():
    data = {"derived_lines": [line(end=3)], "ack_nodes": []}
    assert C.retrieve(data, [], 3, "writer")["selected_line_ids"] == ["D1"]
    assert not C.retrieve(data, [], 4, "writer")["selected_line_ids"]
    data["ack_nodes"] = [ack(target=3)]
    result = C.retrieve(data, [], 4, "writer")
    assert result["selected_line_ids"] == ["D1"]
    assert result["overdue_ack_ids"] == ["A1"]


def test_audit_reads_full_history_and_future_without_truncation():
    data = canon()
    result = C.retrieve(data, [base_line()], 3, "audit", budget_lines=1)
    assert result["model_context"]["derived_lines"] == data["derived_lines"]
    assert result["decision"] == "BLOCKED"
    assert not result["trimmed_clusters"]


@pytest.mark.parametrize("mode", ["required", "ack"])
def test_future_line_cannot_become_current_fact(mode):
    data = canon()
    required = ["FUTURE"] if mode == "required" else []
    if mode == "ack":
        data["ack_nodes"][0]["related_lines"] = ["FUTURE"]
    with pytest.raises(ValueError, match="FUTURE_REQUIRED_LINE|ACK_REFERENCES_UNBORN_LINE"):
        C.retrieve(data, [], 3, "writer", required_line_ids=required)


@pytest.mark.parametrize("mode", ["duplicate_line", "duplicate_ack", "missing_line", "no_obligation",
                                 "no_ack_status", "node_order", "interval", "no_purpose", "cause"])
def test_invalid_canon_never_silently_drops_records(mode):
    data = canon()
    if mode == "duplicate_line":
        data["derived_lines"].append(line())
    elif mode == "duplicate_ack":
        data["ack_nodes"].append(ack())
    elif mode == "missing_line":
        data["ack_nodes"][0]["related_lines"] = ["GHOST"]
    elif mode == "no_obligation":
        data["ack_nodes"][0]["obligations"] = []
    elif mode == "no_ack_status":
        data["ack_nodes"][0].pop("status")
    elif mode == "node_order":
        data["derived_lines"][0]["nodes"] *= 2
    elif mode == "interval":
        data["derived_lines"][0]["valid_to"] = 1
    elif mode == "no_purpose":
        data["derived_lines"][0]["purpose"] = {"unexpected": {"ref": "B1"}}
    else:
        data["derived_lines"][0]["cause"]["kind"] = "user_input"
    with pytest.raises(ValueError, match="CANON_"):
        C.retrieve(data, [], 3, "generator")


@pytest.mark.parametrize("budget", [True, 0, -1, "20"])
def test_budget_contract_rejects_invalid_values(budget):
    with pytest.raises(ValueError, match="BUDGET_INVALID"):
        C.retrieve(canon(), [], 3, "writer", budget_lines=budget)


def test_compiler_verifies_frozen_sources_and_current_index_read_only(tmp_path, monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: pytest.fail("NO_API"))
    path = fixture(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.glob("*.json")}
    result = compile(path)
    assert result["source_artifact_ids"] == ["CANON", "BASE", "OUTLINE"]
    assert all(p.read_bytes() == raw for p, raw in before.items())


@pytest.mark.parametrize("mode", ["hash", "version", "status", "removed"])
def test_current_index_changes_block_immutable_canon(tmp_path, mode):
    path = fixture(tmp_path)
    source_before = (tmp_path / "CANON.json").read_bytes()
    index = read(tmp_path / "index.json")
    if mode == "removed":
        index["artifacts"].pop("CANON")
    else:
        index["artifacts"]["CANON"][mode if mode != "hash" else "sha256"] = "changed"
    save(tmp_path / "index.json", index)
    with pytest.raises(ValueError, match="CURRENT_INDEX_STALE"):
        compile(path)
    assert (tmp_path / "CANON.json").read_bytes() == source_before


@pytest.mark.parametrize("mode", ["source_hash", "draft", "id", "scope", "escape"])
def test_source_bindings_fail_closed(tmp_path, mode):
    path = fixture(tmp_path)
    manifest = read(path)
    value = read(tmp_path / "CANON.json")
    if mode == "escape":
        manifest["canon"]["path"] = "../outside.json"
    elif mode == "source_hash":
        save(tmp_path / "CANON.json", {"changed": True})
    else:
        value[{"draft": "status", "id": "artifact_id", "scope": "project_id"}[mode]] = "changed"
        manifest["canon"]["sha256"] = save(tmp_path / "CANON.json", value)
    save(path, manifest)
    with pytest.raises(ValueError, match="CANON_|DEPENDENCY_"):
        compile(path)


def test_p16a_valid_new_current_line_and_no_derived_terminal():
    x = inputs()
    assert C.validate_p16("P16A", derived_candidate(), x) == []
    assert C.validate_p16("P16A", {"decision": "NO_DERIVED"}, x) == []
    assert C.validate_p16("P16A", {"decision": "NO_DERIVED", "nodes": [{}]}, x)


@pytest.mark.parametrize("field,value,error", [
    ("scope", "other", "TARGET_MISMATCH"), ("dimension", "token", "TARGET_MISMATCH"),
    ("id", "D2", "ALREADY_REGISTERED"), ("valid_from", 2, "OPENING_NODE_MISSING")])
def test_p16a_wrong_identity_or_registered_id_blocks(field, value, error):
    candidate = derived_candidate()
    candidate[field] = value
    assert any(error in e for e in C.validate_p16("P16A", candidate, inputs()))


@pytest.mark.parametrize("kind,ref,passes", [
    ("event", "E1", True), ("outline_beat", "B1", True), ("asset", "AS1", True),
    ("line", "BASE1", True), ("event", "GHOST", False), ("user_input", "USER", False)])
def test_p16a_cause_is_grounded_in_actual_approved_refs(kind, ref, passes):
    candidate = derived_candidate()
    candidate["cause"].update({"kind": kind, "ref": ref})
    assert (not C.validate_p16("P16A", candidate, inputs())) == passes


def test_p16a_future_nodes_or_established_plan_are_rejected():
    candidate = derived_candidate()
    candidate["nodes"][1].update({"chapter": 4, "causal_status": "established"})
    errors = C.validate_p16("P16A", candidate, inputs())
    assert "DERIVED_NOT_CURRENT_CHAPTER" in errors
    assert "DERIVED_PLAN_NOT_PROPOSED" in errors


def test_p16a_unknown_future_purpose_is_not_approved():
    candidate = derived_candidate()
    candidate["purpose"]["payoff"] = {"ref": "FUTURE-BEAT", "note": "unapproved"}
    assert "DERIVED_PURPOSE_UNREGISTERED_REF" in C.validate_p16("P16A", candidate, inputs())


def test_p16b_valid_single_approved_obligation():
    assert not C.validate_p16("P16B", ack("NEW"), inputs())


@pytest.mark.parametrize("mode,error", [
    ("executed", "CANNOT_EXECUTE_OR_CANCEL"), ("cancelled", "CANNOT_EXECUTE_OR_CANCEL"),
    ("multiple", "ONE_OBLIGATION"), ("duplicate", "ALREADY_REGISTERED"),
    ("future", "APPROVED_TARGET_OUTLINE"), ("bad_ref", "NOT_IN_APPROVED_PLAN"),
    ("unknown_line", "UNREGISTERED_LINE")])
def test_p16b_invalid_registration_cannot_enter_canon(mode, error):
    candidate = ack("NEW")
    if mode in ("executed", "cancelled"):
        candidate["status"] = mode
    elif mode == "multiple":
        candidate["obligations"].append("Other obligation")
    elif mode == "duplicate":
        candidate["id"] = "A1"
    elif mode == "future":
        candidate["target_chapter"] = 4
    elif mode == "bad_ref":
        candidate["ack_ref"] = "GHOST"
    else:
        candidate["related_lines"] = ["GHOST"]
    assert any(error in e for e in C.validate_p16("P16B", candidate, inputs()))


def test_p16c_checks_refs_without_changing_the_line():
    x = inputs()
    before = copy.deepcopy(x)
    assert C.validate_p16("P16C", lifecycle(), x) == []
    assert x == before


@pytest.mark.parametrize("field,value", [("line_id", "GHOST"), ("cause_ref", "GHOST"),
                                        ("effective_chapter", True), ("dependency_refs", ["GHOST"])])
def test_p16c_bad_lifecycle_evidence_blocks(field, value):
    candidate = lifecycle()
    candidate[field] = value
    assert C.validate_p16("P16C", candidate, inputs())


@pytest.mark.parametrize("task", ["P16A", "P16B", "P16C"])
def test_p16_preflight_requires_actual_frozen_context(task):
    x = inputs()
    assert P.evaluate(task, {"inputs": x})["decision"] == "NEEDS_HOST_CHECK"
    x["chapter_outline"]["status"] = "DRAFT"
    assert P.evaluate(task, {"inputs": x})["decision"] == "BLOCKED"


@pytest.mark.parametrize("task,field", [("P16A", "derived_state_required"),
                                       ("P16B", "obligation_required"),
                                       ("P16C", "lifecycle_change_required")])
def test_explicit_no_need_does_not_trigger_empty_model_calls(task, field):
    assert P.evaluate(task, {"inputs": {field: False}})["decision"] == "NOT_APPLICABLE"


def test_runtime_canon_projection_cannot_be_substituted(tmp_path):
    path = fixture(tmp_path)
    receipt = compile(path)
    x = inputs(receipt["model_context"])
    x["chapter_outline"] = read(tmp_path / "OUTLINE.json")
    pack = {"project_id": "TEST", "version": "V2", "inputs": x,
            "canon_context_gate": {"path": str(path), "sha256": C.sha(path.read_bytes())}}
    assert P.evaluate("P16A", pack, require_canon_context=True)["decision"] == "NEEDS_HOST_CHECK"
    x["canon_context"]["derived_lines"][0]["nodes"][0]["state"] = "changed"
    assert P.evaluate("P16A", pack, require_canon_context=True)["decision"] == "BLOCKED"


def test_runtime_outline_cannot_invent_frozen_events(tmp_path):
    path = fixture(tmp_path)
    receipt = compile(path)
    x = inputs(receipt["model_context"])
    x["chapter_outline"] = read(tmp_path / "OUTLINE.json")
    pack = {"project_id": "TEST", "version": "V2", "inputs": x,
            "canon_context_gate": {"path": str(path), "sha256": C.sha(path.read_bytes())}}
    x["chapter_outline"]["content"]["event_records"].append({"id": "INVENTED", "chapter": 3})
    result = P.evaluate("P16A", pack, require_canon_context=True)
    assert result["decision"] == "BLOCKED"
    assert result["evidence"]["canon_context_error"] == "P16_OUTLINE_RUNTIME_SUBSTITUTION"


def test_context_read_race_cannot_create_a_fresh_snapshot(tmp_path, monkeypatch):
    path = fixture(tmp_path)
    original = C.retrieve

    def race(*args, **kwargs):
        result = original(*args, **kwargs)
        save(tmp_path / "CANON.json", {"changed_after_read": True})
        return result

    monkeypatch.setattr(C, "retrieve", race)
    with pytest.raises(ValueError, match="SOURCE_CHANGED_DURING_CHECK"):
        compile(path)


def test_p16b_cannot_change_the_selected_obligation():
    candidate = ack("NEW")
    candidate["obligations"] = ["A different action."]
    assert "ACK_SELECTED_OBLIGATION_MISMATCH" in C.validate_p16("P16B", candidate, inputs())


def test_placeholder_beats_cannot_serve_as_p16_provenance():
    x = inputs()
    x["chapter_outline"]["beats"][0]["beat_id"] = "BEAT-PLACEHOLDER-01"
    assert "P16_STABLE_PLAN_IDS_REQUIRED" in C.validate_p16("P16A", derived_candidate(), x)


def test_finalizer_accepts_pre_p16_frozen_plan_without_mutating_it(tmp_path):
    snapshot = {**inputs()["chapter_outline"], "project_id": "TEST", "version": "PLAN-V1"}
    snapshot["beats"][0]["beat_id"] = "BEAT-TEST-CH03-01"
    before = save(tmp_path / "plan-snapshot.json", snapshot)
    manifest = {"project_id": "TEST", "chapter": 3, "version": "FINAL-V2",
                "dependency_manifest_complete": True, "blocking_findings": [],
                "artifacts": [{"role": "outline", "path": "plan-snapshot.json",
                               "required_status": "FROZEN"}], "output_dir": "new-final"}
    save(tmp_path / "freeze.json", manifest)
    result = subprocess.run([sys.executable, str(ROOT / "tools/finalize_chapter_plan.py"),
                             "--manifest", str(tmp_path / "freeze.json")],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    final = read(tmp_path / "new-final/outline.FROZEN.json")
    assert final["project_id"] == "TEST" and final["version"] == "FINAL-V2"
    assert final["beats"][0]["beat_id"] == snapshot["beats"][0]["beat_id"]
    assert C.sha((tmp_path / "plan-snapshot.json").read_bytes()) == before


@pytest.mark.parametrize("task", ["P16A", "P16B", "P16C"])
def test_output_validator_cli_uses_bound_sources_and_does_not_approve(tmp_path, task):
    path = fixture(tmp_path)
    receipt = compile(path)
    x = inputs(receipt["model_context"])
    x["chapter_outline"] = read(tmp_path / "OUTLINE.json")
    pack = {"project_id": "TEST", "version": "V2", "inputs": x,
            "canon_context_gate": {"path": str(path), "sha256": C.sha(path.read_bytes())}}
    pack_hash = save(tmp_path / "pack.json", pack)
    value = {"P16A": derived_candidate(), "P16B": ack("NEW"), "P16C": lifecycle()}[task]
    save(tmp_path / "candidate.json", {"task_id": task, "status": "DRAFT", "content": value})
    before = (tmp_path / "candidate.json").read_bytes()
    output = tmp_path / "validation.json"
    result = subprocess.run([sys.executable, str(ROOT / "tools/canon_context.py"),
                             "--input-pack", str(tmp_path / "pack.json"), "--sha256", pack_hash,
                             "--candidate", str(tmp_path / "candidate.json"), "--task-id", task,
                             "--out", str(output)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert read(output)["activation"] == "NOT_GRANTED"
    assert read(output)["decision"] == "P16_STRUCTURE_VALIDATED"
    assert (tmp_path / "candidate.json").read_bytes() == before


def test_p17c_only_consumes_canon_summary_not_full_context(tmp_path):
    path = fixture(tmp_path, "writer")
    receipt = compile(path)
    pack = {"project_id": "TEST", "version": "V2",
            "inputs": {"chapter": 3, "canon_context_summary": {
                "context_object_sha256": receipt["context_object_sha256"],
                "source_hashes": receipt["source_hashes"]}},
            "canon_context_gate": {"path": str(path), "sha256": C.sha(path.read_bytes())}}
    assert P.evaluate("P17C", pack)["decision"] == "NEEDS_HOST_CHECK"
    pack["inputs"]["canon_context"] = receipt["model_context"]
    assert P.evaluate("P17C", pack)["decision"] == "BLOCKED"


def fulfillment():
    record = ack()
    prose = "Hero shows the token. The witness nods."
    review = {"decision": "PASS", "status": "VALIDATED", "chapter": 3,
              "blocking_findings": [], "ack_object_sha256": C.object_sha(record),
              "text_sha256": C.sha(prose.encode()), "obligation_evidence": [
                  {"obligation": record["obligations"][0], "decision": "PASS",
                   "start": 0, "end": 21, "quote": prose[:21], "basis": "direct observable action"}]}
    ship = {"decision": "SHIPPED", "chapter": 3, "text_sha256": C.sha(prose.encode()),
            "ack_review_object_sha256": C.object_sha(review)}
    return record, prose, review, ship


def test_ack_fulfillment_is_source_bound_and_does_not_execute_it():
    record, prose, review, ship = fulfillment()
    before = copy.deepcopy(record)
    assert C.validate_ack_fulfillment(record, prose, review, ship) == []
    assert record == before and record["status"] == "pre-registered"


@pytest.mark.parametrize("mode", ["not_shipped", "new_text", "new_ack", "old_review",
                                 "no_evidence", "bad_quote", "wrong_offset", "blockers"])
def test_ack_execution_requires_current_ship_and_actual_obligation_evidence(mode):
    record, prose, review, ship = fulfillment()
    if mode == "not_shipped":
        ship["decision"] = "DRAFT"
    elif mode == "new_text":
        prose += " Changed."
    elif mode == "new_ack":
        record["obligations"][0] = "A different action."
    elif mode == "old_review":
        review["status"] = "DRAFT"
    elif mode == "no_evidence":
        review["obligation_evidence"] = []
    elif mode == "bad_quote":
        review["obligation_evidence"][0]["quote"] = "not in prose"
    elif mode == "wrong_offset":
        review["obligation_evidence"][0]["start"] = True
    else:
        review["blocking_findings"] = ["unresolved"]
    assert C.validate_ack_fulfillment(record, prose, review, ship)


def test_cli_uses_no_api_and_refuses_overwrite(tmp_path):
    path = fixture(tmp_path)
    output = tmp_path / "context.json"
    command = [sys.executable, str(ROOT / "tools/canon_context.py"), "--manifest", str(path),
               "--sha256", C.sha(path.read_bytes()), "--out", str(output)]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    before = output.read_bytes()
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode != 0 and output.read_bytes() == before


def test_host_cli_canon_policy_cannot_be_disabled_by_pack(tmp_path):
    path = tmp_path / "pack.json"
    save(path, {"task_id": "P17C", "requires_canon_context": False, "inputs": {"chapter": 3}})
    result = subprocess.run([sys.executable, str(ROOT / "tools/preflight_task_call.py"),
                             "--input-pack", str(path), "--require-canon-context"],
                            capture_output=True, text=True)
    assert result.returncode == 1
    assert "CANON_CONTEXT_GATE_REQUIRED" in result.stdout


def test_writer_canon_sources_must_be_in_p17c_checked_dependency_closure(tmp_path):
    path = fixture(tmp_path, "writer")
    receipt = compile(path)
    frozen_id = "FROZEN"
    frozen_data = {"artifact_id": frozen_id, "version": "V2", "status": "FROZEN"}
    frozen_hash = save(tmp_path / "frozen.json", frozen_data)
    freeze = {"project_id": "TEST", "version": "V2", "chapter": 3, "status": "FROZEN",
              "artifacts": [{"artifact_id": frozen_id, "sha256": frozen_hash, "frozen_path": "frozen.json"}]}
    freeze_hash = save(tmp_path / "freeze.json", freeze)
    sources = read(path)["base_lines"] + [read(path)["canon"], read(path)["outline"]]
    nodes = [{**row, "depends_on": []} for row in sources]
    nodes.append({"artifact_id": frozen_id, "version": "V2", "path": "frozen.json",
                  "sha256": frozen_hash, "depends_on": [row["artifact_id"] for row in sources]})
    graph = {"project_id": "TEST", "version": "V2", "chapter": 3, "complete": True,
             "nodes": nodes, "root_ids": [frozen_id], "current_index": {"path": "index.json"},
             "frozen": {"path": "freeze.json", "sha256": freeze_hash}}
    p17c = {"artifact_id": "P17C", "task_id": "P17C", "version": "V2", "chapter": 3,
            "project_id": "TEST", "status": "VALIDATED", "decision": "READY",
            "missing_refs": [], "version_mismatches": [],
            "checked_dependency_subject_sha256": G.subject(graph),
            "checked_frozen_manifest_sha256": freeze_hash}
    p17c_hash = save(tmp_path / "p17c.json", p17c)
    graph["p17c"] = {"path": "p17c.json", "sha256": p17c_hash}
    graph_hash = save(tmp_path / "graph.json", graph)
    index = read(tmp_path / "index.json")
    index["artifacts"][frozen_id] = {"version": "V2", "sha256": frozen_hash, "status": "FROZEN"}
    index["artifacts"]["P17C"] = {"version": "V2", "sha256": p17c_hash, "status": "VALIDATED"}
    save(tmp_path / "index.json", index)
    pack = {"project_id": "TEST", "version": "V2", "frozen_manifest_sha256": freeze_hash,
            "inputs": {"chapter": 3, "canon_context": receipt["model_context"], "p17c_preflight": p17c},
            "canon_context_gate": {"path": str(path), "sha256": C.sha(path.read_bytes())},
            "writer_dependency_gate": {"path": str(tmp_path / "graph.json"), "sha256": graph_hash}}
    assert P.evaluate("P18", pack, True, True)["decision"] == "NEEDS_HOST_CHECK"
    graph["nodes"][-1]["depends_on"] = []
    p17c["checked_dependency_subject_sha256"] = G.subject(graph)
    graph["p17c"]["sha256"] = save(tmp_path / "p17c.json", p17c)
    index["artifacts"]["P17C"]["sha256"] = graph["p17c"]["sha256"]
    save(tmp_path / "index.json", index)
    pack["writer_dependency_gate"]["sha256"] = save(tmp_path / "graph.json", graph)
    result = P.evaluate("P18", pack, True, True)
    assert result["decision"] == "BLOCKED"
    assert result["evidence"]["canon_context_error"] == "CANON_SOURCES_NOT_IN_WRITER_DEPENDENCY_CLOSURE"
