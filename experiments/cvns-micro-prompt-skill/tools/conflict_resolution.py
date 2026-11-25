#!/usr/bin/env python3
"""Validate explanation proposals and freeze approved, hash-bound revisions.

Semantic review and authorization remain host responsibilities. This tool copies
complete approved artifacts; it never invents a rule or edits a source in place.
Requires jsonschema for payload validation; missing dependencies fail closed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APPROVED = {"VALIDATED", "FROZEN"}
CONTROL_NAMES = ("candidates", "review", "impact", "scan", "classification")
REVISION_SCHEMAS = {
    "BASE_LINE": "BASE_LINE_SCHEMA.json",
    "WORLD_RULE": "MECHANISM_RULE_SCHEMA.json",
}


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def manifest_digest(data):
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def payload(data):
    return data.get("content", data) if isinstance(data, dict) else data


def schema_errors(data, filename):
    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        return ["JSONSCHEMA_DEPENDENCY_MISSING"]
    schema = load(ROOT / "schemas" / filename)
    return [
        f"SCHEMA:{'/'.join(map(str, error.absolute_path))}:{error.message}"
        for error in Draft202012Validator(schema).iter_errors(data)
    ]


def validate_candidates(data, expected_claim_refs=None):
    errors = schema_errors(data, "CONFLICT_EXPLANATION_SCHEMA.json")
    if errors:
        return errors
    if "::" not in data["source_candidate_ref"]:
        errors.append("NON_CANONICAL_SOURCE_CANDIDATE_REF")
    claims = set(data["source_claim_refs"])
    if len(claims) < 2:
        errors.append("CONFLICT_NEEDS_AT_LEAST_TWO_SOURCE_CLAIMS")
    if expected_claim_refs is not None and claims != set(expected_claim_refs):
        errors.append("SOURCE_CLAIM_SET_MISMATCH")
    ids = [hypothesis["hypothesis_id"] for hypothesis in data["hypotheses"]]
    if len(ids) != len(set(ids)):
        errors.append("DUPLICATE_HYPOTHESIS_ID")
    if not any(h["kind"] == "MINIMAL_REPAIR" for h in data["hypotheses"]):
        errors.append("MINIMAL_REPAIR_COMPARATOR_MISSING")
    for hypothesis in data["hypotheses"]:
        hid = hypothesis["hypothesis_id"]
        actual = [item["claim_ref"] for item in hypothesis["claim_treatments"]]
        if len(actual) != len(set(actual)) or set(actual) != claims:
            errors.append(f"{hid}:SOURCE_CLAIM_COVERAGE")
        if not set(hypothesis["source_refs"]).issubset(data["source_refs"]):
            errors.append(f"{hid}:UNDECLARED_SOURCE_REF")
        if hypothesis["kind"] == "NEW_MECHANISM" and not any(
            rule["kind"] == "NEW" for rule in hypothesis["rule_requirements"]
        ):
            errors.append(f"{hid}:NEW_MECHANISM_WITHOUT_RULE_PROPOSAL")
    return errors


def validate_review(review, candidates):
    errors = validate_candidates(candidates)
    errors += schema_errors(review, "CONFLICT_EXPLANATION_REVIEW_SCHEMA.json")
    if errors:
        return errors
    if review["source_candidate_ref"] != candidates["source_candidate_ref"]:
        errors.append("REVIEW_SOURCE_CANDIDATE_MISMATCH")
    if review["candidate_set_ref"] != candidates["artifact_id"]:
        errors.append("REVIEW_CANDIDATE_SET_MISMATCH")
    hypothesis_map = {h["hypothesis_id"]: h for h in candidates["hypotheses"]}
    reviewed = [r["hypothesis_id"] for r in review["reviews"]]
    if len(reviewed) != len(set(reviewed)) or set(reviewed) != set(hypothesis_map):
        errors.append("REVIEW_MUST_COVER_EVERY_HYPOTHESIS_ONCE")
    for item in review["reviews"]:
        verdicts = {check["verdict"] for check in item["checks"].values()}
        if item["verdict"] == "ACCEPT_FOR_REVISION" and verdicts != {"PASS"}:
            errors.append(f"{item['hypothesis_id']}:ACCEPT_WITH_FAIL_OR_UNKNOWN")
    selected = review["selected_hypothesis_id"]
    if review["decision"] == "PROPOSE_REVISION":
        item = next((r for r in review["reviews"] if r["hypothesis_id"] == selected), None)
        if selected not in hypothesis_map or item is None:
            errors.append("SELECTED_HYPOTHESIS_MISSING")
        elif item["verdict"] != "ACCEPT_FOR_REVISION":
            errors.append("SELECTED_HYPOTHESIS_NOT_ACCEPTED")
        elif hypothesis_map[selected]["kind"] == "MINIMAL_REPAIR":
            errors.append("MINIMAL_REPAIR_MUST_USE_REPAIR_ROUTE")
        if review["unresolved_gaps"]:
            errors.append("PROPOSE_REVISION_WITH_UNRESOLVED_GAPS")
    elif selected is not None:
        errors.append("TERMINAL_REVIEW_MUST_NOT_SELECT_HYPOTHESIS")
    return errors


def _read_bound(base, row):
    if not isinstance(row, dict) or not isinstance(row.get("path"), str):
        raise ValueError("BOUND_ARTIFACT_PATH_MISSING")
    path = (base / row["path"]).resolve()
    if digest(path) != row.get("sha256"):
        raise ValueError(f"ARTIFACT_HASH_MISMATCH:{row['path']}")
    return path, load(path)


def _pointer(data, pointer):
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ValueError("CLAIM_JSON_POINTER_INVALID")
    current = data
    for raw in pointer[1:].split("/"):
        key = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            if not key.isdigit() or (len(key) > 1 and key.startswith("0")):
                raise ValueError("CLAIM_ARRAY_INDEX_INVALID")
            current = current[int(key)]
        else:
            current = current[key]
    if not isinstance(current, str) or not current.strip():
        raise ValueError("CLAIM_MUST_BIND_NONEMPTY_SOURCE_TEXT")
    return current


def prepare_resolution(manifest, base):
    """Check a host manifest and return exact files to copy, or block."""
    errors, files = [], []
    if not isinstance(manifest, dict):
        return ["RESOLUTION_MANIFEST_NOT_OBJECT"], []
    required = {
        "resolution_id", "source_candidate_ref", "selected_hypothesis_id",
        "candidates_path", "review_path", "impact_path", "approval_path",
        "scan_path", "classification_path", "sources", "claim_bindings",
        "revisions", "rechecks", "dependency_closure_complete",
    }
    if required - manifest.keys():
        return [f"MANIFEST_FIELDS_MISSING:{sorted(required - manifest.keys())}"], []
    if not isinstance(manifest["resolution_id"], str) or not manifest["resolution_id"].strip():
        return ["RESOLUTION_ID_MISSING"], []
    if manifest["dependency_closure_complete"] is not True:
        return ["DEPENDENCY_CLOSURE_NOT_COMPLETE"], []
    try:
        controls = {}
        approval_path = (base / manifest["approval_path"]).resolve()
        approval = load(approval_path)
        if approval.get("decision") != "APPROVED":
            errors.append("HOST_APPROVAL_REQUIRED")
        if approval.get("manifest_sha256") != manifest_digest(manifest):
            errors.append("HOST_APPROVAL_MANIFEST_HASH_MISMATCH")
        hashes = approval.get("control_hashes", {})
        if not isinstance(hashes, dict):
            raise ValueError("CONTROL_HASHES_MUST_BE_OBJECT")
        for name in CONTROL_NAMES:
            path = (base / manifest[f"{name}_path"]).resolve()
            if hashes.get(name) != digest(path):
                errors.append(f"APPROVAL_CONTROL_HASH_MISMATCH:{name}")
            data = load(path)
            if not isinstance(data, dict) or data.get("status") not in APPROVED:
                errors.append(f"CONTROL_NOT_APPROVED:{name}")
            controls[name] = data
            files.append((f"controls/{name}.json", path, data, name, hashes.get(name)))
        files.append(("controls/approval.json", approval_path, approval, "approval", digest(approval_path)))
        candidates, review = controls["candidates"], controls["review"]
        bindings = manifest["claim_bindings"]
        if not isinstance(bindings, list) or not bindings:
            raise ValueError("CLAIM_BINDINGS_MISSING")
        claim_refs = [item["claim_ref"] for item in bindings]
        if len(claim_refs) != len(set(claim_refs)):
            errors.append("DUPLICATE_CLAIM_BINDING")
        errors += validate_candidates(candidates, claim_refs)
        errors += validate_review(review, candidates)
        if errors:
            return errors, []
        if review["decision"] != "PROPOSE_REVISION":
            errors.append("EXPLANATION_NOT_SELECTED_FOR_REVISION")
        if candidates["source_candidate_ref"] != manifest["source_candidate_ref"]:
            errors.append("MANIFEST_SOURCE_CANDIDATE_MISMATCH")
        if review["selected_hypothesis_id"] != manifest["selected_hypothesis_id"]:
            errors.append("MANIFEST_SELECTED_HYPOTHESIS_MISMATCH")
        if errors:
            return errors, []

        sources = {}
        if not isinstance(manifest["sources"], list):
            raise ValueError("SOURCES_MUST_BE_ARRAY")
        for index, row in enumerate(manifest["sources"]):
            path, data = _read_bound(base, row)
            aid = row["artifact_id"]
            if aid in sources or data.get("artifact_id") != aid:
                errors.append(f"SOURCE_ARTIFACT_ID_MISMATCH_OR_DUPLICATE:{aid}")
            if data.get("status") not in APPROVED:
                errors.append(f"SOURCE_NOT_APPROVED:{aid}")
            sources[aid] = data
            files.append((f"sources/{index:03d}.json", path, data, "source", row["sha256"]))
        if not set(candidates["source_refs"]).issubset(sources):
            errors.append("EXPLANATION_SOURCE_ARTIFACTS_MISSING")
        original = sources.get(candidates["source_decision_ref"], {})
        if original.get("task_id") != "P10" or original.get("status") not in APPROVED:
            errors.append("ORIGINAL_RELATIONSHIP_NOT_APPROVED")
        if original.get("candidate_ref") != candidates["source_candidate_ref"]:
            errors.append("ORIGINAL_RELATIONSHIP_CANDIDATE_MISMATCH")
        if original.get("decision") not in {"MUTUALLY_EXCLUSIVE_ERROR", "PENDING_EVIDENCE"}:
            errors.append("ORIGINAL_RELATIONSHIP_NOT_ELIGIBLE")
        for binding in bindings:
            _pointer(sources[binding["artifact_id"]], binding["json_pointer"])
        if not set(claim_refs).issubset(set(original.get("evidence_refs", []))):
            errors.append("CLAIMS_NOT_IN_ORIGINAL_RELATIONSHIP_EVIDENCE")

        impact = controls["impact"]
        change_ref = f"{candidates['artifact_id']}::{review['selected_hypothesis_id']}"
        if impact.get("task_id") != "P20" or impact.get("change_ref") != change_ref:
            errors.append("IMPACT_NOT_BOUND_TO_SELECTED_EXPLANATION")
        for field in ("affected_artifacts", "invalidated_reviews", "retest_scope", "unaffected_scope"):
            if not isinstance(impact.get(field), list) or any(
                not isinstance(value, str) for value in impact.get(field, [])
            ):
                errors.append(f"IMPACT_SCOPE_INVALID:{field}")
        if errors:
            return errors, []

        revisions = {}
        if not isinstance(manifest["revisions"], list):
            raise ValueError("REVISIONS_MUST_BE_ARRAY")
        for index, row in enumerate(manifest["revisions"]):
            path, data = _read_bound(base, row)
            aid, role = row["artifact_id"], row["role"]
            if aid in revisions or aid in sources or data.get("artifact_id") != aid:
                errors.append(f"REVISION_ID_NOT_NEW_OR_DUPLICATE:{aid}")
            if role not in REVISION_SCHEMAS:
                errors.append(f"UNSUPPORTED_REVISION_ROLE:{role}")
                continue
            if data.get("status") not in APPROVED:
                errors.append(f"REVISION_NOT_APPROVED:{aid}")
            errors += [f"{aid}:{e}" for e in schema_errors(payload(data), REVISION_SCHEMAS[role])]
            old_id = row.get("source_artifact_id")
            if old_id:
                if old_id not in sources or old_id not in impact["affected_artifacts"]:
                    errors.append(f"REVISION_SOURCE_NOT_IN_IMPACT_SCOPE:{aid}")
                elif role == "BASE_LINE":
                    for field in ("id", "entity_id", "dimension"):
                        if payload(data).get(field) != payload(sources[old_id]).get(field):
                            errors.append(f"BASE_IDENTITY_CHANGED:{aid}:{field}")
            elif role == "BASE_LINE":
                errors.append(f"BASE_REVISION_REQUIRES_SOURCE:{aid}")
            revisions[aid] = (row, data)
            files.append((f"revisions/{index:03d}.json", path, data, "revision", row["sha256"]))

        receipts = []
        if not isinstance(manifest["rechecks"], list):
            raise ValueError("RECHECKS_MUST_BE_ARRAY")
        for index, row in enumerate(manifest["rechecks"]):
            path, data = _read_bound(base, row)
            if data.get("status") not in APPROVED or data.get("decision") not in {"PASS", "NOT_APPLICABLE"}:
                errors.append(f"RECHECK_NOT_APPROVED_OR_PASS:{index}")
            if data.get("task_id") != row.get("task_id"):
                errors.append(f"RECHECK_TASK_MISMATCH:{index}")
            checked = data.get("checked_artifact_hashes", {})
            if not isinstance(checked, dict):
                errors.append(f"RECHECK_HASH_MAP_INVALID:{index}")
                checked = {}
            for aid, hash_value in checked.items():
                if aid not in revisions or revisions[aid][0]["sha256"] != hash_value:
                    errors.append(f"RECHECK_STALE_OR_UNKNOWN_REVISION:{aid}")
            receipts.append(data)
            files.append((f"rechecks/{index:03d}.json", path, data, "recheck", row["sha256"]))
        rule_ids = {
            payload(data)["rule_id"] for data in sources.values()
            if isinstance(payload(data), dict) and payload(data).get("rule_id")
        }
        rule_ids.update(
            payload(data).get("rule_id") for row, data in revisions.values()
            if row["role"] == "WORLD_RULE"
        )
        revised_rules = {
            payload(data).get("rule_id") for row, data in revisions.values()
            if row["role"] == "WORLD_RULE"
        }
        selected = next(h for h in candidates["hypotheses"] if h["hypothesis_id"] == review["selected_hypothesis_id"])
        revised_source_ids = {row.get("source_artifact_id") for row, _ in revisions.values()}
        source_for_claim = {b["claim_ref"]: b["artifact_id"] for b in bindings}
        for treatment in selected["claim_treatments"]:
            if treatment["treatment"] != "PRESERVED" and (
                source_for_claim[treatment["claim_ref"]] not in revised_source_ids and not revised_rules
            ):
                errors.append(f"CLAIM_CHANGE_NOT_MATERIALIZED:{treatment['claim_ref']}")
        for requirement in selected["rule_requirements"]:
            target = revised_rules if requirement["kind"] != "EXISTING" else rule_ids
            if requirement["rule_ref"] not in target:
                errors.append(f"REQUIRED_RULE_NOT_MATERIALIZED:{requirement['rule_ref']}")
        for aid, (row, data) in revisions.items():
            relevant = [
                receipt for receipt in receipts
                if receipt.get("checked_artifact_hashes", {}).get(aid) == row["sha256"]
            ]
            if row["role"] == "BASE_LINE":
                for task in ("P07A", "P07B", "P07C"):
                    if not any(r.get("task_id") == task and r.get("decision") == "PASS" for r in relevant):
                        errors.append(f"BASE_RECHECK_MISSING:{aid}:{task}")
            else:
                rid = payload(data).get("rule_id")
                peers = rule_ids - {rid}
                for peer in peers:
                    if not any(
                        r.get("task_id") in {"P01C", "P21B"} and r.get("decision") == "PASS"
                        and set(r.get("pair_rule_refs", [])) == {rid, peer}
                        for r in relevant
                    ):
                        errors.append(f"RULE_INTERFACE_RECHECK_MISSING:{rid}:{peer}")
                if not peers and not any(
                    r.get("task_id") in {"P01C", "P21B"} and r.get("decision") == "NOT_APPLICABLE"
                    and r.get("pair_rule_refs") == [] for r in relevant
                ):
                    errors.append(f"RULE_INTERFACE_TERMINAL_MISSING:{rid}")

        scan, classification = controls["scan"], controls["classification"]
        if scan.get("task_id") != "P09" or classification.get("task_id") != "P10":
            errors.append("FRESH_P09_P10_REQUIRED")
        control_ids = [data.get("artifact_id") for data in controls.values()]
        if (any(not isinstance(aid, str) or not aid.strip() for aid in control_ids)
                or len(set(control_ids)) != len(control_ids)
                or set(control_ids) & (set(sources) | set(revisions))):
            errors.append("CONTROL_ARTIFACT_IDS_MISSING_OR_REUSED")
        if scan.get("artifact_id") == manifest["source_candidate_ref"].split("::", 1)[0]:
            errors.append("FRESH_SCAN_ARTIFACT_ID_REQUIRED")
        if not set(revisions).issubset(set(scan.get("source_refs", []))):
            errors.append("FRESH_SCAN_NOT_BOUND_TO_REVISIONS")
        local_ids = {
            row.get("candidate_local_id")
            for key in ("conflict_candidates", "causal_interaction_candidates")
            for row in scan.get(key, []) if isinstance(row, dict)
        }
        expected_refs = {f"{scan.get('artifact_id')}::{local_id}" for local_id in local_ids if local_id}
        if classification.get("candidate_ref") not in expected_refs:
            errors.append("FRESH_CLASSIFICATION_NOT_FROM_FRESH_SCAN")
        if classification.get("candidate_ref") == manifest["source_candidate_ref"]:
            errors.append("ORIGINAL_CANDIDATE_REF_CANNOT_BE_RELABELED")
        if classification.get("decision") not in {
            "TENSION_ASSET_CANDIDATE", "CAUSAL_INTERACTION_CONFIRMED", "NO_MATERIAL_RELATION"
        }:
            errors.append("CONFLICT_STILL_UNRESOLVED")
        for field in ("candidate_ref", "evidence_refs", "time_alignment_basis", "reason"):
            if not classification.get(field):
                errors.append(f"FRESH_CLASSIFICATION_MISSING:{field}")
        contract = load(ROOT / "schemas/output_contracts/P10.json")
        if classification.get("candidate_kind") not in contract["candidate_kind_values"]:
            errors.append("FRESH_CLASSIFICATION_KIND_INVALID")
        if not isinstance(classification.get("evidence_refs"), list):
            errors.append("FRESH_CLASSIFICATION_EVIDENCE_MUST_BE_ARRAY")
        for field in contract["required_fields"] + contract["conditional_fields"].get(classification.get("decision"), []):
            if classification.get(field) in (None, "", [], {}):
                errors.append(f"FRESH_CLASSIFICATION_CONTRACT_MISSING:{field}")
        if classification.get("decision") == "CAUSAL_INTERACTION_CONFIRMED":
            for field in contract["causal_requirements"]:
                if classification.get(field) in (None, "", [], {}):
                    errors.append(f"FRESH_CAUSAL_EVIDENCE_MISSING:{field}")
        if errors:
            return errors, []
        return [], files
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
        return errors + [f"INVALID_RESOLUTION_INPUT:{exc}"], []


def materialize(manifest_path, out_dir):
    base = Path(manifest_path).resolve().parent
    manifest = load(manifest_path)
    errors, files = prepare_resolution(manifest, base)
    if errors:
        raise ValueError(";".join(errors))
    out = Path(out_dir).resolve()
    if out.exists():
        raise ValueError("OUTPUT_EXISTS_NO_OVERWRITE")
    out.parent.mkdir(parents=True, exist_ok=True)
    lock = out.parent / f".{out.name}.resolution-lock"
    lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    stage = None
    try:
        if out.exists():
            raise ValueError("OUTPUT_EXISTS_NO_OVERWRITE")
        stage = Path(tempfile.mkdtemp(prefix=f".{out.name}.", dir=out.parent))
        entries = []
        for relative, source, data, role, expected_hash in files:
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            if digest(source) != expected_hash or digest(target) != expected_hash:
                raise ValueError("SOURCE_CHANGED_DURING_COPY")
            entries.append({
                "path": relative, "sha256": digest(target), "role": role,
                "artifact_id": data.get("artifact_id"),
            })
        # Recheck authorization after copying; refuse inputs changed during staging.
        fresh_errors, _ = prepare_resolution(manifest, base)
        if fresh_errors:
            raise ValueError(";".join(fresh_errors))
        impact = load(stage / "controls/impact.json")
        record = {
            "status": "FROZEN", "resolution_id": manifest["resolution_id"],
            "origin_candidate_ref": manifest["source_candidate_ref"],
            "resolved_candidate_ref": load(stage / "controls/classification.json")["candidate_ref"],
            "selected_hypothesis_id": manifest["selected_hypothesis_id"],
            "approved_manifest_sha256": manifest_digest(manifest),
            "invalidated_artifact_ids": sorted(set(impact["affected_artifacts"] + impact["invalidated_reviews"])),
            "artifacts": entries, "activation_required": True,
            "note": "Host must activate this revision bundle and invalidate affected consumers before Writer use.",
        }
        (stage / "CONFLICT_RESOLUTION_MANIFEST.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        if out.exists():
            raise ValueError("OUTPUT_EXISTS_NO_OVERWRITE")
        stage.rename(out)
        stage = None
        return record
    finally:
        if stage is not None:
            shutil.rmtree(stage)
        os.close(lock_fd)
        lock.unlink()


def verify_bundle(path, expected_sha256):
    try:
        path = Path(path).resolve()
        if not expected_sha256 or digest(path) != expected_sha256:
            return ["RESOLUTION_BUNDLE_HASH_MISMATCH"]
        record = load(path)
        if record.get("status") != "FROZEN" or not record.get("artifacts"):
            return ["RESOLUTION_BUNDLE_NOT_FROZEN"]
        seen = set()
        for item in record["artifacts"]:
            target = (path.parent / item["path"]).resolve()
            if not target.is_relative_to(path.parent) or target == path:
                return ["RESOLUTION_BUNDLE_PATH_ESCAPE"]
            if item["path"] in seen or digest(target) != item["sha256"]:
                return ["RESOLUTION_BUNDLE_ARTIFACT_HASH_MISMATCH"]
            seen.add(item["path"])
        if not {f"controls/{name}.json" for name in CONTROL_NAMES}.issubset(seen):
            return ["RESOLUTION_BUNDLE_CONTROLS_MISSING"]
        return []
    except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError):
        return ["RESOLUTION_BUNDLE_INVALID"]


def main():
    ap = argparse.ArgumentParser()
    target = ap.add_mutually_exclusive_group(required=True)
    target.add_argument("--candidates")
    target.add_argument("--review")
    target.add_argument("--manifest")
    ap.add_argument("--candidate-set")
    ap.add_argument("--claims", help="JSON array of host-bound claim references")
    ap.add_argument("--out-dir", help="Freeze the approved manifest into a new directory")
    args = ap.parse_args()
    try:
        if args.candidates:
            if not args.claims:
                raise ValueError("HOST_BOUND_CLAIMS_REQUIRED")
            errors = validate_candidates(load(args.candidates), load(args.claims))
        elif args.review:
            if not args.candidate_set:
                raise ValueError("CANDIDATE_SET_REQUIRED")
            errors = validate_review(load(args.review), load(args.candidate_set))
        elif args.out_dir:
            materialize(args.manifest, args.out_dir)
            errors = []
        else:
            path = Path(args.manifest).resolve()
            errors, _ = prepare_resolution(load(path), path.parent)
        if errors:
            print("BLOCKED", *errors, sep="\n")
            return 1
        print("PASS: structure/provenance only; semantic review and host activation remain required")
        return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"BLOCKED:{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
