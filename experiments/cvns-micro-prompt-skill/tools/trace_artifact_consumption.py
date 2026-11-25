#!/usr/bin/env python3
"""Conservative provenance and consumption tracer for a recorded CVNS run.

It separates exact artifact references from domain/content references. Generic
entity IDs are deliberately excluded: an entity appearing in context does not
mean a decision artifact produced for that entity was consumed.

The tool reports evidence of reference, not semantic usefulness. A downstream
reference can be unused in reasoning; lack of a reference can still be a valid
terminal gate, diagnostic, rejected proposal, or superseded version.
"""
from __future__ import annotations
import argparse, csv, json
from pathlib import Path
from collections import defaultdict

ARTIFACT_KEYS = {"artifact_id", "source_artifact_id", "source_artifact_ids", "artifact_refs", "source_artifacts"}
DOMAIN_KEYS = {
    "asset_id", "asset_ids", "event_id", "event_ids", "source_event_ids",
    "line_id", "line_ids", "conflict_id", "conflict_ids", "source_conflict_id",
    "candidate_id", "candidate_ref", "anchor_id", "lens_slice_id", "node_id", "beat_id", "entry_id",
    "source_variable_lines", "source_variable_refs", "variable_line_refs",
    "relationship_id", "relationship_ids", "profile_ref", "decision_plan_id", "cluster_id"
}
# Only these typed root `id` values are treated as producer identities.
ROOT_ID_PREFIXES = ("BASE-", "EV-", "EVT-", "SA-", "AST-", "CAND-", "ANCHOR-", "ART-")


def load_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def walk_key_values(obj, allowed):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in allowed:
                if isinstance(v, str) and v.strip():
                    yield v.strip()
                elif isinstance(v, list):
                    for x in v:
                        if isinstance(x, str) and x.strip():
                            yield x.strip()
            yield from walk_key_values(v, allowed)
    elif isinstance(obj, list):
        for x in obj:
            yield from walk_key_values(x, allowed)


def collect_producer_ids(data):
    art = set(walk_key_values(data, ARTIFACT_KEYS))
    domain = set(walk_key_values(data, DOMAIN_KEYS))
    if isinstance(data, dict):
        # P09 emits an ordered conflict_candidates array; downstream P10 tasks
        # address candidates as P09-C01, P09-C02, ... . Materialize those stable
        # logical IDs here so the tracer can see that the scan was consumed.
        if isinstance(data.get("conflict_candidates"), list):
            for i, candidate in enumerate(data["conflict_candidates"], 1):
                domain.add(f"P09-C{i:02d}")
                if isinstance(candidate, dict):
                    for k in ("candidate_id", "conflict_id", "id"):
                        v = candidate.get(k)
                        if isinstance(v, str) and v.strip():
                            domain.add(v.strip())
        root_id = data.get("id")
        if isinstance(root_id, str) and root_id.startswith(ROOT_ID_PREFIXES):
            domain.add(root_id)
        # Some event/model records place an Event.id below `event`/`payload`.
        for key in ("event", "canonical_event", "payload"):
            child = data.get(key)
            if isinstance(child, dict):
                cid = child.get("id")
                if isinstance(cid, str) and cid.startswith(ROOT_ID_PREFIXES):
                    domain.add(cid)
    return art, domain


def contains_exact_value(obj, needle):
    """Exact string value match, not substring match."""
    if isinstance(obj, dict):
        return any(contains_exact_value(v, needle) for v in obj.values())
    if isinstance(obj, list):
        return any(contains_exact_value(v, needle) for v in obj)
    return isinstance(obj, str) and obj.strip() == needle


def walk_values(obj, key):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                yield v
            yield from walk_values(v, key)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk_values(v, key)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-root", required=True)
    ap.add_argument("--out", default="artifact_usage.csv")
    args = ap.parse_args()
    root = Path(args.run_root).resolve()
    with (root / "call_log.csv").open(encoding="utf-8-sig", newline="") as f:
        calls = list(csv.DictReader(f))

    packs = []
    pack_by_call = {}
    for call in calls:
        ip = root / call.get("input_pack", "")
        if ip.is_file():
            d = load_json(ip)
            if d is not None:
                packs.append((ip, d))
                pack_by_call[call.get("call_id", "")] = d

    rows = []
    for call in calls:
        op = root / call.get("output_file", "")
        d = load_json(op) if op.is_file() else None
        if d is None:
            rows.append({"call_id": call.get("call_id"), "task_id": call.get("task_id"),
                         "target": call.get("target"), "output_file": call.get("output_file"),
                         "artifact_ids": "", "domain_ids": "", "artifact_consumers": "",
                         "domain_consumers": "", "disposition": "OUTPUT_MISSING",
                         "note": "Output missing/unparseable; inspect run log."})
            continue

        artifact_ids, domain_ids = collect_producer_ids(d)
        artifact_hits = defaultdict(set)
        domain_hits = defaultdict(set)
        for ip, pack in packs:
            consumer_task = pack.get("task_id", ip.stem)
            consumer_pack = ip.name
            # Do not count producer input pack self-reference.
            if consumer_pack == Path(call.get("input_pack", "")).name:
                continue
            for ident in artifact_ids:
                if contains_exact_value(pack, ident):
                    artifact_hits[consumer_task].add(ident)
            for ident in domain_ids:
                if contains_exact_value(pack, ident):
                    domain_hits[consumer_task].add(ident)

        applied_values = list(walk_values(d, "applied"))
        task = call.get("task_id", "")
        # If another call later produced a newer result for the same task/target,
        # keep this earlier artifact as superseded history rather than waste.
        later_same_target = any(
            other.get("task_id") == task and other.get("target") == call.get("target")
            and other.get("call_id", "") > call.get("call_id", "")
            for other in calls
        )
        if task == "P05C" and (d.get("status") in {"NO_ENVELOPE", "NOT_APPLICABLE", "EMPTY"} or not d.get("voice_card")):
            disposition = "EMPTY_OUTPUT_SHOULD_HAVE_BEEN_SKIPPED"
        elif task == "P19F" and (d.get("voice_card_status") == "EMPTY_CARD_NO_LANGUAGE_SAMPLES" or d.get("baseline_assessment", {}).get("usable_baseline") is False):
            disposition = "NO_BASELINE_PARTIAL_DIAGNOSTIC"
        elif task == "P10" and d.get("decision") in {"NO_MATERIAL_RELATION", "NO_MATERIAL_CONFLICT"}:
            disposition = "TERMINAL_NO_ASSET_NEEDED"
        elif later_same_target and (artifact_hits or domain_hits or task.startswith(("P07", "P17"))):
            disposition = "SUPERSEDED_PRIOR_VERSION"
        elif applied_values and any(v is False for v in applied_values):
            disposition = "PROPOSAL_ONLY_NOT_APPLIED"
        elif artifact_hits:
            disposition = "EXACT_ARTIFACT_REFERENCE"
        elif domain_hits:
            disposition = "DOMAIN_REFERENCE_ONLY"
        elif task.startswith(("P07", "P17", "P19", "P21")):
            disposition = "TERMINAL_GATE_OR_AUDIT"
        else:
            disposition = "UNREFERENCED_REVIEW_REQUIRED"

        rows.append({
            "call_id": call.get("call_id"),
            "task_id": task,
            "target": call.get("target"),
            "output_file": call.get("output_file"),
            "artifact_ids": "|".join(sorted(artifact_ids)),
            "domain_ids": "|".join(sorted(domain_ids)),
            "artifact_consumers": "|".join(sorted({f"{t}:{','.join(sorted(v))}" for t,v in artifact_hits.items()})),
            "domain_consumers": "|".join(sorted({f"{t}:{','.join(sorted(v))}" for t,v in domain_hits.items()})),
            "disposition": disposition,
            "note": "Exact artifact/domain references are provenance evidence only; they do not prove semantic use. Generic entity IDs are excluded. Superseded, proposal, audit, and diagnostic artifacts remain preserved history, not deleted data."
        })

    out = root / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else ["call_id", "task_id"]
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader(); w.writerows(rows)

    counts = defaultdict(int)
    for row in rows:
        counts[row["disposition"]] += 1
    print("rows", len(rows), "output", out)
    for k in sorted(counts):
        print(k, counts[k])
    print("IMPORTANT: references are not proof of semantic use; unreferenced is not automatically waste.")

if __name__ == "__main__":
    main()
