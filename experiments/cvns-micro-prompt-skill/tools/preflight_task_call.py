#!/usr/bin/env python3
"""Deterministic pre-call skip/block checks for known empty or ineligible tasks.

This tool does not approve arbitrary tasks. When no specific skip rule applies,
it returns NEEDS_HOST_CHECK so the host still verifies dependencies and approvals.
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
from datetime import datetime, timezone


def conflict_tool():
    import importlib.util
    path = Path(__file__).resolve().parent / 'conflict_resolution.py'
    spec = importlib.util.spec_from_file_location('cvns_conflict_resolution', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def canon_tool():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'cvns_canon_context', Path(__file__).with_name('canon_context.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def get(d, *path, default=None):
    cur = d
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def resolution_dependency_errors(entry):
    """Check a host-resolved explanation dependency, never infer it from prose."""
    if not isinstance(entry, dict):
        return ['RESOLUTION_DEPENDENCY_MUST_BE_OBJECT']
    path = entry.get('resolution_manifest_path')
    hash_value = entry.get('resolution_manifest_sha256')
    errors = conflict_tool().verify_bundle(path, hash_value) if path else ['RESOLUTION_MANIFEST_MISSING']
    if errors:
        return errors
    try:
        record = json.loads(Path(path).read_text(encoding='utf-8'))
        classification = json.loads((Path(path).parent / 'controls/classification.json').read_text(encoding='utf-8'))
        if (entry.get('resolution_origin_candidate_ref') != record['origin_candidate_ref']
                or entry.get('relationship_decision') != classification):
            return ['RESOLUTION_CLASSIFICATION_OR_ORIGIN_MISMATCH']
        activation = entry.get('resolution_activation', {})
        revision_ids = {a['artifact_id'] for a in record['artifacts'] if a['role'] == 'revision'}
        active = get(activation, 'active_revision_ids', default=[])
        invalidated = get(activation, 'invalidated_artifact_ids', default=[])
        if (get(activation, 'decision') != 'ACTIVATED'
                or get(activation, 'manifest_sha256') != hash_value
                or get(activation, 'resolution_id') != record['resolution_id']
                or not isinstance(active, list) or any(not isinstance(i, str) for i in active)
                or not revision_ids.issubset(active)
                or not isinstance(invalidated, list) or any(not isinstance(i, str) for i in invalidated)
                or not set(record['invalidated_artifact_ids']).issubset(invalidated)):
            return ['RESOLUTION_NOT_ACTIVATED_BY_HOST']
        return []
    except (OSError, ValueError, TypeError, KeyError):
        return ['RESOLUTION_DEPENDENCY_INVALID']


def writer_dependency_evidence(pack):
    """Resolve host-owned evidence; a READY string alone never certifies freshness."""
    binding = pack.get('writer_dependency_gate')
    if not isinstance(binding, dict):
        raise ValueError('WRITER_DEPENDENCY_GATE_REQUIRED')
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'cvns_writer_dependency_gate', Path(__file__).with_name('writer_dependency_gate.py'))
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    receipt = gate.verify(binding['path'], binding['sha256'], {
        'project_id': pack.get('project_id'), 'version': pack.get('version'),
        'chapter': get(pack, 'inputs', 'chapter')})
    if receipt['decision'] == 'BLOCKED':
        return receipt
    current = get(pack, 'inputs', 'p17c_preflight',
                  default=get(pack, 'inputs', 'chapter_input_preflight', default={}))
    if (not isinstance(current, dict) or current.get('decision') != 'READY'
            or gate.object_sha(current) != receipt['p17c_object_sha256']
            or current.get('artifact_id') != receipt['p17c_artifact_id']
            or current.get('checked_dependency_subject_sha256') != receipt['dependency_subject_sha256']
            or current.get('checked_frozen_manifest_sha256') != receipt['frozen_manifest_sha256']
            or pack.get('frozen_manifest_sha256') != receipt['frozen_manifest_sha256']):
        raise ValueError('WRITER_RUNTIME_EVIDENCE_MISMATCH')
    return receipt


def evaluate(task_id: str, pack: dict, require_writer_dependency_gate=False,
             require_canon_context=False) -> dict:
    x = pack.get('inputs', {}) if isinstance(pack, dict) else {}
    decision, reason = 'NEEDS_HOST_CHECK', 'NO_AUTOMATIC_SKIP_RULE_MATCHED'
    evidence = {}

    if task_id == 'P04B':
        candidate_id = get(x, 'candidate', 'entity_id')
        registry = get(x, 'current_entity_registry', 'entities', default=[])
        existing = {e.get('entity_id') for e in registry if isinstance(e, dict)} if isinstance(registry, list) else set()
        evidence = {'candidate_entity_id': candidate_id, 'registered_entity_ids': sorted(v for v in existing if v)}
        if candidate_id and candidate_id in existing:
            decision, reason = 'SKIP', 'ENTITY_ALREADY_REGISTERED'
        elif not candidate_id:
            decision, reason = 'BLOCKED', 'MISSING_CANDIDATE_ENTITY_ID'

    elif task_id == 'P05E':
        actor_class = get(x, 'actor_class', default=get(x, 'entity', 'actor_class'))
        required = get(x, 'decision_profile_required', default=True)
        existing = get(x, 'existing_profile_status')
        evidence = {'actor_class': actor_class, 'decision_profile_required': required, 'existing_profile_status': existing}
        if required is False or actor_class in {'NON_DECISIONAL', 'NON_AGENT', 'SCENE_ONLY'}:
            decision, reason = 'SKIP', 'NO_CONSEQUENTIAL_DECISION_ROLE'
        elif existing in {'FROZEN', 'VALIDATED'}:
            decision, reason = 'SKIP', 'DECISION_PROFILE_ALREADY_VALID'

    elif task_id == 'P10':
        candidate = get(x, 'relationship_candidate', default={})
        candidate_kind = get(candidate, 'candidate_kind')
        candidate_ref = get(candidate, 'candidate_ref')
        evidence_refs = get(candidate, 'evidence_refs', default=[])
        evidence_refs = evidence_refs if isinstance(evidence_refs, list) else []
        evidence = {'candidate_present': isinstance(candidate, dict) and bool(candidate),
                    'candidate_kind': candidate_kind, 'candidate_ref_present': bool(candidate_ref),
                    'evidence_ref_count': len(evidence_refs)}
        if candidate is None or candidate == {}:
            decision, reason = 'NOT_APPLICABLE', 'NO_MATERIAL_RELATION_CANDIDATE'
        elif not isinstance(candidate, dict):
            decision, reason = 'BLOCKED', 'RELATIONSHIP_CANDIDATE_MUST_BE_OBJECT'
        elif candidate_kind not in {'CONFLICT', 'CAUSAL_INTERACTION'}:
            decision, reason = 'BLOCKED', 'INVALID_RELATIONSHIP_CANDIDATE_KIND'
        elif not candidate_ref or not evidence_refs:
            decision, reason = 'BLOCKED', 'CANONICAL_CANDIDATE_REF_OR_EVIDENCE_MISSING'
        elif candidate_kind == 'CAUSAL_INTERACTION':
            required = ('source_line_id', 'source_node_id', 'target_line_id', 'target_node_id',
                        'causal_direction', 'mechanism_or_path', 'temporal_order_basis')
            missing = [field for field in required if not get(candidate, field)]
            evidence['missing_causal_fields'] = missing
            if missing:
                decision, reason = 'BLOCKED', 'CAUSAL_CANDIDATE_EVIDENCE_INCOMPLETE'

    elif task_id == 'P11A':
        relation = get(x, 'relationship_decision', default={})
        claims = get(x, 'conflict_claim_refs', default=[])
        relation_state = get(relation, 'decision')
        evidence = {'relationship_decision': relation_state,
                    'relationship_status': get(relation, 'status'),
                    'source_claim_count': len(claims) if isinstance(claims, list) else 0}
        if get(x, 'explanation_authorized', default=True) is False:
            decision, reason = 'SKIP', 'CREATIVE_EXPLANATION_DISABLED_USE_REPAIR_ROUTE'
        elif get(relation, 'status') not in {'VALIDATED', 'FROZEN'}:
            decision, reason = 'BLOCKED', 'RELATIONSHIP_NOT_APPROVED'
        elif relation_state not in {'MUTUALLY_EXCLUSIVE_ERROR', 'PENDING_EVIDENCE'}:
            decision, reason = 'NOT_APPLICABLE', 'RELATIONSHIP_DOES_NOT_NEED_EXPLANATION'
        elif relation_state == 'PENDING_EVIDENCE' and get(x, 'explanation_authorized') is not True:
            decision, reason = 'BLOCKED', 'PENDING_EVIDENCE_EXPLANATION_REQUIRES_AUTHORIZATION'
        elif not get(relation, 'artifact_id') or '::' not in str(get(relation, 'candidate_ref', default='')):
            decision, reason = 'BLOCKED', 'RELATIONSHIP_ID_OR_CANONICAL_REF_MISSING'
        elif (not isinstance(claims, list) or len(claims) < 2
              or any(not isinstance(c, str) or not c for c in claims)
              or len(claims) != len(set(claims))):
            decision, reason = 'BLOCKED', 'HOST_BOUND_CONFLICT_CLAIMS_MISSING'
        elif not isinstance(get(relation, 'evidence_refs'), list) or not set(claims).issubset(relation['evidence_refs']):
            decision, reason = 'BLOCKED', 'CONFLICT_CLAIMS_NOT_IN_SOURCE_EVIDENCE'

    elif task_id == 'P11B':
        candidates = get(x, 'explanation_candidates', default={})
        claims = get(x, 'conflict_claim_refs')
        evidence = {'candidate_set_status': get(candidates, 'status')}
        if get(candidates, 'status') not in {'VALIDATED', 'FROZEN'}:
            decision, reason = 'BLOCKED', 'EXPLANATION_CANDIDATES_NOT_APPROVED'
        elif not isinstance(claims, list) or any(not isinstance(c, str) for c in claims):
            decision, reason = 'BLOCKED', 'HOST_BOUND_CONFLICT_CLAIMS_MISSING'
        else:
            errors = conflict_tool().validate_candidates(candidates, claims)
            evidence['structural_errors'] = errors
            if errors:
                decision, reason = 'BLOCKED', 'EXPLANATION_CANDIDATE_STRUCTURE_INVALID'

    elif task_id == 'P12':
        relation = get(x, 'relationship_decision', default={})
        evidence = {'relationship_decision': get(relation, 'decision'),
                    'relationship_status': get(relation, 'status')}
        if get(relation, 'status') not in {'VALIDATED', 'FROZEN'}:
            decision, reason = 'BLOCKED', 'RELATIONSHIP_NOT_APPROVED'
        elif get(relation, 'decision') != 'TENSION_ASSET_CANDIDATE':
            decision, reason = 'NOT_APPLICABLE', 'ONLY_RESOLVED_TENSION_CAN_ENTER_ASSET_DECISION'
        elif not get(relation, 'candidate_ref'):
            decision, reason = 'BLOCKED', 'RELATIONSHIP_CANDIDATE_REF_MISSING'
        elif get(x, 'resolution_origin_candidate_ref'):
            errors = resolution_dependency_errors(x)
            evidence['resolution_errors'] = errors
            if errors:
                decision, reason = 'BLOCKED', 'CONFLICT_RESOLUTION_BUNDLE_INVALID'

    elif task_id == 'P13B':
        assets = get(x, 'selected_assets', default=[])
        links = get(x, 'confirmed_causal_interactions', default=[])
        evidence = {'selected_asset_count': len(assets) if isinstance(assets, list) else 0,
                    'confirmed_causal_interaction_count': len(links) if isinstance(links, list) else 0}
        if not isinstance(assets, list) or not isinstance(links, list):
            decision, reason = 'BLOCKED', 'P13B_INPUT_ARRAY_SHAPE_INVALID'
        elif not assets and not links:
            decision, reason = 'NOT_APPLICABLE', 'NO_ASSET_OR_CAUSAL_EVENT_DRIVER'
        else:
            missing_phase_chapters = []
            for asset in assets:
                if not isinstance(asset, dict):
                    missing_phase_chapters.append('NON_OBJECT_ASSET')
                    continue
                phases = asset.get('required_phases', [])
                phase_map = asset.get('phase_chapters', {})
                if not isinstance(phases, list) or not isinstance(phase_map, dict):
                    missing_phase_chapters.append(asset.get('asset_id', 'UNKNOWN_ASSET'))
                    continue
                for phase in phases:
                    if not isinstance(phase_map.get(phase), int) or phase_map.get(phase) < 1:
                        missing_phase_chapters.append(f"{asset.get('asset_id', 'UNKNOWN')}:{phase}")
            evidence['missing_phase_chapter_refs'] = missing_phase_chapters
            if missing_phase_chapters:
                decision, reason = 'BLOCKED', 'P12B_PHASE_CHAPTERS_MISSING_OR_INVALID'

    elif task_id == 'P13C':
        cluster = get(x, 'event_opportunity_cluster', default={})
        opportunity_status = get(x, 'opportunity_plan_status', default=get(x, 'event_opportunity_plan', 'status'))
        actors = get(cluster, 'primary_actors') if isinstance(cluster, dict) else None
        decision_required = [a for a in actors if isinstance(a, dict) and a.get('decision_required', True)] if isinstance(actors, list) else []
        malformed_actor_rows = [i for i, a in enumerate(actors) if not isinstance(a, dict)] if isinstance(actors, list) else []
        profiles = get(x, 'decision_profiles', default=[])
        profiles = profiles if isinstance(profiles, list) else []
        valid_profiles = {p.get('entity_id'): p for p in profiles if isinstance(p, dict) and p.get('status') in {'VALIDATED', 'FROZEN'}}
        missing_profiles = [a.get('entity_id') for a in decision_required if a.get('profile_required') is True and a.get('entity_id') not in valid_profiles]
        target_chapter = get(cluster, 'target_chapter') if isinstance(cluster, dict) else None
        evidence = {'cluster_present': isinstance(cluster, dict) and bool(cluster), 'decision_actor_count': len(decision_required),
                    'target_chapter': target_chapter,
                    'opportunity_plan_status': opportunity_status,
                    'malformed_actor_indexes': malformed_actor_rows,
                    'required_profile_actor_ids': [a.get('entity_id') for a in decision_required if a.get('profile_required') is True],
                    'missing_valid_profile_actor_ids': missing_profiles}
        if opportunity_status not in {'VALIDATED', 'FROZEN'}:
            decision, reason = 'BLOCKED', 'OPPORTUNITY_PLAN_NOT_VALIDATED_OR_FROZEN'
        elif not isinstance(cluster, dict) or not cluster:
            decision, reason = 'BLOCKED', 'EVENT_OPPORTUNITY_CLUSTER_MISSING'
        elif not isinstance(target_chapter, int) or target_chapter < 1:
            decision, reason = 'BLOCKED', 'EVENT_OPPORTUNITY_TARGET_CHAPTER_MISSING'
        elif not isinstance(actors, list):
            decision, reason = 'BLOCKED', 'PRIMARY_ACTORS_MUST_BE_ARRAY'
        elif malformed_actor_rows:
            decision, reason = 'BLOCKED', 'PRIMARY_ACTOR_ROW_NOT_OBJECT'
        elif any(not a.get('entity_id') for a in decision_required):
            decision, reason = 'BLOCKED', 'DECISION_ACTOR_MISSING_ENTITY_ID'
        elif not decision_required:
            decision, reason = 'NOT_APPLICABLE', 'NO_AGENT_DECISION_REQUIRED'
        elif missing_profiles:
            decision, reason = 'BLOCKED', 'REQUIRED_DECISION_PROFILE_MISSING_OR_UNVALIDATED'

    elif task_id == 'P14A':
        cluster = get(x, 'event_opportunity_cluster', default={})
        opportunity_status = get(x, 'opportunity_plan_status', default=get(x, 'event_opportunity_plan_status'))
        decision_plan = get(x, 'event_decision_plan', default={})
        decision_plan_status = get(decision_plan, 'status') if isinstance(decision_plan, dict) else None
        plan_state = get(decision_plan, 'decision') if isinstance(decision_plan, dict) else None
        allowed = {'READY', 'CONDITIONAL', 'NO_AGENT_DECISION_REQUIRED'}
        evidence = {'cluster_present': isinstance(cluster, dict) and bool(cluster),
                    'target_chapter': get(cluster, 'target_chapter') if isinstance(cluster, dict) else None,
                    'opportunity_plan_status': opportunity_status,
                    'decision_plan_status': decision_plan_status,
                    'decision_plan_state': plan_state}
        if not isinstance(cluster, dict) or not cluster:
            decision, reason = 'BLOCKED', 'EVENT_OPPORTUNITY_CLUSTER_MISSING'
        elif not isinstance(get(cluster, 'target_chapter'), int) or get(cluster, 'target_chapter') < 1:
            decision, reason = 'BLOCKED', 'EVENT_OPPORTUNITY_TARGET_CHAPTER_MISSING'
        elif opportunity_status not in {'VALIDATED', 'FROZEN'}:
            decision, reason = 'BLOCKED', 'OPPORTUNITY_PLAN_NOT_VALIDATED_OR_FROZEN'
        elif not isinstance(decision_plan, dict) or not decision_plan:
            decision, reason = 'BLOCKED', 'EVENT_DECISION_PLAN_MISSING'
        elif decision_plan_status not in {'VALIDATED', 'FROZEN'}:
            decision, reason = 'BLOCKED', 'EVENT_DECISION_PLAN_NOT_VALIDATED_OR_FROZEN'
        elif plan_state not in allowed:
            decision, reason = 'BLOCKED', 'EVENT_DECISION_PLAN_NOT_READY'
        elif plan_state == 'CONDITIONAL' and not get(decision_plan, 'unresolved_gaps', default=[]):
            decision, reason = 'BLOCKED', 'CONDITIONAL_DECISION_PLAN_WITHOUT_EXPLICIT_GAPS'

    elif task_id == 'P17A':
        event = get(x, 'event', default={})
        feasibility = get(x, 'p14b_result', default=get(x, 'feasibility_result', default={}))
        feasibility_status = get(feasibility, 'decision') if isinstance(feasibility, dict) else None
        feasibility_artifact_status = get(feasibility, 'status') if isinstance(feasibility, dict) else None
        decision_plan = get(x, 'event_decision_plan', default={})
        opportunity_cluster = get(x, 'event_opportunity_cluster', default={})
        event_chapter = get(event, 'chapter') if isinstance(event, dict) else None
        opportunity_chapter = get(opportunity_cluster, 'target_chapter') if isinstance(opportunity_cluster, dict) else None
        decision_state = get(decision_plan, 'decision') if isinstance(decision_plan, dict) else None
        decision_status = get(decision_plan, 'status') if isinstance(decision_plan, dict) else None
        evidence = {'event_present': isinstance(event, dict) and bool(event),
                    'event_id': get(event, 'id') if isinstance(event, dict) else None,
                    'p14b_decision': feasibility_status,
                    'p14b_status': feasibility_artifact_status,
                    'event_chapter': event_chapter,
                    'opportunity_target_chapter': opportunity_chapter,
                    'decision_plan_decision': decision_state,
                    'decision_plan_status': decision_status}
        if not isinstance(event, dict) or not event:
            decision, reason = 'BLOCKED', 'EVENT_MISSING'
        elif opportunity_cluster and event_chapter != opportunity_chapter:
            decision, reason = 'BLOCKED', 'EVENT_CHAPTER_OPPORTUNITY_MISMATCH'
        elif feasibility_artifact_status not in {'VALIDATED', 'FROZEN'}:
            decision, reason = 'BLOCKED', 'P14B_NOT_VALIDATED_OR_FROZEN'
        elif feasibility_status not in {'FEASIBLE', 'FEASIBLE_WITH_CONDITIONS'}:
            decision, reason = 'BLOCKED', 'EVENT_NOT_FEASIBLE'
        elif not isinstance(decision_plan, dict) or decision_state not in {'READY', 'CONDITIONAL', 'NO_AGENT_DECISION_REQUIRED'}:
            decision, reason = 'BLOCKED', 'EVENT_DECISION_PLAN_MISSING_OR_NOT_READY'
        elif decision_status not in {'VALIDATED', 'FROZEN'}:
            decision, reason = 'BLOCKED', 'EVENT_DECISION_PLAN_NOT_VALIDATED_OR_FROZEN'
        elif decision_state == 'CONDITIONAL' and not get(decision_plan, 'unresolved_gaps', default=[]):
            decision, reason = 'BLOCKED', 'CONDITIONAL_DECISION_PLAN_WITHOUT_EXPLICIT_GAPS'

    elif task_id == 'P19D':
        event = get(x, 'event', default={})
        feasibility = get(x, 'p14b_result', default=get(x, 'feasibility_result', default={}))
        preflight = get(x, 'p17a_result', default=get(x, 'event_preflight', default={}))
        decision_plan = get(x, 'event_decision_plan', default={})
        profiles = get(x, 'decision_profiles', default=[])
        actors = get(x, 'primary_actors', default=[])
        feasibility_state = get(feasibility, 'decision') if isinstance(feasibility, dict) else None
        feasibility_artifact_status = get(feasibility, 'status') if isinstance(feasibility, dict) else None
        preflight_state = get(preflight, 'decision') if isinstance(preflight, dict) else None
        preflight_artifact_status = get(preflight, 'status') if isinstance(preflight, dict) else None
        decision_state = get(decision_plan, 'decision') if isinstance(decision_plan, dict) else None
        decision_status = get(decision_plan, 'status') if isinstance(decision_plan, dict) else None
        profile_map = {p.get('entity_id'): p for p in profiles if isinstance(p, dict)} if isinstance(profiles, list) else {}
        required_profile_ids = [a.get('entity_id') for a in actors if isinstance(a, dict) and a.get('decision_required', True) and a.get('profile_required') is True] if isinstance(actors, list) else []
        invalid_profile_ids = [aid for aid in required_profile_ids if aid not in profile_map or profile_map[aid].get('status') not in {'VALIDATED', 'FROZEN'}]
        evidence = {'event_present': isinstance(event, dict) and bool(event),
                    'p14b_decision': feasibility_state, 'p14b_status': feasibility_artifact_status,
                    'p17a_decision': preflight_state, 'p17a_status': preflight_artifact_status,
                    'decision_plan_decision': decision_state, 'decision_plan_status': decision_status,
                    'required_profile_actor_ids': required_profile_ids,
                    'invalid_profile_actor_ids': invalid_profile_ids}
        if not isinstance(event, dict) or not event:
            decision, reason = 'SKIP', 'NO_EVENT_TO_AUDIT'
        elif feasibility_artifact_status not in {'VALIDATED', 'FROZEN'}:
            decision, reason = 'SKIP', 'P14B_NOT_VALIDATED_OR_FROZEN'
        elif feasibility_state not in {'FEASIBLE', 'FEASIBLE_WITH_CONDITIONS'}:
            decision, reason = 'SKIP', 'EVENT_NOT_FEASIBLE_OR_UNASSESSED'
        elif preflight_artifact_status not in {'VALIDATED', 'FROZEN'}:
            decision, reason = 'SKIP', 'P17A_NOT_VALIDATED_OR_FROZEN'
        elif preflight_state not in {'READY', 'CONDITIONAL_READY'}:
            decision, reason = 'SKIP', 'EVENT_NOT_READY_FOR_DESIGN_AUDIT'
        elif decision_state not in {'READY', 'CONDITIONAL', 'NO_AGENT_DECISION_REQUIRED'}:
            decision, reason = 'BLOCKED', 'EVENT_DECISION_PLAN_MISSING_OR_NOT_READY'
        elif decision_status not in {'VALIDATED', 'FROZEN'}:
            decision, reason = 'BLOCKED', 'EVENT_DECISION_PLAN_NOT_VALIDATED_OR_FROZEN'
        elif decision_state == 'CONDITIONAL' and not get(decision_plan, 'unresolved_gaps', default=[]):
            decision, reason = 'BLOCKED', 'CONDITIONAL_DECISION_PLAN_WITHOUT_EXPLICIT_GAPS'
        elif invalid_profile_ids:
            decision, reason = 'BLOCKED', 'REQUIRED_DECISION_PROFILE_MISSING_OR_UNVALIDATED'

    elif task_id == 'P05C':
        samples = get(x, 'language_samples', default=[])
        status = get(x, 'language_samples_status')
        evidence = {'language_samples_count': len(samples) if isinstance(samples, list) else 0,
                    'language_samples_status': status}
        if not isinstance(samples, list) or len(samples) == 0 or status in {'EMPTY', 'EMPTY_BY_FIXTURE', 'MISSING'}:
            decision, reason = 'SKIP', 'NO_LANGUAGE_SAMPLE'

    elif task_id == 'P19F':
        card = get(x, 'approved_voice_card', default=get(x, 'voice_card'))
        status = get(x, 'voice_card_status', default=get(card or {}, 'status'))
        evidence = {'voice_card_present': isinstance(card, dict) and bool(card), 'voice_card_status': status}
        if not isinstance(card, dict) or not card or status not in {'APPROVED', 'FROZEN', 'VALIDATED'}:
            decision, reason = 'SKIP', 'NO_APPROVED_VOICE_CARD'

    elif task_id == 'P19B':
        calls = get(x, 'mechanism_calls', default=get(x, 'mechanism_invocations', default=[]))
        evidence = {'mechanism_call_count': len(calls) if isinstance(calls, list) else 0}
        if not isinstance(calls, list) or not calls:
            decision, reason = 'NOT_APPLICABLE', 'NO_MECHANISM_CALLS_IN_SCOPE'

    elif task_id == 'P15B':
        outline = get(x, 'chapter_outline_beat_map', default={})
        payload = get(outline, 'payload', default={})
        hook = get(payload, 'hook')
        evidence = {'hook_present': bool(hook), 'hook_value': hook}
        if not hook:
            decision, reason = 'NOT_APPLICABLE', 'NO_HOOK_OPPORTUNITY'

    elif task_id == 'P14C0':
        events = get(x, 'preflight_events', default=get(x, 'events', default=[]))
        ready = [e for e in events if isinstance(e, dict) and e.get('preflight_decision') in {'READY', 'CONDITIONAL_READY'}]
        evidence = {'event_count': len(events) if isinstance(events, list) else 0, 'ready_event_count': len(ready)}
        if not ready:
            decision, reason = 'NOT_APPLICABLE', 'NO_READY_EVENTS'

    elif task_id in {'P16A', 'P16B', 'P16C'}:
        empty = ((task_id == 'P16A' and get(x, 'derived_state_required') is False)
                 or (task_id == 'P16B' and get(x, 'obligation_required') is False)
                 or (task_id == 'P16C' and get(x, 'lifecycle_change_required') is False))
        if empty:
            decision, reason = 'NOT_APPLICABLE', 'NO_EXPLICIT_P16_TARGET'
        else:
            try:
                refs = canon_tool().plan_refs(x)
                evidence['approved_plan_ref_counts'] = {key: len(value) for key, value in refs.items()}
                if task_id == 'P16A':
                    if not get(x, 'entity_id') or not get(x, 'dimension'):
                        raise ValueError('DERIVED_ENTITY_AND_DIMENSION_REQUIRED')
                elif task_id == 'P16B':
                    selected = get(x, 'obligation_candidate', default={})
                    if (not isinstance(selected, dict) or not isinstance(selected.get('obligation'), str)
                            or not selected['obligation'].strip()
                            or selected.get('source_ref') not in refs['event'] | refs['outline_beat']):
                        raise ValueError('ACK_APPROVED_OBLIGATION_TARGET_REQUIRED')
                elif get(x, 'line_id') not in refs['line']:
                    raise ValueError('LIFECYCLE_REGISTERED_LINE_REQUIRED')
            except (OSError, ValueError, TypeError, KeyError) as error:
                evidence['p16_input_error'] = str(error)
                decision, reason = 'BLOCKED', 'P16_INPUT_NOT_READY'

    elif task_id == 'P18':
        preflight = get(x, 'p17c_preflight', default=get(x, 'chapter_input_preflight', default={}))
        status = get(preflight, 'decision', default=get(preflight, 'status'))
        evidence = {'p17c_decision': status}
        if status != 'READY':
            decision, reason = 'BLOCKED', 'P17C_NOT_READY'
        elif (require_writer_dependency_gate or pack.get('requires_writer_dependency_gate') is True
              or 'writer_dependency_gate' in pack
              or 'canon_context_gate' in pack or require_canon_context
              or pack.get('requires_canon_context') is True
              or get(x, 'chapter_plan_approval', 'timing_approval_gate') is not None):
            try:
                receipt = writer_dependency_evidence(pack)
                evidence['writer_dependency_gate'] = receipt
                if receipt['decision'] == 'BLOCKED':
                    decision, reason = 'BLOCKED', receipt['reason']
            except (OSError, ValueError, TypeError, KeyError) as error:
                evidence['writer_dependency_error'] = str(error)
                decision, reason = 'BLOCKED', 'WRITER_DEPENDENCY_EVIDENCE_INVALID'

    elif task_id == 'P18D':
        authorized = get(pack, 'diagnostic_authorization', default=get(x, 'diagnostic_authorization', False))
        p17c = get(x, 'p17c_preflight', default={})
        p17c_status = get(p17c, 'decision', default=get(p17c, 'status'))
        evidence = {'diagnostic_authorization': authorized, 'p17c_decision': p17c_status}
        if authorized is not True or p17c_status != 'BLOCKED':
            decision, reason = 'BLOCKED', 'DIAGNOSTIC_AUTHORIZATION_OR_BLOCKED_STATE_MISSING'

    elif task_id == 'P19D2':
        text = get(x, 'chapter_text', default=get(x, 'rendered_text'))
        approved = get(x, 'approved_event', default=get(x, 'event', default={}))
        status = get(approved, 'status')
        evidence = {'chapter_text_present': bool(text), 'event_status': status}
        if not text or status not in {'APPROVED', 'FROZEN', 'VALIDATED'}:
            decision, reason = 'SKIP', 'RENDERED_TEXT_OR_APPROVED_EVENT_MISSING'

    elif task_id in {'P19A1', 'P19A2', 'P19C1', 'P19C2', 'P19C3', 'P19G', 'P19H', 'P19I'}:
        text = get(x, 'chapter_text', default=get(x, 'scene_text', default=get(x, 'passage_text')))
        evidence = {'text_present': bool(text)}
        if not text:
            decision, reason = 'SKIP', 'NO_TEXT_TO_AUDIT'

    # Host traces transitive origins for causal/asset/Writer branches alike.
    dependencies = pack.get('resolution_dependencies', [])
    resolution_errors = []
    if not isinstance(dependencies, list):
        resolution_errors.append('RESOLUTION_DEPENDENCIES_MUST_BE_ARRAY')
    else:
        for entry in dependencies:
            resolution_errors.extend(resolution_dependency_errors(entry))
    if resolution_errors:
        evidence['resolution_dependency_errors'] = resolution_errors
        decision, reason = 'BLOCKED', 'CONFLICT_RESOLUTION_DEPENDENCY_INVALID'

    if task_id in {'P16A', 'P16B', 'P16C', 'P17C', 'P18'} and (
            require_canon_context or pack.get('requires_canon_context') is True
            or 'canon_context_gate' in pack):
        try:
            binding = pack.get('canon_context_gate')
            if not isinstance(binding, dict):
                raise ValueError('CANON_CONTEXT_GATE_REQUIRED')
            tool = canon_tool()
            receipt = tool.compile_manifest(binding['path'], binding['sha256'])
            expected_mode = 'writer' if task_id in {'P17C', 'P18'} else 'generator'
            if (receipt['decision'] == 'BLOCKED' or receipt['mode'] != expected_mode
                    or receipt['project_id'] != pack.get('project_id')
                    or receipt['version'] != pack.get('version')
                    or receipt['chapter'] != get(x, 'chapter')):
                raise ValueError('CANON_CONTEXT_SCOPE_OR_BUDGET_MISMATCH')
            if task_id == 'P17C':
                summary = get(x, 'canon_context_summary', default={})
                if (summary.get('context_object_sha256') != receipt['context_object_sha256']
                        or summary.get('source_hashes') != receipt['source_hashes']):
                    raise ValueError('CANON_CONTEXT_SUMMARY_MISMATCH')
                if 'canon_context' in x:
                    raise ValueError('P17C_FULL_CANON_CONTEXT_PROHIBITED')
            elif tool.object_sha(get(x, 'canon_context')) != receipt['context_object_sha256']:
                raise ValueError('CANON_CONTEXT_RUNTIME_SUBSTITUTION')
            if task_id in {'P16A', 'P16B', 'P16C'} and (
                    not receipt['outline_object_sha256']
                    or tool.object_sha(get(x, 'chapter_outline')) != receipt['outline_object_sha256']):
                raise ValueError('P16_OUTLINE_RUNTIME_SUBSTITUTION')
            if task_id == 'P18':
                closure = get(evidence, 'writer_dependency_gate', 'closure_hashes', default={})
                if any(closure.get(ident) != digest for ident, digest in receipt['source_hashes'].items()):
                    raise ValueError('CANON_SOURCES_NOT_IN_WRITER_DEPENDENCY_CLOSURE')
            evidence['canon_context'] = {key: receipt[key] for key in (
                'manifest_sha256', 'context_object_sha256', 'source_hashes',
                'selected_line_ids', 'pending_ack_ids', 'overdue_ack_ids')}
        except (OSError, ValueError, TypeError, KeyError) as error:
            evidence['canon_context_error'] = str(error)
            decision, reason = 'BLOCKED', 'CANON_CONTEXT_EVIDENCE_INVALID'

    return {
        'task_id': task_id,
        'decision': decision,
        'reason': reason,
        'evidence': evidence,
        'input_pack_id': pack.get('pack_id'),
        'run_id': pack.get('run_id'),
        'checked_at_utc': datetime.now(timezone.utc).isoformat(),
        'note': 'NEEDS_HOST_CHECK is not approval; host must still verify dependency closure, version, permissions and consumer.'
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--input-pack', required=True)
    ap.add_argument('--task-id', help='Defaults to input pack task_id')
    ap.add_argument('--out')
    ap.add_argument('--require-writer-dependency-gate', action='store_true',
                    help='Host policy: require current dependency/P17C evidence for P18')
    ap.add_argument('--require-canon-context', action='store_true',
                    help='Host policy: require source-bound CanonIndex context for P16/P17C/P18')
    args = ap.parse_args()
    p = Path(args.input_pack)
    try:
        pack = json.loads(p.read_text(encoding='utf-8'))
    except Exception as exc:
        print(json.dumps({'decision':'BLOCKED','reason':'INVALID_INPUT_PACK','error':str(exc)}, ensure_ascii=False))
        return 2
    task_id = args.task_id or pack.get('task_id')
    if not task_id:
        print(json.dumps({'decision':'BLOCKED','reason':'MISSING_TASK_ID'}, ensure_ascii=False)); return 2
    result = evaluate(task_id, pack, args.require_writer_dependency_gate, args.require_canon_context)
    raw = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    if args.out:
        out = Path(args.out)
        if out.exists():
            print(json.dumps({'decision':'BLOCKED','reason':'REFUSE_OVERWRITE','path':str(out)}, ensure_ascii=False)); return 2
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(raw, encoding='utf-8')
    print(raw, end='')
    return 0 if result['decision'] in {'NEEDS_HOST_CHECK','NOT_APPLICABLE','SKIP'} else 1

if __name__ == '__main__':
    raise SystemExit(main())
