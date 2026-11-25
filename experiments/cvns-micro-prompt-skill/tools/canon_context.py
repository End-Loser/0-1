#!/usr/bin/env python3
"""Compile source-bound CanonIndex context and validate P16 proposals, read-only."""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
from functools import cache
from collections import deque
from pathlib import Path
from types import SimpleNamespace

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]


@cache
def lifecycle_module():
    spec = importlib.util.spec_from_file_location(
        "line_lifecycle", Path(__file__).with_name("line_lifecycle.py"))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def object_sha(value):
    return sha(json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False).encode("utf-8"))


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def text(value):
    return isinstance(value, str) and bool(value.strip())


def ids(value, reason):
    require(isinstance(value, list) and all(text(x) for x in value)
            and len(value) == len(set(value)), reason)
    return value


def schema_errors(value, name):
    schema = json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
    if name == "DERIVED_LINE_SCHEMA.json":
        base = json.loads((ROOT / "schemas/BASE_LINE_SCHEMA.json").read_text(encoding="utf-8"))
        schema["properties"]["nodes"]["items"] = base["$defs"]["TimelineNode"]
    return [f"SCHEMA:{name}:{'/'.join(map(str, error.path))}:{error.message}"
            for error in Draft202012Validator(schema).iter_errors(value)]


def line_errors(line, derived=True):
    errors = schema_errors(line, "DERIVED_LINE_SCHEMA.json" if derived else "BASE_LINE_SCHEMA.json")
    if errors:
        return errors
    for field in ("id", "dimension", "scope" if derived else "entity_id"):
        if not text(line.get(field)):
            errors.append("LINE_IDENTITY_INVALID:" + field)
    start, end = line["valid_from"], line.get("valid_to")
    lifecycle = line.get("lifecycle") if derived else None
    if type(start) is not int or (end is not None and
            (type(end) is not int or end < start or (derived and end == start and not lifecycle))):
        errors.append("LINE_INTERVAL_INVALID")
        return errors
    nodes = line["nodes" if derived else "change_nodes"]
    if nodes[0]["chapter"] != start:
        errors.append("LINE_OPENING_NODE_MISSING")
    previous = None
    for node in nodes:
        chapter, seq = node["chapter"], node.get("seq", 0)
        if type(chapter) is not int or type(seq) is not int:
            errors.append("LINE_POSITION_INVALID")
            continue
        position = (chapter, seq)
        if previous is not None and position <= previous:
            errors.append("LINE_NODE_ORDER_INVALID")
        previous = position
        if chapter < start or (end is not None and chapter > end):
            errors.append("LINE_NODE_OUTSIDE_INTERVAL")
    if derived:
        for field in ("subject_ref", "predecessor_line_id"):
            if field in line and not text(line[field]):
                errors.append("LINE_IDENTITY_INVALID:" + field)
        if lifecycle:
            boundary = (lifecycle["effective_chapter"], lifecycle["effective_seq"])
            if (not text(lifecycle["reason"]) or end != boundary[0]
                    or any((node["chapter"], node.get("seq", 0)) >= boundary for node in nodes)
                    or (lifecycle["status"] == "terminated") != (lifecycle["successor_line_id"] is None)):
                errors.append("LINE_LIFECYCLE_BOUNDARY_INVALID")
        if not text(line["cause"].get("ref")) or not text(line["cause"].get("note")):
            errors.append("DERIVED_CAUSE_EVIDENCE_REQUIRED")
        if not any(isinstance(v, dict) and text(v.get("ref"))
                   for v in (line.get("purpose", {}).get(key) for key in ("setup", "reveal", "payoff"))):
            errors.append("DERIVED_PURPOSE_REQUIRED")
    return errors


def ack_errors(ack, line_ids):
    errors = schema_errors(ack, "ACK_NODE_SCHEMA.json")
    if errors:
        return errors
    if not text(ack["id"]) or not text(ack.get("ack_ref")):
        errors.append("ACK_ID_OR_PLAN_REF_REQUIRED")
    if ack.get("status") not in ("pre-registered", "executed", "cancelled"):
        errors.append("ACK_EXPLICIT_STATUS_REQUIRED")
    if (not isinstance(ack.get("obligations"), list) or not ack["obligations"]
            or any(not text(x) for x in ack["obligations"])):
        errors.append("ACK_OBLIGATIONS_REQUIRED")
    related = ack.get("related_lines", [])
    if (not isinstance(related, list) or any(not text(x) for x in related)
            or len(related) != len(set(related))):
        errors.append("ACK_RELATED_LINES_INVALID")
    elif not set(related).issubset(line_ids):
        errors.append("ACK_UNREGISTERED_LINE")
    if type(ack["target_chapter"]) is not int:
        errors.append("ACK_CHAPTER_INVALID")
    return errors


def retrieve(canon, base_lines, chapter, mode, entity_ids=(), required_line_ids=(), budget_lines=None):
    """Return whole relevance clusters; never compress nodes or authorize POV knowledge."""
    require(type(chapter) is int and chapter > 0, "CANON_CHAPTER_INVALID")
    require(mode in ("generator", "writer", "audit"), "CANON_MODE_INVALID")
    entities = ids(list(entity_ids), "CANON_ENTITIES_INVALID")
    required = ids(list(required_line_ids), "CANON_REQUIRED_LINES_INVALID")
    require(budget_lines is None or (type(budget_lines) is int and budget_lines > 0),
            "CANON_BUDGET_INVALID")
    require(isinstance(canon, dict) and isinstance(canon.get("derived_lines"), list)
            and isinstance(canon.get("ack_nodes"), list) and isinstance(base_lines, list),
            "CANON_COLLECTIONS_REQUIRED")
    derived = canon["derived_lines"]
    all_lines = {}
    for line, is_derived in [(line, False) for line in base_lines] + [(line, True) for line in derived]:
        errors = line_errors(line, is_derived)
        require(not errors, "CANON_INVALID_LINE:" + ";".join(errors))
        require(line["id"] not in all_lines, "CANON_DUPLICATE_LINE_ID")
        all_lines[line["id"]] = line
    lifecycle_module().validate_history(derived, SimpleNamespace(require=require, text=text))
    ack_ids = set()
    for ack in canon["ack_nodes"]:
        errors = ack_errors(ack, all_lines)
        require(not errors, "CANON_INVALID_ACK:" + ";".join(errors))
        require(ack["id"] not in ack_ids, "CANON_DUPLICATE_ACK_ID")
        ack_ids.add(ack["id"])
    require(set(required).issubset(all_lines), "CANON_REQUIRED_LINE_NOT_REGISTERED")
    require(all(all_lines[ident]["valid_from"] <= chapter for ident in required),
            "CANON_FUTURE_REQUIRED_LINE")
    pending = [ack for ack in canon["ack_nodes"]
               if ack["status"] == "pre-registered" and ack["target_chapter"] <= chapter]
    protected = set(required)
    protected.update(ident for ack in pending for ident in ack.get("related_lines", []))
    for line in derived:
        end = line.get("valid_to")
        if (line["valid_from"] <= chapter and (mode != "writer" or end is None or end >= chapter)
                and (line["scope"] == "world" or line["scope"] in entities
                     or line.get("lifecycle", {}).get("effective_chapter") == chapter)):
            protected.add(line["id"])
    queue = deque(protected)
    while queue:
        line = all_lines[queue.popleft()]
        if line.get("layer") != "DERIVED":
            continue
        successor = line.get("lifecycle", {}).get("successor_line_id")
        predecessor = line.get("predecessor_line_id")
        linked = [successor] if successor is not None else []
        if predecessor is not None and all_lines[predecessor].get("valid_to") == chapter:
            linked.append(predecessor)
        for ident in linked:
            if ident not in protected and all_lines[ident]["valid_from"] <= chapter:
                protected.add(ident)
                queue.append(ident)
    require(all(all_lines[ident]["valid_from"] <= chapter for ident in protected),
            "CANON_ACK_REFERENCES_UNBORN_LINE")
    groups = {}
    excluded = []
    for line in derived:
        ident, end = line["id"], line.get("valid_to")
        if mode != "audit" and line["valid_from"] > chapter:
            excluded.append({"line_id": ident, "reason": "NOT_YET_VALID"})
            continue
        if mode == "writer" and end is not None and end < chapter and ident not in protected:
            excluded.append({"line_id": ident, "reason": "HISTORICAL_NOT_REQUIRED"})
            continue
        groups.setdefault((line["scope"], line["dimension"]), []).append(line)
    mandatory, optional = [], []
    for key, cluster in sorted(groups.items()):
        if (mode == "audit" or key[0] == "world" or key[0] in entities
                or any(line["id"] in protected for line in cluster)):
            mandatory.append((key, cluster))
        elif mode == "writer":
            optional.append((key, cluster))
        else:
            excluded.extend({"line_id": line["id"], "reason": "NO_RELEVANCE"} for line in cluster)
    selected, trimmed = mandatory + optional, []
    if budget_lines is not None:
        total = sum(len(cluster) for _, cluster in selected)
        for group in reversed(optional):
            if total <= budget_lines:
                break
            selected.remove(group)
            key, cluster = group
            total -= len(cluster)
            trimmed.append({"scope": key[0], "dimension": key[1],
                            "line_ids": [line["id"] for line in cluster], "reason": "OPTIONAL_CLUSTER_BUDGET"})
    count = sum(len(cluster) for _, cluster in selected)
    over_budget = budget_lines is not None and count > budget_lines
    selected_lines = derived if mode == "audit" else [line for _, cluster in selected for line in cluster]
    context = {"derived_lines": copy.deepcopy(selected_lines),
               "ack_nodes": copy.deepcopy(canon["ack_nodes"] if mode == "audit" else pending),
               "base_lines": copy.deepcopy(base_lines),
               "registered_line_ids": sorted(all_lines), "registered_ack_ids": sorted(ack_ids)}
    if any("lifecycle" in line for line in selected_lines):
        context["line_lifecycle_context"] = {
            "closed_before_chapter": [line["id"] for line in selected_lines
                                      if line.get("valid_to") is not None and line["valid_to"] < chapter],
            "closing_in_chapter": [line["id"] for line in selected_lines
                                   if line.get("lifecycle", {}).get("effective_chapter") == chapter],
            "policy": "WHOLE_HISTORY_WITH_SEQ_BOUNDARIES_NOT_SIMULTANEOUS_ACTIVE_STATES",
            "ack_policy": "PENDING_OBLIGATIONS_PRESERVED_NOT_AUTOMATICALLY_FULFILLED",
        }
    return {"decision": "BLOCKED" if over_budget else "CANON_CONTEXT_COMPILED",
            "reason": "CANON_REQUIRED_CLUSTER_OVER_BUDGET" if over_budget else "SOURCE_PROJECTION_ONLY",
            "model_context": context, "context_object_sha256": object_sha(context),
            "selected_line_ids": [line["id"] for line in selected_lines],
            "pending_ack_ids": [ack["id"] for ack in pending],
            "overdue_ack_ids": [ack["id"] for ack in pending if ack["target_chapter"] < chapter],
            "trimmed_clusters": trimmed, "excluded_lines": excluded,
            "budget_unit": "DERIVED_LINE_COUNT_ONLY_NOT_TOKENS",
            "knowledge_permission": "DESIGN_CONTEXT_ONLY_NOT_POV_KNOWLEDGE",
            "base_policy": "COMPLETE_INPUT_LINES_PRESERVED_NO_TRIMMING"}


def compile_manifest(path, expected_hash):
    """Use the same host-controlled evidence root and live index as the Writer gate."""
    spec = importlib.util.spec_from_file_location("canon_dependency",
                                                Path(__file__).with_name("writer_dependency_gate.py"))
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    path = Path(path).resolve()
    base, snapshots = path.parent, {}
    manifest, _ = gate.evidence(base, {"path": path.name, "sha256": expected_hash}, snapshots)
    require(isinstance(manifest, dict), "CANON_MANIFEST_INVALID")
    errors = schema_errors(manifest, "CANON_CONTEXT_MANIFEST_SCHEMA.json")
    require(not errors, "CANON_MANIFEST_SCHEMA_INVALID:" + ";".join(errors))
    index_binding = manifest.get("current_index", {})
    require(isinstance(index_binding, dict) and text(index_binding.get("path")), "CANON_CURRENT_INDEX_REQUIRED")
    index_path = (base / index_binding["path"]).resolve()
    require(index_path.is_relative_to(base), "CANON_INDEX_OUTSIDE_ROOT")
    raw = index_path.read_bytes()
    index_hash = sha(raw)
    index, _ = gate.evidence(base, {"path": index_binding["path"], "sha256": index_hash}, snapshots)
    require(isinstance(index, dict) and index.get("project_id") == manifest.get("project_id")
            and text(manifest.get("project_id")) and text(manifest.get("version"))
            and isinstance(index.get("artifacts"), dict), "CANON_SCOPE_INVALID")
    source_ids, source_hashes = [], {}

    def load(binding, outline=False):
        data, digest = gate.evidence(base, binding, snapshots)
        require(isinstance(data, dict) and text(data.get("artifact_id"))
                and data.get("artifact_id") == binding.get("artifact_id")
                and text(data.get("version")) and data.get("version") == binding.get("version")
                and data.get("project_id") == manifest["project_id"]
                and data.get("status") == "FROZEN", "CANON_SOURCE_ID_VERSION_OR_STATUS_INVALID")
        ident = data["artifact_id"]
        require(ident not in source_hashes, "CANON_DUPLICATE_SOURCE_ID")
        active = index["artifacts"].get(ident)
        require(isinstance(active, dict) and active.get("version") == data["version"]
                and active.get("sha256") == digest and active.get("status") == "FROZEN",
                "CANON_CURRENT_INDEX_STALE")
        source_ids.append(ident)
        source_hashes[ident] = digest
        if outline:
            return data
        require(isinstance(data.get("content"), dict), "CANON_CONTENT_ENVELOPE_REQUIRED")
        return data["content"]

    canon = load(manifest.get("canon"))
    bases = manifest.get("base_lines", [])
    require(isinstance(bases, list), "CANON_BASE_BINDINGS_INVALID")
    base_lines = [load(binding) for binding in bases]
    result = retrieve(canon, base_lines, manifest.get("chapter"), manifest.get("mode"),
                      manifest.get("entity_ids", []), manifest.get("required_line_ids", []),
                      manifest.get("budget_lines"))
    outline = load(manifest["outline"], outline=True) if "outline" in manifest else None
    if outline is not None:
        value = outline.get("content", outline.get("payload", outline))
        require(isinstance(value, dict) and value.get("chapter") == manifest["chapter"]
                and type(value.get("chapter")) is int, "CANON_OUTLINE_CHAPTER_MISMATCH")
    for source, before in snapshots.items():
        require(sha(source.read_bytes()) == before, "CANON_SOURCE_CHANGED_DURING_CHECK")
    return {**result, "project_id": manifest["project_id"], "version": manifest["version"],
            "chapter": manifest["chapter"], "mode": manifest["mode"], "manifest_sha256": expected_hash,
            "source_artifact_ids": source_ids, "source_hashes": source_hashes,
            "outline_object_sha256": object_sha(outline) if outline is not None else None,
            "current_index_sha256": index_hash}


def plan_refs(inputs):
    """Only return domain references actually present in the approved outline."""
    outline = inputs.get("chapter_outline")
    require(isinstance(outline, dict) and outline.get("status") == "FROZEN",
            "P16_FROZEN_OUTLINE_REQUIRED")
    content = outline.get("content", outline.get("payload", outline))
    require(isinstance(content, dict) and type(content.get("chapter")) is int
            and content.get("chapter") == inputs.get("chapter")
            and type(inputs.get("chapter")) is int, "P16_OUTLINE_CHAPTER_MISMATCH")
    beats = content.get("beats", [])
    events = content.get("event_records", [])
    assets = content.get("active_assets", [])
    require(all(isinstance(rows, list) and all(isinstance(row, dict) for row in rows)
                for rows in (beats, events, assets)), "P16_PLAN_REFERENCES_INVALID")
    require(all("BEAT-PLACEHOLDER" not in str(row.get("beat_id", "")) for row in beats)
            and "ARTIFACT-ID-PENDING" not in str(outline.get("artifact_id", "")),
            "P16_STABLE_PLAN_IDS_REQUIRED")
    context = inputs.get("canon_context")
    require(isinstance(context, dict) and isinstance(context.get("base_lines"), list)
            and isinstance(context.get("derived_lines"), list), "P16_CANON_CONTEXT_REQUIRED")
    lines = context["base_lines"] + context["derived_lines"]
    require(all(isinstance(line, dict) and text(line.get("id")) for line in lines),
            "P16_LINE_REFERENCES_INVALID")
    registered = ids(context.get("registered_line_ids"), "P16_FULL_LINE_REGISTRY_REQUIRED")
    ids(context.get("registered_ack_ids"), "P16_FULL_ACK_REGISTRY_REQUIRED")
    require({line["id"] for line in lines}.issubset(registered), "P16_LINE_REGISTRY_MISMATCH")
    return {"event": {row["id"] for row in events if text(row.get("id"))
                      and row.get("chapter") == inputs["chapter"]},
            "outline_beat": {row["beat_id"] for row in beats if text(row.get("beat_id"))},
            "asset": {row.get("asset_id", row.get("id")) for row in assets
                      if text(row.get("asset_id", row.get("id")))},
            "line": {line["id"] for line in lines}}


def validate_p16(task_id, candidate, inputs):
    """Structural checks return gaps, not semantic approval or index mutations."""
    try:
        refs = plan_refs(inputs)
        require(isinstance(candidate, dict), "P16_CANDIDATE_INVALID")
        require("task_id" not in candidate or candidate["task_id"] == task_id,
                "P16_CANDIDATE_TASK_MISMATCH")
        value = candidate.get("content", candidate.get("payload", candidate))
        require(isinstance(value, dict), "P16_CANDIDATE_CONTENT_INVALID")
        chapter = inputs["chapter"]
        if task_id == "P16A":
            if value.get("decision") == "NO_DERIVED":
                return [] if not value.get("nodes") and not value.get("id") else ["NO_DERIVED_WITH_LINE"]
            if "lifecycle" in value or "predecessor_line_id" in value:
                return ["DERIVED_LIFECYCLE_REQUIRES_P16C_TRANSACTION"]
            errors = line_errors(value)
            if errors:
                return errors
            if value["scope"] != inputs.get("entity_id") or value["dimension"] != inputs.get("dimension"):
                errors.append("DERIVED_TARGET_MISMATCH")
            if value["id"] in inputs["canon_context"]["registered_line_ids"]:
                errors.append("DERIVED_ID_ALREADY_REGISTERED")
            if value["valid_from"] != chapter or any(node["chapter"] != chapter for node in value["nodes"]):
                errors.append("DERIVED_NOT_CURRENT_CHAPTER")
            cause = value["cause"]
            if cause["ref"] not in refs.get(cause["kind"], set()):
                errors.append("DERIVED_CAUSE_NOT_IN_APPROVED_PLAN")
            for purpose in (value.get("purpose", {}).get(key) for key in ("setup", "reveal", "payoff")):
                if isinstance(purpose, dict) and purpose.get("ref") not in set().union(*refs.values()):
                    errors.append("DERIVED_PURPOSE_UNREGISTERED_REF")
            if any(node.get("causal_status") != "proposed" for node in value["nodes"]):
                errors.append("DERIVED_PLAN_NOT_PROPOSED")
            return errors
        if task_id == "P16B":
            errors = ack_errors(value, refs["line"])
            if errors:
                return errors
            if value["status"] != "pre-registered":
                errors.append("ACK_REGISTRATION_CANNOT_EXECUTE_OR_CANCEL")
            if len(value["obligations"]) != 1:
                errors.append("ACK_ONE_OBLIGATION_REQUIRED")
            selected = inputs.get("obligation_candidate", {})
            if (not isinstance(selected, dict) or value["obligations"] != [selected.get("obligation")]
                    or value["ack_ref"] != selected.get("source_ref")):
                errors.append("ACK_SELECTED_OBLIGATION_MISMATCH")
            if value["target_chapter"] != chapter:
                errors.append("ACK_TARGET_REQUIRES_APPROVED_TARGET_OUTLINE")
            if value["ack_ref"] not in refs["event"] | refs["outline_beat"]:
                errors.append("ACK_REF_NOT_IN_APPROVED_PLAN")
            if value["id"] in inputs["canon_context"]["registered_ack_ids"]:
                errors.append("ACK_ID_ALREADY_REGISTERED")
            return errors
        if task_id == "P16C":
            required = ("line_id", "decision", "cause_ref", "previous_status", "new_status", "reason")
            errors = ["LIFECYCLE_FIELD_REQUIRED:" + key for key in required if not text(value.get(key))]
            if value.get("line_id") != inputs.get("line_id") or value.get("line_id") not in refs["line"]:
                errors.append("LIFECYCLE_LINE_NOT_REGISTERED_OR_TARGET_MISMATCH")
            if type(value.get("effective_chapter")) is not int or value["effective_chapter"] != chapter:
                errors.append("LIFECYCLE_EFFECTIVE_CHAPTER_MISMATCH")
            if value.get("cause_ref") not in refs["event"] | refs["outline_beat"]:
                errors.append("LIFECYCLE_CAUSE_NOT_IN_APPROVED_PLAN")
            deps = value.get("dependency_refs")
            if (not isinstance(deps, list)
                    or any(not text(ref) or ref not in set().union(*refs.values()) for ref in deps)
                    or len(deps) != len(set(deps))
                    or not {value.get("line_id"), value.get("cause_ref")}.issubset(deps)):
                errors.append("LIFECYCLE_DEPENDENCY_REF_INVALID")
            if not errors:
                lifecycle_module().transition(value, inputs, SimpleNamespace(
                    schema_errors=schema_errors, require=require, line_errors=line_errors,
                    plan_refs=plan_refs, object_sha=object_sha, text=text))
            return errors
        return ["UNSUPPORTED_P16_TASK"]
    except (ValueError, TypeError, KeyError) as error:
        return [str(error)]


def validate_ack_fulfillment(ack, chapter_text, review, ship_receipt):
    """Require source-bound post-ship evidence; never mark the ACK executed here."""
    errors = []
    if (not isinstance(ack, dict) or ack.get("status") != "pre-registered"
            or not isinstance(chapter_text, str) or not chapter_text.strip()
            or not isinstance(review, dict) or not isinstance(ship_receipt, dict)):
        return ["ACK_FULFILLMENT_INPUT_INVALID"]
    if (schema_errors(ack, "ACK_NODE_SCHEMA.json") or type(ack.get("target_chapter")) is not int
            or not text(ack.get("id")) or not text(ack.get("ack_ref"))):
        return ["ACK_FULFILLMENT_ACK_INVALID"]
    digest = sha(chapter_text.encode("utf-8"))
    if (ship_receipt.get("decision") != "SHIPPED" or ship_receipt.get("chapter") != ack.get("target_chapter")
            or type(ship_receipt.get("chapter")) is not int
            or ship_receipt.get("text_sha256") != digest
            or ship_receipt.get("ack_review_object_sha256") != object_sha(review)):
        errors.append("ACK_CHAPTER_NOT_SHIPPED_WITH_CURRENT_TEXT")
    if (review.get("decision") != "PASS" or review.get("status") not in ("VALIDATED", "FROZEN")
            or type(review.get("chapter")) is not int
            or review.get("ack_object_sha256") != object_sha(ack)
            or review.get("text_sha256") != digest
            or review.get("blocking_findings") != []
            or review.get("chapter") != ack.get("target_chapter")):
        errors.append("ACK_FULFILLMENT_REVIEW_NOT_CURRENT")
    obligations = ack.get("obligations")
    evidence = review.get("obligation_evidence")
    if (not isinstance(obligations, list) or not obligations or any(not text(x) for x in obligations)
            or not isinstance(evidence, list) or len(evidence) != len(obligations)):
        return errors + ["ACK_OBLIGATION_EVIDENCE_INCOMPLETE"]
    for index, row in enumerate(evidence):
        if not isinstance(row, dict):
            errors.append("ACK_OBLIGATION_EVIDENCE_INVALID")
            continue
        start, end = row.get("start"), row.get("end")
        if (row.get("obligation") != obligations[index] or row.get("decision") != "PASS"
                or type(start) is not int or type(end) is not int
                or not (0 <= start < end <= len(chapter_text))
                or chapter_text[start:end] != row.get("quote") or not text(row.get("basis"))):
            errors.append("ACK_OBLIGATION_EVIDENCE_INVALID")
    return errors


def main():
    ap = argparse.ArgumentParser()
    source = ap.add_mutually_exclusive_group(required=True)
    source.add_argument("--manifest")
    source.add_argument("--input-pack")
    ap.add_argument("--sha256", required=True)
    ap.add_argument("--candidate")
    ap.add_argument("--task-id", choices=("P16A", "P16B", "P16C"))
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    try:
        if args.manifest:
            require(not args.candidate and not args.task_id, "CANON_RETRIEVAL_HAS_NO_CANDIDATE")
            result = compile_manifest(args.manifest, args.sha256)
        else:
            require(args.candidate and args.task_id, "P16_CANDIDATE_AND_TASK_REQUIRED")
            raw = Path(args.input_pack).read_bytes()
            require(sha(raw) == args.sha256, "P16_INPUT_PACK_HASH_CHANGED")
            pack = json.loads(raw.decode("utf-8-sig"))
            require(isinstance(pack, dict), "P16_INPUT_PACK_INVALID")
            binding = pack.get("canon_context_gate")
            require(isinstance(binding, dict), "CANON_CONTEXT_GATE_REQUIRED")
            receipt = compile_manifest(binding["path"], binding["sha256"])
            inputs = pack.get("inputs")
            require(isinstance(inputs, dict) and receipt["decision"] != "BLOCKED"
                    and receipt["mode"] == "generator" and receipt["project_id"] == pack.get("project_id")
                    and receipt["version"] == pack.get("version") and receipt["chapter"] == inputs.get("chapter"),
                    "P16_CONTEXT_SCOPE_OR_BUDGET_MISMATCH")
            require(object_sha(inputs.get("canon_context")) == receipt["context_object_sha256"]
                    and receipt["outline_object_sha256"]
                    and object_sha(inputs.get("chapter_outline")) == receipt["outline_object_sha256"],
                    "P16_RUNTIME_CONTEXT_SUBSTITUTION")
            candidate_raw = Path(args.candidate).read_bytes()
            candidate = json.loads(candidate_raw.decode("utf-8-sig"))
            errors = validate_p16(args.task_id, candidate, inputs)
            result = {"decision": "BLOCKED" if errors else "P16_STRUCTURE_VALIDATED", "errors": errors,
                      "input_pack_sha256": args.sha256, "candidate_sha256": sha(candidate_raw),
                      "source_hashes": receipt["source_hashes"], "activation": "NOT_GRANTED"}
        with Path(args.out).open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(result["decision"])
        return 1 if result["decision"] == "BLOCKED" else 0
    except (OSError, ValueError, TypeError, KeyError) as error:
        print("BLOCKED:" + str(error))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
