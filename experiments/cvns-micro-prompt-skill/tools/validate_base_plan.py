"""Check host-bound opening facts and planning provenance, not literary truth."""
import argparse
import copy
import hashlib
import json
import re
from pathlib import Path


def validate_plan(line, anchor, approved_refs=()):
    errors = []
    if not isinstance(line, dict) or not isinstance(anchor, dict):
        return ["INVALID_PLAN_OR_ANCHOR"]
    for field in ("entity_id", "dimension"):
        if line.get(field) != anchor.get(field):
            errors.append("ANCHOR_IDENTITY:" + field)
    if (not anchor.get("source_ref")
            or not re.fullmatch(r"[0-9a-f]{64}", str(anchor.get("source_sha256", "")))):
        errors.append("UNBOUND_OPENING_SOURCE")
    start, end = line.get("valid_from"), line.get("valid_to")
    if (not isinstance(start, int) or isinstance(start, bool) or start < 1
            or (end is not None and (not isinstance(end, int) or isinstance(end, bool) or end < start))):
        return errors + ["INVALID_INTERVAL"]
    nodes = line.get("change_nodes", [])
    if not isinstance(nodes, list) or not nodes or not all(isinstance(n, dict) for n in nodes):
        return errors + ["INVALID_NODES"]
    first = nodes[0]
    if first.get("chapter") != line.get("valid_from"):
        errors.append("OPENING_CHAPTER_MISMATCH")
    if not isinstance(anchor.get("state"), str) or first.get("state") != anchor.get("state"):
        errors.append("OPENING_STATE_MISMATCH")
    if first.get("causal_status") != "established":
        errors.append("OPENING_NOT_ESTABLISHED")
    previous = None
    allowed = set(approved_refs)
    for i, node in enumerate(nodes):
        chapter = node.get("chapter")
        seq = node.get("seq", 0)
        if (not isinstance(chapter, int) or isinstance(chapter, bool)
                or not isinstance(seq, int) or isinstance(seq, bool)):
            errors.append(f"INVALID_POSITION:{i}")
            continue
        position = (chapter, seq)
        if previous is not None and position <= previous:
            errors.append(f"NODE_ORDER:{i}")
        previous = position
        if chapter < start or (end is not None and chapter > end):
            errors.append(f"OUTSIDE_INTERVAL:{i}")
        if i and node.get("causal_status") != "proposed":
            errors.append(f"FUTURE_NOT_PROPOSED:{i}")
        if i and node.get("state") == nodes[i - 1].get("state"):
            errors.append(f"NO_STATE_CHANGE:{i}")
        for field in ("beat_id", "trigger_event_id"):
            if node.get(field) and node[field] not in allowed:
                errors.append(f"UNAPPROVED_REF:{i}:{field}")
    return errors


def validate_bound_source(anchor, source_path):
    raw = Path(source_path).read_bytes()
    errors = []
    if hashlib.sha256(raw).hexdigest() != anchor.get("source_sha256"):
        return ["OPENING_SOURCE_HASH_MISMATCH"]
    try:
        value = json.loads(raw.decode("utf-8-sig"))
        pointer = anchor["source_pointer"]
        if not isinstance(pointer, str) or not pointer.startswith("/"):
            raise ValueError("INVALID_POINTER")
        for token in pointer[1:].split("/"):
            token = token.replace("~1", "/").replace("~0", "~")
            if isinstance(value, list):
                if not token.isdigit() or str(int(token)) != token:
                    raise ValueError("INVALID_ARRAY_INDEX")
                value = value[int(token)]
            else:
                value = value[token]
        if value != anchor.get("source_value"):
            errors.append("OPENING_SOURCE_VALUE_MISMATCH")
    except (KeyError, IndexError, TypeError, ValueError):
        errors.append("INVALID_OPENING_POINTER")
    return errors


def validate_coverage_audit(report):
    if not isinstance(report, dict) or not isinstance(report.get("blocking_findings"), list):
        return ["COVERAGE_BLOCKERS_REQUIRED"]
    decision, blockers = report.get("decision"), report["blocking_findings"]
    errors = []
    if decision == "PASS" and blockers:
        errors.append("COVERAGE_PASS_WITH_BLOCKERS")
    if decision in {"REVIEW", "CONFIRMED_MISSING", "NOT_YET_VERIFIABLE"} and not blockers:
        errors.append("COVERAGE_BLOCKED_WITHOUT_IN_SCOPE_FINDING")
    for i, finding in enumerate(blockers):
        if not isinstance(finding, dict) or any(not finding.get(f) for f in
                ("issue", "source_ref", "affected_node_or_interval")):
            errors.append(f"INVALID_COVERAGE_BLOCKER:{i}")
    return errors


def compact_redundant_nodes(line):
    """Return a new sparse plan plus a loss ledger; never discard action metadata."""
    result = copy.deepcopy(line)
    kept, removed = [], []
    inert_fields = {"chapter", "seq", "state", "change_reason", "change_magnitude",
                    "is_anchor_point", "causal_status"}
    for i, node in enumerate(result["change_nodes"]):
        if (kept and node.get("state") == kept[-1].get("state")
                and node.get("change_magnitude") == 0
                and node.get("causal_status") == "proposed"
                and not node.get("is_anchor_point")
                and set(node) <= inert_fields):
            removed.append({"original_index": i, "node": node,
                            "reason": "Exact repeated state carries forward; no action or other payload."})
        else:
            kept.append(node)
    result["change_nodes"] = kept
    return result, removed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--line", required=True)
    parser.add_argument("--anchor", required=True)
    parser.add_argument("--source", required=True)
    args = parser.parse_args()
    line = json.loads(Path(args.line).read_text(encoding="utf-8-sig"))
    anchor = json.loads(Path(args.anchor).read_text(encoding="utf-8-sig"))
    errors = validate_bound_source(anchor, args.source) + validate_plan(line.get("content", line), anchor)
    print(json.dumps({"decision": "BLOCKED" if errors else "PLAN_STRUCTURE_VALID",
                      "errors": errors, "semantic_source_alignment_requires_review": True}))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
