"""Audit recorded task coverage and prepare revision work without API access.

References are evidence of inclusion, not semantic use. This tool does not
approve artifacts, infer missing SKIP receipts, or change the active plan.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REFERENCE_KEYS = {
    "source_refs", "source_ref", "source_artifact_id", "source_artifact_ids",
    "artifact_refs", "profile_ref", "decision_refs", "decision_plan_refs",
    "decision_plan_id", "selected_refs", "source_opportunity_plan_ref",
}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


class Sources:
    """Keep byte hashes of every read source and reject concurrent edits."""

    def __init__(self, work):
        self.work = work.resolve()
        self.hashes = {}

    def raw(self, path):
        path = path.resolve()
        if not path.is_relative_to(self.work) and not path.is_relative_to(ROOT):
            raise ValueError("SOURCE_OUTSIDE_ALLOWED_ROOTS")
        raw = path.read_bytes()
        current = digest(raw)
        if path in self.hashes and self.hashes[path] != current:
            raise ValueError("AUDIT_SOURCE_CHANGED:" + path.name)
        self.hashes[path] = current
        return raw

    def json(self, path):
        return json.loads(self.raw(path).decode("utf-8-sig"))

    def binding(self, path):
        path = path.resolve()
        if path not in self.hashes:
            self.raw(path)
        base = self.work if path.is_relative_to(self.work) else ROOT
        return {"root": "run" if base == self.work else "project",
                "path": path.relative_to(base).as_posix(), "sha256": self.hashes[path]}

    def verify(self):
        for path, expected in self.hashes.items():
            if digest(path.read_bytes()) != expected:
                raise ValueError("AUDIT_SOURCE_CHANGED:" + path.name)


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from strings(child)


def explicit_refs(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in REFERENCE_KEYS:
                yield from strings(child)
            yield from explicit_refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from explicit_refs(child)


def registry(sources, filename):
    text = sources.raw(ROOT / filename).decode("utf-8-sig")
    rows = list(csv.DictReader(text.splitlines()))
    result = {row["task_id"]: row for row in rows}
    if len(result) != len(rows):
        raise ValueError("DUPLICATE_REGISTRY_TASK")
    return result


def task_coverage(work, sources):
    tasks = registry(sources, "TASK_REGISTRY.csv")
    life = registry(sources, "TASK_LIFECYCLE_REGISTRY.csv")
    if tasks.keys() != life.keys():
        raise ValueError("LIFECYCLE_REGISTRY_MISMATCH")
    usage_path = work / "usage.jsonl"
    calls = [json.loads(line) for line in sources.raw(usage_path).decode("utf-8-sig").splitlines()
             if line.strip()]
    identities = [(row["key"], row["attempt"]) for row in calls]
    if len(set(identities)) != len(identities):
        raise ValueError("DUPLICATE_USAGE_ATTEMPT")
    by_task, input_index, output_index = defaultdict(list), defaultdict(set), defaultdict(set)
    request_bindings, gaps = {}, []
    for row in calls:
        task, key, attempt = row["task_id"], row["key"], row["attempt"]
        if task not in tasks or Path(key).name != key or "/" in key or "\\" in key:
            raise ValueError("INVALID_USAGE_IDENTITY")
        by_task[task].append(row)
        path = work / "requests" / f"{key}.attempt-{attempt}.json"
        if not path.exists():
            gaps.append({"code": "RESPONSE_WITHOUT_REQUEST_EVIDENCE", "key": key, "attempt": attempt})
            continue
        pack = sources.json(path)["input_pack"]
        if pack["pack_id"] != key or pack["task_id"] != task:
            raise ValueError("REQUEST_IDENTITY_MISMATCH:" + path.name)
        request_bindings[path.name] = sources.binding(path)
        for value in set(strings(pack["inputs"])):
            input_index[value].add(path.name)

    artifacts = []
    for path in sorted((work / "validated").glob("*.json")):
        data = sources.json(path)
        task, ident = data.get("task_id"), data.get("artifact_id")
        if task not in tasks or not isinstance(ident, str) or ident != path.stem:
            gaps.append({"code": "VALIDATED_IDENTITY_UNRESOLVED", "source": sources.binding(path)})
            continue
        artifact = {"artifact_id": ident, "task_id": task, "source": sources.binding(path),
                    "stored_status": data.get("status"), "decision": data.get("decision"),
                    "approval_inferred": False}
        receipt = work / "normalization_receipts" / path.name
        artifact["deterministic_receipt"] = None
        if task == "P14C0" and receipt.exists():
            recorded = sources.json(receipt)
            if (recorded.get("operation") == "EXACT_SINGLE_SOURCE_COPY"
                    and recorded.get("paid_api_call") is False
                    and data.get("issuer") == "deterministic_single_source_host"):
                artifact["deterministic_receipt"] = sources.binding(receipt)
        artifacts.append(artifact)
        # Ignore the producer's own artifact_id and only index typed source refs.
        for value in set(explicit_refs(data)) - {ident}:
            output_index[value].add(ident)

    for artifact in artifacts:
        ident = artifact["artifact_id"]
        own_names = {f"{ident}.attempt-{row['attempt']}.json"
                     for row in by_task[artifact["task_id"]] if row["key"] == ident}
        artifact["downstream_request_references"] = [
            request_bindings[name] for name in sorted(input_index[ident] - own_names)]
        artifact["downstream_output_explicit_references"] = sorted(output_index[ident])
        artifact["semantic_use"] = "NOT_PROVEN_BY_REFERENCE"

    rows = []
    for task, definition in tasks.items():
        outputs = [row for row in artifacts if row["task_id"] == task]
        deterministic = [row for row in outputs if row["deterministic_receipt"]]
        state = ("RESPONSE_RECORDED" if by_task[task] else
                 "DETERMINISTIC_RECEIPT_RECORDED_WITHOUT_LLM" if deterministic else
                 "ARTIFACT_ONLY_NO_RESPONSE_EVIDENCE" if outputs else "NO_RUN_EVIDENCE")
        rows.append({"task_id": task, "goal": definition["primary_goal"],
                     "call_unit": definition["call_unit"], "coverage": state,
                     "response_count": len(by_task[task]),
                     "recorded_tokens": sum(row["usage"].get("total_tokens", 0) for row in by_task[task]),
                     "stored_artifact_count": len(outputs),
                     "deterministic_receipt_count": len(deterministic),
                     "artifacts_with_downstream_request_reference": sum(
                         bool(row["downstream_request_references"]) for row in outputs),
                     "artifacts_with_downstream_output_reference": sum(
                         bool(row["downstream_output_explicit_references"]) for row in outputs),
                     "expected_consumer_or_terminal": life[task]["expected_consumer_or_terminal"],
                     "skip_precondition": life[task]["skip_precondition"],
                     "applicability": "NOT_INFERRED_FROM_ABSENCE",
                     "end_to_end_functionality": "NOT_PROVEN_BY_THIS_AUDIT"})
    return {"summary": {"registered_tasks": len(tasks),
                        "tasks_with_response": sum(bool(rows) for rows in by_task.values()),
                        "recorded_calls": len(calls),
                        "recorded_tokens": sum(row["usage"].get("total_tokens", 0) for row in calls),
                        "coverage_states": dict(Counter(row["coverage"] for row in rows))},
            "tasks": rows, "artifacts": artifacts, "evidence_gaps": gaps}


def boundaries_module():
    spec = importlib.util.spec_from_file_location("readiness_boundaries", ROOT / "tools/writer_boundaries.py")
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def revision_queue(work, sources):
    needed = ("setup.json", "chapters-r2.json", "writers-r2.json")
    missing = [name for name in needed if not (work / name).exists()]
    if missing:
        return {"status": "INSUFFICIENT_BOUNDARY_EVIDENCE", "missing": missing, "items": []}
    plans = sources.json(work / "chapters-r2.json")["chapters"]
    generated = sources.json(work / "writers-r2.json")["writers"]
    sources.json(work / "setup.json")
    for plan in plans:
        for item in plan["dependencies"]["items"]:
            relative = plan["source_files"][item["source_id"]]
            path = (work / relative).resolve()
            if not path.is_relative_to(work.resolve()):
                raise ValueError("DEPENDENCY_OUTSIDE_RUN")
            if digest(sources.raw(path)) != item["sha256"]:
                raise ValueError("DEPENDENCY_HASH_CHANGED:" + item["source_id"])
        approval = plan["chapter_approval_source"]
        path = (work / approval["path"]).resolve()
        if not path.is_relative_to(work.resolve()):
            raise ValueError("APPROVAL_OUTSIDE_RUN")
        receipt = sources.json(path)
        if sources.hashes[path] != approval["sha256"]:
            raise ValueError("APPROVAL_HASH_CHANGED")
        for row in receipt["artifacts"]:
            frozen = (path.parent / row["frozen_path"]).resolve()
            if not frozen.is_relative_to(work.resolve()):
                raise ValueError("FROZEN_ARTIFACT_OUTSIDE_RUN")
            if digest(sources.raw(frozen)) != row["sha256"]:
                raise ValueError("FROZEN_ARTIFACT_HASH_CHANGED")
    audit = boundaries_module().audit_run(work)["audit"]
    items = []
    for finding in audit["findings"]:
        number = finding["chapter"]
        index = next(i for i, row in enumerate(generated) if row["chapter"] == number)
        affected = sorted({number, finding.get("previous", {}).get("chapter", number)})
        design = finding["code"].startswith("FROZEN_TIME_")
        bindings = [sources.binding(work / "chapters-r2.json"),
                    sources.binding(work / "writers-r2.json"), sources.binding(work / "setup.json")]
        if design:
            refs = [finding["source_ref"]]
            if "source_ref" in finding.get("previous", {}):
                refs.append(finding["previous"]["source_ref"])
            for plan in plans:
                for ref in refs:
                    if ref in plan["source_files"]:
                        bindings.append(sources.binding(work / plan["source_files"][ref]))
        steps = (["P08_NEW_VERSION", "TIME_INTERFACE_REVIEW", "CHAPTER_DEPENDENCY_REAPPROVAL",
                  "NEW_FREEZE", "P17C", "P18", "P19H", "GLOBAL_TIME_AUDIT"] if design else
                 ["VERIFY_APPROVED_EVENT_AND_POV_SOURCE", "DESIGN_REVISION_IF_NEEDED",
                  "REAPPROVE_CHANGED_DEPENDENCIES", "P17C", "P18_NEW_VERSION", "P19D2",
                  "GLOBAL_TIME_AUDIT"])
        items.append({"issue_id": "REV-" + digest(json.dumps(finding, ensure_ascii=False, sort_keys=True)
                                                 .encode("utf-8"))[:16],
                      "kind": "DESIGN_TIME" if design else
                      "FACT_CANDIDATE" if finding["decision"] == "REVIEW_REQUIRED" else "PROSE_TIME",
                      "state": "PENDING_REVIEW", "diagnostic_decision": finding["decision"],
                      "review_affected_chapters": affected, "evidence": finding,
                      "source_bindings": bindings, "writer_pointer": f"/writers/{index}/content",
                      "required_steps": steps, "approved_replacement": None,
                      "active_plan_changed": False})
    reviews_path = work / "reviews-r2.json"
    review_scope = None
    if reviews_path.exists():
        reviews = sources.json(reviews_path)
        review_scope = reviews.get("scope")
        for index, row in enumerate(reviews["reviews"]):
            data = row.get("content", row)
            task = row["task_id"]
            if task == "P19G" and data.get("main_obstacle"):
                reason, kind = data["main_obstacle"], "LITERARY_REVIEW"
            elif task == "P19D2" and data.get("decision") == "PARTIAL":
                reason, kind = data, "EVENT_FIDELITY_REVIEW"
            else:
                continue
            items.append({"issue_id": "REVIEW-" + row["artifact_id"], "kind": kind,
                          "state": "PENDING_REVIEW", "diagnostic_decision": "MODEL_REVIEW_NOT_APPROVAL",
                          "review_artifact_id": row["artifact_id"], "evidence": reason,
                          "source_bindings": [sources.binding(reviews_path)],
                          "review_pointer": f"/reviews/{index}", "approved_replacement": None,
                          "required_steps": ["CHECK_REVIEW_AGAINST_PROSE_AND_EVENTS",
                                             "BOUNDED_NEW_VERSION_REVISION", task, "INDEPENDENT_REVIEW"],
                          "active_plan_changed": False})
    return {"status": "PENDING_REVIEW" if items else "NO_DETECTED_FINDINGS_NOT_QUALITY_APPROVAL",
            "boundary_decision": audit["decision"], "boundary_findings": len(audit["findings"]),
            "review_scope": review_scope, "items": items,
            "graph_invalidation": "HOST_MUST_REVIEW_TRANSITIVE_DEPENDENCIES",
            "writer_neighbor_text_consumed": False}


def analyze_run(work):
    work = work.resolve()
    sources = Sources(work)
    sources.raw(Path(__file__))
    sources.raw(ROOT / "tools/writer_boundaries.py")
    coverage = task_coverage(work, sources)
    queue = revision_queue(work, sources)
    sources.verify()
    return {"mode": "OFFLINE_NO_API", "run_root": str(work), "task_coverage": coverage,
            "revision_queue": queue, "quality_approval": "NOT_GRANTED",
            "source_manifest": [sources.binding(path) for path in sorted(sources.hashes)],
            "source_verification": "ALL_READ_SOURCES_UNCHANGED",
            "additional_paid_calls": 0, "active_plan_changed": False,
            "limits": ["Response, stored artifact, input inclusion and output reference are separate evidence.",
                       "No missing task is automatically classified as skipped or not applicable.",
                       "Stored DRAFT/validated-path artifacts are not automatically approved.",
                       "Explicit output references do not prove semantic use or all-function correctness.",
                       "Revision items are pending work, not approved edits or a completed repair loop.",
                       "No batch execution, independent literary review or long-book run was performed."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    output = args.out.resolve()
    if output.is_relative_to(args.work_dir.resolve()):
        raise ValueError("AUDIT_OUTPUT_MUST_BE_OUTSIDE_RUN")
    if output.exists():
        raise FileExistsError("IMMUTABLE_AUDIT_OUTPUT_EXISTS")
    report = analyze_run(args.work_dir)
    with output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"mode": report["mode"], **report["task_coverage"]["summary"],
                      "revision_items": len(report["revision_queue"]["items"]),
                      "quality_approval": report["quality_approval"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
