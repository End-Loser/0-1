#!/usr/bin/env python3
"""Host-controlled Canon mutations with immutable snapshots and atomic invalidation."""
from __future__ import annotations

import argparse
from collections import deque
from contextlib import contextmanager
import copy
import importlib.util
import json
import os
from pathlib import Path
import re
import tempfile


def module(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + ".py"))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


C = module("canon_context")
G = module("writer_dependency_gate")
L = module("line_lifecycle")


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


@contextmanager
def index_lock(root):
    """Kernel-owned locks are released by process death; never delete a stale lockfile."""
    stream = (root / ".index.lock").open("a+b")
    locked = False
    try:
        if os.fstat(stream.fileno()).st_size == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as error:
            raise ValueError("INDEX_LOCK_BUSY") from error
        yield
    finally:
        if locked:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()


def fsync_directory(path):
    # Windows has no portable directory fsync; process recovery is not a power-loss guarantee.
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def publish_file(path, raw, replace=False):
    """Flush before publication; hard-link new immutable files without overwriting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".stage-", dir=path.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            try:
                os.link(temporary, path)
            except FileExistsError:
                require(path.read_bytes() == raw, "IMMUTABLE_TRANSACTION_FILE_CONFLICT")
        fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def graph(index):
    require(isinstance(index, dict) and C.text(index.get("project_id"))
            and type(index.get("generation")) is int and index["generation"] >= 0
            and isinstance(index.get("artifacts"), dict)
            and index.get("dependency_graph_complete") is True
            and isinstance(index.get("dependencies"), dict)
            and isinstance(index.get("transactions"), dict), "TRANSACTION_INDEX_SCHEMA_INVALID")
    entries, edges = index["artifacts"], index["dependencies"]
    require(set(edges) == set(entries), "TRANSACTION_GRAPH_INCOMPLETE")
    reverse = {ident: [] for ident in entries}
    indegree = {}
    for ident, row in entries.items():
        require(C.text(ident) and isinstance(row, dict) and C.text(row.get("version"))
                and isinstance(row.get("sha256"), str)
                and re.fullmatch(r"[0-9a-f]{64}", row["sha256"])
                and row.get("status") in ("VALIDATED", "FROZEN", "INVALIDATED"),
                "TRANSACTION_INDEX_ENTRY_INVALID")
        deps = G.identifiers(edges[ident], "TRANSACTION_GRAPH_EDGES_INVALID")
        require(set(deps).issubset(entries), "TRANSACTION_GRAPH_UNKNOWN_SOURCE")
        indegree[ident] = len(deps)
        for source in deps:
            reverse[source].append(ident)
    queue = deque(ident for ident, degree in indegree.items() if degree == 0)
    count = 0
    while queue:
        count += 1
        for consumer in reverse[queue.popleft()]:
            indegree[consumer] -= 1
            if indegree[consumer] == 0:
                queue.append(consumer)
    require(count == len(entries), "TRANSACTION_GRAPH_CYCLE")
    return reverse


def read_request(root, path, expected_hash):
    snapshots = {}
    request, _ = G.evidence(root, {"path": str(path), "sha256": expected_hash}, snapshots)
    require(isinstance(request, dict), "TRANSACTION_REQUEST_INVALID")
    errors = C.schema_errors(request, "INDEX_TRANSACTION_SCHEMA.json")
    require(not errors, "TRANSACTION_REQUEST_SCHEMA_INVALID:" + ";".join(errors))
    return request, snapshots


def subject(request):
    return C.object_sha({key: value for key, value in request.items() if key != "approval"})


def read_index(root):
    path = root / "index.json"
    raw = path.read_bytes()
    index = json.loads(raw.decode("utf-8-sig"))
    graph(index)
    return index, C.sha(raw)


def proposal_sources(root, request, snapshots, index, canon, base_lines):
    """Bind P16 proposals to current sources without creating a live Canon/outline cycle."""
    binding = request["registration_context"]
    manifest, _ = G.evidence(root, binding, snapshots)
    require(isinstance(manifest, dict), "REGISTRATION_MANIFEST_INVALID")
    manifest_path = (root / binding["path"]).resolve()
    receipt = C.compile_manifest(manifest_path, binding["sha256"])
    require(receipt["decision"] == "CANON_CONTEXT_COMPILED"
            and receipt["mode"] == "generator"
            and receipt["project_id"] == request["project_id"]
            and receipt["version"] == canon["version"]
            and receipt["chapter"] == request["chapter"]
            and receipt["current_index_sha256"] == request["expected_index_sha256"]
            and (manifest_path.parent / manifest["current_index"]["path"]).resolve() == root / "index.json",
            "REGISTRATION_CONTEXT_SCOPE_INVALID")
    sources = [manifest["canon"], *manifest.get("base_lines", []), manifest.get("outline")]
    require(isinstance(sources[-1], dict), "REGISTRATION_OUTLINE_REQUIRED")
    for source in sources:
        data, digest = G.evidence(manifest_path.parent, source, snapshots)
        require((manifest_path.parent / source["path"]).resolve().is_relative_to(root),
                "REGISTRATION_SOURCE_OUTSIDE_ROOT")
        active = index["artifacts"].get(data["artifact_id"])
        require(isinstance(active, dict)
                and (root / active.get("path", "")).resolve()
                == (manifest_path.parent / source["path"]).resolve()
                and active["sha256"] == digest, "REGISTRATION_SOURCE_NOT_CURRENT")
    require((manifest_path.parent / manifest["canon"]["path"]).resolve()
            == (root / request["canon"]["path"]).resolve()
            and manifest["canon"]["sha256"] == request["canon"]["sha256"],
            "REGISTRATION_CANON_SUBSTITUTION")
    actual_bases = {(manifest_path.parent / row["path"]).resolve(): row["sha256"]
                    for row in manifest.get("base_lines", [])}
    requested_bases = {(root / row["path"]).resolve(): row["sha256"]
                       for row in request.get("base_lines", [])}
    require(actual_bases == requested_bases, "REGISTRATION_BASE_SUBSTITUTION")
    closure, queue = set(), deque(receipt["source_artifact_ids"])
    while queue:
        source = queue.popleft()
        if source in closure:
            continue
        closure.add(source)
        row = index["artifacts"][source]
        require(row["status"] in ("VALIDATED", "FROZEN"), "REGISTRATION_DEPENDENCY_INVALID")
        data, _ = G.evidence(root, {"path": row.get("path"), "sha256": row["sha256"]}, snapshots)
        require(isinstance(data, dict) and data.get("artifact_id") == source
                and data.get("project_id") == request["project_id"]
                and data.get("version") == row["version"] and data.get("status") == row["status"],
                "REGISTRATION_DEPENDENCY_SOURCE_INVALID")
        queue.extend(index["dependencies"][source])
    pack, _ = G.evidence(root, request["input_pack"], snapshots)
    candidate, _ = G.evidence(root, request["candidate"], snapshots)
    task = {"DERIVED_REGISTER": "P16A", "ACK_REGISTER": "P16B",
            "LIFECYCLE_APPLY": "P16C"}[request["operation"]]
    require(isinstance(pack, dict) and pack.get("task_id") == task
            and pack.get("project_id") == request["project_id"]
            and pack.get("version") == canon["version"]
            and isinstance(pack.get("inputs"), dict), "REGISTRATION_PACK_SCOPE_INVALID")
    inputs = pack["inputs"]
    require(type(inputs.get("chapter")) is int and inputs["chapter"] == request["chapter"]
            and C.object_sha(inputs.get("canon_context")) == receipt["context_object_sha256"]
            and C.object_sha(inputs.get("chapter_outline")) == receipt["outline_object_sha256"],
            "REGISTRATION_RUNTIME_SUBSTITUTION")
    require(isinstance(candidate, dict) and candidate.get("task_id") == task
            and candidate.get("project_id") == request["project_id"]
            and candidate.get("version") == canon["version"]
            and candidate.get("status") in ("VALIDATED", "FROZEN")
            and type(candidate.get("chapter")) is int and candidate["chapter"] == request["chapter"]
            and C.text(candidate.get("artifact_id")), "REGISTRATION_CANDIDATE_SCOPE_INVALID")
    errors = C.validate_p16(task, candidate, inputs)
    require(not errors, "REGISTRATION_P16_INVALID:" + ";".join(errors))
    value = candidate.get("content", candidate.get("payload", candidate))
    return value, candidate, pack, receipt, closure


def register(root, request, snapshots, index, canon, base_lines):
    value, candidate, pack, receipt, closure = proposal_sources(
        root, request, snapshots, index, canon, base_lines)
    require(C.text(value.get("id")), "REGISTRATION_NO_EFFECT_PROPOSAL")
    updated = copy.deepcopy(canon)
    updated["version"] = request["new_version"]
    collection = "derived_lines" if request["operation"] == "DERIVED_REGISTER" else "ack_nodes"
    updated["content"][collection].append(copy.deepcopy(value))
    records = updated["content"].setdefault("registration_records", [])
    require(isinstance(records, list), "REGISTRATION_HISTORY_INVALID")
    records.append({"tx_id": request["tx_id"], "operation": request["operation"],
                    "registered_id": value["id"], "chapter": request["chapter"],
                    "candidate": request["candidate"], "input_pack": request["input_pack"],
                    "registration_context": request["registration_context"],
                    "source_hashes": {source: index["artifacts"][source]["sha256"]
                                      for source in sorted(closure)},
                    "provenance_policy": "IMMUTABLE_HISTORY_NOT_LIVE_DEPENDENCY"})
    C.retrieve(updated["content"], base_lines, request["chapter"], "audit")
    return updated, encoded(updated)


def lifecycle(root, request, snapshots, index, canon, base_lines):
    value, candidate, pack, receipt, closure = proposal_sources(
        root, request, snapshots, index, canon, base_lines)
    result = L.transition(value, pack["inputs"], C, canon["content"]["derived_lines"],
                          canon["content"]["ack_nodes"])
    review, _ = G.evidence(root, request["lifecycle_review"], snapshots)
    L.validate_review(review, candidate, pack, receipt["context_object_sha256"], result, C)
    updated = copy.deepcopy(canon)
    updated["version"] = request["new_version"]
    lines = updated["content"]["derived_lines"]
    position = next(i for i, row in enumerate(lines) if row["id"] == value["line_id"])
    lines[position] = result["closed_line"]
    if result["successor_line"] is not None:
        lines.append(result["successor_line"])
    records = updated["content"].setdefault("lifecycle_records", [])
    require(isinstance(records, list), "LIFECYCLE_HISTORY_RECORDS_INVALID")
    records.append({
        "tx_id": request["tx_id"], "operation": request["operation"], "line_id": value["line_id"],
        "decision": value["decision"], "effective_chapter": value["effective_chapter"],
        "effective_seq": value["effective_seq"], "cause_ref": value["cause_ref"],
        "previous_line_object_sha256": result["previous_line_object_sha256"],
        "successor_line_id": result["successor_line"]["id"] if result["successor_line"] else None,
        "preserved_pending_ack_ids": [ack["id"] for ack in result["pending_acks"]],
        "candidate": request["candidate"], "input_pack": request["input_pack"],
        "registration_context": request["registration_context"],
        "lifecycle_review": request["lifecycle_review"],
        "source_hashes": {source: index["artifacts"][source]["sha256"] for source in sorted(closure)},
        "provenance_policy": "IMMUTABLE_HISTORY_NOT_LIVE_DEPENDENCY",
        "execution_status": "PLANNED_NOT_SHIPPED",
        "ack_policy": "PRESERVED_NOT_EXECUTED_CANCELLED_OR_RETARGETED",
    })
    C.retrieve(updated["content"], base_lines, request["chapter"], "audit")
    return updated, encoded(updated)


def build(root, request, snapshots, index, before_hash):
    require(before_hash == request["expected_index_sha256"], "INDEX_COMPARE_AND_SWAP_FAILED")
    require(index["project_id"] == request["project_id"], "TRANSACTION_PROJECT_MISMATCH")
    ident = request["canon_id"]
    current = index["artifacts"].get(ident)
    require(isinstance(current, dict) and current.get("status") == "FROZEN"
            and current.get("sha256") == request["canon"]["sha256"]
            and current.get("path") == request["canon"]["path"], "TRANSACTION_CANON_NOT_CURRENT")
    canon, _ = G.evidence(root, request["canon"], snapshots)
    require(isinstance(canon, dict) and canon.get("artifact_id") == ident
            and canon.get("version") == current["version"] and canon.get("status") == "FROZEN"
            and canon.get("project_id") == index["project_id"]
            and C.text(request["new_version"]) and request["new_version"] != canon["version"],
            "TRANSACTION_CANON_SCOPE_OR_VERSION_INVALID")
    used_versions = {canon["version"]}
    for prior in index["transactions"].values():
        require(isinstance(prior, dict), "TRANSACTION_HISTORY_INVALID")
        if prior.get("canon_id") == ident:
            used_versions.update([prior.get("previous_version"), prior.get("new_version")])
    require(request["new_version"] not in used_versions, "TRANSACTION_VERSION_ALREADY_USED")
    ancestors, queue = set(), deque(index["dependencies"][ident])
    while queue:
        source = queue.popleft()
        if source not in ancestors:
            ancestors.add(source)
            queue.extend(index["dependencies"][source])
    require(all(index["artifacts"][source]["status"] in ("VALIDATED", "FROZEN") for source in ancestors),
            "TRANSACTION_CANON_DEPENDENCY_INVALID")
    ancestor_base_ids = set()
    for source in ancestors:
        row = index["artifacts"][source]
        data, _ = G.evidence(root, {"path": row.get("path"), "sha256": row["sha256"]}, snapshots)
        require(isinstance(data, dict) and data.get("artifact_id") == source
                and data.get("version") == row["version"] and data.get("status") == row["status"]
                and data.get("project_id") == index["project_id"],
                "TRANSACTION_CANON_DEPENDENCY_SOURCE_INVALID")
        value = data.get("content")
        if isinstance(value, dict) and (value.get("layer") == "BASE"
                or ("entity_id" in value and "change_nodes" in value)):
            ancestor_base_ids.add(source)
    content = canon.get("content")
    require(isinstance(content, dict), "TRANSACTION_CANON_CONTENT_REQUIRED")
    bases = request.get("base_lines", [])
    base_lines = []
    supplied_base_ids = set()
    for binding in bases:
        value, digest = G.evidence(root, binding, snapshots)
        require(isinstance(value, dict) and value.get("status") == "FROZEN"
                and value.get("project_id") == index["project_id"]
                and isinstance(value.get("content"), dict), "TRANSACTION_BASE_SOURCE_INVALID")
        active = index["artifacts"].get(value.get("artifact_id"))
        require(isinstance(active, dict) and active.get("sha256") == digest
                and active.get("version") == value.get("version") and active.get("status") == "FROZEN"
                and value["artifact_id"] in ancestors, "TRANSACTION_BASE_NOT_IN_CANON_DEPENDENCIES")
        base_lines.append(value["content"])
        supplied_base_ids.add(value["artifact_id"])
    if request["operation"] == "LIFECYCLE_APPLY":
        require(supplied_base_ids == ancestor_base_ids, "LIFECYCLE_COMPLETE_BASE_CONTEXT_REQUIRED")
    C.retrieve(content, base_lines, request["chapter"], "audit")
    if request["operation"] in ("DERIVED_REGISTER", "ACK_REGISTER"):
        return register(root, request, snapshots, index, canon, base_lines)
    if request["operation"] == "LIFECYCLE_APPLY":
        return lifecycle(root, request, snapshots, index, canon, base_lines)
    matches = [row for row in content["ack_nodes"] if row["id"] == request["ack_id"]]
    require(len(matches) == 1, "TRANSACTION_ACK_NOT_REGISTERED")
    ack = matches[0]
    text_path = (root / request["prose"]["path"]).resolve()
    require(text_path.is_relative_to(root), "TRANSACTION_PROSE_OUTSIDE_ROOT")
    prose_raw = text_path.read_bytes()
    require(C.sha(prose_raw) == request["prose"]["sha256"], "TRANSACTION_PROSE_HASH_CHANGED")
    require(text_path not in snapshots or snapshots[text_path] == C.sha(prose_raw),
            "TRANSACTION_SOURCE_CHANGED_DURING_CHECK")
    snapshots[text_path] = C.sha(prose_raw)
    prose = prose_raw.decode("utf-8")
    review, _ = G.evidence(root, request["review"], snapshots)
    ship, _ = G.evidence(root, request["ship"], snapshots)
    require(isinstance(review, dict) and isinstance(ship, dict)
            and review.get("project_id") == index["project_id"]
            and ship.get("project_id") == index["project_id"], "TRANSACTION_FULFILLMENT_PROJECT_MISMATCH")
    require(ack["target_chapter"] == request["chapter"], "TRANSACTION_ACK_CHAPTER_MISMATCH")
    errors = C.validate_ack_fulfillment(ack, prose, review, ship)
    require(not errors, "TRANSACTION_ACK_FULFILLMENT_INVALID:" + ";".join(errors))
    updated = copy.deepcopy(canon)
    updated["version"] = request["new_version"]
    target = next(row for row in updated["content"]["ack_nodes"] if row["id"] == ack["id"])
    target["status"] = "executed"
    records = updated["content"].setdefault("execution_records", [])
    require(isinstance(records, list), "TRANSACTION_EXECUTION_HISTORY_INVALID")
    records.append({"tx_id": request["tx_id"], "ack_id": ack["id"], "chapter": request["chapter"],
                    "previous_ack_object_sha256": C.object_sha(ack),
                    "prose_sha256": request["prose"]["sha256"],
                    "review_sha256": request["review"]["sha256"], "ship_sha256": request["ship"]["sha256"]})
    C.retrieve(updated["content"], base_lines, request["chapter"], "audit")
    raw = encoded(updated)
    return updated, raw


def verify_snapshots(snapshots):
    for path, digest in snapshots.items():
        require(C.sha(path.read_bytes()) == digest, "TRANSACTION_SOURCE_CHANGED_DURING_CHECK")


def preview(root, request_path, expected_hash):
    root = Path(root).resolve()
    request, snapshots = read_request(root, request_path, expected_hash)
    index, before = read_index(root)
    _, candidate_raw = build(root, request, snapshots, index, before)
    verify_snapshots(snapshots)
    require(C.sha((root / "index.json").read_bytes()) == before, "INDEX_CHANGED_DURING_PREVIEW")
    return {"decision": "TRANSACTION_PREVIEW_ONLY", "request_subject_sha256": subject(request),
            "candidate_sha256": C.sha(candidate_raw), "expected_index_sha256": before,
            "activation": "NOT_GRANTED"}


def commit(root, request_path, expected_hash, trusted_issuers):
    root = Path(root).resolve()
    require(root.is_dir(), "TRANSACTION_ROOT_REQUIRED")
    G.identifiers(trusted_issuers, "TRANSACTION_TRUSTED_ISSUERS_REQUIRED", True)
    with index_lock(root):
        request, snapshots = read_request(root, request_path, expected_hash)
        index, before = read_index(root)
        prior = index["transactions"].get(request["tx_id"])
        if prior is not None:
            require(isinstance(prior, dict) and prior.get("request_sha256") == expected_hash
                    and prior.get("approved_by") in trusted_issuers
                    and (prior.get("operation") != "LIFECYCLE_APPLY"
                         or prior.get("lifecycle_reviewed_by") in trusted_issuers),
                    "TRANSACTION_ID_REUSED_WITH_DIFFERENT_REQUEST")
            G.evidence(root, {"path": prior["snapshot_path"], "sha256": prior["snapshot_sha256"]}, {})
            G.evidence(root, {"path": prior["previous_index_path"],
                              "sha256": prior["previous_index_sha256"]}, {})
            return {**prior, "decision": "ALREADY_COMMITTED", "current_index_sha256": before,
                    "writer_activation": "NOT_GRANTED"}
        _, candidate_raw = build(root, request, snapshots, index, before)
        lifecycle_reviewer = None
        if request["operation"] == "LIFECYCLE_APPLY":
            review, _ = G.evidence(root, request["lifecycle_review"], snapshots)
            lifecycle_reviewer = review.get("reviewed_by")
            require(lifecycle_reviewer in trusted_issuers, "LIFECYCLE_REVIEWER_NOT_TRUSTED")
        approval, _ = G.evidence(root, request["approval"], snapshots)
        require(isinstance(approval, dict) and approval.get("decision") == "APPROVED"
                and approval.get("approved_by") in trusted_issuers
                and approval.get("project_id") == request["project_id"]
                and approval.get("tx_id") == request["tx_id"]
                and approval.get("request_subject_sha256") == subject(request)
                and approval.get("candidate_sha256") == C.sha(candidate_raw)
                and approval.get("expected_index_sha256") == before,
                "TRANSACTION_APPROVAL_NOT_BOUND_OR_TRUSTED")
        relative = f"transactions/{request['tx_id']}/canon.json"
        target = (root / relative).resolve()
        require(target.is_relative_to(root), "TRANSACTION_STAGE_OUTSIDE_ROOT")
        intent = {"tx_id": request["tx_id"], "request_sha256": expected_hash,
                  "candidate_sha256": C.sha(candidate_raw)}
        publish_file(target.parent / "intent.json", encoded(intent))
        before_raw = (root / "index.json").read_bytes()
        require(C.sha(before_raw) == before, "INDEX_CHANGED_DESPITE_LOCK")
        publish_file(target.parent / "index.before.json", before_raw)
        publish_file(target, candidate_raw)
        verify_snapshots(snapshots)
        require(C.sha((root / "index.json").read_bytes()) == before, "INDEX_CHANGED_DESPITE_LOCK")
        reverse = graph(index)
        affected, queue = set(), deque([request["canon_id"]])
        while queue:
            for consumer in reverse[queue.popleft()]:
                if consumer not in affected:
                    affected.add(consumer)
                    queue.append(consumer)
        updated_index = copy.deepcopy(index)
        for ident in affected:
            updated_index["artifacts"][ident]["status"] = "INVALIDATED"
        updated_index["artifacts"][request["canon_id"]] = {
            **index["artifacts"][request["canon_id"]], "version": request["new_version"],
            "sha256": C.sha(candidate_raw), "path": relative, "status": "FROZEN"}
        updated_index["generation"] += 1
        receipt = {"tx_id": request["tx_id"], "request_sha256": expected_hash,
                   "operation": request["operation"],
                   "canon_id": request["canon_id"],
                   "previous_version": index["artifacts"][request["canon_id"]]["version"],
                   "new_version": request["new_version"],
                   "request_subject_sha256": subject(request), "approved_by": approval["approved_by"],
                   "previous_index_sha256": before, "generation": updated_index["generation"],
                   "previous_index_path": f"transactions/{request['tx_id']}/index.before.json",
                   "snapshot_path": relative, "snapshot_sha256": C.sha(candidate_raw),
                   "invalidated_artifact_ids": sorted(affected)}
        updated_index["transactions"][request["tx_id"]] = receipt
        if lifecycle_reviewer is not None:
            receipt["lifecycle_reviewed_by"] = lifecycle_reviewer
        graph(updated_index)
        # Index contents, invalidation statuses and idempotency ledger share one commit point.
        publish_file(root / "index.json", encoded(updated_index), replace=True)
        return {**receipt, "decision": "COMMITTED", "current_index_sha256": C.sha(encoded(updated_index)),
                "writer_activation": "NOT_GRANTED"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=("preview", "commit"))
    ap.add_argument("--root", required=True)
    ap.add_argument("--request", required=True)
    ap.add_argument("--sha256", required=True)
    ap.add_argument("--trusted-issuer", action="append", default=[])
    args = ap.parse_args()
    try:
        if args.action == "preview":
            result = preview(args.root, args.request, args.sha256)
        else:
            result = commit(args.root, args.request, args.sha256, args.trusted_issuer)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as error:
        print("BLOCKED:" + str(error))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
