"""Verify host-approved same-day timing evidence without approving or activating.

The caller supplies trusted issuer policy; JSON issuer names are not signatures.
Use only on host-controlled evidence files. No model calls or source writes.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
CHECKS = ("event_order", "duration", "transitions", "text_consistency", "unknown_time_policy")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def approval_subject(manifest):
    # The approval file binds the subject; excluding its own link avoids a cycle.
    subject = {key: value for key, value in manifest.items() if key != "approval"}
    return sha(json.dumps(subject, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":")).encode("utf-8"))


class Evidence:
    def __init__(self, base):
        self.base = base.resolve()
        self.hashes = {}

    def read(self, binding):
        path = (self.base / binding["path"]).resolve()
        if not path.is_relative_to(self.base):
            raise ValueError("TIMING_EVIDENCE_OUTSIDE_ROOT")
        raw = path.read_bytes()
        actual = sha(raw)
        if actual != binding["sha256"] or (path in self.hashes and self.hashes[path] != actual):
            raise ValueError("TIMING_EVIDENCE_HASH_CHANGED")
        self.hashes[path] = actual
        return json.loads(raw.decode("utf-8-sig"))

    def verify(self):
        for path, expected in self.hashes.items():
            if sha(path.read_bytes()) != expected:
                raise ValueError("TIMING_EVIDENCE_CHANGED_DURING_CHECK")


def boundaries():
    spec = importlib.util.spec_from_file_location("gate_boundaries", ROOT / "tools/writer_boundaries.py")
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def verify(manifest_path, trusted_issuers=()):
    path = Path(manifest_path).resolve()
    raw = path.read_bytes()
    manifest_hash = sha(raw)
    manifest = json.loads(raw.decode("utf-8-sig"))
    evidence = Evidence(path.parent)
    if any(not isinstance(manifest.get(key), str) or not manifest[key].strip()
           for key in ("project_id", "version")):
        raise ValueError("TIMING_PROJECT_VERSION_REQUIRED")
    order = manifest["chapter_order"]
    if (not order or any(type(ch) is not int or ch < 1 for ch in order)
            or order != sorted(set(order))):
        raise ValueError("TIMING_CHAPTER_ORDER_INVALID")
    if manifest["same_day"] is not True:
        raise ValueError("TIMING_MULTI_DAY_REQUIRES_SEPARATE_CLOCK_CONTRACT")
    b = boundaries()
    window = manifest["global_window"]
    low, high = [b.PHASES[window[key]] for key in ("start_phase", "end_phase")]
    if low > high:
        raise ValueError("TIMING_GLOBAL_WINDOW_INVALID")
    schema = json.loads((ROOT / "schemas/RHYTHM_INTENT_SCHEMA.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    checked, candidate_chapters, context_roles, rhythms = {}, [], {}, []
    for row in manifest["candidates"] + manifest["context"]:
        ident, chapter = row["artifact_id"], row["chapter"]
        if (not isinstance(ident, str) or not ident or ident in checked
                or type(chapter) is not int or chapter not in order):
            raise ValueError("TIMING_BOUND_IDENTITY_INVALID")
        data = evidence.read(row)
        if data.get("artifact_id") != ident or data.get("chapter") != chapter:
            raise ValueError("TIMING_ARTIFACT_IDENTITY_MISMATCH")
        checked[ident] = row["sha256"]
    for row in manifest["candidates"]:
        data = evidence.read(row)
        if list(validator.iter_errors(data)):
            raise ValueError("TIMING_CANDIDATE_SCHEMA_INVALID")
        anchor = data["target_anchor"]
        if (data["rhythm_type"] != "顺延" or not isinstance(anchor, dict)
                or not {"start_phase", "end_phase"} <= anchor.keys()):
            raise ValueError("TIMING_STRUCTURED_FORWARD_PLAN_REQUIRED")
        refs = anchor.get("source_refs")
        if not isinstance(refs, list) or not refs or any(not isinstance(ref, str) for ref in refs):
            raise ValueError("TIMING_CANDIDATE_SOURCE_UNBOUND")
        for key in ("start_phase", "end_phase"):
            phase = anchor[key]
            if phase is not None and not low <= b.PHASES[phase] <= high:
                raise ValueError("TIMING_OUTSIDE_APPROVED_WINDOW")
        candidate_chapters.append(row["chapter"])
        rhythms.append({"chapter": row["chapter"], "source_ref": row["artifact_id"],
                        "sha256": row["sha256"], "plan": data})
    if sorted(candidate_chapters) != order:
        raise ValueError("TIMING_CANDIDATE_COVERAGE_INCOMPLETE")
    for row in manifest["context"]:
        role = row["role"]
        if role not in ("event_sequence", "outline"):
            raise ValueError("TIMING_CONTEXT_ROLE_INVALID")
        pair = (row["chapter"], role)
        if pair in context_roles:
            raise ValueError("TIMING_CONTEXT_DUPLICATE")
        context_roles[pair] = row
    if set(context_roles) != {(ch, role) for ch in order for role in ("event_sequence", "outline")}:
        raise ValueError("TIMING_CONTEXT_COVERAGE_INCOMPLETE")
    contextual_refs = {}
    for ch in order:
        sequence = evidence.read(context_roles[(ch, "event_sequence")])
        outline = evidence.read(context_roles[(ch, "outline")])
        if (sequence.get("status") not in ("VALIDATED", "FROZEN")
                or outline.get("status") not in ("VALIDATED", "FROZEN")):
            raise ValueError("TIMING_CONTEXT_NOT_VALIDATED")
        event_order = sequence.get("event_order")
        records = outline.get("payload", outline).get("event_records", [])
        if (not isinstance(event_order, list) or not event_order
                or any(not isinstance(item, str) for item in event_order)
                or len(set(event_order)) != len(event_order)
                or [record.get("id") for record in records] != event_order):
            raise ValueError("TIMING_CONTEXT_EVENT_ORDER_MISMATCH")
        contextual_refs[ch] = {sequence["artifact_id"], outline["artifact_id"], *event_order}
    for index, rhythm in enumerate(sorted(rhythms, key=lambda row: row["chapter"])):
        allowed = set().union(*(contextual_refs[order[j]]
                               for j in range(max(0, index - 1), min(len(order), index + 2))))
        if not set(rhythm["plan"]["target_anchor"]["source_refs"]) <= allowed:
            raise ValueError("TIMING_CANDIDATE_SOURCE_UNBOUND")
    if b.rhythm_findings(rhythms):
        raise ValueError("TIMING_STRUCTURAL_CONFLICT")
    review = evidence.read(manifest["review"])
    if (review.get("decision") != "PASS" or review.get("project_id") != manifest["project_id"]
            or review.get("version") != manifest["version"]
            or review.get("checked_artifact_hashes") != checked
            or review.get("unresolved_blockers") != []):
        raise ValueError("TIMING_SEMANTIC_REVIEW_NOT_CURRENT_PASS")
    for key in CHECKS:
        check = review.get("checks", {}).get(key, {})
        if (check.get("decision") != "PASS" or not isinstance(check.get("evidence"), str)
                or not check["evidence"].strip()):
            raise ValueError("TIMING_SEMANTIC_CHECK_MISSING:" + key)
    if any(row["plan"]["target_anchor"][key] is None for row in rhythms
           for key in ("start_phase", "end_phase")):
        restrictions = review.get("writer_restrictions")
        if (not isinstance(restrictions, list) or not restrictions
                or any(not isinstance(item, str) or not item.strip() for item in restrictions)):
            raise ValueError("TIMING_UNKNOWN_ENDPOINT_RESTRICTIONS_REQUIRED")
    approval = evidence.read(manifest["approval"])
    if approval.get("issuer") not in set(trusted_issuers):
        raise ValueError("TIMING_HOST_ISSUER_NOT_TRUSTED")
    if (approval.get("decision") != "APPROVED"
            or approval.get("scope") != "FREEZE_ONLY"
            or approval.get("subject_sha256") != approval_subject(manifest)
            or approval.get("review_sha256") != manifest["review"]["sha256"]):
        raise ValueError("TIMING_HOST_APPROVAL_NOT_BOUND")
    evidence.verify()
    if sha(path.read_bytes()) != manifest_hash:
        raise ValueError("TIMING_GATE_MANIFEST_CHANGED")
    return {"decision": "HOST_APPROVAL_EVIDENCE_VERIFIED", "project_id": manifest["project_id"],
            "version": manifest["version"], "chapter_order": order,
            "gate_sha256": manifest_hash, "review_sha256": manifest["review"]["sha256"],
            "approval_sha256": manifest["approval"]["sha256"], "checked_artifact_hashes": checked,
            "candidate_ids": [row["artifact_id"] for row in manifest["candidates"]],
            "context": manifest["context"], "issuer": approval["issuer"],
            "writer_restrictions": review.get("writer_restrictions", []),
            "writer_activation": "NOT_GRANTED_REQUIRES_NEW_DEPENDENCIES_AND_P17C",
            "authentication": "CALLER_TRUST_POLICY_AND_HOST_CONTROLLED_FILES_NOT_DIGITAL_SIGNATURE",
            "semantic_truth": "NOT_PROVEN_BY_HASH_OR_STRUCTURAL_CODE"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--trusted-issuer", action="append", default=[])
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    try:
        result = verify(args.manifest, args.trusted_issuer)
        if args.out:
            with args.out.open("x", encoding="utf-8") as stream:
                stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print("BLOCKED:" + str(error))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
