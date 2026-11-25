"""Deterministic DERIVED lifecycle planning; semantic approval remains a host duty."""
from __future__ import annotations

import copy


TERMINAL_STATES = {"TERMINATE": "terminated", "TRANSFER": "transferred", "UPGRADE": "upgraded"}
REVIEW_CHECKS = ("cause", "continuity", "time", "base_consistency", "single_fact_source",
                 "ack_obligations", "downstream_impact")


def validate_history(lines, C):
    registered = {line["id"]: line for line in lines}
    groups = {}
    for line in lines:
        predecessor = line.get("predecessor_line_id")
        if predecessor is not None:
            parent = registered.get(predecessor, {})
            C.require(parent.get("lifecycle", {}).get("successor_line_id") == line["id"],
                      "LIFECYCLE_ORPHAN_OR_FORKED_PREDECESSOR")
        closure = line.get("lifecycle")
        if closure and closure["successor_line_id"] is not None:
            child = registered.get(closure["successor_line_id"], {})
            C.require(child.get("predecessor_line_id") == line["id"]
                      and C.text(line.get("subject_ref"))
                      and child.get("subject_ref") == line["subject_ref"]
                      and child.get("dimension") == line["dimension"]
                      and child.get("valid_from") == closure["effective_chapter"]
                      and (child["nodes"][0]["chapter"], child["nodes"][0].get("seq", 0))
                      == (closure["effective_chapter"], closure["effective_seq"]),
                      "LIFECYCLE_SUCCESSOR_HISTORY_INCONSISTENT")
            C.require((child["scope"] != line["scope"]) if closure["status"] == "transferred"
                      else (child["scope"] == line["scope"]), "LIFECYCLE_HISTORY_SCOPE_INVALID")
            if closure["status"] == "upgraded":
                C.require(child["nodes"][0]["state"] != line["nodes"][-1]["state"]
                          and C.text(child["nodes"][0].get("state_delta")),
                          "LIFECYCLE_HISTORY_UPGRADE_EVIDENCE_REQUIRED")
            else:
                C.require(child["nodes"][0]["state"] == line["nodes"][-1]["state"],
                          "LIFECYCLE_HISTORY_TRANSFER_CHANGED_STATE")
        if C.text(line.get("subject_ref")):
            groups.setdefault((line["subject_ref"], line["dimension"]), []).append(line)
    for cluster in groups.values():
        previous_end = None
        for index, line in enumerate(sorted(cluster, key=lambda row:
                (row["valid_from"], row["nodes"][0].get("seq", 0)))):
            start = (line["valid_from"], line["nodes"][0].get("seq", 0))
            C.require(index == 0 or (previous_end is not None and previous_end <= start),
                      "LIFECYCLE_HISTORY_SINGLE_FACT_SOURCE_OVERLAP")
            end = line.get("valid_to")
            previous_end = ((end, line.get("lifecycle", {}).get("effective_seq", 0))
                            if end is not None else None)


def transition(value, inputs, C, full_lines=None, full_acks=None):
    errors = C.schema_errors(value, "LINE_LIFECYCLE_DECISION_SCHEMA.json")
    C.require(not errors, "LIFECYCLE_SCHEMA_INVALID:" + ";".join(errors))
    lines = inputs["canon_context"]["derived_lines"]
    matches = [line for line in lines if line["id"] == value["line_id"]]
    C.require(len(matches) == 1, "LIFECYCLE_DERIVED_TARGET_REQUIRED_BASE_READ_ONLY")
    old = matches[0]
    C.require(not C.line_errors(old), "LIFECYCLE_SOURCE_LINE_INVALID")
    C.require(old.get("valid_to") in (None, value["effective_chapter"]) and "lifecycle" not in old
              and value["previous_status"] == "active", "LIFECYCLE_OPEN_SOURCE_REQUIRED")
    chapter, seq = value["effective_chapter"], value["effective_seq"]
    C.require(chapter == inputs["chapter"] and chapter >= old["valid_from"],
              "LIFECYCLE_EFFECTIVE_BOUNDARY_INVALID")
    boundary = (chapter, seq)
    C.require(all((node["chapter"], node.get("seq", 0)) < boundary for node in old["nodes"]),
              "LIFECYCLE_WOULD_DISCARD_OR_OVERLAP_APPROVED_NODES")
    decision = value["decision"]
    C.require(value["new_status"] == TERMINAL_STATES[decision],
              "LIFECYCLE_STATUS_TRANSITION_INVALID")
    successor = value["successor_line"]
    refs = C.plan_refs(inputs)
    if decision == "TERMINATE":
        C.require(successor is None, "LIFECYCLE_TERMINATION_HAS_SUCCESSOR")
    else:
        C.require(isinstance(successor, dict), "LIFECYCLE_SUCCESSOR_REQUIRED")
        C.require(not C.line_errors(successor), "LIFECYCLE_SUCCESSOR_LINE_INVALID")
        C.require(successor["id"] not in inputs["canon_context"]["registered_line_ids"]
                  and successor.get("predecessor_line_id") == old["id"],
                  "LIFECYCLE_SUCCESSOR_ID_OR_PREDECESSOR_INVALID")
        C.require(C.text(old.get("subject_ref")) and successor.get("subject_ref") == old["subject_ref"],
                  "LIFECYCLE_STABLE_SUBJECT_REF_REQUIRED")
        C.require(successor["dimension"] == old["dimension"] and C.text(successor["scope"]),
                  "LIFECYCLE_DIMENSION_CHANGE_FORBIDDEN")
        C.require((successor["scope"] != old["scope"]) if decision == "TRANSFER"
                  else (successor["scope"] == old["scope"]), "LIFECYCLE_SCOPE_TRANSITION_INVALID")
        C.require(successor["valid_from"] == chapter and successor.get("valid_to") is None
                  and "lifecycle" not in successor and len(successor["nodes"]) == 1,
                  "LIFECYCLE_SUCCESSOR_INTERVAL_OR_NODES_INVALID")
        node = successor["nodes"][0]
        C.require((node["chapter"], node.get("seq", 0)) == boundary
                  and node.get("causal_status") == "proposed",
                  "LIFECYCLE_SUCCESSOR_MUST_BE_CURRENT_PROPOSAL")
        if decision == "UPGRADE":
            C.require(node["state"] != old["nodes"][-1]["state"] and C.text(node.get("state_delta")),
                      "LIFECYCLE_UPGRADE_CHANGE_EVIDENCE_REQUIRED")
        else:
            C.require(node["state"] == old["nodes"][-1]["state"],
                      "LIFECYCLE_TRANSFER_CANNOT_ALSO_UPGRADE")
        cause = successor["cause"]
        C.require(cause["kind"] in ("event", "outline_beat")
                  and cause["ref"] == value["cause_ref"] and cause["ref"] in refs[cause["kind"]],
                  "LIFECYCLE_SUCCESSOR_CAUSE_MISMATCH")
        for purpose in (successor.get("purpose", {}).get(key) for key in ("setup", "reveal", "payoff")):
            if isinstance(purpose, dict):
                C.require(purpose.get("ref") in set().union(*refs.values()),
                          "LIFECYCLE_SUCCESSOR_PURPOSE_UNREGISTERED")
        # Search the full audit registry, not a relevance-trimmed Writer projection.
        all_lines = full_lines if full_lines is not None else lines
        for other in all_lines:
            if other["id"] == old["id"] or other.get("subject_ref") != old["subject_ref"] \
                    or other["dimension"] != old["dimension"]:
                continue
            end = other.get("valid_to")
            end_position = ((end, other.get("lifecycle", {}).get("effective_seq", 0))
                            if end is not None else None)
            C.require(end_position is not None and end_position <= boundary,
                      "LIFECYCLE_SINGLE_FACT_SOURCE_OVERLAP")
    closed = copy.deepcopy(old)
    closed["valid_to"] = chapter
    closed["lifecycle"] = {
        "status": value["new_status"], "effective_chapter": chapter, "effective_seq": seq,
        "successor_line_id": successor["id"] if successor else None, "reason": value["reason"],
        "causal_status": "proposed",
    }
    C.require(not C.line_errors(closed), "LIFECYCLE_CLOSED_LINE_INVALID")
    acks = full_acks if full_acks is not None else inputs["canon_context"]["ack_nodes"]
    pending = [ack for ack in acks
               if ack["status"] == "pre-registered" and old["id"] in ack.get("related_lines", [])]
    return {"closed_line": closed, "successor_line": copy.deepcopy(successor),
            "previous_line_object_sha256": C.object_sha(old), "pending_acks": pending}


def validate_review(review, candidate, pack, context_hash, result, C):
    C.require(isinstance(review, dict) and review.get("decision") == "PASS"
              and review.get("status") in ("VALIDATED", "FROZEN")
              and C.text(review.get("reviewed_by"))
              and review.get("project_id") == pack["project_id"]
              and type(review.get("chapter")) is int and review["chapter"] == pack["inputs"]["chapter"]
              and review.get("candidate_object_sha256") == C.object_sha(candidate)
              and review.get("input_pack_object_sha256") == C.object_sha(pack)
              and review.get("context_object_sha256") == context_hash
              and review.get("previous_line_object_sha256") == result["previous_line_object_sha256"]
              and review.get("blocking_findings") == [], "LIFECYCLE_REVIEW_NOT_SOURCE_BOUND")
    checks = review.get("transition_checks")
    C.require(isinstance(checks, dict) and set(checks) == set(REVIEW_CHECKS)
              and all(isinstance(row, dict) and row.get("decision") == "PASS" and C.text(row.get("basis"))
                      for row in checks.values()), "LIFECYCLE_SEMANTIC_REVIEW_INCOMPLETE")
    rows = review.get("ack_evidence")
    C.require(isinstance(rows, list) and all(isinstance(row, dict) for row in rows)
              and len(rows) == len(result["pending_acks"]), "LIFECYCLE_ACK_REVIEW_INCOMPLETE")
    by_id = {row.get("ack_id"): row for row in rows if C.text(row.get("ack_id"))}
    C.require(len(by_id) == len(rows), "LIFECYCLE_ACK_REVIEW_DUPLICATE")
    for ack in result["pending_acks"]:
        row = by_id.get(ack["id"], {})
        C.require(row.get("ack_object_sha256") == C.object_sha(ack)
                  and row.get("decision") == "PRESERVE_PENDING" and C.text(row.get("basis")),
                  "LIFECYCLE_ACK_CANNOT_CANCEL_OR_EXECUTE")
