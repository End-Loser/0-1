"""Order and atomically replace invalidated consumers using independent host evidence."""
from __future__ import annotations

import argparse
import copy
import csv
import json
from collections import deque
from pathlib import Path

import importlib.util


def module(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + ".py"))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


I = module("index_transaction")
C, G = I.C, I.G
H = module("real_novel_test")
O = module("call_optimization")
ROOT = Path(__file__).resolve().parents[1]


def order(index, selected):
    I.graph(index)
    G.identifiers(selected, "REBUILD_IDS_INVALID", True)
    I.require(set(selected).issubset(index["artifacts"]), "REBUILD_ID_NOT_REGISTERED")
    pending, result = set(selected), []
    while pending:
        ready = sorted(ident for ident in pending
                       if not pending.intersection(index["dependencies"][ident]))
        I.require(bool(ready), "REBUILD_CYCLE")
        result.extend(ready)
        pending.difference_update(ready)
    return result


def ancestors(index, identifiers):
    seen, queue = set(), deque(identifiers)
    while queue:
        ident = queue.popleft()
        if ident not in seen:
            seen.add(ident)
            queue.extend(index["dependencies"][ident])
    return sorted(seen)


def source(root, index, ident, snapshots, allow_invalidated=False):
    row = index["artifacts"][ident]
    I.require(allow_invalidated or row["status"] in ("FROZEN", "VALIDATED"),
              "REBUILD_UPSTREAM_INVALIDATED")
    value, _ = G.evidence(root, row, snapshots)
    I.require(isinstance(value, dict) and value.get("artifact_id") == ident
              and value.get("project_id") == index["project_id"]
              and value.get("version") == row["version"]
              and value.get("status") in ("FROZEN", "VALIDATED"), "REBUILD_SOURCE_IDENTITY")
    return value


def plan(root, selected=None):
    root = Path(root).resolve()
    index, digest = I.read_index(root)
    I.graph(index)
    default_selection = selected is None
    selected = [ident for ident, row in index["artifacts"].items()
                if row["status"] == "INVALIDATED"] if selected is None else selected
    snapshots, rows = {}, []
    ordered = [] if default_selection and not selected else order(index, selected)
    for ident in ordered:
        I.require(index["artifacts"][ident]["status"] == "INVALIDATED", "REBUILD_TARGET_NOT_INVALIDATED")
        old = source(root, index, ident, snapshots, True)
        missing = [dep for dep in ancestors(index, index["dependencies"][ident])
                   if index["artifacts"][dep]["status"] == "INVALIDATED" and dep not in selected]
        rows.append({"artifact_id": ident, "task_id": old.get("task_id"),
                     "depends_on": index["dependencies"][ident], "old": copy.deepcopy(index["artifacts"][ident]),
                     "blocked_unselected_upstreams": missing})
    I.verify_snapshots(snapshots)
    I.require(C.sha((root / "index.json").read_bytes()) == digest, "REBUILD_INDEX_CHANGED")
    return {"decision": "REBUILD_PLAN_ONLY" if rows else "NO_INVALIDATED_CONSUMERS",
            "project_id": index["project_id"],
            "expected_index_sha256": digest, "ordered_items": rows, "activation": "NOT_GRANTED"}


def read_request(root, path, digest):
    snapshots = {path: C.sha(path.read_bytes()) for folder, suffix in (("tools", "*.py"), ("schemas", "*.json"))
                 for path in (ROOT / folder).rglob(suffix) if "__pycache__" not in path.parts}
    request, _ = G.evidence(root, {"path": str(path), "sha256": digest}, snapshots)
    I.require(not C.schema_errors(request, "CONSUMER_REBUILD_SCHEMA.json"), "REBUILD_REQUEST_SCHEMA")
    return request, snapshots


def validation_fingerprint(snapshots):
    return C.object_sha({str(path.relative_to(ROOT)).replace("\\", "/"): digest
                         for path, digest in snapshots.items()
                         if path.is_relative_to(ROOT) and (path.parts[len(ROOT.parts)] in ("tools", "schemas")
                                                          or path.name == "TASK_REGISTRY.csv")})


def build(root, request, index, before, snapshots):
    I.require(index["project_id"] == request["project_id"]
              and before == request["expected_index_sha256"], "REBUILD_INDEX_CAS")
    items = {item["artifact_id"]: item for item in request["items"]}
    I.require(len(items) == len(request["items"]), "REBUILD_DUPLICATE_TARGET")
    ordered = order(index, list(items))
    updated = copy.deepcopy(index)
    reverse = I.graph(index)
    affected, queue = set(), deque(ordered)
    while queue:
        for ident in reverse[queue.popleft()]:
            if ident not in affected:
                affected.add(ident)
                queue.append(ident)
    for ident in affected - set(ordered):
        updated["artifacts"][ident]["status"] = "INVALIDATED"
    registry_path = ROOT / "TASK_REGISTRY.csv"
    snapshots[registry_path] = C.sha(registry_path.read_bytes())
    with registry_path.open(encoding="utf-8-sig", newline="") as stream:
        registry = {row["task_id"]: row for row in csv.DictReader(stream)}
    historical_paths = {(root / row["path"]).resolve() for row in index["artifacts"].values()
                        if isinstance(row.get("path"), str)}
    historical_versions = {ident: {row["version"]} for ident, row in index["artifacts"].items()}
    for receipt in index["transactions"].values():
        previous, _ = G.evidence(root, {"path": receipt["previous_index_path"],
                                       "sha256": receipt["previous_index_sha256"]}, snapshots)
        I.require(previous.get("project_id") == index["project_id"], "REBUILD_HISTORY_PROJECT")
        for ident, row in previous["artifacts"].items():
            historical_versions.setdefault(ident, set()).add(row["version"])
            if isinstance(row.get("path"), str):
                historical_paths.add((root / row["path"]).resolve())
    candidates, reviewers = {}, {}
    for ident in ordered:
        item, current = items[ident], index["artifacts"][ident]
        I.require(current["status"] == "INVALIDATED", "REBUILD_TARGET_NOT_INVALIDATED")
        I.require(item["depends_on"] == index["dependencies"][ident], "REBUILD_DEPENDENCIES_CHANGED")
        old = source(root, index, ident, snapshots, True)
        task = old.get("task_id")
        kind = old.get("kind", old.get("content", {}).get("kind")
                       if isinstance(old.get("content"), dict) else None)
        I.require(task in registry and task not in ("P00", "P16A", "P16B", "P16C", "P18")
                  and not task.startswith(("P01", "P02"))
                  and kind not in ("BASE", "BASE_LINE", "CANON", "CANON_INDEX", "CanonIndex"),
                  "REBUILD_CANON_BASE_OR_UNCLASSIFIED_SOURCE")
        contract_path = ROOT / registry[task]["output_contract_file"]
        contract_raw = contract_path.read_bytes()
        snapshots[contract_path] = C.sha(contract_raw)
        contract = json.loads(contract_raw.decode("utf-8-sig"))
        candidate, _ = G.evidence(root, item["candidate"], snapshots)
        candidate_path = (root / item["candidate"]["path"]).resolve()
        I.require(candidate_path not in historical_paths and candidate_path not in candidates,
                  "REBUILD_CANDIDATE_OVERWRITES_HISTORY")
        I.require(isinstance(candidate, dict) and candidate.get("project_id") == request["project_id"]
                  and candidate.get("artifact_id") == ident and candidate.get("task_id") == task
                  and C.text(candidate.get("version")) and candidate["version"] != current["version"]
                  and candidate.get("status") in ("FROZEN", "VALIDATED"), "REBUILD_CANDIDATE_IDENTITY")
        I.require(candidate["version"] not in historical_versions[ident], "REBUILD_VERSION_REUSED")
        for prior in index["transactions"].values():
            for replacement in prior.get("replacements", {}).values():
                I.require(replacement.get("artifact_id") != ident
                          or replacement.get("version") != candidate["version"], "REBUILD_VERSION_REUSED")
        body = H.payload(candidate)
        I.require(isinstance(body, dict), "REBUILD_BODY_INVALID")
        schema = contract.get("payload_schema")
        if schema:
            # Bind every local schema byte used by the shared resolver to this preview/commit.
            for path in (ROOT / "schemas").rglob("*.json"):
                snapshots.setdefault(path, C.sha(path.read_bytes()))
        errors = H.validate_schema(body, schema) if schema else H.validate_contract(body, contract)
        for key, allowed in (("status", contract.get("status_values")), ("decision", contract.get("decision_values"))):
            if key in body and allowed and body[key] not in allowed:
                errors.append("REBUILD_BODY_ENUM:" + key)
        I.require(all(body.get(key, expected) == expected
                      for key, expected in (("artifact_id", ident), ("task_id", task))),
                  "REBUILD_BODY_IDENTITY")
        I.require(not errors, "REBUILD_OUTPUT_CONTRACT:" + ";".join(errors))
        closure = ancestors(updated, item["depends_on"])
        bindings = {}
        for dependency in closure:
            source(root, updated, dependency, snapshots)
            bindings[dependency] = copy.deepcopy(updated["artifacts"][dependency])
        pack, _ = G.evidence(root, item["input_pack"], snapshots)
        I.require(isinstance(pack, dict) and pack.get("project_id") == request["project_id"]
                  and pack.get("task_id") == task and pack.get("version") == candidate["version"]
                  and isinstance(pack.get("inputs"), dict) and pack.get("source_bindings") == bindings,
                  "REBUILD_INPUT_SOURCE_BINDING")
        review, _ = G.evidence(root, item["review"], snapshots)
        I.require(isinstance(review, dict) and review.get("decision") == "ACCEPT_REBUILT_CONSUMER"
                  and C.text(review.get("reviewed_by")) and review.get("project_id") == request["project_id"]
                  and review.get("artifact_id") == ident and review.get("old_sha256") == current["sha256"]
                  and review.get("candidate_sha256") == item["candidate"]["sha256"]
                  and review.get("input_pack_sha256") == item["input_pack"]["sha256"]
                  and review.get("source_bindings") == bindings
                  and isinstance(review.get("semantic_checks"), dict)
                  and all(review.get("semantic_checks", {}).get(name) is True
                          for name in ("source_fidelity", "knowledge_isolation", "time", "obligations"))
                  and (candidate["status"] != "FROZEN" or review.get("freeze_authorized") is True),
                  "REBUILD_REVIEW_NOT_SOURCE_BOUND")
        updated["artifacts"][ident] = {
            **current, "path": str(candidate_path.relative_to(root)).replace("\\", "/"),
            "version": candidate["version"], "sha256": item["candidate"]["sha256"],
            "status": candidate["status"]}
        reviewers[ident] = review["reviewed_by"]
        candidates[candidate_path] = candidate_path.read_bytes()
        if task == "P17C":
            execution, _ = G.evidence(root, item.get("execution"), snapshots)
            actual_request, _ = G.evidence(root, execution["request"], snapshots)
            actual_response, _ = G.evidence(root, execution["response"], snapshots)
            usage, _ = G.evidence(root, execution["usage"], snapshots)
            choices = actual_response.get("choices")
            wire = json.loads(actual_request["model_request_without_credentials"]["messages"][1]["content"])
            wire_inputs = O.expand_inputs(wire["inputs"]) if wire.get("input_projection") else wire["inputs"]
            I.require(actual_request.get("input_pack") == pack
                      and C.object_sha(wire_inputs) == C.object_sha(pack["inputs"])
                      and isinstance(choices, list) and len(choices) == 1
                      and isinstance(choices[0], dict) and choices[0].get("finish_reason") == "stop"
                      and json.loads(choices[0]["message"]["content"]) == body
                      and isinstance(actual_response.get("usage"), dict)
                      and type(actual_response["usage"].get("total_tokens")) is int
                      and actual_response["usage"]["total_tokens"] >= 0
                      and usage.get("task_id") == task and type(usage.get("attempt")) is int
                      and usage["attempt"] == 1 and usage.get("usage") == actual_response["usage"]
                      and usage.get("key") == pack.get("pack_id") and C.text(pack.get("pack_id")),
                      "REBUILD_P17C_EXECUTION_NOT_BOUND")
            binding = item.get("p17c_manifest")
            manifest, _ = G.evidence(root, binding, snapshots)
            path = (root / binding["path"]).resolve()
            I.require(isinstance(manifest, dict) and path.parent == root
                      and manifest.get("current_index") == {"path": "index.json"}
                      and isinstance(manifest.get("nodes"), list)
                      and all(isinstance(node, dict) for node in manifest["nodes"]),
                      "REBUILD_P17C_INDEX_ROOT")
            for node in manifest["nodes"]:
                active = updated["artifacts"].get(node["artifact_id"])
                I.require(active is not None and node["depends_on"] == updated["dependencies"][node["artifact_id"]]
                          and all(node.get(key) == active.get(key) for key in ("version", "path", "sha256")),
                          "REBUILD_P17C_GRAPH_NOT_CURRENT")
            preflight, _ = G.evidence(root, manifest["p17c"], snapshots)
            I.require(preflight == candidate and pack.get("expected_p17c_subject_sha256") == G.subject(manifest),
                      "REBUILD_P17C_NOT_NEW_SUBJECT")
            inputs = pack["inputs"]
            dependency = inputs.get("dependency_closure_manifest", {})
            frozen, _ = G.evidence(root, manifest["frozen"], snapshots)
            summary = dependency.get("items")
            I.require(inputs.get("chapter") == candidate["chapter"]
                      and inputs.get("chapter_plan_approval") == frozen
                      and inputs.get("checked_dependency_subject_sha256") == G.subject(manifest)
                      and inputs.get("checked_frozen_manifest_sha256") == manifest["frozen"]["sha256"]
                      and dependency.get("chapter") == candidate["chapter"] and dependency.get("complete") is True
                      and G.identifiers(dependency.get("required_ref_ids"), "REBUILD_P17C_REQUIRED_REFS") == closure
                      and G.identifiers(dependency.get("resolved_ref_ids"), "REBUILD_P17C_RESOLVED_REFS") == closure
                      and isinstance(summary, list) and len(summary) == len(closure)
                      and all(isinstance(row, dict) for row in summary)
                      and {row.get("source_id") for row in summary} == set(closure)
                      and all(all(row.get(key) == bindings[row["source_id"]][key] for key in ("version", "sha256"))
                              for row in summary) and inputs.get("missing_items") == [],
                      "REBUILD_P17C_MODEL_INPUT_NOT_CURRENT")
            result = G.verify_projection(path, binding["sha256"], {
                key: candidate[key] for key in ("project_id", "version", "chapter")}, updated)
            I.require(result["decision"] == "PROJECTED_DEPENDENCY_VERIFIED_NOT_ACTIVE"
                      and set(result["closure_ids"]) == set(closure), "REBUILD_P17C_CLOSURE")
            for node in manifest["nodes"]:
                G.evidence(root, node, snapshots)
            G.evidence(root, manifest["frozen"], snapshots)
        else:
            I.require("p17c_manifest" not in item and "execution" not in item, "REBUILD_UNEXPECTED_P17C")
    I.graph(updated)
    return updated, candidates, reviewers, ordered, sorted(affected - set(ordered))


def preview(root, path, digest):
    root = Path(root).resolve()
    request, snapshots = read_request(root, path, digest)
    index, before = I.read_index(root)
    updated, _, _, ordered, invalidated = build(root, request, index, before, snapshots)
    I.verify_snapshots(snapshots)
    I.require(C.sha((root / "index.json").read_bytes()) == before, "REBUILD_INDEX_CHANGED")
    return {"decision": "REBUILD_PREVIEW_ONLY", "request_subject_sha256": I.subject(request),
            "candidate_index_object_sha256": C.object_sha(updated), "ordered_ids": ordered,
            "validation_fingerprint": validation_fingerprint(snapshots),
            "remaining_invalidated_ids": invalidated, "expected_index_sha256": before,
            "activation": "NOT_GRANTED"}


def commit(root, path, digest, trusted_issuers):
    root = Path(root).resolve()
    G.identifiers(trusted_issuers, "REBUILD_TRUSTED_ISSUERS_REQUIRED", True)
    with I.index_lock(root):
        request, snapshots = read_request(root, path, digest)
        index, before = I.read_index(root)
        prior = index["transactions"].get(request["tx_id"])
        if prior is not None:
            I.require(prior.get("operation") == "CONSUMER_REBUILD"
                      and prior.get("request_sha256") == digest
                      and prior.get("approved_by") in trusted_issuers
                      and all(issuer in trusted_issuers for issuer in prior.get("reviewers", {}).values()),
                      "REBUILD_TRANSACTION_REUSED")
            G.evidence(root, {"path": prior["previous_index_path"],
                              "sha256": prior["previous_index_sha256"]}, {})
            for binding in prior["archived_candidates"].values():
                G.evidence(root, binding, {})
            return {**prior, "decision": "ALREADY_COMMITTED", "writer_activation": "NOT_GRANTED"}
        updated, candidates, reviewers, ordered, invalidated = build(
            root, request, index, before, snapshots)
        approval, _ = G.evidence(root, request["approval"], snapshots)
        I.require(isinstance(approval, dict) and all(issuer in trusted_issuers for issuer in reviewers.values())
                  and approval.get("decision") == "APPROVED"
                  and approval.get("approved_by") in trusted_issuers
                  and approval.get("project_id") == request["project_id"]
                  and approval.get("tx_id") == request["tx_id"]
                  and approval.get("request_subject_sha256") == I.subject(request)
                  and approval.get("candidate_index_object_sha256") == C.object_sha(updated)
                  and approval.get("validation_fingerprint") == validation_fingerprint(snapshots)
                  and approval.get("expected_index_sha256") == before, "REBUILD_APPROVAL_NOT_TRUSTED_OR_BOUND")
        stage = (root / "transactions" / request["tx_id"]).resolve()
        I.require(stage.is_relative_to(root), "REBUILD_STAGE_OUTSIDE_ROOT")
        I.publish_file(stage / "intent.json", I.encoded({"operation": "CONSUMER_REBUILD",
                       "request_sha256": digest, "candidate_index_object_sha256": C.object_sha(updated)}))
        before_raw = (root / "index.json").read_bytes()
        I.require(C.sha(before_raw) == before, "REBUILD_INDEX_CHANGED")
        I.publish_file(stage / "index.before.json", before_raw)
        archived = {}
        for number, (candidate_path, raw) in enumerate(candidates.items()):
            target = stage / f"candidate-{number}.json"
            I.publish_file(target, raw)
            archived[str(candidate_path.relative_to(root))] = {
                "path": str(target.relative_to(root)).replace("\\", "/"), "sha256": C.sha(raw)}
        I.verify_snapshots(snapshots)
        I.require(C.sha((root / "index.json").read_bytes()) == before, "REBUILD_INDEX_CHANGED")
        updated["generation"] += 1
        receipt = {"operation": "CONSUMER_REBUILD", "tx_id": request["tx_id"],
                   "request_sha256": digest, "request_subject_sha256": I.subject(request),
                   "previous_index_path": str((stage / "index.before.json").relative_to(root)).replace("\\", "/"),
                   "previous_index_sha256": before, "generation": updated["generation"],
                   "ordered_ids": ordered, "remaining_invalidated_ids": invalidated,
                   "replacements": {ident: {"artifact_id": ident, **updated["artifacts"][ident]} for ident in ordered},
                   "reviewers": reviewers, "approved_by": approval["approved_by"],
                   "archived_candidates": archived}
        updated["transactions"][request["tx_id"]] = receipt
        I.graph(updated)
        I.publish_file(root / "index.json", I.encoded(updated), replace=True)
        return {**receipt, "decision": "COMMITTED", "writer_activation": "NOT_GRANTED"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "preview", "commit"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--request", type=Path)
    parser.add_argument("--sha256")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--trusted-issuer", action="append", default=[])
    args = parser.parse_args()
    try:
        if args.out:
            I.require(not args.out.resolve().is_relative_to(args.root.resolve()), "REBUILD_REPORT_INSIDE_RUNTIME")
            I.require(not args.out.exists(), "REBUILD_REPORT_EXISTS")
        if args.action == "plan":
            result = plan(args.root)
        else:
            I.require(args.request is not None and args.sha256 is not None, "REBUILD_REQUEST_REQUIRED")
            result = preview(args.root, args.request, args.sha256) if args.action == "preview" else \
                commit(args.root, args.request, args.sha256, args.trusted_issuer)
        if args.out:
            I.publish_file(args.out.resolve(), I.encoded(result))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as error:
        print("BLOCKED:" + str(error))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
