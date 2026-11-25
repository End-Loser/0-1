#!/usr/bin/env python3
"""Create a host-authored P13C terminal artifact when an event has no decision agents.

No LLM call is made. The output is an EventDecisionPlan-compatible terminal receipt.
The source opportunity cluster is never modified and existing output files are never overwritten.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
from pathlib import Path


def canonical_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def materialize(cluster):
    if not isinstance(cluster, dict):
        raise ValueError('EVENT_CLUSTER_NOT_OBJECT')
    cid = cluster.get('cluster_id')
    actors = cluster.get('primary_actors')
    if not isinstance(cid, str) or not cid.strip():
        raise ValueError('MISSING_CLUSTER_ID')
    if not isinstance(actors, list):
        raise ValueError('PRIMARY_ACTORS_MUST_BE_ARRAY')
    required = [a for a in actors if isinstance(a, dict) and a.get('decision_required', True)]
    malformed = [i for i, a in enumerate(actors) if not isinstance(a, dict)]
    if malformed:
        raise ValueError(f'PRIMARY_ACTOR_NOT_OBJECT:{malformed}')
    if required:
        raise ValueError('DECISION_AGENT_PRESENT_P13C_MODEL_TASK_REQUIRED')
    cluster_hash = hashlib.sha256(canonical_bytes(cluster)).hexdigest()
    artifact_id = f'P13C-NA-{hashlib.sha256((cid + cluster_hash).encode()).hexdigest()[:16]}'
    plan = {
        'artifact_id': artifact_id,
        'task_id': 'P13C',
        'status': 'FROZEN',
        'cluster_id': cid,
        'decision': 'NO_AGENT_DECISION_REQUIRED',
        'actor_decisions': [],
        'unresolved_gaps': [],
        'decision_summary': 'Host-authored terminal receipt: this event opportunity has no decision_required participants; no model call was made.',
        'version': 'host-terminal-v1'
    }
    receipt = {
        'artifact_id': artifact_id,
        'task_id': 'P13C',
        'terminal_reason': 'NO_AGENT_DECISION_REQUIRED',
        'cluster_id': cid,
        'source_cluster_sha256': cluster_hash,
        'producer': 'tools/materialize_no_agent_decision.py',
        'model_call_made': False
    }
    return plan, receipt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cluster', required=True, help='EventOpportunityCluster JSON')
    parser.add_argument('--out', required=True, help='New EventDecisionPlan output path')
    parser.add_argument('--receipt', required=True, help='Separate provenance receipt path')
    args = parser.parse_args()
    try:
        cluster = json.loads(Path(args.cluster).read_text(encoding='utf-8'))
        plan, receipt = materialize(cluster)
        out, receipt_path = Path(args.out), Path(args.receipt)
        if out.exists() or receipt_path.exists():
            raise FileExistsError('REFUSE_OVERWRITE_EXISTING_OUTPUT_OR_RECEIPT')
        out.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'decision':'CREATED','plan_path':str(out),'receipt_path':str(receipt_path),'artifact_id':plan['artifact_id'],'model_call_made':False},ensure_ascii=False))
        return 0
    except FileExistsError as exc:
        print(json.dumps({'decision':'BLOCKED','reason':'REFUSE_OVERWRITE','detail':str(exc)},ensure_ascii=False))
        return 2
    except Exception as exc:
        print(json.dumps({'decision':'BLOCKED','reason':'NO_AGENT_TERMINAL_NOT_VALID','detail':str(exc)},ensure_ascii=False))
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
