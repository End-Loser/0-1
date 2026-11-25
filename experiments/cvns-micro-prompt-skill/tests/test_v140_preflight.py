import importlib.util, json, tempfile, unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / 'tools' / 'preflight_task_call.py'
spec = importlib.util.spec_from_file_location('preflight_task_call', TOOL)
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

class TestPreflight(unittest.TestCase):
    def test_p04b_already_registered_skips(self):
        p={'task_id':'P04B','inputs':{'candidate':{'entity_id':'E1'},'current_entity_registry':{'entities':[{'entity_id':'E1'}]}}}
        r=mod.evaluate('P04B',p); self.assertEqual(r['decision'],'SKIP'); self.assertEqual(r['reason'],'ENTITY_ALREADY_REGISTERED')
    def test_p05c_empty_samples_skips(self):
        p={'task_id':'P05C','inputs':{'language_samples':[],'language_samples_status':'EMPTY_BY_FIXTURE'}}
        r=mod.evaluate('P05C',p); self.assertEqual(r['decision'],'SKIP')
    def test_p19f_without_approved_card_skips(self):
        p={'task_id':'P19F','inputs':{'passage_text':'text','character_id':'E1'}}
        r=mod.evaluate('P19F',p); self.assertEqual(r['decision'],'SKIP')
    def test_p18_blocks_when_preflight_missing(self):
        p={'task_id':'P18','inputs':{}}
        r=mod.evaluate('P18',p); self.assertEqual(r['decision'],'BLOCKED')
    def test_p18_allows_host_check_only_when_p17c_ready(self):
        p={'task_id':'P18','inputs':{'p17c_preflight':{'decision':'READY'}}}
        r=mod.evaluate('P18',p); self.assertEqual(r['decision'],'NEEDS_HOST_CHECK')
    def test_p19b_no_mechanism_is_not_applicable(self):
        p={'task_id':'P19B','inputs':{'mechanism_calls':[]}}
        r=mod.evaluate('P19B',p); self.assertEqual(r['decision'],'NOT_APPLICABLE')
    def test_unknown_tasks_do_not_auto_approve(self):
        p={'task_id':'P09','inputs':{'all_signed_lines_a':[]}}
        r=mod.evaluate('P09',p); self.assertEqual(r['decision'],'NEEDS_HOST_CHECK')

if __name__ == '__main__': unittest.main()


class V150RelationshipPreflightTests(unittest.TestCase):
    def test_p10_blocks_candidate_without_canonical_ref(self):
        pack = {'task_id':'P10','inputs':{'relationship_candidate':{
            'candidate_kind':'CAUSAL_INTERACTION','evidence_refs':['NODE-A','NODE-B'],
            'source_line_id':'L-A','source_node_id':'N-A','target_line_id':'L-B','target_node_id':'N-B',
            'causal_direction':'A_TO_B','mechanism_or_path':'A action changes B access','temporal_order_basis':'A precedes B'
        }}}
        result = mod.evaluate('P10', pack)
        self.assertEqual(result['decision'], 'BLOCKED')
        self.assertEqual(result['reason'], 'CANONICAL_CANDIDATE_REF_OR_EVIDENCE_MISSING')

    def test_p10_accepts_canonicalized_causal_candidate_for_host_check(self):
        pack = {'task_id':'P10','inputs':{'relationship_candidate':{
            'candidate_kind':'CAUSAL_INTERACTION','candidate_ref':'SCAN-1::REL-1','evidence_refs':['NODE-A','NODE-B'],
            'source_line_id':'L-A','source_node_id':'N-A','target_line_id':'L-B','target_node_id':'N-B',
            'causal_direction':'A_TO_B','mechanism_or_path':'A action changes B access','temporal_order_basis':'A precedes B'
        }}}
        result = mod.evaluate('P10', pack)
        self.assertEqual(result['decision'], 'NEEDS_HOST_CHECK')

    def test_p10_skips_when_there_is_no_material_candidate(self):
        result = mod.evaluate('P10', {'task_id':'P10','inputs':{'relationship_candidate':{}}})
        self.assertEqual(result['decision'], 'NOT_APPLICABLE')


class V150PreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod=mod
    def test_p05e_skips_non_agent(self):
        r=self.mod.evaluate('P05E',{'task_id':'P05E','inputs':{'actor_class':'NON_DECISIONAL'}})
        self.assertEqual(r['decision'],'SKIP')
    def test_p13b_can_run_without_assets_when_causal_interaction_exists(self):
        r=self.mod.evaluate('P13B',{'task_id':'P13B','inputs':{'selected_assets':[],'confirmed_causal_interactions':[{'candidate_id':'REL-1'}]}})
        self.assertEqual(r['decision'],'NEEDS_HOST_CHECK')
    def test_p13b_blocks_when_selected_asset_phase_chapters_missing(self):
        pack = {'task_id':'P13B','inputs':{'selected_assets':[{'asset_id':'A1','required_phases':['SETUP'],'phase_chapters':{}}], 'confirmed_causal_interactions':[]}}
        result = mod.evaluate('P13B', pack)
        self.assertEqual(result['decision'], 'BLOCKED')
        self.assertEqual(result['reason'], 'P12B_PHASE_CHAPTERS_MISSING_OR_INVALID')

    def test_p13b_skips_without_any_driver(self):
        r=self.mod.evaluate('P13B',{'task_id':'P13B','inputs':{'selected_assets':[],'confirmed_causal_interactions':[]}})
        self.assertEqual(r['decision'],'NOT_APPLICABLE')
    def test_p13c_skips_for_non_agent_event(self):
        r=self.mod.evaluate('P13C',{'task_id':'P13C','inputs':{'opportunity_plan_status':'FROZEN','event_opportunity_cluster':{'target_chapter':3,'primary_actors':[{'entity_id':'weather','decision_required':False}]}}})
        self.assertEqual(r['decision'],'NOT_APPLICABLE')

class V150PreflightGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = mod

    def test_p13c_blocks_when_required_profile_missing(self):
        pack = {'task_id':'P13C','inputs':{
            'event_opportunity_cluster':{'cluster_id':'C1','target_chapter':6,'primary_actors':[{'entity_id':'A','decision_required':True,'profile_required':True}]},
            'opportunity_plan_status':'FROZEN',
            'decision_profiles':[]
        }}
        result = self.mod.evaluate('P13C', pack)
        self.assertEqual(result['decision'], 'BLOCKED')
        self.assertEqual(result['reason'], 'REQUIRED_DECISION_PROFILE_MISSING_OR_UNVALIDATED')

    def test_p13c_allows_validated_required_profile_to_reach_host_check(self):
        pack = {'task_id':'P13C','inputs':{
            'event_opportunity_cluster':{'cluster_id':'C1','target_chapter':6,'primary_actors':[{'entity_id':'A','decision_required':True,'profile_required':True}]},
            'opportunity_plan_status':'FROZEN',
            'decision_profiles':[{'entity_id':'A','artifact_id':'PROFILE-A','status':'FROZEN'}]
        }}
        result = self.mod.evaluate('P13C', pack)
        self.assertEqual(result['decision'], 'NEEDS_HOST_CHECK')

    def test_p14a_blocks_without_ready_decision_plan(self):
        pack = {'task_id':'P14A','inputs':{
            'event_opportunity_cluster':{'cluster_id':'C1','target_chapter':6},
            'opportunity_plan_status':'FROZEN',
            'event_decision_plan':{'status':'FROZEN','decision':'BLOCKED','unresolved_gaps':['missing motive evidence']}
        }}
        result = self.mod.evaluate('P14A', pack)
        self.assertEqual(result['decision'], 'BLOCKED')
        self.assertEqual(result['reason'], 'EVENT_DECISION_PLAN_NOT_READY')

    def test_p14a_requires_explicit_gaps_for_conditional_plan(self):
        pack = {'task_id':'P14A','inputs':{
            'event_opportunity_cluster':{'cluster_id':'C1','target_chapter':6},
            'opportunity_plan_status':'FROZEN',
            'event_decision_plan':{'status':'FROZEN','decision':'CONDITIONAL','unresolved_gaps':[]}
        }}
        result = self.mod.evaluate('P14A', pack)
        self.assertEqual(result['decision'], 'BLOCKED')
        self.assertEqual(result['reason'], 'CONDITIONAL_DECISION_PLAN_WITHOUT_EXPLICIT_GAPS')


class V150LifecycleGateTests(unittest.TestCase):
    def test_p13c_blocks_rejected_opportunity_plan(self):
        pack = {'task_id':'P13C','inputs':{
            'opportunity_plan_status':'REJECTED',
            'event_opportunity_cluster':{'cluster_id':'C1','target_chapter':3,'primary_actors':[{'entity_id':'A','decision_required':True}]},
            'decision_profiles':[]
        }}
        result = mod.evaluate('P13C', pack)
        self.assertEqual(result['decision'], 'BLOCKED')
        self.assertEqual(result['reason'], 'OPPORTUNITY_PLAN_NOT_VALIDATED_OR_FROZEN')

    def test_p14a_blocks_draft_decision_plan_even_when_decision_says_ready(self):
        pack = {'task_id':'P14A','inputs':{
            'opportunity_plan_status':'FROZEN',
            'event_opportunity_cluster':{'cluster_id':'C1','target_chapter':3},
            'event_decision_plan':{'status':'DRAFT','decision':'READY','unresolved_gaps':[]}
        }}
        result = mod.evaluate('P14A', pack)
        self.assertEqual(result['decision'], 'BLOCKED')
        self.assertEqual(result['reason'], 'EVENT_DECISION_PLAN_NOT_VALIDATED_OR_FROZEN')

    def test_p14a_blocks_missing_target_chapter(self):
        pack = {'task_id':'P14A','inputs':{
            'event_opportunity_cluster':{'cluster_id':'C1'},
            'event_decision_plan':{'status':'FROZEN','decision':'READY','unresolved_gaps':[]}
        }}
        result = mod.evaluate('P14A', pack)
        self.assertEqual(result['reason'], 'EVENT_OPPORTUNITY_TARGET_CHAPTER_MISSING')

    def test_p17a_blocks_unfrozen_p14b_result(self):
        pack = {'task_id':'P17A','inputs':{
            'event':{'id':'EV-1','chapter':3},
            'p14b_result':{'status':'DRAFT','decision':'FEASIBLE'},
            'event_decision_plan':{'status':'FROZEN','decision':'READY','unresolved_gaps':[]}
        }}
        result = mod.evaluate('P17A', pack)
        self.assertEqual(result['decision'], 'BLOCKED')
        self.assertEqual(result['reason'], 'P14B_NOT_VALIDATED_OR_FROZEN')

    def test_p17a_requires_decision_plan_receipt(self):
        pack = {'task_id':'P17A','inputs':{
            'event':{'id':'EV-1','chapter':3},
            'p14b_result':{'status':'FROZEN','decision':'FEASIBLE'},
            'event_decision_plan':{'status':'FROZEN','decision':'READY','unresolved_gaps':[]}
        }}
        result = mod.evaluate('P17A', pack)
        self.assertEqual(result['decision'], 'NEEDS_HOST_CHECK')

    def test_p19d_skips_unapproved_or_unready_event(self):
        pack = {'task_id':'P19D','inputs':{
            'event':{'id':'EV-1'},
            'p14b_result':{'status':'FROZEN','decision':'FEASIBLE'},
            'p17a_result':{'status':'DRAFT','decision':'READY'},
            'event_decision_plan':{'status':'FROZEN','decision':'READY'},
            'primary_actors':[], 'decision_profiles':[]
        }}
        result = mod.evaluate('P19D', pack)
        self.assertEqual(result['decision'], 'SKIP')
        self.assertEqual(result['reason'], 'P17A_NOT_VALIDATED_OR_FROZEN')

    def test_p19d_requires_frozen_decision_plan_and_profiles(self):
        pack = {'task_id':'P19D','inputs':{
            'event':{'id':'EV-1'},
            'p14b_result':{'status':'FROZEN','decision':'FEASIBLE'},
            'p17a_result':{'status':'FROZEN','decision':'READY'},
            'event_decision_plan':{'status':'DRAFT','decision':'READY'},
            'primary_actors':[{'entity_id':'A','decision_required':True,'profile_required':True}],
            'decision_profiles':[]
        }}
        result = mod.evaluate('P19D', pack)
        self.assertEqual(result['decision'], 'BLOCKED')
        self.assertEqual(result['reason'], 'EVENT_DECISION_PLAN_NOT_VALIDATED_OR_FROZEN')


class V150TemporalAndAuditPreflightTests(unittest.TestCase):
    def test_p13c_blocks_missing_opportunity_target_chapter(self):
        pack = {'task_id':'P13C','inputs':{
            'opportunity_plan_status':'FROZEN',
            'event_opportunity_cluster':{'cluster_id':'C1','primary_actors':[{'entity_id':'A','decision_required':True}]},
            'decision_profiles':[]
        }}
        result = mod.evaluate('P13C', pack)
        self.assertEqual(result['decision'], 'BLOCKED')
        self.assertEqual(result['reason'], 'EVENT_OPPORTUNITY_TARGET_CHAPTER_MISSING')

    def test_p17a_blocks_event_chapter_drift_from_opportunity(self):
        pack = {'task_id':'P17A','inputs':{
            'event':{'id':'EV-1','chapter':4},
            'event_opportunity_cluster':{'cluster_id':'C1','target_chapter':5},
            'p14b_result':{'status':'FROZEN','decision':'FEASIBLE'},
            'event_decision_plan':{'status':'FROZEN','decision':'READY','unresolved_gaps':[]}
        }}
        result = mod.evaluate('P17A', pack)
        self.assertEqual(result['decision'], 'BLOCKED')
        self.assertEqual(result['reason'], 'EVENT_CHAPTER_OPPORTUNITY_MISMATCH')

    def test_p19d_blocks_conditional_decision_without_explicit_gap(self):
        pack = {'task_id':'P19D','inputs':{
            'event':{'id':'EV-1'},
            'p14b_result':{'status':'FROZEN','decision':'FEASIBLE'},
            'p17a_result':{'status':'FROZEN','decision':'READY'},
            'event_decision_plan':{'status':'FROZEN','decision':'CONDITIONAL','unresolved_gaps':[]},
            'primary_actors':[], 'decision_profiles':[]
        }}
        result = mod.evaluate('P19D', pack)
        self.assertEqual(result['decision'], 'BLOCKED')
        self.assertEqual(result['reason'], 'CONDITIONAL_DECISION_PLAN_WITHOUT_EXPLICIT_GAPS')
