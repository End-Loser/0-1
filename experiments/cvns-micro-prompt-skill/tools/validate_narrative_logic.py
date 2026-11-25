#!/usr/bin/env python3
"""Deterministic structural checks for V1.5.0 event opportunities and decision plans.

This validator checks IDs, coverage, phase assignment, option feasibility and artifact
references. It does NOT judge semantic truth, literary quality, or whether a model's
reasoning is psychologically convincing.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

PHASES = ('SETUP', 'REVEAL', 'PAYOFF')
VALID_PROFILE_STATUSES = {'VALIDATED', 'FROZEN'}
VALID_DECISION_FEASIBILITY = {'FEASIBLE', 'CONDITIONALLY_FEASIBLE'}


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def nonempty(value):
    return value is not None and value != '' and value != [] and value != {}


def _asset_required_phases(asset):
    """Prefer P12B's explicit required_phases; use source requirements only for legacy packs."""
    if 'required_phases' in asset:
        raw = asset.get('required_phases')
        if not isinstance(raw, list):
            return None
        return raw
    mapping = (
        ('SETUP', 'setup_requirements'),
        ('REVEAL', 'reveal_condition'),
        ('PAYOFF', 'payoff_requirements'),
    )
    return [phase for phase, field in mapping if nonempty(asset.get(field))]


def _relationship_id(item):
    if not isinstance(item, dict):
        return None
    return item.get('candidate_ref') or item.get('candidate_id') or item.get('relationship_id') or item.get('id')


def _driver_key(driver):
    if not isinstance(driver, dict):
        return None
    kind, ref = driver.get('kind'), driver.get('ref')
    if kind not in {'ASSET_PHASE', 'CONFIRMED_CAUSAL_INTERACTION'} or not nonempty(ref):
        return None
    return kind, str(ref)


def validate_portfolio(plan, candidates):
    """Check domain-ID partition and immutable phase targets, not narrative value."""
    if not isinstance(plan, dict) or not isinstance(candidates, list):
        return ['PORTFOLIO_OR_CANDIDATES_INVALID']
    errors, sources = [], {}
    for source in candidates:
        if not isinstance(source, dict) or not isinstance(source.get('asset_id'), str):
            errors.append('PORTFOLIO_SOURCE_ID_INVALID')
            continue
        sid = source['asset_id']
        if sid in sources:
            errors.append(f'PORTFOLIO_DUPLICATE_SOURCE:{sid}')
        sources[sid] = source
    accounted, selected, groups = [], {}, {}
    for field in ('selected_assets', 'deferred_assets', 'rejected_assets', 'merged_groups'):
        if not isinstance(plan.get(field), list):
            errors.append(f'PORTFOLIO_ARRAY_REQUIRED:{field}')
    for row in plan.get('selected_assets', []) if isinstance(plan.get('selected_assets'), list) else []:
        if not isinstance(row, dict) or not isinstance(row.get('source_asset_ids'), list):
            errors.append('PORTFOLIO_SELECTED_ROW_INVALID')
            continue
        if not row['source_asset_ids'] or any(not isinstance(sid, str) for sid in row['source_asset_ids']):
            errors.append('PORTFOLIO_SOURCE_REF_INVALID')
            continue
        canonical = row.get('asset_id')
        if not isinstance(canonical, str):
            errors.append('PORTFOLIO_CANONICAL_ID_INVALID')
            continue
        if canonical in selected:
            errors.append(f'PORTFOLIO_DUPLICATE_CANONICAL:{canonical}')
        selected[canonical] = row
        phases = row.get('phase_chapters', {})
        for sid in row['source_asset_ids']:
            if not isinstance(sid, str):
                errors.append('PORTFOLIO_SOURCE_REF_INVALID')
                continue
            accounted.append(sid)
            if sid not in sources:
                errors.append(f'PORTFOLIO_UNKNOWN_SOURCE:{sid}')
                continue
            for phase, field in zip(PHASES, ('introduce_chapter', 'reveal_chapter', 'payoff_chapter')):
                target = sources[sid].get(field)
                actual = phases.get(phase) if isinstance(phases, dict) else None
                if type(target) is not int or type(actual) is not int or actual != target:
                    errors.append(f'PORTFOLIO_PHASE_DRIFT:{sid}:{phase}')
    for field in ('deferred_assets', 'rejected_assets'):
        for row in plan.get(field, []) if isinstance(plan.get(field), list) else []:
            if not isinstance(row, dict) or not isinstance(row.get('asset_id'), str):
                errors.append(f'PORTFOLIO_DISPOSITION_ROW_INVALID:{field}')
                continue
            accounted.append(row['asset_id'])
    for sid in sorted(set(accounted)):
        if accounted.count(sid) > 1:
            errors.append(f'PORTFOLIO_DUPLICATE_DISPOSITION:{sid}')
        if sid not in sources:
            errors.append(f'PORTFOLIO_UNKNOWN_DISPOSITION:{sid}')
    for sid in sorted(set(sources) - set(accounted)):
        errors.append(f'PORTFOLIO_MISSING_DISPOSITION:{sid}')
    for group in plan.get('merged_groups', []) if isinstance(plan.get('merged_groups'), list) else []:
        if not isinstance(group, dict) or not isinstance(group.get('canonical_asset_id'), str):
            errors.append('PORTFOLIO_MERGE_ROW_INVALID')
            continue
        canonical = group['canonical_asset_id']
        refs = group.get('source_asset_ids')
        if canonical in groups:
            errors.append(f'PORTFOLIO_DUPLICATE_MERGE:{canonical}')
        groups[canonical] = group
        row = selected.get(canonical)
        if (not isinstance(refs, list) or any(not isinstance(sid, str) for sid in refs)
                or len(refs) < 2 or len(refs) != len(set(refs))
                or not row or set(refs) != set(row['source_asset_ids'])):
            errors.append(f'PORTFOLIO_MERGE_MAPPING_MISMATCH:{canonical}')
    for canonical, row in selected.items():
        if len(row['source_asset_ids']) > 1 and canonical not in groups:
            errors.append(f'PORTFOLIO_MERGE_GROUP_MISSING:{canonical}')
    return errors


def validate_base_targets(plan, base_nodes):
    """Bind planned changes to source nodes; chapter-end snapshots are not sources."""
    if not isinstance(plan, dict) or not isinstance(base_nodes, list):
        return ['BASE_TARGET_INPUT_INVALID']
    errors, index = [], {}
    for row in base_nodes:
        if not isinstance(row, dict) or not isinstance(row.get('source_node_ref'), str):
            errors.append('BASE_SOURCE_NODE_INVALID')
            continue
        ref, node = row['source_node_ref'], row.get('node')
        if not isinstance(node, dict) or type(node.get('chapter')) is not int:
            errors.append(f'BASE_SOURCE_NODE_INVALID:{ref}')
            continue
        if ref in index:
            errors.append(f'BASE_SOURCE_NODE_DUPLICATE:{ref}')
        index[ref] = node
    clusters = plan.get('event_clusters', [])
    if not isinstance(clusters, list):
        return errors + ['event_clusters_not_array']
    for cluster in clusters:
        if not isinstance(cluster, dict):
            continue
        cid = cluster.get('cluster_id', '?')
        changes = cluster.get('expected_state_changes', [])
        if not isinstance(changes, list):
            continue
        for change in changes:
            if not isinstance(change, dict) or change.get('target_kind') != 'BASE_LINE':
                continue
            ref = change.get('target_ref')
            if not isinstance(ref, str) or ref not in index:
                errors.append(f'{cid}:BASE_TARGET_UNKNOWN:{ref}')
                continue
            node = index[ref]
            chapter = cluster.get('target_chapter')
            if type(chapter) is not int or node['chapter'] != chapter:
                errors.append(f'{cid}:BASE_TARGET_CHAPTER_DRIFT:{ref}:{node["chapter"]}!={chapter}')
            if node.get('causal_status') != 'proposed':
                errors.append(f'{cid}:BASE_TARGET_NOT_PROPOSED:{ref}')
    return errors


def validate_opportunity(plan, assets, confirmed_links, base_nodes=None):
    errors = []
    if not isinstance(plan, dict):
        return ['opportunity_plan_not_object']
    if not nonempty(plan.get('scope_ref')):
        errors.append('MISSING_SCOPE_REF')
    if not isinstance(assets, list):
        return errors + ['selected_assets_not_array']
    if not isinstance(confirmed_links, list):
        return errors + ['confirmed_interactions_not_array']

    asset_map = {}
    expected = set()
    for i, asset in enumerate(assets):
        if not isinstance(asset, dict) or not nonempty(asset.get('asset_id')):
            errors.append(f'ASSET_{i}:MISSING_ASSET_ID')
            continue
        aid = asset['asset_id']
        if aid in asset_map:
            errors.append(f'DUPLICATE_SELECTED_ASSET_ID:{aid}')
            continue
        asset_map[aid] = asset
        phases = _asset_required_phases(asset)
        if phases is None:
            errors.append(f'{aid}:REQUIRED_PHASES_NOT_ARRAY')
            continue
        if not phases:
            errors.append(f'{aid}:SELECTED_ASSET_HAS_NO_REQUIRED_PHASES')
        phase_map = asset.get('phase_chapters', {})
        if not isinstance(phase_map, dict):
            errors.append(f'{aid}:PHASE_CHAPTERS_NOT_OBJECT')
            phase_map = {}
        for phase in phases:
            if phase not in PHASES:
                errors.append(f'{aid}:INVALID_REQUIRED_PHASE:{phase}')
            else:
                expected.add((aid, phase))
                chapter = phase_map.get(phase)
                if type(chapter) is not int or chapter < 1:
                    errors.append(f'{aid}:REQUIRED_PHASE_CHAPTER_MISSING_OR_INVALID:{phase}:{chapter}')

    link_ids = set()
    for i, item in enumerate(confirmed_links):
        rid = _relationship_id(item)
        if not nonempty(rid):
            errors.append(f'CONFIRMED_INTERACTION_{i}:MISSING_RELATIONSHIP_ID')
            continue
        if rid in link_ids:
            errors.append(f'DUPLICATE_CONFIRMED_INTERACTION_ID:{rid}')
        link_ids.add(rid)
        if isinstance(item, dict) and item.get('decision') not in (None, 'CAUSAL_INTERACTION_CONFIRMED'):
            errors.append(f'{rid}:INPUT_NOT_CONFIRMED_CAUSAL_INTERACTION')

    clusters = plan.get('event_clusters', [])
    if not isinstance(clusters, list):
        return errors + ['event_clusters_not_array']
    actual_phases = {}
    assigned_links = {}
    seen_cluster_ids = set()

    for i, cluster in enumerate(clusters):
        if not isinstance(cluster, dict):
            errors.append(f'CLUSTER_{i}_NOT_OBJECT')
            continue
        cid = cluster.get('cluster_id') or f'#{i}'
        if cid in seen_cluster_ids:
            errors.append(f'DUPLICATE_CLUSTER_ID:{cid}')
        seen_cluster_ids.add(cid)
        cluster_chapter = cluster.get('target_chapter')
        if not isinstance(cluster_chapter, int) or cluster_chapter < 1:
            errors.append(f'{cid}:INVALID_TARGET_CHAPTER')

        required_text = (
            'shared_dramatic_question', 'shared_event_purpose', 'clustering_basis',
            'time_window', 'location', 'core_action'
        )
        for field in required_text:
            if not nonempty(cluster.get(field)):
                errors.append(f'{cid}:MISSING:{field}')
        # Parse cluster drivers before checking state-change provenance.
        drivers = cluster.get('driver_refs', [])
        if not isinstance(drivers, list) or not drivers:
            errors.append(f'{cid}:NO_EVENT_DRIVER')
            drivers = []
        driver_keys = []
        for j, driver in enumerate(drivers):
            key = _driver_key(driver)
            if key is None:
                errors.append(f'{cid}:INVALID_DRIVER_REF:{j}')
            else:
                driver_keys.append(key)
        if len(driver_keys) != len(set(driver_keys)):
            errors.append(f'{cid}:DUPLICATE_DRIVER_REF')

        expected_changes = cluster.get('expected_state_changes')
        if not isinstance(expected_changes, list) or not expected_changes:
            errors.append(f'{cid}:MISSING_EXPECTED_STATE_CHANGES')
        else:
            for j, change in enumerate(expected_changes):
                if not isinstance(change, dict):
                    errors.append(f'{cid}:EXPECTED_STATE_CHANGE_{j}_NOT_OBJECT')
                    continue
                if change.get('target_kind') not in {'BASE_LINE', 'DERIVED_LINE_PROPOSAL', 'READER_COGNITION', 'WORLD_STATE'}:
                    errors.append(f'{cid}:EXPECTED_STATE_CHANGE_{j}_INVALID_TARGET_KIND')
                for field in ('target_ref', 'change_intent'):
                    if not nonempty(change.get(field)):
                        errors.append(f'{cid}:EXPECTED_STATE_CHANGE_{j}_MISSING:{field}')
                causes = change.get('cause_driver_refs', [])
                if not isinstance(causes, list) or not causes:
                    errors.append(f'{cid}:EXPECTED_STATE_CHANGE_{j}_MISSING_CAUSE_DRIVER_REFS')
                else:
                    for cause in causes:
                        key = _driver_key(cause)
                        if key is None or key not in driver_keys:
                            errors.append(f'{cid}:EXPECTED_STATE_CHANGE_{j}_CAUSE_NOT_IN_CLUSTER:{cause}')
        if not isinstance(cluster.get('primary_actors'), list):
            errors.append(f'{cid}:PRIMARY_ACTORS_NOT_ARRAY')
        else:
            actor_ids = [a.get('entity_id') for a in cluster['primary_actors'] if isinstance(a, dict)]
            if len(actor_ids) != len(set(actor_ids)):
                errors.append(f'{cid}:DUPLICATE_PRIMARY_ACTOR')

        deployments = cluster.get('asset_deployments', [])
        if not isinstance(deployments, list):
            errors.append(f'{cid}:ASSET_DEPLOYMENTS_NOT_ARRAY')
            deployments = []
        deployment_keys = set()
        for dep in deployments:
            if not isinstance(dep, dict):
                errors.append(f'{cid}:BAD_ASSET_DEPLOYMENT')
                continue
            pair = (dep.get('asset_id'), dep.get('phase'))
            if pair in deployment_keys:
                errors.append(f'{cid}:DUPLICATE_ASSET_PHASE_IN_CLUSTER:{pair}')
            deployment_keys.add(pair)
            if pair[0] not in asset_map:
                errors.append(f'{cid}:UNKNOWN_ASSET:{pair[0]}')
            if pair[1] not in PHASES:
                errors.append(f'{cid}:INVALID_PHASE:{pair[1]}')
            deployment_chapter = dep.get('target_chapter')
            if not isinstance(deployment_chapter, int) or deployment_chapter != cluster_chapter:
                errors.append(f'{cid}:ASSET_PHASE_TARGET_CHAPTER_MISMATCH:{pair}:{deployment_chapter}!={cluster_chapter}')
            source_phase_map = asset_map.get(pair[0], {}).get('phase_chapters', {})
            source_phase_chapter = source_phase_map.get(pair[1]) if isinstance(source_phase_map, dict) else None
            if not isinstance(source_phase_chapter, int):
                errors.append(f'{cid}:SOURCE_PHASE_CHAPTER_MISSING:{pair}')
            elif deployment_chapter != source_phase_chapter:
                errors.append(f'{cid}:ASSET_PHASE_RESCHEDULE_FORBIDDEN:{pair}:{deployment_chapter}!={source_phase_chapter}')
            if pair not in expected:
                errors.append(f'{cid}:UNEXPECTED_OR_EMPTY_PHASE:{pair}')
            actual_phases[pair] = actual_phases.get(pair, 0) + 1
            if not nonempty(dep.get('function_to_serve')):
                errors.append(f'{cid}:MISSING_PHASE_FUNCTION:{pair}')
            if not nonempty(dep.get('payload_ceiling')):
                errors.append(f'{cid}:MISSING_PHASE_PAYLOAD_CEILING:{pair}')
            expected_driver = ('ASSET_PHASE', f'{pair[0]}:{pair[1]}')
            if expected_driver not in driver_keys:
                errors.append(f'{cid}:MISSING_ASSET_PHASE_DRIVER:{pair}')

        source_relationships = cluster.get('source_relationship_ids', [])
        if not isinstance(source_relationships, list):
            errors.append(f'{cid}:SOURCE_RELATIONSHIPS_NOT_ARRAY')
            source_relationships = []
        if len(source_relationships) != len(set(source_relationships)):
            errors.append(f'{cid}:DUPLICATE_SOURCE_RELATIONSHIP_ID')
        for rid in source_relationships:
            if rid not in link_ids:
                errors.append(f'{cid}:UNCONFIRMED_RELATIONSHIP_REF:{rid}')
            assigned_links[rid] = assigned_links.get(rid, 0) + 1
            if ('CONFIRMED_CAUSAL_INTERACTION', str(rid)) not in driver_keys:
                errors.append(f'{cid}:MISSING_CAUSAL_DRIVER_REF:{rid}')
        for kind, ref in driver_keys:
            if kind == 'CONFIRMED_CAUSAL_INTERACTION' and ref not in source_relationships:
                errors.append(f'{cid}:ORPHAN_CAUSAL_DRIVER_REF:{ref}')

        if not deployments and not source_relationships:
            errors.append(f'{cid}:NO_MATERIAL_DRIVER_CONTENT')
        if source_relationships and not nonempty(cluster.get('initiating_force_or_condition')):
            errors.append(f'{cid}:CAUSAL_CLUSTER_MISSING_INITIATING_FORCE')

    for key in ('deferred_asset_phases', 'no_event_asset_phases'):
        items = plan.get(key, [])
        if not isinstance(items, list):
            errors.append(f'{key}_not_array')
            continue
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                errors.append(f'{key}:ITEM_{i}_NOT_OBJECT')
                continue
            pair = (item.get('asset_id'), item.get('phase'))
            if pair not in expected:
                errors.append(f'{key}:UNEXPECTED_PHASE:{pair}')
            actual_phases[pair] = actual_phases.get(pair, 0) + 1
            if not nonempty(item.get('reason')):
                errors.append(f'{key}:MISSING_REASON:{pair}')
            if not nonempty(item.get('revisit_condition')):
                errors.append(f'{key}:MISSING_REVISIT_CONDITION:{pair}')

    for pair in sorted(expected):
        n = actual_phases.get(pair, 0)
        if n != 1:
            errors.append(f'PHASE_ASSIGNMENT_COUNT:{pair}:{n}')
    for pair, n in actual_phases.items():
        if n > 1:
            errors.append(f'DUPLICATE_PHASE_ASSIGNMENT:{pair}:{n}')

    unassigned = plan.get('unassigned_confirmed_interactions', [])
    if not isinstance(unassigned, list):
        errors.append('UNASSIGNED_INTERACTIONS_NOT_ARRAY')
        unassigned = []
    unassigned_ids = []
    for i, item in enumerate(unassigned):
        if not isinstance(item, dict):
            errors.append(f'UNASSIGNED_INTERACTION_{i}_NOT_OBJECT')
            continue
        rid = item.get('relationship_id')
        if rid not in link_ids:
            errors.append(f'UNASSIGNED_INTERACTION_UNKNOWN_ID:{rid}')
        unassigned_ids.append(rid)
        if not nonempty(item.get('reason')):
            errors.append(f'UNASSIGNED_INTERACTION_MISSING_REASON:{rid}')
        if not nonempty(item.get('revisit_condition')):
            errors.append(f'UNASSIGNED_INTERACTION_MISSING_REVISIT_CONDITION:{rid}')
    if len(unassigned_ids) != len(set(unassigned_ids)):
        errors.append('DUPLICATE_UNASSIGNED_INTERACTION')

    for rid in sorted(link_ids):
        n = assigned_links.get(rid, 0) + unassigned_ids.count(rid)
        if n != 1:
            errors.append(f'CONFIRMED_INTERACTION_COVERAGE:{rid}:{n}')
    for rid, n in assigned_links.items():
        if n > 1:
            errors.append(f'DUPLICATE_CAUSAL_INTERACTION_ASSIGNMENT:{rid}:{n}')

    if base_nodes is not None:
        errors += validate_base_targets(plan, base_nodes)
    return errors


def validate_decision_plan(plan, cluster, profiles):
    errors = []
    if not isinstance(plan, dict) or not isinstance(cluster, dict):
        return ['DECISION_PLAN_OR_CLUSTER_NOT_OBJECT']
    if plan.get('cluster_id') != cluster.get('cluster_id'):
        errors.append('CLUSTER_ID_MISMATCH')
    if plan.get('status') not in {'DRAFT', 'REVIEW', 'VALIDATED', 'FROZEN'}:
        errors.append('DECISION_PLAN_INVALID_LIFECYCLE_STATUS')
    actor_rows = cluster.get('primary_actors', [])
    if not isinstance(actor_rows, list):
        return errors + ['PRIMARY_ACTORS_NOT_ARRAY']
    required = set()
    profile_required = set()
    for actor in actor_rows:
        if not isinstance(actor, dict):
            errors.append('PRIMARY_ACTOR_NOT_OBJECT')
            continue
        aid = actor.get('entity_id') or actor.get('actor_id')
        if actor.get('decision_required', True):
            required.add(aid)
            if actor.get('profile_required') is True:
                profile_required.add(aid)
    if None in required:
        errors.append('PRIMARY_ACTOR_MISSING_ENTITY_ID')
        required.discard(None)

    profile_by_actor = {}
    if not isinstance(profiles, list):
        return errors + ['DECISION_PROFILES_NOT_ARRAY']
    for profile in profiles:
        if isinstance(profile, dict) and nonempty(profile.get('entity_id')):
            aid = profile['entity_id']
            if aid in profile_by_actor:
                errors.append(f'DUPLICATE_INPUT_PROFILE:{aid}')
            profile_by_actor[aid] = profile

    decisions = plan.get('actor_decisions', [])
    if not isinstance(decisions, list):
        return errors + ['ACTOR_DECISIONS_NOT_ARRAY']
    seen = set()
    conditional_reason_seen = False
    blocked_reason_seen = False
    for i, decision in enumerate(decisions):
        if not isinstance(decision, dict):
            errors.append(f'ACTOR_DECISION_{i}_NOT_OBJECT')
            continue
        aid = decision.get('actor_id')
        if aid in seen:
            errors.append(f'DUPLICATE_ACTOR_DECISION:{aid}')
        seen.add(aid)
        if aid not in required:
            errors.append(f'UNDECLARED_DECISION_ACTOR:{aid}')
        options = decision.get('feasible_options', [])
        if not isinstance(options, list) or not options:
            errors.append(f'{aid}:NO_OPTIONS')
            continue
        malformed_options = [j for j, option in enumerate(options) if not isinstance(option, dict)]
        if malformed_options:
            errors.append(f'{aid}:OPTIONS_CONTAIN_NON_OBJECT_ITEMS:{malformed_options}')
        option_ids = [o.get('option_id') for o in options if isinstance(o, dict)]
        if any(not nonempty(option_id) for option_id in option_ids):
            errors.append(f'{aid}:OPTION_ID_MISSING')
        if len(option_ids) != len(set(option_ids)):
            errors.append(f'{aid}:DUPLICATE_OPTION_ID')
        for j, option in enumerate(options):
            if not isinstance(option, dict):
                continue
            for field in ('action_intent', 'feasibility', 'expected_gain', 'expected_cost', 'reason_for_or_against'):
                if not nonempty(option.get(field)):
                    errors.append(f'{aid}:OPTION_{j}_MISSING:{field}')
            if option.get('feasibility') not in {'FEASIBLE', 'CONDITIONALLY_FEASIBLE', 'INFEASIBLE'}:
                errors.append(f'{aid}:OPTION_{j}_INVALID_FEASIBILITY')
        option_map = {o.get('option_id'): o for o in options if isinstance(o, dict) and nonempty(o.get('option_id'))}
        selected = option_map.get(decision.get('selected_option_id'))
        if selected is None:
            errors.append(f'{aid}:SELECTED_OPTION_NOT_FOUND')
        elif selected.get('feasibility') == 'INFEASIBLE':
            errors.append(f'{aid}:SELECTED_OPTION_IS_INFEASIBLE')
        elif selected.get('feasibility') == 'CONDITIONALLY_FEASIBLE':
            conditional_reason_seen = True

        feasible = [o for o in options if isinstance(o, dict) and o.get('feasibility') in VALID_DECISION_FEASIBILITY]
        if not feasible:
            errors.append(f'{aid}:NO_FEASIBLE_OR_CONDITIONALLY_FEASIBLE_OPTION')
            blocked_reason_seen = True
        if len(feasible) < 2 and not nonempty(decision.get('single_option_explanation')):
            errors.append(f'{aid}:INSUFFICIENT_ALTERNATIVES_WITHOUT_EXPLANATION')

        for field in ('current_goal', 'operative_desire', 'choice_reason', 'rejected_alternative_reason', 'evidence_refs', 'coherence_status', 'tendency_consistency', 'tendency_consistency_reason'):
            if not nonempty(decision.get(field)):
                errors.append(f'{aid}:MISSING:{field}')
        if not isinstance(decision.get('tendencies_invoked'), list):
            errors.append(f'{aid}:TENDENCIES_INVOKED_NOT_ARRAY')
        coherence = decision.get('coherence_status')
        if coherence == 'CONDITIONAL' or coherence == 'UNRESOLVED' or decision.get('tendency_consistency') == 'UNRESOLVED':
            conditional_reason_seen = True
        if coherence == 'INCOHERENT':
            blocked_reason_seen = True
        if decision.get('tendency_consistency') not in {'CONSISTENT', 'CONTEXTUAL_DEVIATION', 'UNRESOLVED'}:
            errors.append(f'{aid}:INVALID_TENDENCY_CONSISTENCY')
        if not nonempty(decision.get('accepted_costs')):
            errors.append(f'{aid}:MISSING_ACCEPTED_COSTS')
        counterfactual = decision.get('counterfactual_sensitivity')
        if not isinstance(counterfactual, dict):
            errors.append(f'{aid}:MISSING_COUNTERFACTUAL_SENSITIVITY')
        else:
            for field in ('smallest_relevant_change', 'would_choice_change', 'reason'):
                if not nonempty(counterfactual.get(field)):
                    errors.append(f'{aid}:COUNTERFACTUAL_MISSING:{field}')

        pref_required = aid in profile_required or decision.get('profile_required') is True
        pref_ref = decision.get('profile_ref')
        if pref_required:
            profile = profile_by_actor.get(aid)
            if not isinstance(profile, dict):
                errors.append(f'{aid}:REQUIRED_DECISION_PROFILE_MISSING')
            elif profile.get('status') not in VALID_PROFILE_STATUSES:
                errors.append(f'{aid}:DECISION_PROFILE_NOT_VALIDATED_OR_FROZEN')
            if not nonempty(pref_ref):
                errors.append(f'{aid}:REQUIRED_PROFILE_REF_MISSING')
            elif isinstance(profile, dict) and nonempty(profile.get('artifact_id')) and pref_ref != profile.get('artifact_id'):
                errors.append(f'{aid}:PROFILE_REF_DOES_NOT_MATCH_PROFILE_ARTIFACT')
        elif pref_ref and aid not in profile_by_actor:
            errors.append(f'{aid}:PROFILE_REF_NOT_IN_INPUT_PROFILES')
        if pref_ref and aid in profile_by_actor and isinstance(decision.get('tendencies_invoked'), list):
            allowed_refs = {f"{pref_ref}::{t.get('tendency_id')}" for t in profile_by_actor[aid].get('stable_tendencies', []) if isinstance(t, dict) and nonempty(t.get('tendency_id'))}
            for invoked in decision['tendencies_invoked']:
                if invoked not in allowed_refs:
                    errors.append(f'{aid}:UNKNOWN_INVOKED_TENDENCY_REF:{invoked}')
        elif not pref_ref and decision.get('tendencies_invoked'):
            errors.append(f'{aid}:TENDENCY_INVOCATION_WITHOUT_PROFILE_REF')

    if required - seen:
        errors.append(f'MISSING_PRIMARY_ACTOR_DECISIONS:{sorted(required - seen)}')
    decision_state = plan.get('decision')
    gaps = plan.get('unresolved_gaps', [])
    if not isinstance(gaps, list):
        errors.append('UNRESOLVED_GAPS_NOT_ARRAY')
        gaps = []
    if decision_state == 'READY':
        if gaps or conditional_reason_seen or blocked_reason_seen:
            errors.append('READY_WITH_UNRESOLVED_CONDITIONAL_OR_BLOCKING_GAPS')
    elif decision_state == 'CONDITIONAL':
        if not (gaps or conditional_reason_seen):
            errors.append('CONDITIONAL_WITHOUT_CONDITIONAL_BASIS')
        if blocked_reason_seen:
            errors.append('CONDITIONAL_WITH_BLOCKING_ACTOR_DECISION')
    elif decision_state == 'BLOCKED':
        if not (gaps or blocked_reason_seen):
            errors.append('BLOCKED_WITHOUT_BLOCKING_REASON')
    elif decision_state == 'NO_AGENT_DECISION_REQUIRED':
        if required:
            errors.append('FALSE_NO_AGENT_DECISION_REQUIRED')
        if decisions:
            errors.append('NO_AGENT_DECISION_PLAN_HAS_ACTOR_DECISIONS')
    else:
        errors.append('INVALID_DECISION_STATE')

    if required and decision_state == 'NO_AGENT_DECISION_REQUIRED':
        errors.append('FALSE_NO_AGENT_DECISION_REQUIRED')
    if not required and decision_state != 'NO_AGENT_DECISION_REQUIRED':
        errors.append('MUST_MARK_NO_AGENT_DECISION_REQUIRED')
    return errors


def validate_causal_continuation(audit, event):
    """Permit only explicitly bound downstream rendering obligations, not causal gaps."""
    if not isinstance(audit, dict) or not isinstance(event, dict):
        return ['CAUSAL_GATE_INPUT_INVALID']
    errors = []
    if audit.get('decision') not in {'PASS', 'CONDITIONAL_PASS'}:
        errors.append('CAUSAL_GATE_DECISION')
    findings, items = audit.get('causal_findings'), audit.get('conditional_items')
    if not isinstance(findings, list) or not findings or not isinstance(items, list):
        return errors + ['CAUSAL_GATE_ROWS_INVALID']
    conditional = []
    for row in findings:
        if not isinstance(row, dict) or type(row.get('blocking')) is not bool:
            errors.append('CAUSAL_FINDING_INVALID')
            continue
        if row['blocking'] or row.get('result') not in {'PASS', 'NOTE', 'NOT_APPLICABLE', 'CONDITIONAL'}:
            errors.append('CAUSAL_FINDING_BLOCKS:' + str(row.get('finding_id')))
        if row.get('result') == 'CONDITIONAL':
            conditional.append(row.get('finding_id'))
    targets = {row.get('target_ref') for row in event.get('causal_links', [])
               if isinstance(row, dict) and row.get('target_kind') == 'READER_COGNITION'
               and isinstance(row.get('target_ref'), str)}
    actions = event.get('actions', [])
    accounted, condition_ids = [], []
    for item in items:
        if not isinstance(item, dict):
            errors.append('CAUSAL_CONDITION_INVALID')
            continue
        accounted.append(item.get('finding_id'))
        condition_ids.append(item.get('condition_id'))
        refs = item.get('event_action_indices')
        if (item.get('condition_type') != 'READER_BEAT_EMBODIMENT' or item.get('owner_stage') != 'P15A'
                or not isinstance(item.get('target_ref'), str) or item.get('target_ref') not in targets
                or not nonempty(item.get('required_action'))
                or not isinstance(actions, list) or not isinstance(refs, list) or not refs
                or any(type(i) is not int or i < 0 or i >= len(actions) for i in refs)):
            errors.append('CAUSAL_CONDITION_NOT_RENDER_ONLY:' + str(item.get('condition_id')))
    if (any(not isinstance(fid, str) or not fid for fid in conditional + accounted + condition_ids)
            or len(conditional) != len(set(conditional))
            or len(condition_ids) != len(set(condition_ids))
            or sorted(conditional) != sorted(accounted)):
        errors.append('CAUSAL_CONDITION_COVERAGE')
    for fid in conditional:
        if isinstance(fid, str) and fid not in accounted:
            errors.append('CAUSAL_CONDITION_UNACCOUNTED:' + fid
                          + ':only existing reader-render obligations may defer; genuine execution gaps must REVISE/BLOCKED')
    if (audit.get('decision') == 'PASS' and items) or (audit.get('decision') == 'CONDITIONAL_PASS' and not items):
        errors.append('CAUSAL_CONDITIONAL_DECISION_MISMATCH')
    return errors


def validate_profile(profile):
    errors = []
    if not isinstance(profile, dict):
        return ['PROFILE_NOT_OBJECT']
    if profile.get('task_id') != 'P05E':
        errors.append('PROFILE_TASK_ID_MISMATCH')
    if not nonempty(profile.get('entity_id')):
        errors.append('PROFILE_MISSING_ENTITY_ID')
    status = profile.get('status')
    forbidden_dynamic_fields = {'current_mood', 'current_belief', 'current_goal', 'current_desire', 'current_relationship', 'current_knowledge'}
    leaked = forbidden_dynamic_fields.intersection(profile)
    if leaked:
        errors.append(f'PROFILE_CONTAINS_DYNAMIC_STATE_FIELDS:{sorted(leaked)}')
    if status in {'NOT_APPLICABLE', 'INSUFFICIENT_BASIS'}:
        if not nonempty(profile.get('unknowns')):
            errors.append('PROFILE_TERMINAL_WITHOUT_EXPLANATORY_UNKNOWNS')
        if status == 'NOT_APPLICABLE' and profile.get('actor_class') not in {'NON_DECISIONAL'}:
            errors.append('PROFILE_NOT_APPLICABLE_WITHOUT_NON_DECISIONAL_CLASS')
        return errors
    if status not in {'DRAFT', 'REVIEW', 'VALIDATED', 'FROZEN'}:
        errors.append('PROFILE_INVALID_LIFECYCLE_STATUS')
    if profile.get('actor_class') not in {'PERSON', 'ORGANIZATION', 'AUTONOMOUS_AGENT'}:
        errors.append('PROFILE_INVALID_DECISION_ACTOR_CLASS')
    if not isinstance(profile.get('stable_tendencies'), list) or not profile.get('stable_tendencies'):
        errors.append('PROFILE_NO_STABLE_TENDENCIES')
    else:
        tendency_ids = []
        for i, item in enumerate(profile['stable_tendencies']):
            if not isinstance(item, dict):
                errors.append(f'PROFILE_TENDENCY_{i}_NOT_OBJECT')
                continue
            for field in ('tendency_id', 'tendency', 'trigger_conditions', 'behavioral_manifestation', 'boundary_or_counterexample', 'certainty'):
                if not nonempty(item.get(field)):
                    errors.append(f'PROFILE_TENDENCY_{i}_MISSING:{field}')
            tendency_ids.append(item.get('tendency_id'))
            if item.get('certainty') not in {'ESTABLISHED', 'SUPPORTED_INFERENCE', 'HYPOTHESIS'}:
                errors.append(f'PROFILE_TENDENCY_{i}_INVALID_CERTAINTY')
            if not nonempty(item.get('evidence_refs')):
                errors.append(f'PROFILE_TENDENCY_{i}_NO_EVIDENCE_REF')
        if len(tendency_ids) != len(set(tendency_ids)):
            errors.append('PROFILE_DUPLICATE_TENDENCY_ID')
    if not isinstance(profile.get('value_priorities'), list) or not profile.get('value_priorities'):
        errors.append('PROFILE_NO_VALUE_PRIORITIES')
    if not nonempty(profile.get('evidence_refs')):
        errors.append('PROFILE_NO_EVIDENCE_REFS')
    patterns = profile.get('decision_patterns')
    if not isinstance(patterns, dict):
        errors.append('PROFILE_DECISION_PATTERNS_NOT_OBJECT')
    else:
        for field in ('risk_posture', 'uncertainty_handling', 'trust_strategy', 'conflict_response', 'pressure_response'):
            if not nonempty(patterns.get(field)):
                errors.append(f'PROFILE_DECISION_PATTERN_MISSING:{field}')
    return errors


def validate_render_obligations(outline, conditions):
    """Verify beat-level binding; prose fidelity still requires rendered review."""
    if not isinstance(outline, dict) or not isinstance(conditions, list):
        return ['RENDER_OBLIGATION_INPUT_INVALID']
    beats = outline.get('beats', [])
    if not isinstance(beats, list):
        return ['RENDER_OBLIGATION_BEATS_INVALID']
    errors = []
    for condition in conditions:
        if not isinstance(condition, dict):
            errors.append('RENDER_OBLIGATION_CONDITION_INVALID')
            continue
        cid, target = condition.get('condition_id'), condition.get('target_ref')
        matches = [beat for beat in beats if isinstance(beat, dict)
                   and isinstance(beat.get('fulfilled_condition_ids'), list)
                   and cid in beat['fulfilled_condition_ids']]
        if len(matches) != 1:
            errors.append(f'RENDER_OBLIGATION_BEAT_COUNT:{cid}:{len(matches)}')
            errors.append(f'RENDER_BIND_EXACTLY_ONE_PAYLOAD_BEAT:fulfilled_condition_ids=[{cid}]:source_reader_obligation_refs=[{target}]')
            continue
        beat = matches[0]
        refs = beat.get('source_reader_obligation_refs', [])
        if (beat.get('payload') is not True or not isinstance(refs, list) or target not in refs
                or not isinstance(beat.get('required_elements'), list) or not beat['required_elements']):
            errors.append(f'RENDER_OBLIGATION_BEAT_UNBOUND:{cid}')
    return errors


def validate_single_event_ownership(plan, records, required_nodes):
    """Bounded host check: approved single-source units cannot rewrite or drop Events."""
    if not isinstance(plan, dict) or not isinstance(records, list):
        return ['OWNERSHIP_SOURCE_INPUT_INVALID']
    sources = {row['event']['id']: row for row in records}
    errors, accounted = [], []
    if plan.get('excluded_source_events'):
        errors.append('OWNERSHIP_EXCLUSION_NOT_AUTHORIZED')
    if set(plan.get('required_state_nodes', [])) != set(required_nodes):
        errors.append('OWNERSHIP_REQUIRED_NODE_SET_CHANGED')
    for unit in plan.get('event_units', []):
        if not isinstance(unit, dict):
            errors.append('OWNERSHIP_UNIT_INVALID')
            continue
        refs = unit.get('source_event_ids', [])
        if not isinstance(refs, list) or len(refs) != 1 or not isinstance(refs[0], str) or refs[0] not in sources:
            errors.append('OWNERSHIP_SINGLE_SOURCE_REQUIRED')
            continue
        accounted += refs
        source = sources[refs[0]]
        if unit.get('canonical_event') != source['event']:
            errors.append('OWNERSHIP_CANONICAL_EVENT_CHANGED:' + refs[0])
        for field in ('decision_plan_refs', 'asset_phase_refs', 'source_relationship_ids'):
            actual, expected = unit.get(field), source.get(field)
            if not isinstance(actual, list) or not isinstance(expected, list):
                errors.append('OWNERSHIP_PROVENANCE_MISSING:' + field)
            elif sorted(json.dumps(row, sort_keys=True) for row in actual) != sorted(
                    json.dumps(row, sort_keys=True) for row in expected):
                errors.append('OWNERSHIP_PROVENANCE_CHANGED:' + field)
    if sorted(accounted) != sorted(sources):
        errors.append('OWNERSHIP_SOURCE_COVERAGE')
    return errors


def validate_event_sequence(sequence, events):
    """Check exact approved ID coverage and classified blocking dependencies."""
    if not isinstance(sequence, dict) or not isinstance(events, list):
        return ['SEQUENCE_INPUT_INVALID']
    expected = [row.get('id') for row in events if isinstance(row, dict)]
    order = sequence.get('event_order')
    dependencies = sequence.get('unresolved_dependencies')
    if not isinstance(order, list) or not isinstance(dependencies, list):
        return ['SEQUENCE_ROWS_INVALID']
    actual = [row.get('event_id') if isinstance(row, dict) else row for row in order]
    errors = []
    if (len(expected) != len(events) or not expected
            or any(not isinstance(ref, str) or not ref for ref in expected + actual)
            or len(expected) != len(set(expected))
            or len(actual) != len(set(actual)) or set(actual) != set(expected)):
        errors.append('SEQUENCE_APPROVED_EVENT_COVERAGE')
    if any(not isinstance(row, dict) or type(row.get('blocking')) is not bool
           or row.get('blocking') or row.get('severity') == 'BLOCKING' for row in dependencies):
        errors.append('SEQUENCE_UNRESOLVED_BLOCKER')
    return errors


def validate_planned_node_coverage(plan, base_nodes):
    """Every actual proposed change in the bounded scope needs one planned owner."""
    errors = validate_base_targets(plan, base_nodes)
    if not isinstance(plan, dict) or not isinstance(base_nodes, list):
        return errors
    expected = [row['source_node_ref'] for row in base_nodes
                if isinstance(row, dict) and isinstance(row.get('node'), dict)
                and row['node'].get('causal_status') == 'proposed']
    actual = []
    clusters = plan.get('event_clusters')
    if not isinstance(clusters, list):
        return errors + ['PLANNED_NODE_CLUSTERS_INVALID']
    for cluster in clusters:
        changes = cluster.get('expected_state_changes') if isinstance(cluster, dict) else None
        if not isinstance(changes, list):
            errors.append('PLANNED_NODE_CHANGES_INVALID')
            continue
        actual += [row.get('target_ref') for row in changes
                   if isinstance(row, dict) and row.get('target_kind') == 'BASE_LINE']
    for ref in expected:
        count = actual.count(ref)
        if count != 1:
            errors.append(f'PLANNED_NODE_OWNER_COUNT:{ref}:{count}')
    return errors


def validate_event_target_coverage(event, cluster):
    """Keep each cluster obligation and its driver provenance in the Event."""
    if not isinstance(event, dict) or not isinstance(cluster, dict):
        return ['EVENT_TARGET_INPUT_INVALID']
    links, changes = event.get('causal_links'), cluster.get('expected_state_changes')
    if not isinstance(links, list) or not isinstance(changes, list):
        return ['EVENT_TARGET_ROWS_INVALID']
    errors = []
    for change in changes:
        if not isinstance(change, dict):
            errors.append('EVENT_TARGET_CHANGE_INVALID')
            continue
        matching = [row for row in links if isinstance(row, dict)
                    and (row.get('target_kind'), row.get('target_ref'))
                    == (change.get('target_kind'), change.get('target_ref'))]
        if len(matching) != 1:
            errors.append('EVENT_TARGET_COVERAGE:' + str(change.get('target_ref')))
            continue
        expected = change.get('cause_driver_refs')
        actual = matching[0].get('driver_refs')
        if (not isinstance(expected, list) or not isinstance(actual, list)
                or sorted(json.dumps(row, sort_keys=True) for row in expected)
                != sorted(json.dumps(row, sort_keys=True) for row in actual)):
            errors.append('EVENT_TARGET_DRIVERS_CHANGED:' + str(change.get('target_ref')))
    return errors


def validate_outline_sources(outline, events):
    """Outline copies must retain all canonical Events and bind every beat."""
    if not isinstance(outline, dict) or not isinstance(events, list):
        return ['OUTLINE_SOURCE_INPUT_INVALID']
    records, beats = outline.get('event_records'), outline.get('beats')
    if not isinstance(records, list) or not isinstance(beats, list) or not beats:
        return ['OUTLINE_SOURCE_ROWS_INVALID']
    errors = []
    if records != events:
        errors.append('OUTLINE_CANONICAL_EVENTS_CHANGED')
        for index, (record, event) in enumerate(zip(records, events)):
            if isinstance(record, dict) and isinstance(event, dict):
                for field in sorted(event.keys() - record.keys()):
                    errors.append(f'OUTLINE_EVENT_MISSING_FIELD:{index}:{field}')
                for field in sorted(record.keys() - event.keys()):
                    errors.append(f'OUTLINE_EVENT_ADDED_FIELD:{index}:{field}')
                for field in sorted(event.keys() & record.keys()):
                    if event[field] != record[field]:
                        errors.append(f'OUTLINE_EVENT_CHANGED_FIELD:{index}:{field}')
    ids = {row['id'] for row in events if isinstance(row, dict) and isinstance(row.get('id'), str)}
    actual = []
    for beat in beats:
        if not isinstance(beat, dict) or not isinstance(beat.get('event_id'), str) or beat['event_id'] not in ids:
            errors.append('OUTLINE_BEAT_EVENT_UNBOUND')
        else:
            actual.append(beat['event_id'])
    if set(actual) != ids:
        errors.append('OUTLINE_BEAT_EVENT_COVERAGE')
    return errors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--portfolio-plan')
    ap.add_argument('--candidate-assets')
    ap.add_argument('--opportunity-plan')
    ap.add_argument('--selected-assets')
    ap.add_argument('--confirmed-interactions')
    ap.add_argument('--base-nodes', help='Source node rows with source_node_ref and node; checks BASE target binding')
    ap.add_argument('--decision-plan')
    ap.add_argument('--event-cluster')
    ap.add_argument('--decision-profiles')
    ap.add_argument('--decision-profile')
    args = ap.parse_args()
    errors = []
    if args.portfolio_plan:
        if not args.candidate_assets:
            errors.append('PORTFOLIO_REQUIRES_CANDIDATE_ASSETS')
        else:
            errors += validate_portfolio(load(args.portfolio_plan), load(args.candidate_assets))
    if args.opportunity_plan:
        if not (args.selected_assets and args.confirmed_interactions):
            errors.append('OPPORTUNITY_REQUIRES_SELECTED_ASSETS_AND_CONFIRMED_INTERACTIONS_INPUTS')
        else:
            errors += ['OPPORTUNITY:' + e for e in validate_opportunity(
                load(args.opportunity_plan), load(args.selected_assets), load(args.confirmed_interactions),
                load(args.base_nodes) if args.base_nodes else None
            )]
    if args.decision_profile:
        errors += ['PROFILE:' + e for e in validate_profile(load(args.decision_profile))]
    if args.decision_plan:
        if not (args.event_cluster and args.decision_profiles):
            errors.append('DECISION_PLAN_REQUIRES_EVENT_CLUSTER_AND_PROFILE_INPUTS')
        else:
            errors += ['DECISION_PLAN:' + e for e in validate_decision_plan(
                load(args.decision_plan), load(args.event_cluster), load(args.decision_profiles)
            )]
    if not (args.portfolio_plan or args.opportunity_plan or args.decision_plan or args.decision_profile):
        errors.append('NO_VALIDATION_TARGET')
    if errors:
        print('FAIL', len(errors))
        for error in errors:
            print('-', error)
        return 1
    print('PASS')
    print('scope: structural invariants only; no LLM semantic or literary quality claim')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
