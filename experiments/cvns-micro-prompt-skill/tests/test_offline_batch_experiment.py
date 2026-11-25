import copy
import json
import sys

import pytest
import test_canon_context as F

B = F.module("offline_batch_experiment")


def items(count=3):
    contract = {"task_id": "P19G", "required_fields": ["artifact_id", "task_id", "status", "evidence"],
                "status_values": ["DRAFT"]}
    shared = {"full_base": [{"nodes": ["approved " * 1200], "future": "proposed", "unknown": None}]}
    return [{"key": "T" + str(i), "task_id": "P19G", "contract": copy.deepcopy(contract),
             "input_pack": {"pack_id": "T" + str(i), "task_id": "P19G",
                            "inputs": copy.deepcopy(shared)}} for i in range(count)]


def response(value):
    return {"finish_reason": "stop", "items": [
        dict(item, content={"artifact_id": item["key"], "task_id": item["task_id"],
                            "status": "DRAFT", "evidence": []}) for item in value["items"]]}


@pytest.mark.parametrize("count", [3, 4, 5])
def test_full_inputs_and_base_roundtrip_in_host_only_batch(count):
    source = items(count)
    original = copy.deepcopy(source)
    value = B.bundle(source)
    restored = B.O.expand_inputs(value["inputs"])
    assert restored == {item["key"]: item["input_pack"] for item in original}
    assert source == original
    assert value["input_projection"]["lossless_roundtrip"] is True
    assert value["model_execution"] == "NOT_GRANTED"
    assert value["mode"] == B.MODE


@pytest.mark.parametrize("count", [0, 1, 2, 6])
def test_batch_size_is_explicit(count):
    with pytest.raises(ValueError, match="SIZE"):
        B.bundle(items(count))


@pytest.mark.parametrize("field", ["key", "pack_id", "task_id", "contract", "reserved"])
def test_invalid_identity_duplicates_and_reserved_keys_rejected(field):
    source = items()
    if field == "key":
        source[1]["key"] = source[0]["key"]
        source[1]["input_pack"]["pack_id"] = source[0]["key"]
    elif field == "pack_id":
        source[0]["input_pack"]["pack_id"] = "other"
    elif field == "task_id":
        source[0]["input_pack"]["task_id"] = "other"
    elif field == "contract":
        source[0]["contract"]["task_id"] = "other"
    else:
        source[0]["input_pack"]["inputs"][B.O.REF] = "/outside"
    with pytest.raises(ValueError):
        B.bundle(source)


def test_same_entity_id_different_facts_never_merged():
    source = items()
    source[1]["input_pack"]["inputs"]["full_base"][0]["nodes"] = ["different " * 1200]
    value = B.bundle(source)
    assert B.O.expand_inputs(value["inputs"])["T1"] == source[1]["input_pack"]


@pytest.mark.parametrize("task", [None, "", "invalid"])
def test_task_identity_cannot_be_missing_even_when_all_copies_agree(task):
    source = items()
    for item in source:
        item["task_id"] = item["input_pack"]["task_id"] = item["contract"]["task_id"] = task
    with pytest.raises(ValueError, match="IDENTITY"):
        B.bundle(source)


def test_reordered_results_demultiplex_by_key_without_acceptance():
    value = B.bundle(items())
    outputs = response(value)
    outputs["items"].reverse()
    calls = []
    def validate(content, contract, pack):
        calls.append(pack["pack_id"])
        return []
    original = copy.deepcopy(outputs)
    results = B.split(value, outputs, validate)
    assert calls == ["T0", "T1", "T2"]
    assert all(row["publication_acceptance"] == "NOT_GRANTED" for row in results)
    assert outputs == original


@pytest.mark.parametrize("change", [
    "missing", "duplicate", "extra", "truncated", "input", "contract", "content", "bundle",
])
def test_incomplete_swapped_or_changed_batch_never_accepted(change):
    value = B.bundle(items())
    outputs = response(value)
    if change == "missing":
        outputs["items"].pop()
    elif change == "duplicate":
        outputs["items"][1] = copy.deepcopy(outputs["items"][0])
    elif change == "extra":
        outputs["items"][1]["key"] = "unknown"
    elif change == "truncated":
        outputs["finish_reason"] = "length"
    elif change in ("input", "contract"):
        outputs["items"][1][change + "_sha256"] = "changed"
    elif change == "content":
        outputs["items"][1]["content"] = []
    else:
        value["inputs"]["T0"]["pack_id"] = "changed"
    with pytest.raises(ValueError):
        B.split(value, outputs, lambda *args: [])


def test_single_bad_item_retains_individual_failure_not_global_pass():
    value = B.bundle(items())
    outputs = response(value)
    outputs["items"][1]["content"]["artifact_id"] = "T0"
    results = B.split(value, outputs, lambda *args: [])
    assert results[0]["decision"] == "REPLAY_STRUCTURAL_PASS_NOT_ACCEPTED"
    assert results[1]["decision"] == "REPLAY_STRUCTURAL_FAILURE"
    assert "BATCH_CONTENT_IDENTITY_MISMATCH" in results[1]["errors"]


def test_dependency_and_visibility_unknowns_do_not_imply_independence():
    source = items()
    result = B.eligibility(source)
    assert result["decision"] == "MODEL_BATCH_NOT_GRANTED"
    assert len(result["blockers"]) == 7


def test_internal_dependencies_and_actor_contexts_block_model_batch():
    source = items()
    for i, item in enumerate(source):
        item.update(task_id="P13C", dependencies_complete=True, dependency_keys=["T2"] if i == 0 else [],
                    visibility_scope={"complete": True, "actor": str(i)})
    reasons = B.eligibility(source)["blockers"]
    assert "T0:INTRA_BATCH_DEPENDENCY" in reasons
    assert "DIFFERENT_VISIBILITY_SCOPES" in reasons
    assert sum("ACTOR_KNOWLEDGE_ISOLATION" in reason for reason in reasons) == 3
    assert "NO_BATCH_MODEL_CONTRACT_OR_PAID_AUTHORIZATION" in reasons


def archive(tmp_path):
    source = items(5)
    work = tmp_path / "archive"
    work.mkdir()
    rows = []
    for item in source:
        key = item["key"]
        wire = {"inputs": item["input_pack"]["inputs"], "output_contract": item["contract"]}
        request = {"input_pack": item["input_pack"], "model_request_without_credentials": {
            "messages": [{"content": "system"}, {"content": json.dumps(wire)}]}}
        raw_response = {"usage": {"total_tokens": 10}, "choices": [{"finish_reason": "stop",
            "message": {"content": json.dumps({"artifact_id": key, "task_id": "P19G",
                                             "status": "DRAFT", "evidence": []})}}]}
        F.save(work / "requests" / (key + ".attempt-1.json"), request)
        F.save(work / "responses" / (key + ".attempt-1.json"), raw_response)
        rows.append({"key": key, "task_id": "P19G", "attempt": 1, "usage": {"total_tokens": 10}})
    (work / "usage.jsonl").write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    return work, ["T" + str(i) for i in range(5)]


@pytest.mark.parametrize("count", [3, 4, 5])
def test_archived_replay_is_read_only_no_network_or_config(tmp_path, monkeypatch, count):
    work, keys = archive(tmp_path)
    before = {path: path.read_bytes() for path in work.rglob("*") if path.is_file()}
    monkeypatch.setattr(B.H, "config", lambda: pytest.fail("NO_CONFIG"))
    monkeypatch.setattr(B.H, "call", lambda *a, **k: pytest.fail("NO_API"))
    monkeypatch.setattr(B.H.urllib.request, "urlopen", lambda *a, **k: pytest.fail("NO_NETWORK"))
    result = B.replay(work, keys[:count])
    assert result["all_inputs_lossless_roundtrip"]
    assert result["recorded_calls"] == 5 and result["recorded_tokens"] == 50
    assert result["additional_paid_calls"] == result["additional_paid_tokens"] == 0
    assert result["saved_bytes"] > 0
    assert before == {path: path.read_bytes() for path in work.rglob("*") if path.is_file()}
    assert not (work / "validated").exists()


@pytest.mark.parametrize("change", ["billing", "duplicate_billing", "wire", "truncated", "identity", "status"])
def test_archive_corruption_or_invalid_output_is_reported(tmp_path, change):
    work, keys = archive(tmp_path)
    request_path = work / "requests/T0.attempt-1.json"
    response_path = work / "responses/T0.attempt-1.json"
    if change in ("billing", "duplicate_billing"):
        rows = [json.loads(line) for line in (work / "usage.jsonl").read_text().splitlines()]
        if change == "billing":
            rows[0]["usage"]["total_tokens"] = 11
        else:
            rows.append(rows[0])
        (work / "usage.jsonl").write_text("\n".join(json.dumps(row) for row in rows))
    elif change in ("wire", "identity"):
        request_value = F.read(request_path)
        if change == "wire":
            wire = json.loads(request_value["model_request_without_credentials"]["messages"][1]["content"])
            wire["inputs"] = {}
            request_value["model_request_without_credentials"]["messages"][1]["content"] = json.dumps(wire)
        else:
            request_value["input_pack"]["pack_id"] = "other"
        request_path.write_text(json.dumps(request_value))
    else:
        raw_response = F.read(response_path)
        if change == "truncated":
            raw_response["choices"][0]["finish_reason"] = "length"
        else:
            content = json.loads(raw_response["choices"][0]["message"]["content"])
            content["status"] = "ACCEPTED"
            raw_response["choices"][0]["message"]["content"] = json.dumps(content)
        response_path.write_text(json.dumps(raw_response))
    if change == "status":
        result = B.replay(work, keys)
        assert result["result_items"][0]["decision"] == "REPLAY_STRUCTURAL_FAILURE"
        assert "BATCH_STATUS_ENUM" in result["result_items"][0]["errors"]
    else:
        with pytest.raises(ValueError):
            B.replay(work, keys)


def test_archive_source_race_blocks_report(tmp_path, monkeypatch):
    work, keys = archive(tmp_path)
    original = B.bundle
    def changed(items):
        result = original(items)
        (work / "usage.jsonl").write_text("changed")
        return result
    monkeypatch.setattr(B, "bundle", changed)
    with pytest.raises(ValueError, match="SOURCE_CHANGED"):
        B.replay(work, keys)


@pytest.mark.parametrize("schema_present,valid", [(True, True), (True, False), (False, True)])
def test_archived_effective_schema_cannot_be_bypassed(tmp_path, schema_present, valid):
    work, keys = archive(tmp_path)
    for key in keys:
        path = work / "requests" / (key + ".attempt-1.json")
        request_value = F.read(path)
        wire = json.loads(request_value["model_request_without_credentials"]["messages"][1]["content"])
        wire["output_contract"]["payload_schema"] = "schemas/DERIVED_LINE_SCHEMA.json"
        if schema_present:
            wire["output_schema"] = {"type": "object", "properties": {"evidence": {"type": "array"}},
                                     "required": ["evidence"]}
        request_value["model_request_without_credentials"]["messages"][1]["content"] = json.dumps(wire)
        path.write_text(json.dumps(request_value))
    if not valid:
        path = work / "responses/T0.attempt-1.json"
        value = F.read(path)
        content = json.loads(value["choices"][0]["message"]["content"])
        content["evidence"] = "not an array"
        value["choices"][0]["message"]["content"] = json.dumps(content)
        path.write_text(json.dumps(value))
    if not schema_present:
        with pytest.raises(ValueError, match="SCHEMA_MISSING"):
            B.replay(work, keys)
    else:
        result = B.replay(work, keys)
        assert bool(result["result_items"][0]["errors"]) is not valid
        assert any(path.endswith("DERIVED_LINE_SCHEMA.json") for path in result["source_hashes"])


def test_cli_never_overwrites_existing_report_or_writes_inside_archive(tmp_path, monkeypatch):
    work, keys = archive(tmp_path)
    target = tmp_path / "report.json"
    target.write_bytes(b"original")
    monkeypatch.setattr(B, "replay", lambda *a: pytest.fail("Output checks must precede replay"))
    monkeypatch.setattr(sys, "argv", [
        "offline_batch_experiment", "--work-dir", str(work), "--keys", *keys, "--out", str(target)])
    assert B.main() == 2
    assert target.read_bytes() == b"original"
    monkeypatch.setattr(sys, "argv", [
        "offline_batch_experiment", "--work-dir", str(work), "--keys", *keys,
        "--out", str(work / "report.json")])
    assert B.main() == 2
    assert not (work / "report.json").exists()
