#!/usr/bin/env python3
"""Materialize P14C0 canonical Event units without modifying source Events.

Each source Event must be represented exactly once either by a canonical event
unit or by an explicit, justified exclusion. Merges preserve every source ID.
The tool validates node ownership, assigns derived Event IDs, writes provenance,
and refuses to overwrite an existing output directory.
"""
from __future__ import annotations
import argparse, hashlib, json, sys, shutil
from pathlib import Path


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def dump(p, d):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--expected-events", required=True, help="JSON array of source Event IDs")
    ap.add_argument("--project", required=True)
    ap.add_argument("--chapter", type=int, required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--allow-legacy-provenance", action="store_true",
                    help="Migration-only: allow archived pre-V1.5.0 event units without complete narrative provenance")
    a = ap.parse_args()
    plan = load(a.plan)
    expected_raw = load(a.expected_events)
    event_schema_path = Path(__file__).resolve().parents[1] / "schemas" / "EVENT_MODEL_SCHEMA.json"
    event_schema = load(event_schema_path)
    allowed_event_fields = set(event_schema.get("properties", {}))
    allowed_event_types = set(event_schema.get("properties", {}).get("event_type", {}).get("enum", []))
    if not isinstance(expected_raw, list) or not all(isinstance(x, str) and x for x in expected_raw):
        print("BLOCKED:BAD_EXPECTED_EVENT_LIST"); return 2
    expected = set(expected_raw)
    units = plan.get("event_units", [])
    exclusions = plan.get("excluded_source_events", [])

    if plan.get("status") not in ("VALIDATED", "FROZEN"):
        print("BLOCKED:PLAN_NOT_VALIDATED", plan.get("status")); return 2
    if plan.get("decision") != "OWNERSHIP_RESOLVED" or plan.get("blocking_conflicts"):
        print("BLOCKED:OWNERSHIP_PLAN"); return 2

    seen_sources, owners = [], []
    unit_ids = set()
    for unit in units:
        if not isinstance(unit, dict):
            print("BLOCKED:BAD_EVENT_UNIT"); return 2
        unit_id = unit.get("unit_id")
        if not unit_id or unit_id in unit_ids:
            print("BLOCKED:DUPLICATE_OR_MISSING_UNIT_ID", unit_id); return 2
        unit_ids.add(unit_id)
        src = unit.get("source_event_ids", [])
        if not isinstance(src, list) or not src:
            print("BLOCKED:EMPTY_SOURCE_EVENT_IDS", unit_id); return 2
        seen_sources.extend(src)
        ev = unit.get("canonical_event")
        if not isinstance(ev, dict) or not {"chapter", "event_type", "purpose"}.issubset(ev):
            print("BLOCKED:BAD_CANONICAL_EVENT", unit_id); return 2
        if ev.get("chapter") != a.chapter:
            print("BLOCKED:CANONICAL_EVENT_CHAPTER_MISMATCH", unit_id, ev.get("chapter"), a.chapter); return 2
        unknown_event_fields = sorted(set(ev) - allowed_event_fields)
        if unknown_event_fields:
            print("BLOCKED:NON_R062_EVENT_FIELDS", unit_id, unknown_event_fields); return 2
        if ev.get("event_type") not in allowed_event_types:
            print("BLOCKED:INVALID_R062_EVENT_TYPE", unit_id, ev.get("event_type")); return 2
        if not isinstance(ev.get("purpose"), str) or not ev.get("purpose").strip():
            print("BLOCKED:EVENT_PURPOSE_EMPTY", unit_id); return 2
        nodes = unit.get("owned_state_nodes", [])
        if not isinstance(nodes, list) or not all(isinstance(x, str) and x for x in nodes):
            print("BLOCKED:BAD_OWNED_STATE_NODES", unit_id); return 2
        decision_refs = unit.get("decision_plan_refs", [])
        asset_phase_refs = unit.get("asset_phase_refs", [])
        relationship_refs = unit.get("source_relationship_ids", [])
        if not isinstance(decision_refs, list) or not all(isinstance(x, str) and x for x in decision_refs):
            print("BLOCKED:BAD_DECISION_PLAN_REFS", unit_id); return 2
        if not isinstance(asset_phase_refs, list) or not all(
            isinstance(x, dict) and x.get("asset_id") and x.get("phase") in {"SETUP", "REVEAL", "PAYOFF"}
            and isinstance(x.get("target_chapter"), int) and x.get("target_chapter") == a.chapter
            and x.get("source_opportunity_plan_ref") for x in asset_phase_refs
        ):
            print("BLOCKED:BAD_ASSET_PHASE_REFS", unit_id); return 2
        if not isinstance(relationship_refs, list) or not all(isinstance(x, str) and x for x in relationship_refs):
            print("BLOCKED:BAD_RELATIONSHIP_REFS", unit_id); return 2
        if len(relationship_refs) != len(set(relationship_refs)):
            print("BLOCKED:DUPLICATE_RELATIONSHIP_REF", unit_id); return 2
        if not a.allow_legacy_provenance and not decision_refs:
            print("BLOCKED:MISSING_DECISION_PLAN_PROVENANCE", unit_id); return 2
        if not a.allow_legacy_provenance and len(decision_refs) != len(src):
            print("BLOCKED:DECISION_PLAN_REF_COUNT_MISMATCH", unit_id, len(decision_refs), len(src)); return 2
        if len(decision_refs) != len(set(decision_refs)):
            print("BLOCKED:DUPLICATE_DECISION_PLAN_REF", unit_id); return 2
        if not a.allow_legacy_provenance and len(asset_phase_refs) + len(relationship_refs) == 0:
            # A purely non-asset, non-relationship narrative event must still declare why it has no driver.
            exception = unit.get("non_asset_event_basis")
            if not isinstance(exception, str) or not exception.strip():
                print("BLOCKED:MISSING_EVENT_DRIVER_PROVENANCE", unit_id); return 2
        owners.extend(nodes)

    excluded_ids = []
    for exc in exclusions:
        if not isinstance(exc, dict) or not exc.get("source_event_id") or not exc.get("reason"):
            print("BLOCKED:BAD_EXCLUSION_RECORD"); return 2
        if exc.get("approved") is not True:
            print("BLOCKED:UNAPPROVED_EVENT_EXCLUSION", exc.get("source_event_id")); return 2
        excluded_ids.append(exc["source_event_id"])

    accounted = seen_sources + excluded_ids
    if len(accounted) != len(set(accounted)):
        print("BLOCKED:DUPLICATE_SOURCE_EVENT_ACCOUNTING"); return 2
    if set(accounted) != expected:
        print("BLOCKED:SOURCE_EVENT_COVERAGE", sorted(expected-set(accounted)), sorted(set(accounted)-expected)); return 2
    if len(owners) != len(set(owners)):
        print("BLOCKED:DUPLICATE_NODE_OWNER"); return 2

    # If a top-level node_owners ledger is provided, it must exactly match the
    # per-unit ownership declarations. This avoids two inconsistent sources.
    top_owners = plan.get("node_owners")
    if top_owners is not None:
        if not isinstance(top_owners, list) or not all(
            isinstance(x, dict) and x.get("node_id") and x.get("unit_id") for x in top_owners
        ):
            print("BLOCKED:BAD_TOP_LEVEL_NODE_OWNERS"); return 2
        mapping = {x["node_id"]: x["unit_id"] for x in top_owners}
        if len(mapping) != len(top_owners):
            print("BLOCKED:DUPLICATE_TOP_LEVEL_NODE_OWNER"); return 2
        expected_mapping = {
            node: unit["unit_id"]
            for unit in units
            for node in unit.get("owned_state_nodes", [])
        }
        if mapping != expected_mapping:
            print("BLOCKED:NODE_OWNER_LEDGER_MISMATCH"); return 2

    out = Path(a.out_dir)
    if out.exists():
        print("BLOCKED:OUTPUT_EXISTS_NO_OVERWRITE"); return 3
    out.mkdir(parents=True)
    entries = []
    try:
        for i, unit in enumerate(units, 1):
            ev = dict(unit["canonical_event"])
            eid = f"EV-{a.project}-CH{a.chapter:03d}-{i:02d}"
            ev["id"] = eid
            ev["chapter"] = a.chapter
            p = out / f"{eid}.json"
            dump(p, ev)
            h = hashlib.sha256(p.read_bytes()).hexdigest()
            entries.append({
                "unit_id": unit["unit_id"],
                "event_id": eid,
                "source_event_ids": unit["source_event_ids"],
                "owned_state_nodes": unit.get("owned_state_nodes", []),
                "decision_plan_refs": unit.get("decision_plan_refs", []),
                "asset_phase_refs": unit.get("asset_phase_refs", []),
                "source_relationship_ids": unit.get("source_relationship_ids", []),
                "non_asset_event_basis": unit.get("non_asset_event_basis"),
                "sha256": h,
                "path": p.name,
            })
        manifest = {
            "project": a.project,
            "chapter": a.chapter,
            "status": "MATERIALIZED_DERIVED_EVENTS",
            "source_plan": str(Path(a.plan).name),
            "events": entries,
            "excluded_source_events": exclusions,
            "source_coverage": {
                "expected_count": len(expected),
                "materialized_count": len(seen_sources),
                "explicitly_excluded_count": len(excluded_ids),
                "complete": True,
            },
        }
        dump(out / "EVENT_MATERIALIZATION_MANIFEST.json", manifest)
    except Exception:
        shutil.rmtree(out, ignore_errors=True)
        raise
    print("MATERIALIZED", len(entries), "EVENTS; EXCLUDED", len(excluded_ids), "SOURCES", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
