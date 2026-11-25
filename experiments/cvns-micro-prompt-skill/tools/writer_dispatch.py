#!/usr/bin/env python3
"""Version-bound Writer dispatch intents and delivery checks; never approve prose."""
from __future__ import annotations

import copy
from pathlib import Path
import re

import importlib.util


def module(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + ".py"))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


I = module("index_transaction")
P = module("preflight_task_call")
C, G = I.C, I.G


def prepare(root, pack_path, pack_hash):
    """Read-only evidence for a separate trusted host dispatch approval."""
    root = Path(root).resolve()
    snapshots = {}
    pack, _ = G.evidence(root, {"path": str(pack_path), "sha256": pack_hash}, snapshots)
    I.require(isinstance(pack, dict) and pack.get("task_id") == "P18", "DISPATCH_P18_PACK_REQUIRED")
    for key in ("writer_dependency_gate", "canon_context_gate"):
        binding = pack.get(key)
        I.require(isinstance(binding, dict), "DISPATCH_GATE_REQUIRED")
        I.require(Path(binding["path"]).is_absolute(), "DISPATCH_ABSOLUTE_GATE_PATH_REQUIRED")
        path = Path(binding["path"]).resolve()
        I.require(path.is_relative_to(root), "DISPATCH_GATE_OUTSIDE_ROOT")
        manifest, _ = G.evidence(root, binding, snapshots)
        index = (path.parent / manifest["current_index"]["path"]).resolve()
        I.require(index == root / "index.json", "DISPATCH_DIFFERENT_INDEX_LOCK")
    result = P.evaluate("P18", pack, True, True)
    I.require(result["decision"] == "NEEDS_HOST_CHECK", "DISPATCH_PREFLIGHT_BLOCKED")
    dependency = P.writer_dependency_evidence(pack)
    canon = C.compile_manifest(pack["canon_context_gate"]["path"], pack["canon_context_gate"]["sha256"])
    subject = {
        "project_id": pack["project_id"], "version": pack["version"],
        "chapter": pack["inputs"]["chapter"], "pack_sha256": pack_hash,
        "dependency_subject_sha256": dependency["dependency_subject_sha256"],
        "closure_hashes": dependency["closure_hashes"],
        "frozen_manifest_sha256": dependency["frozen_manifest_sha256"],
        "p17c_sha256": dependency["p17c_sha256"],
        "canon_manifest_sha256": canon["manifest_sha256"],
        "canon_context_object_sha256": canon["context_object_sha256"],
        "canon_source_hashes": canon["source_hashes"],
    }
    I.verify_snapshots(snapshots)
    return pack, subject


def dispatch(root, pack_path, pack_hash, lease_id, approval_binding, trusted_issuers, submit):
    """Call a durable nonblocking enqueue callback under the same lock as Canon commits.

    A saved intent is not proof of submission. Ambiguous submission must be resolved by
    the host queue; this function never retries a callback for an existing lease ID.
    """
    root = Path(root).resolve()
    I.require(isinstance(lease_id, str) and re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", lease_id),
              "DISPATCH_LEASE_ID_INVALID")
    G.identifiers(trusted_issuers, "DISPATCH_TRUSTED_ISSUERS_REQUIRED", True)
    I.require(callable(submit), "DISPATCH_SUBMIT_CALLBACK_REQUIRED")
    with I.index_lock(root):
        target = root / "leases" / lease_id / "intent.json"
        I.require(not target.exists(), "DISPATCH_INTENT_EXISTS_RECONCILE_QUEUE")
        pack, subject = prepare(root, pack_path, pack_hash)
        snapshots = {}
        approval, _ = G.evidence(root, approval_binding, snapshots)
        I.require(isinstance(approval, dict)
                  and approval.get("decision") == "AUTHORIZE_WRITER_DISPATCH"
                  and approval.get("approved_by") in trusted_issuers
                  and approval.get("lease_id") == lease_id
                  and approval.get("subject_sha256") == C.object_sha(subject),
                  "DISPATCH_APPROVAL_NOT_BOUND_OR_TRUSTED")
        I.verify_snapshots(snapshots)
        intent = {"lease_id": lease_id, "task_id": "P18",
                  "pack": {"path": str((root / pack_path).resolve()), "sha256": pack_hash},
                  "subject": subject, "approved_by": approval["approved_by"],
                  "approval": approval_binding, "state": "DISPATCH_INTENT_NOT_SUBMISSION_PROOF"}
        I.publish_file(target, I.encoded(intent))
        # The callback must enqueue, not wait for the model while holding the index lock.
        job = submit(copy.deepcopy(pack), copy.deepcopy(intent))
        _, current = prepare(root, pack_path, pack_hash)
        I.require(current == subject, "DISPATCH_SOURCE_CHANGED_DURING_SUBMISSION")
        return {"lease_path": str(target), "lease_sha256": C.sha(I.encoded(intent)),
                "job": job, "quality_approval": "NOT_GRANTED"}


def deliver(root, lease_path, lease_hash, prose, output_path, trusted_issuers):
    """Persist only a version-validated draft; separate semantic/ship review is required."""
    root = Path(root).resolve()
    G.identifiers(trusted_issuers, "DISPATCH_TRUSTED_ISSUERS_REQUIRED", True)
    I.require(isinstance(prose, str) and prose.strip(), "DISPATCH_EMPTY_PROSE")
    with I.index_lock(root):
        lease, _ = G.evidence(root, {"path": str(lease_path), "sha256": lease_hash}, {})
        I.require(isinstance(lease, dict) and lease.get("task_id") == "P18"
                  and lease.get("approved_by") in trusted_issuers
                  and lease.get("state") == "DISPATCH_INTENT_NOT_SUBMISSION_PROOF",
                  "DISPATCH_LEASE_INVALID")
        _, current = prepare(root, lease["pack"]["path"], lease["pack"]["sha256"])
        I.require(current == lease["subject"], "DISPATCH_DELIVERY_VERSION_CHANGED")
        target = (root / output_path).resolve()
        I.require(target.is_relative_to(root) and target != root / "index.json",
                  "DISPATCH_OUTPUT_OUTSIDE_ROOT_OR_INDEX")
        I.require(not target.exists(), "DISPATCH_OUTPUT_EXISTS")
        artifact = {
            "task_id": "P18", "project_id": current["project_id"],
            "version": current["version"], "chapter": current["chapter"],
            "decision": "VERSION_VALIDATED_NOT_SHIPPED",
            "lease_sha256": lease_hash, "text_sha256": C.sha(prose.encode("utf-8")),
            "content": prose, "quality_approval": "NOT_GRANTED",
        }
        I.publish_file(target, I.encoded(artifact))
        return artifact
