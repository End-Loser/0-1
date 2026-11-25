import copy
import importlib.util
import json
from contextlib import contextmanager
from pathlib import Path

import pytest
from jsonschema.exceptions import SchemaError

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


OPT = load("call_optimization")


def test_projection_roundtrip_preserves_all_lines_and_duplicate_events():
    event = {"id": "E1", "actions": ["approved action " * 500], "unknown": None}
    source = {"writer_input_pack": {
        "ChapterOutline": {"event_records": [event]},
        "approved_events": [copy.deepcopy(event)],
        "complete_frozen_base_lines": [{"change_nodes": list(range(67))}],
        "future_plan": {"chapter": 2000, "proposed": True}}}
    original = copy.deepcopy(source)
    projected, manifest = OPT.project_inputs(source)
    assert manifest and manifest["lossless_roundtrip"]
    assert projected["writer_input_pack"]["approved_events"][OPT.REF] == (
        "/writer_input_pack/ChapterOutline/event_records")
    assert OPT.expand_inputs(projected) == original
    assert source == original
    assert len(OPT.canonical(projected)) + len(OPT.canonical(manifest)) < len(OPT.canonical(source))


def test_projection_handles_nested_references_and_escaped_pointers():
    leaf = {"facts": ["x" * 6000]}
    branch = {"leaf": copy.deepcopy(leaf), "future": "y" * 6000}
    source = {"~/": leaf, "branch": branch, "again": copy.deepcopy(branch)}
    projected, manifest = OPT.project_inputs(source)
    assert manifest
    assert projected["branch"]["leaf"][OPT.REF] == "/~0~1"
    assert OPT.expand_inputs(projected) == source


def test_projection_does_not_merge_same_id_with_different_facts():
    source = {"a": {"id": "E1", "facts": "x" * 6000},
              "b": {"id": "E1", "facts": "y" * 6000}}
    projected, manifest = OPT.project_inputs(source)
    assert projected == source and manifest is None


def test_projection_small_duplicates_do_not_grow_request():
    source = {"a": {"fact": 1}, "b": {"fact": 1}}
    assert OPT.project_inputs(source) == (source, None)


def test_projection_rejects_ambiguous_reserved_key():
    with pytest.raises(ValueError, match="RESERVED_INPUT_REFERENCE_KEY"):
        OPT.project_inputs({"data": {OPT.REF: "user supplied"}})


def test_reference_hash_and_cycle_are_checked():
    with pytest.raises(ValueError, match="INPUT_REFERENCE_HASH"):
        OPT.expand_inputs({"a": [1], "b": {OPT.REF: "/a", "sha256": "wrong"}})
    with pytest.raises(ValueError, match="INPUT_REFERENCE_CYCLE"):
        OPT.expand_inputs({"a": {OPT.REF: "/b", "sha256": "wrong"},
                           "b": {OPT.REF: "/a", "sha256": "wrong"}})


def test_length_distance_improvement_is_not_stalled():
    old = {"text": "x" * 1700}
    new = {"text": "x" * 1750}
    errors = ["WRITER_CHARACTER_RANGE:1800-2400", "WRITER_ACTUAL_CHARACTERS:1700:TARGET_DELTA:400"]
    new_errors = ["WRITER_CHARACTER_RANGE:1800-2400", "WRITER_ACTUAL_CHARACTERS:1750:TARGET_DELTA:350"]
    assert not OPT.stalled(old, errors, new, new_errors)
    assert OPT.stalled(new, new_errors, new, new_errors)


def test_changed_error_or_success_is_not_same_error_stall():
    assert not OPT.stalled({"a": 1}, ["MISSING:a"], {"b": 1}, ["MISSING:b"])
    assert not OPT.stalled({"a": 1}, ["MISSING:a"], {"a": 1}, [])


def host_setup(tmp_path, monkeypatch):
    host = load("real_novel_test")
    host.WORK, host.BUDGET = tmp_path, 1000000
    host.ROWS = {"P13C": {
        "prompt_file": "prompts/P13C_EVENT_ACTOR_DECISION_PLAN.md",
        "output_contract_file": "schemas/output_contracts/P13C.json"}}
    monkeypatch.setattr(host.PREFLIGHT, "evaluate", lambda *args: {"decision": "NEEDS_HOST_CHECK"})
    monkeypatch.setattr(host, "validate_schema", lambda *args: [])
    monkeypatch.setattr(host, "config", lambda: {
        "LLM_PRIMARY_MODEL": "test", "LLM_BASE_URL": "https://example.invalid", "LLM_API_KEY": "test-only"})
    return host


def fake_api(host, monkeypatch, outputs, captured):
    iterator = iter(outputs)

    @contextmanager
    def response(request, timeout):
        captured.append(json.loads(request.data))
        output = next(iterator)

        class Response:
            def read(self):
                return json.dumps({"usage": {"total_tokens": 1}, "choices": [
                    {"finish_reason": "stop", "message": {"content": json.dumps(output)}}]}).encode()
        yield Response()

    monkeypatch.setattr(host.urllib.request, "urlopen", response)


def test_stall_persists_across_resume_and_preview_without_extra_call(tmp_path, monkeypatch):
    host = host_setup(tmp_path, monkeypatch)
    captured = []
    fake_api(host, monkeypatch, [{"missing": True}, {"missing": True}], captured)
    check = lambda data: ["MISSING:action"]
    with pytest.raises(RuntimeError, match="RETRY_STALLED"):
        host.call("P13C", "T", {}, check=check)
    assert len(captured) == 2
    assert host.RESERVED == host.INFLIGHT_CALLS == 0
    assert not (tmp_path / "validated/T.json").exists()
    assert len(list((tmp_path / "blocked").glob("T.retry-*.json"))) == 1
    for preview in (False, True):
        with pytest.raises(RuntimeError, match="RETRY_STALLED"):
            host.call("P13C", "T", {}, check=check, preview=preview)
    assert len(captured) == 2
    # An explicit corrected contract/instruction gets its own retry policy identity.
    fake_api(host, monkeypatch, [{"fixed": True}], captured)
    host.call("P13C", "T", {}, notes="Corrected action contract",
              check=lambda data: [] if data.get("fixed") else ["MISSING:action"])
    assert len(captured) == 3


@pytest.mark.parametrize("changed", [
    {"notes": "changed scope"}, {"max_tokens": 100}, {"thinking": True},
])
def test_cached_contract_change_requires_new_version(tmp_path, monkeypatch, changed):
    host = host_setup(tmp_path, monkeypatch)
    captured = []
    fake_api(host, monkeypatch, [{"fixed": True}], captured)
    host.call("P13C", "T", {})
    assert host.call("P13C", "T", {}, preview=True) == 0
    with pytest.raises(RuntimeError, match="CACHED_CONTRACT_CHANGED"):
        host.call("P13C", "T", {}, **changed)
    assert len(captured) == 1


def test_old_cache_without_contract_identity_is_not_silently_trusted(tmp_path, monkeypatch):
    host = host_setup(tmp_path, monkeypatch)
    host.write(tmp_path / "validated/T.json", {"content": {"fixed": True}})
    host.write(tmp_path / "requests/T.attempt-1.json", {"input_pack": {"inputs": {}}})
    with pytest.raises(RuntimeError, match="CACHED_CONTRACT_CHANGED"):
        host.call("P13C", "T", {}, preview=True)
    assert not (tmp_path / "usage.jsonl").exists()


def test_changed_resume_inputs_rejected_before_api(tmp_path, monkeypatch):
    host = host_setup(tmp_path, monkeypatch)
    host.write(tmp_path / "requests/T.attempt-1.json", {"input_pack": {"inputs": {"old": True}}})
    with pytest.raises(RuntimeError, match="RESUME_INPUT_CHANGED"):
        host.call("P13C", "T", {"new": True})
    assert not (tmp_path / "usage.jsonl").exists()


def test_incomplete_resume_evidence_rejected_before_api(tmp_path, monkeypatch):
    host = host_setup(tmp_path, monkeypatch)
    host.write(tmp_path / "requests/T.attempt-1.json", {})
    with pytest.raises(RuntimeError, match="RESUME_INPUT_EVIDENCE_MISSING"):
        host.call("P13C", "T", {})
    assert not (tmp_path / "usage.jsonl").exists()


def test_json_types_not_conflated_when_reusing_inputs(tmp_path, monkeypatch):
    host = host_setup(tmp_path, monkeypatch)
    host.write(tmp_path / "requests/T.attempt-1.json", {"input_pack": {"inputs": {"flag": True}}})
    with pytest.raises(RuntimeError, match="RESUME_INPUT_CHANGED"):
        host.call("P13C", "T", {"flag": 1})


def test_four_attempt_limit_survives_resume_when_errors_change(tmp_path, monkeypatch):
    host = host_setup(tmp_path, monkeypatch)
    captured = []
    fake_api(host, monkeypatch, [{"step": n} for n in range(1, 5)], captured)
    check = lambda data: ["MISSING:field" + str(data["step"])]
    for _ in range(2):
        with pytest.raises(RuntimeError, match="STRUCTURE_BLOCKED"):
            host.call("P13C", "T", {}, check=check)
    for preview in (False, True):
        with pytest.raises(RuntimeError, match="EXECUTION_ATTEMPT_LIMIT"):
            host.call("P13C", "T", {}, check=check, preview=preview)
    assert len(captured) == 4
    assert host.RESERVED == host.INFLIGHT_CALLS == 0


def test_single_attempt_policy_never_automatically_retries(tmp_path, monkeypatch):
    host = host_setup(tmp_path, monkeypatch)
    captured = []
    fake_api(host, monkeypatch, [{"missing": True}], captured)
    with pytest.raises(RuntimeError, match="STRUCTURE_BLOCKED"):
        host.call("P13C", "T", {}, check=lambda data: ["MISSING:action"], max_attempts=1)
    assert len(captured) == 1
    assert host.RESERVED == host.INFLIGHT_CALLS == 0


@pytest.mark.parametrize("limit", [0, 3, True, "1"])
def test_invalid_single_call_attempt_limit_is_rejected_before_request(tmp_path, monkeypatch, limit):
    host = host_setup(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="INVALID_PER_INVOCATION_ATTEMPT_LIMIT"):
        host.call("P13C", "T", {}, max_attempts=limit)
    assert not (tmp_path / "requests").exists()


def test_changing_contract_does_not_reset_same_artifact_four_attempt_cap(tmp_path, monkeypatch):
    host = host_setup(tmp_path, monkeypatch)
    captured = []
    fake_api(host, monkeypatch, [{"step": n} for n in range(4)], captured)
    for n in range(4):
        with pytest.raises(RuntimeError, match="STRUCTURE_BLOCKED"):
            host.call("P13C", "T", {}, notes=f"Corrected contract {n}", max_attempts=1,
                      check=lambda data: ["MISSING:" + str(data["step"])])
    with pytest.raises(RuntimeError, match="EXECUTION_ATTEMPT_LIMIT"):
        host.call("P13C", "T", {}, notes="Another contract", max_attempts=1)
    assert len(captured) == 4


def test_invalid_request_schema_is_blocked_before_api(tmp_path, monkeypatch):
    host = host_setup(tmp_path, monkeypatch)
    original_read = host.read

    schema = original_read(ROOT / host.ROWS["P13C"]["output_contract_file"])["payload_schema"]
    schema_name = Path(schema).name

    def actual_invalid(path):
        data = original_read(path)
        if Path(path).name == schema_name:
            data["required"] = "not-an-array"
        return data

    monkeypatch.setattr(host, "read", actual_invalid)
    with pytest.raises(SchemaError):
        host.call("P13C", "T", {})
    assert not (tmp_path / "requests").exists()


def test_broken_schema_pointer_is_blocked_before_api(tmp_path, monkeypatch):
    host = host_setup(tmp_path, monkeypatch)
    original_read = host.read
    schema = original_read(ROOT / host.ROWS["P13C"]["output_contract_file"])["payload_schema"]

    def broken_reference(path):
        data = original_read(path)
        if Path(path).name == Path(schema).name:
            data["$ref"] = "#/$defs/MISSING_DEFINITION"
        return data

    monkeypatch.setattr(host, "read", broken_reference)
    with pytest.raises(RuntimeError, match="UNRESOLVED_SCHEMA_REFERENCE"):
        host.call("P13C", "T", {})
    assert not (tmp_path / "requests").exists()


def test_p15a_effective_request_schema_is_also_validation_schema(tmp_path, monkeypatch):
    host = load("real_novel_test")
    source = ROOT / "schemas/CHAPTER_OUTLINE_SCHEMA.json"
    schema = host.read(source)
    schema["required"] = ["chapter", "event_records"]
    assert host.validate_schema({"chapter": 1}, {"path": "schemas/CHAPTER_OUTLINE_SCHEMA.json",
                                               "schema": schema})
    assert host.validate_schema({"chapter": 1}, "schemas/CHAPTER_OUTLINE_SCHEMA.json") == []


def test_writer_wire_is_lossless_and_source_evidence_remains_full(tmp_path, monkeypatch):
    host = host_setup(tmp_path, monkeypatch)
    import csv
    with (ROOT / "TASK_REGISTRY.csv").open(encoding="utf-8-sig") as stream:
        host.ROWS["P18"] = next(row for row in csv.DictReader(stream) if row["task_id"] == "P18")
    event = {"id": "E1", "actions": ["approved " * 1000]}
    inputs = {"writer_input_pack": {"ChapterOutline": {"event_records": [event]},
                                   "approved_events": [copy.deepcopy(event)]}}
    captured = []
    fake_api(host, monkeypatch, [{"text": "test stub"}], captured)
    host.call("P18", "T", inputs)
    wire = json.loads(captured[0]["messages"][1]["content"])
    assert wire["input_projection"]["lossless_roundtrip"]
    assert OPT.expand_inputs(wire["inputs"]) == inputs
    request = host.read(tmp_path / "requests/T.attempt-1.json")
    assert request["input_pack"]["inputs"] == inputs
    assert request["execution_fingerprint"]
    assert host.call("P18", "T", inputs, preview=True) == 0
    row = host.usage()[0]
    assert row["source_input_bytes"] > row["model_input_bytes"]
    assert row["input_projection_references"] >= 1
