import copy
import json

import pytest

import test_canon_context as F

A = F.module("audit_legacy_canon")


def fixture(root):
    for name in ("requests", "responses", "validated", "frozen", "approvals"):
        (root / name).mkdir()
    row = {"key": "P00", "task_id": "P00", "attempt": 1,
           "usage": {"total_tokens": 10}}
    (root / "usage.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    F.save(root / "requests/P00.attempt-1.json", {"input_pack": {"task_id": "P00"}})
    F.save(root / "responses/P00.attempt-1.json", {
        "usage": row["usage"], "choices": [{"message": {"content": '{"task_id":"P00"}'}}]})
    F.save(root / "validated/P16-misnamed.json", {"task_id": "P19D", "status": "VALIDATED"})


def test_negative_inventory_is_content_based_and_read_only(tmp_path):
    fixture(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    report = A.audit(tmp_path)
    assert report["decision"] == "NO_STRUCTURED_CANON_REGISTRATION_IN_LEGACY_SAMPLE"
    assert report["recorded_p16_calls"] == 0 and report["new_api_calls"] == 0
    assert report["stored_task_counts"] == {"P19D": 1}
    assert A.verify(report) == report
    assert all(p.read_bytes() == raw for p, raw in before.items())


@pytest.mark.parametrize("value", [
    {"task_id": "P16A"}, {"task_id": "P16B"}, {"task_id": "P16C"},
    {"nested": {"layer": "DERIVED"}}, {"ack_nodes": []}, {"derived_lines": []},
    {"x": [{"ack_ref": "B1", "target_chapter": 1, "obligations": ["show"]}]},
])
def test_unexpected_filename_cannot_hide_registration(tmp_path, value):
    fixture(tmp_path)
    F.save(tmp_path / "unexpected.json", value)
    report = A.audit(tmp_path)
    assert report["decision"] == "REGISTRATION_EVIDENCE_REQUIRES_REVIEW"
    assert any(row["source"] == "unexpected.json"
               for row in report["structured_registration_candidates"])


def test_response_json_string_is_examined(tmp_path):
    fixture(tmp_path)
    path = tmp_path / "responses/P00.attempt-1.json"
    value = F.read(path)
    value["choices"][0]["message"]["content"] = '{"layer":"DERIVED","id":"D1"}'
    F.save(path, value)
    report = A.audit(tmp_path)
    assert report["decision"] == "REGISTRATION_EVIDENCE_REQUIRES_REVIEW"
    assert report["structured_registration_candidates"][0]["response_choice"] == 0


@pytest.mark.parametrize("mutation", ["addition", "changed", "removed", "report"])
def test_negative_audit_is_not_reusable_after_source_change(tmp_path, mutation):
    fixture(tmp_path)
    report = A.audit(tmp_path)
    if mutation == "addition":
        F.save(tmp_path / "new.json", {"ack_nodes": []})
    elif mutation == "changed":
        F.save(tmp_path / "validated/P16-misnamed.json", {"task_id": "P16B"})
    elif mutation == "removed":
        (tmp_path / "validated/P16-misnamed.json").unlink()
    else:
        report = copy.deepcopy(report)
        report["recorded_calls"] = 0
    with pytest.raises(ValueError, match="LEGACY_CANON_AUDIT_NO_LONGER_CURRENT"):
        A.verify(report)


@pytest.mark.parametrize("mutation", ["task", "usage", "duplicate", "bool", "missing"])
def test_invalid_billing_binding_blocks_audit(tmp_path, mutation):
    fixture(tmp_path)
    if mutation == "task":
        F.save(tmp_path / "requests/P00.attempt-1.json", {"input_pack": {"task_id": "P16B"}})
    elif mutation == "usage":
        path = tmp_path / "responses/P00.attempt-1.json"
        value = F.read(path)
        value["usage"]["total_tokens"] = 11
        F.save(path, value)
    elif mutation == "duplicate":
        path = tmp_path / "usage.jsonl"
        path.write_text(path.read_text() * 2)
    elif mutation == "bool":
        path = tmp_path / "usage.jsonl"
        value = json.loads(path.read_text())
        value["attempt"] = True
        path.write_text(json.dumps(value) + "\n")
    else:
        (tmp_path / "requests/P00.attempt-1.json").unlink()
    with pytest.raises(ValueError, match="LEGACY_CANON"):
        A.audit(tmp_path)
