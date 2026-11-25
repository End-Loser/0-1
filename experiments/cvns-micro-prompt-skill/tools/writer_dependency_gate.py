#!/usr/bin/env python3
"""Read-only dependency invalidation and version-bound Writer evidence checks."""
from __future__ import annotations

import argparse
from collections import deque
import hashlib
import json
from pathlib import Path
import re


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def object_sha(value):
    return sha(json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False).encode("utf-8"))


def subject(manifest):
    """Exclude the subsequent P17C binding to avoid a circular evidence hash."""
    value = {k: v for k, v in manifest.items() if k != "p17c"}
    return object_sha(value)


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def identifiers(value, reason, nonempty=False):
    require(isinstance(value, list) and (bool(value) or not nonempty), reason)
    require(all(isinstance(x, str) and x.strip() for x in value), reason)
    require(len(value) == len(set(value)), reason)
    return value


def evidence(base, binding, snapshots, check_hash=True):
    require(isinstance(binding, dict), "DEPENDENCY_BINDING_INVALID")
    require(isinstance(binding.get("path"), str) and binding["path"].strip(),
            "DEPENDENCY_PATH_REQUIRED")
    require(isinstance(binding.get("sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", binding["sha256"]),
            "DEPENDENCY_HASH_INVALID")
    path = (base / binding["path"]).resolve()
    require(path.is_relative_to(base), "DEPENDENCY_EVIDENCE_OUTSIDE_ROOT")
    raw = path.read_bytes()
    digest = sha(raw)
    require(path not in snapshots or snapshots[path] == digest,
            "DEPENDENCY_SOURCE_CHANGED_DURING_CHECK")
    snapshots[path] = digest
    if check_hash:
        require(digest == binding["sha256"], "DEPENDENCY_EVIDENCE_HASH_CHANGED")
    return json.loads(raw.decode("utf-8-sig")), digest


def analyze(manifest, base, snapshots=None, *, projected_index=None):
    """Propagate changed/missing sources through a declared acyclic graph."""
    snapshots = {} if snapshots is None else snapshots
    require(manifest.get("complete") is True, "DEPENDENCY_GRAPH_NOT_COMPLETE")
    for key in ("project_id", "version"):
        require(isinstance(manifest.get(key), str) and manifest[key].strip(),
                "DEPENDENCY_SCOPE_REQUIRED")
    require(type(manifest.get("chapter")) is int and manifest["chapter"] > 0,
            "DEPENDENCY_CHAPTER_INVALID")
    index_binding = manifest.get("current_index")
    require(isinstance(index_binding, dict) and isinstance(index_binding.get("path"), str)
            and index_binding["path"].strip(), "DEPENDENCY_CURRENT_INDEX_REQUIRED")
    index_path = (base / index_binding["path"]).resolve()
    require(index_path.is_relative_to(base), "DEPENDENCY_EVIDENCE_OUTSIDE_ROOT")
    index_raw = index_path.read_bytes()
    snapshots[index_path] = sha(index_raw)
    index = json.loads(index_raw.decode("utf-8-sig"))
    if projected_index is not None:
        index = projected_index
    require(isinstance(index, dict) and index.get("project_id") == manifest["project_id"]
            and isinstance(index.get("artifacts"), dict), "DEPENDENCY_CURRENT_INDEX_INVALID")
    rows = manifest.get("nodes")
    require(isinstance(rows, list) and bool(rows), "DEPENDENCY_NODES_REQUIRED")
    nodes, paths = {}, set()
    for row in rows:
        require(isinstance(row, dict), "DEPENDENCY_NODE_INVALID")
        ident = row.get("artifact_id")
        require(isinstance(ident, str) and ident.strip() and ident not in nodes,
                "DEPENDENCY_DUPLICATE_OR_INVALID_ID")
        require(isinstance(row.get("version"), str) and row["version"].strip(),
                "DEPENDENCY_NODE_VERSION_REQUIRED")
        identifiers(row.get("depends_on"), "DEPENDENCY_EDGES_INVALID")
        require(isinstance(row.get("path"), str) and row["path"].strip(),
                "DEPENDENCY_PATH_REQUIRED")
        path = (base / row["path"]).resolve()
        require(path.is_relative_to(base), "DEPENDENCY_EVIDENCE_OUTSIDE_ROOT")
        require(path not in paths, "DEPENDENCY_DUPLICATE_PATH")
        paths.add(path)
        nodes[ident] = row
    roots = identifiers(manifest.get("root_ids"), "DEPENDENCY_ROOTS_INVALID", True)
    require(set(roots).issubset(nodes), "DEPENDENCY_ROOT_NOT_REGISTERED")
    reverse = {ident: [] for ident in nodes}
    indegree = {}
    for ident, row in nodes.items():
        require(set(row["depends_on"]).issubset(nodes), "DEPENDENCY_EDGE_NOT_REGISTERED")
        indegree[ident] = len(row["depends_on"])
        for source in row["depends_on"]:
            reverse[source].append(ident)
    queue = deque(ident for ident, degree in indegree.items() if not degree)
    visited = 0
    while queue:
        visited += 1
        for consumer in reverse[queue.popleft()]:
            indegree[consumer] -= 1
            if not indegree[consumer]:
                queue.append(consumer)
    require(visited == len(nodes), "DEPENDENCY_GRAPH_CYCLE")
    changes = {}
    for ident, row in nodes.items():
        active = index["artifacts"].get(ident)
        if (not isinstance(active, dict) or active.get("version") != row["version"]
                or active.get("sha256") != row.get("sha256")
                or active.get("status") not in ("VALIDATED", "FROZEN")):
            changes[ident] = "CURRENT_INDEX_VERSION_HASH_OR_STATUS_CHANGED"
        # A removed file is stale evidence, not permission to omit its consumers.
        try:
            data, digest = evidence(base, row, snapshots, check_hash=False)
        except FileNotFoundError:
            changes[ident] = "SOURCE_MISSING"
            continue
        except (json.JSONDecodeError, UnicodeError):
            changes[ident] = "SOURCE_UNREADABLE"
            continue
        if digest != row["sha256"]:
            changes[ident] = "SOURCE_HASH_CHANGED"
        elif (not isinstance(data, dict) or data.get("artifact_id") != ident
              or data.get("version") != row["version"]
              or data.get("status") not in ("VALIDATED", "FROZEN")):
            changes[ident] = "SOURCE_ID_VERSION_OR_STATUS_INVALID"
    affected = set(changes)
    queue = deque(changes)
    while queue:
        for consumer in reverse[queue.popleft()]:
            if consumer not in affected:
                affected.add(consumer)
                queue.append(consumer)
    closure = set(roots)
    queue = deque(roots)
    while queue:
        for source in nodes[queue.popleft()]["depends_on"]:
            if source not in closure:
                closure.add(source)
                queue.append(source)
    return {"changed_sources": changes, "invalidated_artifact_ids": sorted(affected),
            "blocked_root_ids": sorted(set(roots) & affected),
            "current_index_sha256": snapshots[index_path],
            "closure_ids": sorted(closure),
            "unaffected_artifact_ids": sorted(set(nodes) - affected)}


def _verify(path, expected_hash, scope, projected_index=None):
    path = Path(path).resolve()
    base, snapshots = path.parent, {}
    manifest, digest = evidence(base, {"path": path.name, "sha256": expected_hash}, snapshots)
    require(isinstance(manifest, dict), "DEPENDENCY_MANIFEST_INVALID")
    require(all(manifest.get(k) == scope.get(k) for k in ("project_id", "version", "chapter"))
            and type(scope.get("chapter")) is int, "DEPENDENCY_SCOPE_MISMATCH")
    result = analyze(manifest, base, snapshots, projected_index=projected_index)
    if result["blocked_root_ids"]:
        return {**result, "decision": "BLOCKED", "reason": "DEPENDENCY_CLOSURE_STALE",
                "manifest_sha256": digest}
    frozen, frozen_hash = evidence(base, manifest.get("frozen"), snapshots)
    require(isinstance(frozen, dict) and frozen.get("status") == "FROZEN"
            and all(frozen.get(k) == manifest[k] for k in ("project_id", "chapter", "version")),
            "DEPENDENCY_FREEZE_SCOPE_MISMATCH")
    nodes = {row["artifact_id"]: row for row in manifest["nodes"]}
    entries = frozen.get("artifacts")
    require(isinstance(entries, list) and bool(entries), "DEPENDENCY_FREEZE_EMPTY")
    seen = set()
    for row in entries:
        require(isinstance(row, dict), "DEPENDENCY_FREEZE_ENTRY_INVALID")
        ident = row.get("artifact_id")
        require(isinstance(ident, str) and ident not in seen and ident in nodes
                and ident in manifest["root_ids"], "DEPENDENCY_FREEZE_ROOT_MISSING")
        seen.add(ident)
        require(nodes[ident]["sha256"] == row.get("sha256"),
                "DEPENDENCY_FREEZE_NODE_HASH_MISMATCH")
        require(isinstance(row.get("frozen_path"), str), "DEPENDENCY_FREEZE_PATH_INVALID")
        freeze_base = (base / manifest["frozen"]["path"]).resolve().parent
        require((freeze_base / row["frozen_path"]).resolve()
                == (base / nodes[ident]["path"]).resolve(), "DEPENDENCY_FREEZE_PATH_MISMATCH")
    p17c, p17c_hash = evidence(base, manifest.get("p17c"), snapshots)
    index, _ = evidence(base, {"path": manifest["current_index"]["path"],
                              "sha256": result["current_index_sha256"]}, snapshots)
    if projected_index is not None:
        index = projected_index
    active_preflight = index["artifacts"].get(p17c.get("artifact_id")) if isinstance(p17c, dict) else None
    require(isinstance(p17c, dict) and p17c.get("task_id") == "P17C"
            and isinstance(p17c.get("artifact_id"), str) and p17c["artifact_id"].strip()
            and p17c.get("status") in ("VALIDATED", "FROZEN")
            and p17c.get("decision") == "READY"
            and all(p17c.get(k) == manifest[k] for k in ("project_id", "version", "chapter"))
            and p17c.get("checked_dependency_subject_sha256") == subject(manifest)
            and p17c.get("checked_frozen_manifest_sha256") == frozen_hash
            and isinstance(active_preflight, dict)
            and active_preflight.get("version") == manifest["version"]
            and active_preflight.get("sha256") == p17c_hash
            and active_preflight.get("status") in ("VALIDATED", "FROZEN")
            and p17c.get("missing_refs") == []
            and p17c.get("version_mismatches") == [], "DEPENDENCY_P17C_NOT_CURRENT")
    for source, before in snapshots.items():
        require(sha(source.read_bytes()) == before, "DEPENDENCY_SOURCE_CHANGED_DURING_CHECK")
    return {**result, "decision": ("DEPENDENCY_EVIDENCE_VERIFIED" if projected_index is None
                                  else "PROJECTED_DEPENDENCY_VERIFIED_NOT_ACTIVE"),
            "closure_hashes": {ident: nodes[ident]["sha256"] for ident in result["closure_ids"]},
            "manifest_sha256": digest, "dependency_subject_sha256": subject(manifest),
            "frozen_manifest_sha256": frozen_hash, "p17c_sha256": p17c_hash,
            "p17c_object_sha256": object_sha(p17c),
            "p17c_artifact_id": p17c["artifact_id"],
            "note": "No activation or approval granted; host must enforce complete trusted indexing."}


def verify(path, expected_hash, scope):
    return _verify(path, expected_hash, scope)


def verify_projection(path, expected_hash, scope, projected_index):
    """Check an upcoming atomic replacement; never return the production gate's success token."""
    require(isinstance(projected_index, dict), "DEPENDENCY_PROJECTED_INDEX_INVALID")
    result = _verify(path, expected_hash, scope, projected_index)
    return {**result, "projected_index_object_sha256": object_sha(projected_index),
            "activation": "NOT_GRANTED"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--sha256", required=True)
    ap.add_argument("--project-id", required=True)
    ap.add_argument("--chapter", type=int, required=True)
    ap.add_argument("--version", required=True)
    ap.add_argument("--out")
    args = ap.parse_args()
    try:
        result = verify(args.manifest, args.sha256,
                        {"project_id": args.project_id, "chapter": args.chapter, "version": args.version})
        if args.out:
            with Path(args.out).open("x", encoding="utf-8") as stream:
                stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result["decision"] == "BLOCKED" else 0
    except (OSError, ValueError, TypeError, KeyError) as error:
        print("BLOCKED:" + str(error))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
