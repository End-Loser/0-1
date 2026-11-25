import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("run_readiness", ROOT / "tools/audit_run_readiness.py")
A = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(A)


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return A.digest(path.read_bytes())


def usage(work, rows):
    work.mkdir(parents=True, exist_ok=True)
    (work / "usage.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows), encoding="utf-8")


def call(key, task="P05E", attempt=1):
    return {"key": key, "task_id": task, "attempt": attempt, "usage": {"total_tokens": 7}}


def request(work, key, task="P05E", inputs=None, attempt=1):
    save(work / "requests" / f"{key}.attempt-{attempt}.json",
         {"input_pack": {"pack_id": key, "task_id": task, "inputs": inputs or {}}})


def artifact(work, key, task="P05E", **fields):
    save(work / "validated" / f"{key}.json",
         {"artifact_id": key, "task_id": task, "status": "DRAFT", **fields})


def by_task(report, task):
    return next(row for row in report["task_coverage"]["tasks"] if row["task_id"] == task)


def boundary_fixture(work):
    usage(work, [])
    save(work / "setup.json", {"seed": {"clock": "同一日清晨到黄昏"},
                              "facts": {"facts": [], "unknowns": [{"id": "U1"}]}})
    plans, writers = [], []
    for chapter, anchor, text in [
        (1, "第1天黄昏（本章末事件落点）", "黄昏时，灯亮了。"),
        (2, "第1天清晨，章内终点锚定当日黄昏", "午后，门开了。六息之后，录音背景起了响声。"),
    ]:
        source = work / "frozen" / f"P08-CH{chapter}.json"
        sid = source.stem
        hashed = save(source, {"target_anchor": anchor})
        approval = work / f"approval-{chapter}.json"
        approval_hash = save(approval, {"artifacts": []})
        plans.append({"chapter": chapter, "events": [{"id": f"E{chapter}"}],
                      "dependencies": {"items": [{"source_id": sid, "sha256": hashed}]},
                      "source_files": {sid: source.relative_to(work).as_posix()},
                      "chapter_approval_source": {"path": approval.name, "sha256": approval_hash}})
        writers.append({"chapter": chapter, "content": {"text": text}})
    save(work / "chapters-r2.json", {"chapters": plans})
    save(work / "writers-r2.json", {"writers": writers})


def test_absence_is_not_skip_or_success(tmp_path):
    usage(tmp_path, [call("CARD")])
    request(tmp_path, "CARD")
    report = A.analyze_run(tmp_path)
    assert report["task_coverage"]["summary"]["tasks_with_response"] == 1
    assert by_task(report, "P16A")["coverage"] == "NO_RUN_EVIDENCE"
    assert by_task(report, "P16A")["applicability"] == "NOT_INFERRED_FROM_ABSENCE"
    assert report["quality_approval"] == "NOT_GRANTED"
    assert report["revision_queue"]["status"] == "INSUFFICIENT_BOUNDARY_EVIDENCE"


def test_failed_attempts_not_counted_as_distinct_artifacts(tmp_path):
    usage(tmp_path, [call("CARD", attempt=1), call("CARD", attempt=2)])
    request(tmp_path, "CARD", attempt=1)
    request(tmp_path, "CARD", attempt=2)
    artifact(tmp_path, "CARD")
    result = by_task(A.analyze_run(tmp_path), "P05E")
    assert result["response_count"] == 2
    assert result["stored_artifact_count"] == 1
    assert result["recorded_tokens"] == 14


def test_request_inclusion_and_output_reference_are_separate(tmp_path):
    usage(tmp_path, [call("CARD"), call("DECISION", "P13C")])
    request(tmp_path, "CARD", inputs={"own": "CARD"})
    request(tmp_path, "DECISION", "P13C", {"profiles": [{"artifact_id": "CARD", "entity_id": "CHEN"}]})
    artifact(tmp_path, "CARD", entity_id="CHEN")
    artifact(tmp_path, "DECISION", "P13C", profile_ref="CARD")
    report = A.analyze_run(tmp_path)
    card = next(row for row in report["task_coverage"]["artifacts"] if row["artifact_id"] == "CARD")
    assert len(card["downstream_request_references"]) == 1
    assert card["downstream_output_explicit_references"] == ["DECISION"]
    assert card["semantic_use"] == "NOT_PROVEN_BY_REFERENCE"
    assert not card["approval_inferred"] and card["stored_status"] == "DRAFT"


def test_generic_entity_and_substring_do_not_prove_card_consumption(tmp_path):
    usage(tmp_path, [call("CARD"), call("DECISION", "P13C")])
    request(tmp_path, "CARD")
    request(tmp_path, "DECISION", "P13C", {"entity_id": "CHEN", "note": "CARD is useful"})
    artifact(tmp_path, "CARD", entity_id="CHEN")
    artifact(tmp_path, "DECISION", "P13C", entity_id="CHEN", note="CARD")
    row = by_task(A.analyze_run(tmp_path), "P05E")
    assert row["artifacts_with_downstream_request_reference"] == 0
    assert row["artifacts_with_downstream_output_reference"] == 0


def test_deterministic_receipt_is_not_a_model_call(tmp_path):
    usage(tmp_path, [])
    artifact(tmp_path, "OWNER", "P14C0", issuer="deterministic_single_source_host")
    save(tmp_path / "normalization_receipts/OWNER.json",
         {"operation": "EXACT_SINGLE_SOURCE_COPY", "paid_api_call": False})
    report = A.analyze_run(tmp_path)
    row = by_task(report, "P14C0")
    assert row["coverage"] == "DETERMINISTIC_RECEIPT_RECORDED_WITHOUT_LLM"
    assert row["response_count"] == 0 and row["deterministic_receipt_count"] == 1
    assert report["task_coverage"]["summary"]["tasks_with_response"] == 0


def test_artifact_without_receipt_is_not_assumed_deterministic(tmp_path):
    usage(tmp_path, [])
    artifact(tmp_path, "OWNER", "P14C0")
    assert by_task(A.analyze_run(tmp_path), "P14C0")["coverage"] == "ARTIFACT_ONLY_NO_RESPONSE_EVIDENCE"


def test_missing_request_preserves_response_count_and_reports_gap(tmp_path):
    usage(tmp_path, [call("CARD")])
    report = A.analyze_run(tmp_path)
    assert report["task_coverage"]["summary"]["recorded_calls"] == 1
    assert report["task_coverage"]["evidence_gaps"][0]["code"] == "RESPONSE_WITHOUT_REQUEST_EVIDENCE"


@pytest.mark.parametrize("row", [call("../CARD"), call("CARD", "NOT_REGISTERED")])
def test_invalid_call_identity_is_rejected(tmp_path, row):
    usage(tmp_path, [row])
    with pytest.raises(ValueError, match="INVALID_USAGE_IDENTITY"):
        A.analyze_run(tmp_path)


def test_duplicate_attempt_cannot_inflate_coverage(tmp_path):
    usage(tmp_path, [call("CARD"), call("CARD")])
    with pytest.raises(ValueError, match="DUPLICATE_USAGE_ATTEMPT"):
        A.analyze_run(tmp_path)


def test_request_identity_must_match_usage(tmp_path):
    usage(tmp_path, [call("CARD")])
    request(tmp_path, "CARD", "P13C")
    with pytest.raises(ValueError, match="REQUEST_IDENTITY_MISMATCH"):
        A.analyze_run(tmp_path)


def test_source_changes_are_detected(tmp_path):
    path = tmp_path / "source.json"
    save(path, {"version": 1})
    sources = A.Sources(tmp_path)
    sources.json(path)
    save(path, {"version": 2})
    with pytest.raises(ValueError, match="AUDIT_SOURCE_CHANGED"):
        sources.verify()


def test_revision_queue_preserves_sources_unknowns_and_pending_status(tmp_path, monkeypatch):
    import urllib.request

    def forbidden(*args, **kwargs):
        raise AssertionError("API_MUST_NOT_BE_CALLED")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    boundary_fixture(tmp_path)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    report = A.analyze_run(tmp_path)
    assert report["revision_queue"]["boundary_findings"] == 3
    assert report["revision_queue"]["boundary_decision"] == "BLOCKED"
    assert {row["kind"] for row in report["revision_queue"]["items"]} == {
        "DESIGN_TIME", "PROSE_TIME", "FACT_CANDIDATE"}
    for row in report["revision_queue"]["items"]:
        assert row["state"] == "PENDING_REVIEW"
        assert row["approved_replacement"] is None
        assert row["source_bindings"] and not row["active_plan_changed"]
    fact = next(row for row in report["revision_queue"]["items"] if row["kind"] == "FACT_CANDIDATE")
    assert fact["diagnostic_decision"] == "REVIEW_REQUIRED"
    assert not fact["evidence"]["automatic_fact_violation"]
    assert all(path.read_bytes() == raw for path, raw in before.items())
    assert report["additional_paid_calls"] == 0


@pytest.mark.parametrize("target", ["rhythm", "approval"])
def test_changed_dependency_or_approval_is_blocked(tmp_path, target):
    boundary_fixture(tmp_path)
    path = (tmp_path / "frozen/P08-CH1.json" if target == "rhythm" else tmp_path / "approval-1.json")
    save(path, {})
    with pytest.raises(ValueError, match="HASH_CHANGED"):
        A.analyze_run(tmp_path)


def test_frozen_approved_bytes_are_checked(tmp_path):
    boundary_fixture(tmp_path)
    frozen = tmp_path / "outline.json"
    expected = save(frozen, {"old": True})
    plans_path = tmp_path / "chapters-r2.json"
    plans = json.loads(plans_path.read_text(encoding="utf-8"))
    approval = tmp_path / "approval-1.json"
    plans["chapters"][0]["chapter_approval_source"]["sha256"] = save(
        approval, {"artifacts": [{"frozen_path": "outline.json", "sha256": expected}]})
    save(plans_path, plans)
    save(frozen, {"changed": True})
    with pytest.raises(ValueError, match="FROZEN_ARTIFACT_HASH_CHANGED"):
        A.analyze_run(tmp_path)


def test_literary_and_partial_reviews_become_unapproved_work(tmp_path):
    boundary_fixture(tmp_path)
    save(tmp_path / "reviews-r2.json", {"scope": {"sample": "one scene"},
         "reviews": [{"artifact_id": "L1", "task_id": "P19G", "main_obstacle": "推进弱"},
                     {"artifact_id": "F1", "task_id": "P19D2", "decision": "PARTIAL"},
                     {"artifact_id": "F2", "task_id": "P19D2", "decision": "FAITHFUL"}]})
    report = A.analyze_run(tmp_path)
    rows = report["revision_queue"]["items"]
    assert len(rows) == 5
    assert report["revision_queue"]["review_scope"] == {"sample": "one scene"}
    assert all(row.get("review_artifact_id") != "F2" for row in rows)
    assert report["quality_approval"] == "NOT_GRANTED"


@pytest.mark.parametrize("existing", [False, True])
def test_cli_refuses_outputs_inside_original_run(tmp_path, monkeypatch, existing):
    import sys
    output = tmp_path / "audit.json"
    if existing:
        save(output, {"history": True})
    monkeypatch.setattr(sys, "argv", ["audit", "--work-dir", str(tmp_path), "--out", str(output)])
    with pytest.raises(ValueError, match="AUDIT_OUTPUT_MUST_BE_OUTSIDE_RUN"):
        A.main()
    assert output.exists() == existing
