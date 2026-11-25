import copy
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


B = load("writer_boundaries")


def contract(count=5, clock="五章在同一日清晨到黄昏"):
    chapters = [{"chapter": n, "events": [{"id": f"E{n}"}]} for n in range(1, count + 1)]
    facts = {"facts": [{"id": "F1"}], "unknowns": [{"id": "U-004"}, {"id": "U-005"}]}
    return B.make_contract(clock, facts, chapters, [{"path": "source", "sha256": "test-source"}])


def test_contract_preserves_unknowns_and_does_not_allocate_hours():
    result = contract()
    assert result["facts"]["unresolved_fact_ids"] == ["U-004", "U-005"]
    assert result["temporal"]["exact_chapter_times"] == "UNSPECIFIED_NOT_HOST_INVENTED"
    assert result["temporal"]["interfaces"][0]["first_event_ref"] == "E1"
    assert result["source_bindings"][0]["sha256"] == "test-source"


def test_actual_failed_prose_anchor_forms_and_gap_carry():
    rows = [{"chapter": 1, "text": "院门开了。"}, {"chapter": 2, "text": "黄昏时，廊下的灯亮起来。"},
            {"chapter": 3, "text": "陈砚站在门口。"},
            {"chapter": 4, "text": "陈砚被带进听证厅前程序区时，午后的光正从高窗斜切下来。"},
            {"chapter": 5, "text": "裁决已经落定。"}]
    report = B.audit(rows, contract())
    finding = next(row for row in report["findings"] if row["code"] == "MAINLINE_TIME_REGRESSION")
    assert report["decision"] == "BLOCKED"
    assert finding["chapter"] == 4 and finding["previous"]["chapter"] == 2
    assert rows[3]["text"][finding["anchor"]["start"]:finding["anchor"]["end"]] == "午后"


def test_in_chapter_time_regression():
    report = B.audit([{"chapter": 1, "text": "黄昏时，灯亮了。\n清晨，门开了。"}], contract(1))
    assert report["decision"] == "BLOCKED"


@pytest.mark.parametrize("text", [
    "他想起，黄昏时，院中曾经有人。",
    "“黄昏时我会回来。”他说。\n午后，陈砚站在门口。",
    "如果到了黄昏，他就离开。",
    "黄昏时，如果有人回来，他就询问。",
])
def test_memories_dialogue_and_hypotheses_do_not_move_mainline(text):
    anchors = B.current_anchors(text)
    assert all(row["phase"] != "黄昏" for row in anchors)


def test_normal_current_sensory_description_is_retained():
    anchors = B.current_anchors("他进入院中时，午后的光落在脸上。")
    assert [row["phase"] for row in anchors] == ["午后"]


def test_unknown_clock_does_not_invent_same_day_rule():
    report = B.audit([{"chapter": 1, "text": "黄昏时，灯亮了。\n清晨，门开了。"}],
                     contract(1, "时间未定"))
    assert report["decision"] == "NO_DETECTED_FINDINGS_NOT_SEMANTIC_PASS"


@pytest.mark.parametrize("text,code", [
    ("夜晚，灯亮了。", "GLOBAL_TIME_WINDOW"),
    ("次日清晨，陈砚进入听证厅。", "SAME_DAY_BOUNDARY"),
])
def test_explicit_outside_global_window_is_blocked(text, code):
    report = B.audit([{"chapter": 1, "text": text}], contract(1))
    assert any(row["code"] == code for row in report["findings"])


def test_empty_scan_is_not_a_fidelity_approval():
    report = B.audit([{"chapter": 1, "text": "他握紧了衣角，掌心发潮。"}], contract(1))
    assert report["decision"] == "NO_DETECTED_FINDINGS_NOT_SEMANTIC_PASS"
    assert report["chapters"][0]["time_anchor_status"] == "UNSPECIFIED"


@pytest.mark.parametrize("text,code", [
    ("六息之后，背景里起了磨刀声。", "RECORDING_NEW_TIMING"),
    ("那声音他认得，却又不是他记得的场合。", "SPEAKER_RECOGNITION"),
    ("这一页只列了封条贴封的时辰，没有列见证人。", "LEDGER_NEW_HISTORY"),
    ("“本院程序里只写须见证。”他说。", "PROCEDURE_NEW_RULE"),
])
def test_real_failure_fact_forms_are_review_candidates_not_proven_errors(text, code):
    rows = B.fact_candidates(text)
    assert any(row["code"] == code for row in rows)
    assert all(row["decision"] == "REVIEW_REQUIRED" and not row["automatic_fact_violation"] for row in rows)


@pytest.mark.parametrize("text", [
    "六息之后，他松开了手，掌心有些发潮。",
    "他认出了磨刀的声音。",
    "她问，底账上有没有对应每一日的核对签记？",
    "底账上的签记可靠吗？",
    "他不知道说话者是谁，也不知道复核程序是否必须登记。",
    "手心像攥着一块冰，声音却没抖。",
])
def test_ordinary_action_background_recognition_questions_and_emotion_are_not_fact_errors(text):
    assert B.fact_candidates(text) == []


def test_character_claim_is_not_promoted_to_world_fact():
    rows = B.fact_candidates("“本院程序规定必须独自开匣。”杜衡故意撒谎。")
    assert rows and rows[0]["scope"] == "CHARACTER_CLAIM"
    assert not rows[0]["automatic_fact_violation"]


def test_frozen_rhythm_interface_conflict_is_detected_without_changing_source():
    rhythms = [
        {"chapter": 1, "source_ref": "P08-CH1", "sha256": "one",
         "plan": {"target_anchor": "第1天黄昏（本章末事件落点）"}},
        {"chapter": 2, "source_ref": "P08-CH2", "sha256": "two",
         "plan": {"target_anchor": "第1天清晨，章内终点锚定当日黄昏"}},
    ]
    original = copy.deepcopy(rhythms)
    findings = B.rhythm_findings(rhythms)
    assert findings[0]["decision"] == "BLOCKED"
    assert findings[0]["source_ref"] == "P08-CH2"
    assert findings[0]["previous"]["source_ref"] == "P08-CH1"
    assert rhythms == original


def test_ambiguous_global_rhythm_window_is_not_assumed_a_chapter_start():
    assert B.rhythm_findings([{"chapter": 1, "source_ref": "P08-CH1", "sha256": "one",
                              "plan": {"target_anchor": "第1天，与相邻章节同日衔接"}}]) == []


def test_non_extreme_rhythm_phases_are_checked():
    rows = [{"chapter": 1, "source_ref": "P08-CH1", "sha256": "one",
             "plan": {"target_anchor": "第1天午后（本章末事件落点）"}},
            {"chapter": 2, "source_ref": "P08-CH2", "sha256": "two",
             "plan": {"target_anchor": "第1天上午，章内终点锚定正午"}}]
    assert B.rhythm_findings(rows)[0]["code"] == "FROZEN_TIME_INTERFACE_CONFLICT"
    assert B.rhythm_findings([{"chapter": 1, "source_ref": "P08-CH1", "sha256": "one",
                              "plan": {"target_anchor": "第1天午后，章内终点锚定上午"}}])[0]["code"] == (
        "FROZEN_TIME_WITHIN_CHAPTER_CONFLICT")


def test_missing_or_duplicate_chapter_set_cannot_be_claimed_complete():
    with pytest.raises(ValueError, match="BOUNDARY_CHAPTER_SET"):
        B.audit([{"chapter": 1, "text": "正文"}], contract())
    with pytest.raises(ValueError, match="BOUNDARY_CHAPTER_SET"):
        B.audit([{"chapter": 1, "text": "正文"}, {"chapter": 1, "text": "正文"}], contract(1))


def test_writer_checker_uses_local_boundary_but_not_neighbor_prose():
    host = load("real_novel_test")
    text = "夜晚，" + "他站在院中。" * 350
    data = {"chapter": 1, "source_outline_id": "O1", "text": text}
    errors = host.writer_check(data, 1, "O1", contract())
    assert "WRITER_TIME_BOUNDARY:GLOBAL_TIME_WINDOW" in errors


def test_conflicting_frozen_plans_stop_writers_before_any_api(tmp_path, monkeypatch):
    host = load("real_novel_test")
    host.WORK = tmp_path
    plans = []
    for number in range(1, 6):
        source = tmp_path / "frozen" / f"P08-CH{number}.json"
        anchor = ("第1天黄昏（本章末事件落点）" if number == 1 else
                  "第1天清晨，章内终点锚定当日黄昏" if number == 2 else "同一日，具体时段未定")
        host.write(source, {"target_anchor": anchor})
        approval = tmp_path / f"approval-{number}.json"
        host.write(approval, {"artifacts": []})
        sid = f"P08-CH{number}"
        plans.append({"chapter": number, "events": [{"id": f"E{number}"}],
                      "dependencies": {"items": [{"source_id": sid, "sha256": host.sha(source)}]},
                      "source_files": {sid: str(source.relative_to(tmp_path))},
                      "chapter_approval_source": {"path": approval.name, "sha256": host.sha(approval)}})
    host.write(tmp_path / "chapters-r2.json", {"chapters": plans})
    host.write(tmp_path / "events-r2.json", {})
    host.write(tmp_path / "bases.json", {"lines": []})
    monkeypatch.setattr(host, "setup_data", lambda: {
        "seed": {"clock": "同一日清晨到黄昏"}, "facts": {"facts": [], "unknowns": []}})
    host.write(tmp_path / "setup.json", {"test_fixture": True})

    def forbidden_call(*args, **kwargs):
        raise AssertionError("API_MUST_NOT_BE_CALLED")

    monkeypatch.setattr(host, "call", forbidden_call)
    with pytest.raises(RuntimeError, match="WRITER_TIME_PLAN_REQUIRES_REVISION"):
        host.writers()
    assert len(list((tmp_path / "blocked").glob("time-plan-*.json"))) == 1
    assert not (tmp_path / "usage.jsonl").exists()
    assert not (tmp_path / "writers-r2.json").exists()


def test_changed_rhythm_hash_cannot_be_used_as_approved_time_source(tmp_path):
    host = load("real_novel_test")
    path = tmp_path / "rhythm.json"
    host.write(path, {"target_anchor": "同一日"})
    chapters = [{"chapter": 1, "dependencies": {"items": [
        {"source_id": "P08-CH1", "sha256": "wrong"}]},
        "source_files": {"P08-CH1": path.name}}]
    with pytest.raises(ValueError, match="RHYTHM_SOURCE_HASH_CHANGED"):
        B.load_rhythms(tmp_path, chapters)


def test_existing_writer_package_does_not_skip_new_boundary_check(tmp_path, monkeypatch):
    host = load("real_novel_test")
    host.WORK = tmp_path
    original = {"historical_result": "retained"}
    host.write(tmp_path / "writers-r2.json", original)
    monkeypatch.setattr(host.BOUNDARIES, "audit_run", lambda work: {
        "audit": {"decision": "BLOCKED", "findings": [{"code": "MAINLINE_TIME_REGRESSION"}]}})

    def forbidden_call(*args, **kwargs):
        raise AssertionError("NO_API_ON_LEGACY_PACKAGE_CHECK")

    monkeypatch.setattr(host, "call", forbidden_call)
    with pytest.raises(RuntimeError, match="EXISTING_WRITER_BOUNDARY_BLOCKED"):
        host.writers()
    assert host.read(tmp_path / "writers-r2.json") == original
    assert len(list((tmp_path / "boundary_audits").glob("*.json"))) == 1
