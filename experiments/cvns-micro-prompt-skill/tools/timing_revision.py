"""Prepare scoped P08 proposals from frozen plans; never approve or activate."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


AUDIT = module("audit_run_readiness")
BOUNDARIES = module("writer_boundaries")


def prepare(work):
    sources = AUDIT.Sources(work)
    setup = sources.json(work / "setup.json")
    plans = sources.json(work / "chapters-r2.json")["chapters"]
    numbers = [row["chapter"] for row in plans]
    if set(numbers) != set(range(1, 6)) or len(numbers) != 5:
        raise ValueError("TIMING_REQUIRES_FIVE_CHAPTERS")
    clock = setup["seed"]["clock"]
    if not (any(word in clock for word in ("同一日", "同一天"))
            and "清晨" in clock and "黄昏" in clock):
        raise ValueError("TIMING_REVISION_UNSUPPORTED_CLOCK")
    rows = []
    for plan in sorted(plans, key=lambda row: row["chapter"]):
        sequence = None
        for item in plan["dependencies"]["items"]:
            path = (work / plan["source_files"][item["source_id"]]).resolve()
            if not path.is_relative_to(work.resolve()):
                raise ValueError("TIMING_DEPENDENCY_OUTSIDE_RUN")
            data = sources.json(path)
            if sources.hashes[path] != item["sha256"]:
                raise ValueError("TIMING_DEPENDENCY_HASH_CHANGED")
            if data.get("task_id") == "P14C" and item["source_id"].startswith("P14C-CH"):
                sequence = data
        if sequence is None or sequence.get("status") != "FROZEN" or sequence.get("decision") != "SEQUENCED":
            raise ValueError("TIMING_SEQUENCE_NOT_APPROVED")
        order = [row["event_id"] if isinstance(row, dict) else row for row in sequence["event_order"]]
        if order != [row["id"] for row in plan["events"]]:
            raise ValueError("TIMING_EVENT_ORDER_MISMATCH")
        approval = plan["chapter_approval_source"]
        path = (work / approval["path"]).resolve()
        if not path.is_relative_to(work.resolve()):
            raise ValueError("TIMING_APPROVAL_OUTSIDE_RUN")
        receipt = sources.json(path)
        if sources.hashes[path] != approval["sha256"]:
            raise ValueError("TIMING_APPROVAL_HASH_CHANGED")
        for item in receipt["artifacts"]:
            frozen = (path.parent / item["frozen_path"]).resolve()
            if not frozen.is_relative_to(work.resolve()):
                raise ValueError("TIMING_FROZEN_OUTSIDE_RUN")
            data = sources.json(frozen)
            if sources.hashes[frozen] != item["sha256"]:
                raise ValueError("TIMING_FROZEN_HASH_CHANGED")
            if item["role"] == "outline" and data.get("event_records") != plan["events"]:
                raise ValueError("TIMING_CANONICAL_EVENTS_CHANGED")
        rows.append({"chapter": plan["chapter"], "source_ref": sequence["artifact_id"],
                     "event_order": order,
                     "events": [{key: event[key] for key in ("id", "event_name", "scene_location", "actions")}
                                for event in plan["events"]]})
    inputs = {}
    for row in rows:
        inputs[row["chapter"]] = {
            "chapter": row["chapter"], "opening_date": BOUNDARIES.payload(setup["origin"]),
            "target_time_span": clock, "approved_event_sequence": row,
            "adjacent_time_interfaces": {
                "approved_order": [item for item in rows if abs(item["chapter"] - row["chapter"]) == 1],
                "known_exact_phase_endpoints": None,
                "instruction": "只按批准事件顺序顺延；全书时间窗不能复制为每章完整跨度。"
                               "相邻章具体时段未批准；可以保留未定，不从旧冲突P08或已生成正文推定。"},
        }
    sources.verify()
    return sources, inputs


def check(data, chapter, allowed_refs):
    errors = []
    if data.get("chapter") != chapter:
        errors.append("TIMING_CHAPTER_MISMATCH")
    if data.get("rhythm_type") != "顺延":
        errors.append("TIMING_UNAPPROVED_ORDER_CHANGE")
    anchor = data.get("target_anchor")
    if not isinstance(anchor, dict):
        return errors + ["TIMING_STRUCTURED_ANCHOR_REQUIRED"]
    if anchor.get("basis") != "PROPOSED_NOT_APPROVED":
        errors.append("TIMING_PROPOSAL_NOT_APPROVAL")
    if not {"start_phase", "end_phase"} <= anchor.keys():
        errors.append("TIMING_ENDPOINTS_REQUIRED_NULL_ALLOWED")
    values = [anchor.get("start_phase"), anchor.get("end_phase")]
    ranks = []
    for phase in values:
        if phase is None:
            ranks.append(None)
        elif not isinstance(phase, str) or phase not in BOUNDARIES.PHASES:
            errors.append("TIMING_PHASE_INVALID")
            ranks.append(None)
        else:
            ranks.append(BOUNDARIES.PHASES[phase])
            if not 0 <= ranks[-1] <= 4:
                errors.append("TIMING_OUTSIDE_GLOBAL_WINDOW")
    if all(rank is not None for rank in ranks) and ranks[0] > ranks[1]:
        errors.append("TIMING_WITHIN_CHAPTER_REGRESSION")
    refs = anchor.get("source_refs")
    if not isinstance(refs, list) or not refs or any(
            not isinstance(ref, str) or ref not in allowed_refs for ref in refs):
        errors.append("TIMING_UNKNOWN_SOURCE_REF")
    return errors


def allowed_refs(inputs):
    rows = [inputs["approved_event_sequence"],
            *inputs["adjacent_time_interfaces"]["approved_order"]]
    return {ref for row in rows for ref in [row["source_ref"], *row["event_order"]]}


def proposal_audit(artifacts, source_hashes):
    numbers = [BOUNDARIES.payload(row)["chapter"] for row in artifacts]
    if len(numbers) != 5 or set(numbers) != set(range(1, 6)):
        raise ValueError("TIMING_PROPOSAL_CHAPTER_SET")
    rhythms = [{"chapter": BOUNDARIES.payload(row)["chapter"], "source_ref": row["artifact_id"],
                "sha256": source_hashes[row["artifact_id"]], "plan": BOUNDARIES.payload(row)}
               for row in artifacts]
    findings = BOUNDARIES.rhythm_findings(rhythms)
    return {"decision": "BLOCKED" if findings else "REVIEW_REQUIRED",
            "findings": findings, "semantic_review": "NOT_PERFORMED",
            "exact_endpoint_count": sum(
                BOUNDARIES.payload(row)["target_anchor"].get(key) is not None
                for row in artifacts for key in ("start_phase", "end_phase")),
            "approved": False, "active_plan_changed": False,
            "limits": "Endpoint order/window checks do not prove event duration, waiting or transition plausibility."}


def verify_manifest(work, manifest):
    for item in manifest:
        path = (work / item["path"]).resolve()
        if not path.is_relative_to(work.resolve()):
            raise ValueError("TIMING_MANIFEST_OUTSIDE_RUN")
        if AUDIT.digest(path.read_bytes()) != item["sha256"]:
            raise ValueError("TIMING_MANIFEST_SOURCE_CHANGED")


def repair_scope(work, review_path):
    sources = AUDIT.Sources(work)
    review = sources.json(review_path)
    if review.get("approval") != "NOT_GRANTED":
        raise ValueError("TIMING_REVIEW_NOT_AN_APPROVAL")
    binding = review["base_package"]
    verify_manifest(work, [binding])
    base = sources.json(work / binding["path"])
    verify_manifest(work, base["source_manifest"])
    verify_manifest(work, base["candidate_sources"])
    checks = review["checks"]
    if len(checks) != 5 or {row["chapter"] for row in checks} != set(range(1, 6)):
        raise ValueError("TIMING_REVIEW_CHAPTER_SET")
    proposals = {BOUNDARIES.payload(row)["chapter"]: row for row in base["proposals"]}
    targets = []
    for row in checks:
        if row["decision"] == "KEEP_AS_UNAPPROVED_PROPOSAL":
            continue
        if row["decision"] != "REVISE" or not row.get("required_correction") or not row.get("findings"):
            raise ValueError("TIMING_REVIEW_ACTION_INVALID")
        ident = proposals[row["chapter"]]["artifact_id"]
        expected = next(item for item in base["candidate_sources"]
                        if item["path"] == f"validated/{ident}.json")
        if row.get("source") != expected:
            raise ValueError("TIMING_REVIEW_CANDIDATE_MISMATCH")
        for finding in row["findings"]:
            value = BOUNDARIES.payload(proposals[row["chapter"]])
            for part in finding["pointer"].lstrip("/").split("/"):
                value = value[part.replace("~1", "/").replace("~0", "~")]
            if not isinstance(value, str) or finding["evidence"] not in value:
                raise ValueError("TIMING_REVIEW_EVIDENCE_MISMATCH")
        targets.append(row)
    if not targets:
        raise ValueError("TIMING_REVIEW_NO_REPAIR_TARGET")
    sources.verify()
    return base, targets
