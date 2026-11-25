import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import validate_narrative_logic as v


def mk_cluster(cid, phase=None, relationship=None):
    deployments = []
    drivers = []
    relationships = []
    if phase:
        aid, stage = phase
        deployments.append({
            'asset_id': aid, 'phase': stage, 'target_chapter': 6,
            'function_to_serve': f'{stage} function',
            'payload_ceiling': 'Do not reveal payload beyond this phase',
            'dependency_refs': []
        })
        drivers.append({'kind': 'ASSET_PHASE', 'ref': f'{aid}:{stage}'})
    if relationship:
        relationships.append(relationship)
        drivers.append({'kind': 'CONFIRMED_CAUSAL_INTERACTION', 'ref': relationship})
    return {
        'cluster_id': cid,
        'target_chapter': 6,
        'driver_refs': drivers,
        'asset_deployments': deployments,
        'source_relationship_ids': relationships,
        'shared_dramatic_question': 'What can the actor safely reveal?',
        'shared_event_purpose': 'Change the evidence state without forcing the final choice',
        'clustering_basis': 'One shared action with interacting choices and compatible state deltas',
        'time_window': 'Current chapter window; exact story time unresolved where not anchored',
        'location': 'Registered archive room',
        'primary_actors': [],
        'initiating_force_or_condition': 'The evidence request exposes a mismatch',
        'core_action': 'Request and challenge the original register',
        'expected_state_changes': [{'target_kind': 'BASE_LINE', 'target_ref': 'BASE-E1-COGNITION', 'change_intent': 'Belief shifts after the record request', 'cause_driver_refs': drivers or [{'kind': 'ASSET_PHASE', 'ref': 'A1:SETUP'}]}]
    }


def test_asset_phases_can_span_events_but_each_phase_unique():
    plan = {
        'scope_ref': 'ARC-01',
        'event_clusters': [mk_cluster('c1', ('A1', 'SETUP')), mk_cluster('c2', ('A1', 'REVEAL'))],
        'deferred_asset_phases': [{'asset_id': 'A1', 'phase': 'PAYOFF', 'reason': 'Payoff belongs to later arc', 'revisit_condition': 'After independent evidence'}],
        'no_event_asset_phases': [],
        'unassigned_confirmed_interactions': []
    }
    assets = [{'asset_id': 'A1', 'required_phases': ['SETUP', 'REVEAL', 'PAYOFF'],
               'phase_chapters': {'SETUP': 6, 'REVEAL': 6, 'PAYOFF': 8}}]
    assert v.validate_opportunity(plan, assets, []) == []




def test_asset_phase_target_chapter_must_match_cluster():
    cluster = mk_cluster('c1', ('A1', 'SETUP'))
    cluster['target_chapter'] = 7
    plan = {'scope_ref': 'ARC-01', 'event_clusters': [cluster], 'deferred_asset_phases': [], 'no_event_asset_phases': [] , 'unassigned_confirmed_interactions': []}
    errors = v.validate_opportunity(plan, [{'asset_id': 'A1', 'required_phases': ['SETUP'], 'phase_chapters': {'SETUP': 6}}], [])
    assert any('ASSET_PHASE_TARGET_CHAPTER_MISMATCH' in e for e in errors)

def test_duplicate_phase_rejected():
    c1 = mk_cluster('c1', ('A1', 'SETUP'))
    c2 = mk_cluster('c2', ('A1', 'SETUP'))
    plan = {'scope_ref': 'ARC-01', 'event_clusters': [c1, c2], 'deferred_asset_phases': [], 'no_event_asset_phases': [], 'unassigned_confirmed_interactions': []}
    errors = v.validate_opportunity(plan, [{'asset_id': 'A1', 'required_phases': ['SETUP'], 'phase_chapters': {'SETUP': 6}}], [])
    assert any('DUPLICATE_PHASE_ASSIGNMENT' in e or 'PHASE_ASSIGNMENT_COUNT' in e for e in errors)


def test_expected_state_change_must_cite_driver_from_same_cluster():
    cluster = mk_cluster('c1', ('A1', 'SETUP'))
    cluster['expected_state_changes'][0]['cause_driver_refs'] = [
        {'kind': 'CONFIRMED_CAUSAL_INTERACTION', 'ref': 'REL-NOT-IN-CLUSTER'}
    ]
    plan = {'scope_ref': 'ARC-01', 'event_clusters': [cluster], 'deferred_asset_phases': [],
            'no_event_asset_phases': [], 'unassigned_confirmed_interactions': []}
    errors = v.validate_opportunity(plan, [{'asset_id': 'A1', 'required_phases': ['SETUP'], 'phase_chapters': {'SETUP': 6}}], [])
    assert any('EXPECTED_STATE_CHANGE_0_CAUSE_NOT_IN_CLUSTER' in e for e in errors)


def test_asset_phase_cannot_be_rescheduled_from_source_story_asset():
    plan = {'scope_ref': 'ARC-01', 'event_clusters': [mk_cluster('c1', ('A1', 'SETUP'))],
            'deferred_asset_phases': [], 'no_event_asset_phases': [], 'unassigned_confirmed_interactions': []}
    assets = [{'asset_id': 'A1', 'required_phases': ['SETUP'], 'phase_chapters': {'SETUP': 7}}]
    errors = v.validate_opportunity(plan, assets, [])
    assert any('ASSET_PHASE_RESCHEDULE_FORBIDDEN' in e for e in errors)


def test_confirmed_causal_interaction_can_drive_event_without_asset():
    plan = {'scope_ref': 'ARC-02', 'event_clusters': [mk_cluster('cause-cluster', relationship='REL-1')],
            'deferred_asset_phases': [], 'no_event_asset_phases': [], 'unassigned_confirmed_interactions': []}
    assert v.validate_opportunity(plan, [], [{'candidate_ref': 'REL-1', 'decision': 'CAUSAL_INTERACTION_CONFIRMED'}]) == []


def test_confirmed_causal_interaction_cannot_be_silently_dropped():
    plan = {'scope_ref': 'ARC-02', 'event_clusters': [], 'deferred_asset_phases': [], 'no_event_asset_phases': [], 'unassigned_confirmed_interactions': []}
    errors = v.validate_opportunity(plan, [], [{'candidate_ref': 'REL-1', 'decision': 'CAUSAL_INTERACTION_CONFIRMED'}])
    assert any('CONFIRMED_INTERACTION_COVERAGE:REL-1:0' in e for e in errors)


def test_confirmed_causal_interaction_must_be_assigned_once():
    plan = {'scope_ref': 'ARC-02', 'event_clusters': [mk_cluster('c1', relationship='REL-1'), mk_cluster('c2', relationship='REL-1')],
            'deferred_asset_phases': [], 'no_event_asset_phases': [], 'unassigned_confirmed_interactions': []}
    errors = v.validate_opportunity(plan, [], [{'candidate_ref': 'REL-1', 'decision': 'CAUSAL_INTERACTION_CONFIRMED'}])
    assert any('DUPLICATE_CAUSAL_INTERACTION_ASSIGNMENT:REL-1' in e for e in errors)


def mk_actor_decision(actor, profile_ref=None, selected='o1', coherence='COHERENT'):
    row = {
        'actor_id': actor,
        'actor_role': 'INITIATOR',
        'current_goal': 'Obtain the register',
        'operative_desire': 'Avoid exposing the mother',
        'known_facts': ['The copy has an inconsistent stamp'],
        'beliefs': ['The original may still exist'],
        'unknown_facts': ['Who ordered the destruction'],
        'active_pressures': ['The office closes soon'],
        'feasible_options': [
            {'option_id': 'o1', 'action_intent': 'Request the original', 'feasibility': 'FEASIBLE', 'expected_gain': 'Direct evidence', 'expected_cost': 'Public attention', 'reason_for_or_against': 'Best evidence path'},
            {'option_id': 'o2', 'action_intent': 'Leave and investigate privately', 'feasibility': 'FEASIBLE', 'expected_gain': 'Protect family', 'expected_cost': 'Delay and evidence risk', 'reason_for_or_against': 'Safer but slower'}
        ],
        'selected_option_id': selected,
        'choice_reason': 'The original is time-sensitive',
        'rejected_alternative_reason': 'The private route risks losing access',
        'accepted_costs': ['May attract scrutiny'],
        'evidence_refs': ['BASE-A-GOAL-02', 'BASE-A-COGNITION-04'],
        'coherence_status': coherence,
        'tendencies_invoked': ['PROFILE-A::T1'] if profile_ref else [],
        'tendency_consistency': 'CONSISTENT',
        'tendency_consistency_reason': 'The stable preference aligns with the current evidence-seeking choice',
        'counterfactual_sensitivity': {
            'smallest_relevant_change': 'A trusted witness confirms the mother is not implicated',
            'would_choice_change': 'YES',
            'reason': 'The protective motive would no longer outweigh the evidence urgency'
        }
    }
    if profile_ref:
        row['profile_ref'] = profile_ref
        row['profile_required'] = True
    return row


def test_decision_plan_covers_all_actors_and_uses_frozen_profile():
    cluster = {'cluster_id': 'c1', 'primary_actors': [
        {'entity_id': 'A', 'decision_required': True, 'profile_required': True},
        {'entity_id': 'B', 'decision_required': True, 'profile_required': False}
    ]}
    plan = {'cluster_id': 'c1', 'status': 'FROZEN', 'decision': 'READY', 'actor_decisions': [
        mk_actor_decision('A', 'PROFILE-A'), mk_actor_decision('B')
    ], 'unresolved_gaps': []}
    profiles = [{'entity_id': 'A', 'artifact_id': 'PROFILE-A', 'status': 'FROZEN',
                 'stable_tendencies': [{'tendency_id': 'T1', 'tendency': 'Evidence-first', 'trigger_conditions': ['When evidence is at risk'],
                                       'behavioral_manifestation': 'Seeks corroboration', 'boundary_or_counterexample': 'May act quickly under immediate danger',
                                       'evidence_refs': ['BIO-1'], 'certainty': 'SUPPORTED_INFERENCE'}]}]
    assert v.validate_decision_plan(plan, cluster, profiles) == []


def test_decision_plan_requires_every_primary_actor():
    cluster = {'cluster_id': 'c1', 'primary_actors': [
        {'entity_id': 'A', 'decision_required': True}, {'entity_id': 'B', 'decision_required': True}
    ]}
    plan = {'cluster_id': 'c1', 'status': 'FROZEN', 'decision': 'READY', 'actor_decisions': [mk_actor_decision('A')], 'unresolved_gaps': []}
    errors = v.validate_decision_plan(plan, cluster, [])
    assert any('MISSING_PRIMARY_ACTOR_DECISIONS' in e for e in errors)


def test_profile_required_actor_cannot_use_missing_or_unvalidated_profile():
    cluster = {'cluster_id': 'c1', 'primary_actors': [{'entity_id': 'A', 'decision_required': True, 'profile_required': True}]}
    plan = {'cluster_id': 'c1', 'status': 'FROZEN', 'decision': 'READY', 'actor_decisions': [mk_actor_decision('A', 'PROFILE-A')], 'unresolved_gaps': []}
    errors = v.validate_decision_plan(plan, cluster, [{'entity_id': 'A', 'artifact_id': 'PROFILE-A', 'status': 'DRAFT'}])
    assert any('DECISION_PROFILE_NOT_VALIDATED_OR_FROZEN' in e for e in errors)


def test_selected_infeasible_option_is_rejected():
    cluster = {'cluster_id': 'c1', 'primary_actors': [{'entity_id': 'A', 'decision_required': True}]}
    row = mk_actor_decision('A')
    row['feasible_options'][0]['feasibility'] = 'INFEASIBLE'
    plan = {'cluster_id': 'c1', 'status': 'FROZEN', 'decision': 'READY', 'actor_decisions': [row], 'unresolved_gaps': []}
    errors = v.validate_decision_plan(plan, cluster, [])
    assert any('SELECTED_OPTION_IS_INFEASIBLE' in e for e in errors)


def test_no_agent_event_has_explicit_terminal_decision():
    cluster = {'cluster_id': 'c0', 'primary_actors': []}
    plan = {'cluster_id': 'c0', 'status': 'FROZEN', 'decision': 'NO_AGENT_DECISION_REQUIRED', 'actor_decisions': [], 'unresolved_gaps': []}
    assert v.validate_decision_plan(plan, cluster, []) == []


def test_decision_profile_is_not_a_base_dimension():
    dims = {p.stem.replace('BASE_', '').lower() for p in (ROOT / 'prompts').glob('BASE_*.md') if p.name != 'BASE_LINE_CORE.md'}
    assert len(dims) == 11 and 'personality' not in dims and 'decision' not in dims


def test_valid_frozen_decision_profile_passes():
    profile = {
        'artifact_id': 'PROFILE-A', 'task_id': 'P05E', 'status': 'FROZEN', 'entity_id': 'A',
        'actor_class': 'PERSON',
        'stable_tendencies': [{'tendency_id': 'T1', 'tendency': 'Prefers verifiable evidence', 'trigger_conditions': ['When source attribution is uncertain'], 'behavioral_manifestation': 'Checks source records before accusation', 'boundary_or_counterexample': 'May bypass verification under immediate physical threat', 'evidence_refs': ['BIO-1'], 'certainty': 'SUPPORTED_INFERENCE'}],
        'value_priorities': [{'value': 'Family protection', 'priority_or_tradeoff': 'Will delay disclosure when exposure risk is high', 'evidence_refs': ['BIO-2']}],
        'decision_patterns': {'risk_posture': 'Cautious under uncertainty', 'uncertainty_handling': 'Seeks corroboration', 'trust_strategy': 'Trust is revised after evidence', 'conflict_response': 'Prefers direct verification', 'pressure_response': 'Narrows to controllable steps'},
        'blind_spots': [], 'evidence_refs': ['BIO-1', 'BIO-2'], 'unknowns': ['Response to physical danger is unknown']
    }
    assert v.validate_profile(profile) == []


def test_insufficient_basis_profile_requires_unknowns_and_does_not_invent_tendencies():
    profile = {
        'artifact_id': 'PROFILE-B', 'task_id': 'P05E', 'status': 'INSUFFICIENT_BASIS', 'entity_id': 'B',
        'actor_class': 'PERSON', 'stable_tendencies': [], 'value_priorities': [],
        'decision_patterns': {'risk_posture':'UNKNOWN','uncertainty_handling':'UNKNOWN','trust_strategy':'UNKNOWN','conflict_response':'UNKNOWN','pressure_response':'UNKNOWN'},
        'blind_spots': [], 'evidence_refs': [], 'unknowns': ['No prior behavior evidence was supplied']
    }
    assert v.validate_profile(profile) == []


def test_profile_mixing_dynamic_state_is_rejected():
    profile = {
        'artifact_id': 'PROFILE-A', 'task_id': 'P05E', 'status': 'FROZEN', 'entity_id': 'A',
        'actor_class': 'PERSON', 'stable_tendencies': [], 'value_priorities': [],
        'decision_patterns': {'risk_posture':'UNKNOWN','uncertainty_handling':'UNKNOWN','trust_strategy':'UNKNOWN','conflict_response':'UNKNOWN','pressure_response':'UNKNOWN'},
        'blind_spots': [], 'evidence_refs': [], 'unknowns': [], 'current_goal': 'Find the register'
    }
    errors = v.validate_profile(profile)
    assert any('PROFILE_CONTAINS_DYNAMIC_STATE_FIELDS' in e for e in errors)


def test_host_materializes_no_agent_terminal_without_llm_call():
    import importlib.util
    tool = ROOT / 'tools' / 'materialize_no_agent_decision.py'
    spec = importlib.util.spec_from_file_location('materialize_no_agent_decision', tool)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    plan, receipt = mod.materialize({'cluster_id': 'C-NOAGENT', 'primary_actors': [{'entity_id': 'weather', 'decision_required': False}]})
    assert plan['decision'] == 'NO_AGENT_DECISION_REQUIRED'
    assert plan['actor_decisions'] == []
    assert receipt['model_call_made'] is False
    assert plan['artifact_id'] == receipt['artifact_id']


def test_host_no_agent_terminal_refuses_cluster_with_decision_actor():
    import importlib.util
    tool = ROOT / 'tools' / 'materialize_no_agent_decision.py'
    spec = importlib.util.spec_from_file_location('materialize_no_agent_decision', tool)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    try:
        mod.materialize({'cluster_id': 'C-HAS-AGENT', 'primary_actors': [{'entity_id': 'A', 'decision_required': True}]})
    except ValueError as exc:
        assert 'DECISION_AGENT_PRESENT' in str(exc)
    else:
        raise AssertionError('must not issue NO_AGENT terminal when an agent exists')





def test_malformed_deferred_phase_row_is_rejected_without_crashing():
    plan = {'scope_ref':'ARC-X','event_clusters':[], 'deferred_asset_phases':['not-an-object'],
            'no_event_asset_phases':[], 'unassigned_confirmed_interactions':[]}
    errors = v.validate_opportunity(plan, [{'asset_id':'A1','required_phases':['SETUP']}], [])
    assert any('ITEM_0_NOT_OBJECT' in e for e in errors)


def test_malformed_option_rows_are_rejected_without_being_ignored():
    cluster = {'cluster_id':'c1','primary_actors':[{'entity_id':'A','decision_required':True}]}
    row = mk_actor_decision('A')
    row['feasible_options'].append('not-an-option-object')
    plan = {'cluster_id':'c1','status':'FROZEN','decision':'READY','actor_decisions':[row],'unresolved_gaps':[]}
    errors = v.validate_decision_plan(plan, cluster, [])
    assert any('OPTIONS_CONTAIN_NON_OBJECT_ITEMS' in e for e in errors)


def test_asset_phase_must_copy_source_story_asset_chapter():
    cluster = mk_cluster('c1', ('A1', 'SETUP'))
    cluster['asset_deployments'][0]['target_chapter'] = 7
    cluster['target_chapter'] = 7
    plan = {'scope_ref':'ARC-01','event_clusters':[cluster],'deferred_asset_phases':[],
            'no_event_asset_phases':[],'unassigned_confirmed_interactions':[]}
    errors = v.validate_opportunity(plan, [{'asset_id':'A1','required_phases':['SETUP'],'phase_chapters':{'SETUP':6}}], [])
    assert any('ASSET_PHASE_RESCHEDULE_FORBIDDEN' in e for e in errors)


def test_draft_decision_plan_can_be_structurally_validated_before_freeze():
    cluster = {'cluster_id':'c1','primary_actors':[{'entity_id':'A','decision_required':True}]}
    plan = {'cluster_id':'c1','status':'DRAFT','decision':'READY','actor_decisions':[mk_actor_decision('A')],'unresolved_gaps':[]}
    assert v.validate_decision_plan(plan, cluster, []) == []

def test_draft_profile_can_be_structurally_validated_before_freeze():
    profile = {
        'artifact_id':'PROFILE-DRAFT','task_id':'P05E','status':'DRAFT','entity_id':'A','actor_class':'PERSON',
        'stable_tendencies':[{'tendency_id':'T1','tendency':'Checks evidence first','trigger_conditions':['When proof is disputed'],
            'behavioral_manifestation':'Requests a second source','boundary_or_counterexample':'May act immediately to prevent harm',
            'evidence_refs':['BIO-1'],'certainty':'SUPPORTED_INFERENCE'}],
        'value_priorities':[{'value':'Accuracy','priority_or_tradeoff':'Delays accusation until corroborated','evidence_refs':['BIO-2']}],
        'decision_patterns':{'risk_posture':'Cautious','uncertainty_handling':'Corroborates','trust_strategy':'Trust revised by evidence',
            'conflict_response':'Verifies directly','pressure_response':'Narrows scope'},
        'blind_spots':[],'evidence_refs':['BIO-1','BIO-2'],'unknowns':[]
    }
    assert v.validate_profile(profile) == []


def test_selected_asset_with_empty_required_phases_is_rejected():
    plan = {'scope_ref':'ARC-01','event_clusters':[], 'deferred_asset_phases':[],
            'no_event_asset_phases':[], 'unassigned_confirmed_interactions':[]}
    asset = {'asset_id':'A1','required_phases':[], 'phase_chapters':{'SETUP':None,'REVEAL':None,'PAYOFF':None}}
    errors = v.validate_opportunity(plan, [asset], [])
    assert any('SELECTED_ASSET_HAS_NO_REQUIRED_PHASES' in e for e in errors)

def test_selected_asset_requires_source_chapter_for_each_required_phase_even_if_deferred():
    plan = {'scope_ref':'ARC-01','event_clusters':[],
            'deferred_asset_phases':[{'asset_id':'A1','phase':'SETUP','reason':'defer','revisit_condition':'new evidence'}],
            'no_event_asset_phases':[], 'unassigned_confirmed_interactions':[]}
    asset = {'asset_id':'A1','required_phases':['SETUP'], 'phase_chapters':{'SETUP':None}}
    errors = v.validate_opportunity(plan, [asset], [])
    assert any('REQUIRED_PHASE_CHAPTER_MISSING_OR_INVALID:SETUP' in e for e in errors)

def test_same_event_may_carry_distinct_asset_phases_when_source_chapters_match():
    cluster = mk_cluster('c1', ('A1', 'SETUP'))
    cluster['asset_deployments'].append({
        'asset_id': 'A1', 'phase': 'REVEAL', 'target_chapter': 6,
        'function_to_serve': 'Reveal a distinct fact',
        'payload_ceiling': 'Do not reveal payoff payload', 'dependency_refs': []
    })
    cluster['driver_refs'].append({'kind': 'ASSET_PHASE', 'ref': 'A1:REVEAL'})
    plan = {'scope_ref':'ARC-01','event_clusters':[cluster], 'deferred_asset_phases':[],
            'no_event_asset_phases':[], 'unassigned_confirmed_interactions':[]}
    assets = [{'asset_id':'A1','required_phases':['SETUP','REVEAL'],
               'phase_chapters':{'SETUP':6,'REVEAL':6,'PAYOFF':9}}]
    assert v.validate_opportunity(plan, assets, []) == []


if __name__ == '__main__':
    test_asset_phases_can_span_events_but_each_phase_unique()
    test_same_event_may_carry_distinct_asset_phases_when_source_chapters_match()
    test_asset_phase_target_chapter_must_match_cluster()
    test_duplicate_phase_rejected()
    test_expected_state_change_must_cite_driver_from_same_cluster()
    test_asset_phase_cannot_be_rescheduled_from_source_story_asset()
    test_confirmed_causal_interaction_can_drive_event_without_asset()
    test_confirmed_causal_interaction_cannot_be_silently_dropped()
    test_confirmed_causal_interaction_must_be_assigned_once()
    test_decision_plan_covers_all_actors_and_uses_frozen_profile()
    test_decision_plan_requires_every_primary_actor()
    test_profile_required_actor_cannot_use_missing_or_unvalidated_profile()
    test_selected_infeasible_option_is_rejected()
    test_no_agent_event_has_explicit_terminal_decision()
    test_decision_profile_is_not_a_base_dimension()
    test_valid_frozen_decision_profile_passes()
    test_insufficient_basis_profile_requires_unknowns_and_does_not_invent_tendencies()
    test_profile_mixing_dynamic_state_is_rejected()
    test_host_materializes_no_agent_terminal_without_llm_call()
    test_host_no_agent_terminal_refuses_cluster_with_decision_actor()
    test_malformed_deferred_phase_row_is_rejected_without_crashing()
    test_malformed_option_rows_are_rejected_without_being_ignored()
    test_asset_phase_must_copy_source_story_asset_chapter()
    test_draft_decision_plan_can_be_structurally_validated_before_freeze()
    test_draft_profile_can_be_structurally_validated_before_freeze()
    test_selected_asset_with_empty_required_phases_is_rejected()
    test_selected_asset_requires_source_chapter_for_each_required_phase_even_if_deferred()
    print('PASS V1.5.0 narrative logic tests: 27')
