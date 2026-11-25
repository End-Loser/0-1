#!/usr/bin/env python3
"""Deterministically validate, assign stable IDs, and freeze an approved chapter plan.

Input manifest JSON example:
{
  "project_id":"LINWAN", "chapter":6, "version":"V1",
  "artifacts":[
    {"role":"event_sequence","path":"P14C.json","required_status":"VALIDATED"},
    {"role":"outline","path":"P15A.json","required_status":"VALIDATED"},
    {"role":"hook","path":"P15B.json","required_status":"VALIDATED","optional":true}
  ],
  "blocking_findings": [], "dependency_manifest_complete": true,
  "output_dir":"frozen/chapter-006"
}

The script never edits sources. It refuses unresolved dependencies, DRAFT inputs,
placeholder IDs, missing dependencies, or overwrite of an existing frozen output.
"""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, re, shutil, sys
from pathlib import Path

def load(p: Path):
    with p.open('r', encoding='utf-8') as f: return json.load(f)
def dump(p: Path, d):
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open('w', encoding='utf-8') as f: json.dump(d, f, ensure_ascii=False, indent=2); f.write('\n')
def has_placeholder(obj):
    if isinstance(obj, dict):
        return any(has_placeholder(v) for v in obj.values())
    if isinstance(obj, list):
        return any(has_placeholder(v) for v in obj)
    return isinstance(obj, str) and ("ARTIFACT-ID-PENDING-CODE-ASSIGNMENT" in obj or "BEAT-PLACEHOLDER" in obj)

def walk_replace(obj, project, chapter, role, counter=None):
    if counter is None: counter={'n':0}
    if isinstance(obj, dict):
        for k,v in list(obj.items()):
            if isinstance(v,str) and ('ARTIFACT-ID-PENDING-CODE-ASSIGNMENT' in v or 'BEAT-PLACEHOLDER' in v):
                counter['n']+=1
                if k=='beat_id': obj[k]=f'BEAT-{project}-CH{chapter:02d}-{counter["n"]:02d}'
                else: obj[k]=f'ART-{role.upper()}-{project}-CH{chapter:02d}-V1'
            else: walk_replace(v,project,chapter,role,counter)
    elif isinstance(obj,list):
        for v in obj: walk_replace(v,project,chapter,role,counter)
    return obj

def assign_plan_ids(staged, project, chapter, version):
    """Bind references to beat definitions, never mint IDs at reference sites."""
    mapping = {}
    for item, _, data in staged:
        if item["role"] != "outline":
            continue
        outline = data.get("payload", data)
        for index, beat in enumerate(outline.get("beats", []), 1):
            old = beat.get("beat_id")
            if not isinstance(old, str) or old in mapping:
                raise ValueError("AMBIGUOUS_BEAT_DEFINITION")
            mapping[old] = f"BEAT-{project}-CH{chapter:02d}-{index:02d}"
    def replace(value, role):
        if isinstance(value, dict):
            return {key: replace(row, role) for key, row in value.items()}
        if isinstance(value, list):
            return [replace(row, role) for row in value]
        if isinstance(value, str):
            # This is a format-template mention, not a concrete beat reference.
            value = value.replace("BEAT-PLACEHOLDER-NN", "BEAT-ID-TEMPLATE")
            for old in sorted(mapping, key=len, reverse=True):
                value = value.replace(old, mapping[old])
            value = re.sub(r"ARTIFACT-ID-PENDING-CODE-ASSIGNMENT[\w-]*",
                           f"ART-{role.upper()}-{project}-CH{chapter:02d}-{version}", value)
            if "BEAT-PLACEHOLDER" in value:
                raise ValueError("UNBOUND_BEAT_REFERENCE")
        return value
    return [(item, path, replace(data, item["role"])) for item, path, data in staged]

def timing_gate_for_freeze(manifest, base, staged, snapshots, trusted_issuers):
    """Require bound host evidence whenever the declared closure includes P08."""
    rhythms = [(item, path, data) for item, path, data in staged
               if item["role"] == "rhythm" or data.get("task_id") == "P08"]
    declared = any(isinstance(ref, str) and ref.startswith(("P08-", "ART-RHYTHM-"))
                   for ref in manifest.get("required_ref_ids", []))
    if not (rhythms or declared or manifest.get("requires_timing_approval") is True
            or "timing_gate" in manifest):
        return None
    if not rhythms:
        raise ValueError("TIMING_ARTIFACT_REQUIRED")
    binding = manifest.get("timing_gate")
    if not binding:
        raise ValueError("TIMING_APPROVAL_GATE_REQUIRED")
    path = (base / binding["path"]).resolve()
    if not path.is_relative_to(base):
        raise ValueError("TIMING_GATE_OUTSIDE_MANIFEST_ROOT")
    if hashlib.sha256(path.read_bytes()).hexdigest() != binding["sha256"]:
        raise ValueError("TIMING_GATE_HASH_CHANGED")
    spec = importlib.util.spec_from_file_location("freeze_timing_gate",
                                                Path(__file__).with_name("timing_approval_gate.py"))
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    receipt = gate.verify(path, trusted_issuers)
    if receipt["gate_sha256"] != binding["sha256"]:
        raise ValueError("TIMING_GATE_CHANGED_DURING_CHECK")
    if (receipt["project_id"] != manifest["project_id"]
            or receipt["version"] != manifest.get("version", "V1")
            or manifest["chapter"] not in receipt["chapter_order"]):
        raise ValueError("TIMING_APPROVAL_SCOPE_MISMATCH")
    hashes = receipt["checked_artifact_hashes"]
    for item, source, data in staged:
        if item["role"] not in ("rhythm", "event_sequence", "outline") and data.get("task_id") != "P08":
            continue
        ident = data.get("artifact_id")
        if hashes.get(ident) != snapshots[source]:
            raise ValueError("TIMING_APPROVAL_STAGED_SOURCE_MISMATCH")
        if data.get("chapter") != manifest["chapter"]:
            raise ValueError("TIMING_APPROVAL_STAGED_CHAPTER_MISMATCH")
        if data.get("task_id") == "P08" or item["role"] == "rhythm":
            if ident not in receipt["candidate_ids"]:
                raise ValueError("TIMING_APPROVAL_CANDIDATE_MISMATCH")
        elif not any(row["artifact_id"] == ident and row["role"] == item["role"]
                     and row["chapter"] == manifest["chapter"] for row in receipt["context"]):
            raise ValueError("TIMING_APPROVAL_CONTEXT_MISMATCH")
    for role in ("event_sequence", "outline"):
        if sum(item["role"] == role for item, _, _ in staged) != 1:
            raise ValueError("TIMING_STAGED_CONTEXT_REQUIRED:" + role)
    return receipt

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--manifest', required=True)
    ap.add_argument('--trusted-timing-issuer', action='append', default=[])
    args=ap.parse_args()
    mp=Path(args.manifest).resolve(); m=load(mp); base=mp.parent
    project=str(m['project_id']); chapter=int(m['chapter']); version=str(m.get('version','V1'))
    if m.get('dependency_manifest_complete') is not True:
        print('BLOCKED:DEP_UNCLOSED'); return 2
    blockers=m.get('blocking_findings',[])
    if blockers:
        print('BLOCKED:UPSTREAM_FINDINGS',len(blockers)); return 2
    required_refs=set(m.get('required_ref_ids',[])); resolved_refs=set(m.get('resolved_ref_ids',[]))
    missing_refs=sorted(required_refs-resolved_refs)
    if missing_refs:
        print('BLOCKED:UNRESOLVED_DEPENDENCY_REFS',missing_refs); return 2
    outdir=(base/Path(m['output_dir'])).resolve()
    if outdir.exists():
        print('BLOCKED:OUTPUT_EXISTS_NO_OVERWRITE'); return 3
    staged=[]
    snapshots={}
    source_ids={}
    for item in m.get('artifacts',[]):
        if item.get('optional') and not item.get('path'): continue
        p=(base/Path(item['path'])).resolve()
        if not p.is_file():
            if item.get('optional'): continue
            print('BLOCKED:MISSING',item.get('role'),p); return 2
        raw=p.read_bytes()
        d=json.loads(raw.decode('utf-8-sig'))
        snapshots[p]=hashlib.sha256(raw).hexdigest()
        source_ids[p]=d.get('artifact_id')
        status=d.get('status') or d.get('validation')
        if status != item.get('required_status','VALIDATED'):
            print('BLOCKED:STATUS',item.get('role'),status); return 2
        # P14C sequence must be fully ordered and have no blocking unresolved dependencies.
        if item.get('role')=='event_sequence':
            unresolved=d.get('unresolved_dependencies',[])
            # A malformed/unclassified dependency is not silently treated as safe.
            if any(not isinstance(x,dict) for x in unresolved):
                print('BLOCKED:UNCLASSIFIED_EVENT_DEPENDENCY'); return 2
            if any(x.get('blocking') is True or x.get('severity')=='BLOCKING' for x in unresolved):
                print('BLOCKED:EVENT_DEPENDENCY'); return 2
            if d.get('decision') not in ('SEQUENCED','VALIDATED'):
                print('BLOCKED:EVENT_SEQUENCE_DECISION',d.get('decision')); return 2
            event_order=d.get('event_order')
            if not isinstance(event_order,list) or not event_order:
                print('BLOCKED:EMPTY_EVENT_ORDER'); return 2
            if len(event_order)!=len(set(event_order)):
                print('BLOCKED:DUPLICATE_EVENT_IN_ORDER'); return 2
            expected=d.get('expected_event_count')
            if expected is not None and expected!=len(event_order):
                print('BLOCKED:EVENT_COUNT_MISMATCH',expected,len(event_order)); return 2
        if item.get('role')=='ownership_plan':
            conflicts=d.get('blocking_conflicts',[])
            if conflicts or d.get('decision')!='OWNERSHIP_RESOLVED':
                print('BLOCKED:OWNERSHIP_PLAN'); return 2
            owners=d.get('node_owners',[])
            if not all(isinstance(x,dict) and x.get('node_id') and x.get('unit_id') for x in owners):
                print('BLOCKED:BAD_NODE_OWNER_RECORD'); return 2
            nodes=[x.get('node_id') for x in owners]
            if len(nodes)!=len(set(nodes)):
                print('BLOCKED:DUPLICATE_NODE_OWNER'); return 2
            required_nodes=set(d.get('required_state_nodes',[]))
            if required_nodes and required_nodes-set(nodes):
                print('BLOCKED:UNOWNED_STATE_NODES',sorted(required_nodes-set(nodes))); return 2
            units=d.get('event_units',[])
            expected_owner_map={}
            for unit in units:
                if not isinstance(unit,dict) or not unit.get('unit_id'):
                    print('BLOCKED:BAD_EVENT_UNIT_IN_OWNERSHIP_PLAN'); return 2
                for node in unit.get('owned_state_nodes',[]):
                    if node in expected_owner_map:
                        print('BLOCKED:DUPLICATE_UNIT_NODE_OWNERSHIP',node); return 2
                    expected_owner_map[node]=unit['unit_id']
            actual_owner_map={x['node_id']:x['unit_id'] for x in owners}
            if expected_owner_map != actual_owner_map:
                print('BLOCKED:NODE_OWNER_LEDGER_MISMATCH'); return 2
        # Chapter outline must not be incomplete and must have final event references.
        if item.get('role')=='outline':
            payload=d.get('payload',{}) if isinstance(d.get('payload'),dict) else {}
            slate=d.get('slate_status') or payload.get('slate_status')
            if slate in ('INCOMPLETE','NOT_FREEZABLE','BLOCKED'):
                print('BLOCKED:OUTLINE_INCOMPLETE'); return 2
            if d.get('status') not in ('VALIDATED', 'FROZEN'):
                print('BLOCKED:OUTLINE_NOT_VALIDATED'); return 2
            exp=d.get('expected_event_count',payload.get('expected_event_count'))
            app=d.get('approved_event_count',payload.get('approved_event_count'))
            if exp is not None and app is not None and exp!=app:
                print('BLOCKED:OUTLINE_EVENT_COUNT_MISMATCH',exp,app); return 2
        if item.get('role')=='hook' and d.get('decision')=='CONDITIONAL_ACCEPT' and m.get('hook_conditions_met') is not True:
            print('BLOCKED:HOOK_CONDITIONS_NOT_MET'); return 2
        staged.append((item,p,d))
    if not staged:
        print('BLOCKED:NO_ARTIFACTS'); return 2
    try:
        timing_receipt = timing_gate_for_freeze(m, base, staged, snapshots, args.trusted_timing_issuer)
        staged = assign_plan_ids(staged, project, chapter, version)
        if any(hashlib.sha256(path.read_bytes()).hexdigest() != snapshots[path] for _, path, _ in staged):
            raise ValueError("SOURCE_CHANGED_BEFORE_FREEZE")
    except (OSError, ValueError, KeyError, TypeError) as error:
        print('BLOCKED:' + str(error)); return 2
    outdir.mkdir(parents=True, exist_ok=False)
    entries=[]
    try:
        for item,src,d in staged:
            role=item['role']
            if has_placeholder(d):
                print('BLOCKED:PLACEHOLDER_REMAINS',role)
                raise RuntimeError('placeholder remains after ID assignment')
            d['source_artifact_id'] = source_ids[src]
            aid=f'ART-{role.upper()}-{project}-CH{chapter:02d}-{version}'
            d['artifact_id']=aid
            d['project_id']=project
            d['version']=version
            d['status']='FROZEN'
            dest=outdir/f'{role}.FROZEN.json'
            dump(dest,d)
            h=hashlib.sha256(dest.read_bytes()).hexdigest()
            entries.append({'role':role,'artifact_id':aid,'source_artifact_id':d['source_artifact_id'],
                            'source_sha256':snapshots[src],
                            'source_path':str(src.relative_to(base)),'frozen_path':dest.name,'sha256':h})
        manifest={'project_id':project,'chapter':chapter,'version':version,'status':'FROZEN','artifacts':entries}
        if timing_receipt:
            manifest['timing_approval_gate'] = timing_receipt
        dump(outdir/'APPROVAL_MANIFEST.json',manifest)
        print('FROZEN',outdir,'ARTIFACTS',len(entries)); return 0
    except Exception:
        shutil.rmtree(outdir, ignore_errors=True)
        raise
if __name__=='__main__': sys.exit(main())
