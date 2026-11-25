import copy
import importlib.util
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


T = load("timing_revision")


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return T.AUDIT.digest(path.read_bytes())


def fixture(work):
    save(work / "setup.json", {"seed": {"clock": "五章在同一日清晨到黄昏"},
                              "origin": {"opening_date_value": "第1天"}})
    plans = []
    for chapter in range(1, 6):
        event = {"id": f"E{chapter}", "event_name": "核验", "scene_location": "院中",
                 "actions": ["查看既有材料"]}
        sid = f"P14C-CH{chapter}-R2"
        source = work / "frozen" / f"{sid}.json"
        hashed = save(source, {"artifact_id": sid, "task_id": "P14C", "status": "FROZEN",
                              "decision": "SEQUENCED", "event_order": [event["id"]]})
        outline = work / f"chapter-{chapter}/outline.json"
        outline_hash = save(outline, {"event_records": [event]})
        approval = work / f"chapter-{chapter}/approval.json"
        approval_hash = save(approval, {"artifacts": [
            {"role": "outline", "frozen_path": "outline.json", "sha256": outline_hash}]})
        plans.append({"chapter": chapter, "events": [event],
                      "dependencies": {"items": [{"source_id": sid, "sha256": hashed}]},
                      "source_files": {sid: source.relative_to(work).as_posix()},
                      "chapter_approval_source": {
                          "path": approval.relative_to(work).as_posix(), "sha256": approval_hash}})
    save(work / "chapters-r2.json", {"chapters": plans})


def candidate(chapter=1, start="清晨", end="上午"):
    return {"artifact_id": f"P08-CH{chapter}-TIME1", "task_id": "P08", "status": "DRAFT",
            "chapter": chapter, "rhythm_type": "顺延", "span": "本章事件期间",
            "precision": "日内候选", "unknowns": ["具体钟点未定"],
            "target_anchor": {"start_phase": start, "end_phase": end,
                              "basis": "PROPOSED_NOT_APPROVED", "description": "按已批准事件顺序",
                              "source_refs": [f"P14C-CH{chapter}-R2"]}}


def test_schema_validates_legacy_and_structured_shapes():
    schema = json.loads((ROOT / "schemas/RHYTHM_INTENT_SCHEMA.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    validator.check_schema(schema)
    assert not list(validator.iter_errors(candidate()))
    legacy = candidate()
    legacy["target_anchor"] = "第1天，本章终点未定"
    assert not list(validator.iter_errors(legacy))
    legacy["chapter"] = True
    assert list(validator.iter_errors(legacy))


def test_preparation_only_sends_local_and_adjacent_frozen_event_interfaces(tmp_path):
    fixture(tmp_path)
    sources, inputs = T.prepare(tmp_path)
    assert {row["chapter"] for row in inputs[3]["adjacent_time_interfaces"]["approved_order"]} == {2, 4}
    assert inputs[3]["approved_event_sequence"]["chapter"] == 3
    serialized = json.dumps(inputs)
    assert "writer_input_pack" not in serialized and "正文" not in serialized.replace(
        "不从旧冲突P08或已生成正文推定", "")
    assert inputs[3]["adjacent_time_interfaces"]["known_exact_phase_endpoints"] is None
    assert not (tmp_path / "usage.jsonl").exists()
    sources.verify()


@pytest.mark.parametrize("target", ["sequence", "approval", "outline"])
def test_changed_frozen_evidence_stops_before_model(tmp_path, target):
    fixture(tmp_path)
    path = {"sequence": tmp_path / "frozen/P14C-CH1-R2.json",
            "approval": tmp_path / "chapter-1/approval.json",
            "outline": tmp_path / "chapter-1/outline.json"}[target]
    save(path, {})
    with pytest.raises(ValueError, match="HASH_CHANGED"):
        T.prepare(tmp_path)


def test_null_endpoints_are_valid_not_fabricated(tmp_path):
    fixture(tmp_path)
    _, inputs = T.prepare(tmp_path)
    assert T.check(candidate(start=None, end=None), 1, T.allowed_refs(inputs[1])) == []


@pytest.mark.parametrize("change,error", [
    ({"chapter": 2}, "TIMING_CHAPTER_MISMATCH"),
    ({"rhythm_type": "回溯"}, "TIMING_UNAPPROVED_ORDER_CHANGE"),
    ({"target_anchor": "清晨"}, "TIMING_STRUCTURED_ANCHOR_REQUIRED"),
])
def test_wrong_task_scope_is_rejected(change, error):
    data = candidate()
    data.update(change)
    assert error in T.check(data, 1, {"P14C-CH1-R2"})


@pytest.mark.parametrize("field,value,error", [
    ("basis", "APPROVED_SOURCE", "TIMING_PROPOSAL_NOT_APPROVAL"),
    ("start_phase", "夜晚", "TIMING_OUTSIDE_GLOBAL_WINDOW"),
    ("end_phase", "未定", "TIMING_PHASE_INVALID"),
    ("source_refs", ["FUTURE-EVENT"], "TIMING_UNKNOWN_SOURCE_REF"),
    ("source_refs", [{"source_ref": "P14C-CH1-R2"}], "TIMING_UNKNOWN_SOURCE_REF"),
])
def test_unapproved_claims_and_invalid_phase_sources_are_rejected(field, value, error):
    data = candidate()
    data["target_anchor"][field] = value
    assert error in T.check(data, 1, {"P14C-CH1-R2"})


def test_structured_time_regression_carries_across_unspecified_chapters():
    rows = [candidate(1, "午后", "黄昏"), candidate(2, None, None),
            candidate(3, None, None), candidate(4, "午后", "午后"), candidate(5, "黄昏", "黄昏")]
    before = copy.deepcopy(rows)
    report = T.proposal_audit(rows, {row["artifact_id"]: "source-hash" for row in rows})
    assert report["decision"] == "BLOCKED"
    assert report["findings"][0]["chapter"] == 4
    assert rows == before and report["approved"] is False


def test_known_start_and_end_only_anchors_are_not_lost():
    rows = [candidate(1, "黄昏", None), candidate(2, None, "午后"),
            *[candidate(ch, None, None) for ch in (3, 4, 5)]]
    report = T.proposal_audit(rows, {row["artifact_id"]: "source-hash" for row in rows})
    assert report["findings"][0]["previous"]["anchor_kind"] == "START"


def test_structural_pass_does_not_approve_or_activate():
    rows = [candidate(ch, None, None) for ch in range(1, 6)]
    report = T.proposal_audit(rows, {row["artifact_id"]: "source-hash" for row in rows})
    assert report["decision"] == "REVIEW_REQUIRED"
    assert report["semantic_review"] == "NOT_PERFORMED"
    assert report["exact_endpoint_count"] == 0 and not report["active_plan_changed"]


def host_fixture(tmp_path, monkeypatch, budget=100000):
    host = load("real_novel_test")
    fixture(tmp_path)
    host.WORK, host.BUDGET, host.CALL_LIMIT = tmp_path, budget, 550
    monkeypatch.setattr(host, "usage", lambda: [])
    calls = []

    def fake_call(task, key, inputs, check=None, preview=False, **kwargs):
        if preview:
            return 100
        data = candidate(inputs["chapter"], None, None)
        data["artifact_id"] = key
        assert check(data) == []
        host.write(tmp_path / "validated" / f"{key}.json", data)
        calls.append(key)
        return data

    monkeypatch.setattr(host, "call", fake_call)
    return host, calls


def test_stage_preserves_active_plan_and_only_saves_pending_proposals(tmp_path, monkeypatch):
    host, calls = host_fixture(tmp_path, monkeypatch)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    host.timing()
    result = host.read(tmp_path / "timing-r1.json")
    assert len(calls) == 5 and result["approval"] == "NOT_GRANTED"
    assert all(path.read_bytes() == data for path, data in before.items())
    assert not list((tmp_path / "frozen").glob("P08*"))
    host.timing()
    assert len(calls) == 5


def test_whole_stage_budget_check_prevents_any_actual_call(tmp_path, monkeypatch):
    host, calls = host_fixture(tmp_path, monkeypatch, budget=999)
    with pytest.raises(RuntimeError, match="TIMING_STAGE_BUDGET"):
        host.timing()
    assert calls == [] and not (tmp_path / "timing-r1.json").exists()


def test_existing_proposal_recheck_rejects_changed_source(tmp_path, monkeypatch):
    host, calls = host_fixture(tmp_path, monkeypatch)
    host.timing()
    save(tmp_path / "setup.json", {"changed": True})
    with pytest.raises(ValueError, match="TIMING_MANIFEST_SOURCE_CHANGED"):
        host.timing()
    assert len(calls) == 5


def review_fixture(host, work):
    base = host.read(work / "timing-r1.json")
    checks = [{"chapter": ch, "decision": "KEEP_AS_UNAPPROVED_PROPOSAL"}
              for ch in range(1, 5)]
    checks.append({"chapter": 5, "decision": "REVISE",
                   "source": base["candidate_sources"][4],
                   "findings": [{"pointer": "/target_anchor/description", "evidence": "按已批准事件顺序"}],
                   "required_correction": "只保留事件相对范围，不复制全书窗口。"})
    path = work / "review.json"
    save(path, {"approval": "NOT_GRANTED", "checks": checks,
                "base_package": {"path": "timing-r1.json", "sha256": host.sha(work / "timing-r1.json")}})
    host.TIMING_REVIEW_PATH = path
    return path


def test_semantic_repair_only_regenerates_review_target(tmp_path, monkeypatch):
    host, calls = host_fixture(tmp_path, monkeypatch)
    host.timing()
    review_fixture(host, tmp_path)
    before = {path: path.read_bytes() for path in (tmp_path / "validated").glob("*.json")}
    host.timing_repair()
    result = host.read(tmp_path / "timing-r2.json")
    assert calls[-1] == "P08-CH5-TIME2" and len(calls) == 6
    assert result["repaired_chapters"] == [5]
    assert result["approval"] == "NOT_GRANTED" and not result["active_plan_changed"]
    assert all(path.read_bytes() == raw for path, raw in before.items())
    assert [row["artifact_id"] for row in result["proposals"][:4]] == [
        f"P08-CH{ch}-TIME1" for ch in range(1, 5)]


@pytest.mark.parametrize("field", ["hash", "evidence"])
def test_forged_or_stale_review_stops_repair_before_api(tmp_path, monkeypatch, field):
    host, calls = host_fixture(tmp_path, monkeypatch)
    host.timing()
    path = review_fixture(host, tmp_path)
    data = host.read(path)
    if field == "hash":
        data["base_package"]["sha256"] = "wrong"
    else:
        data["checks"][-1]["findings"][0]["evidence"] = "不存在的原句"
    save(path, data)
    with pytest.raises(ValueError, match="TIMING_"):
        host.timing_repair()
    assert len(calls) == 5 and not (tmp_path / "timing-r2.json").exists()
