#!/usr/bin/env python3
"""Validate micro-prompt package structure and attention budget; not LLM quality."""
from pathlib import Path
import csv, re, json, sys
ROOT=Path(__file__).resolve().parents[1]
errors=[]
MAX_ACTIVE_CONSTRAINTS=15
MAX_PROMPT_CHARS=3000
BASE_DIMENSION_MAX_CONSTRAINTS=5
rows=list(csv.DictReader((ROOT/'TASK_REGISTRY.csv').open(encoding='utf-8-sig',newline='')))
ids=[r['task_id'] for r in rows]
if len(ids)!=len(set(ids)): errors.append('DUPLICATE_TASK_ID')
registered={r['prompt_file'] for r in rows}
for p in (ROOT/'prompts').glob('*.md'):
    rel=p.relative_to(ROOT).as_posix()
    if rel=='prompts/BASE_LINE_CORE.md': continue
    if rel not in registered: errors.append(f'UNREGISTERED_PROMPT:{rel}')
for r in rows:
    p=ROOT/r['prompt_file']
    if not p.is_file():
        errors.append(f'MISSING_PROMPT:{r["task_id"]}:{r["prompt_file"]}'); continue
    contract=ROOT/r['output_contract_file']
    if not contract.is_file(): errors.append(f'MISSING_OUTPUT_CONTRACT:{r["task_id"]}:{r["output_contract_file"]}')
    txt=p.read_text(encoding='utf-8')
    if '本轮唯一目标' not in txt: errors.append(f'NO_PRIMARY_GOAL:{r["task_id"]}')
    n=int(r['active_constraints_count'])
    if not 1<=n<=MAX_ACTIVE_CONSTRAINTS: errors.append(f'CONSTRAINT_BUDGET:{r["task_id"]}:{n}')
    if '只遵守本任务的限制' not in txt: errors.append(f'NO_LIMIT_SECTION:{r["task_id"]}')
    body=txt
    if len(body)>MAX_PROMPT_CHARS: errors.append(f'PROMPT_TOO_LONG:{r["task_id"]}:{len(body)}')
    if txt.lstrip().startswith('---'): errors.append(f'PROMPT_HAS_REDUNDANT_METADATA:{r["task_id"]}')
    actual=len(re.findall(r'^\d+\. ',body,re.M))
    if actual!=n: errors.append(f'CONSTRAINT_COUNT_MISMATCH:{r["task_id"]}:{actual}!={n}')
    if 'validators:' in txt or 'predecessor:' in txt: errors.append(f'ORCHESTRATION_METADATA_LEAK:{r["task_id"]}')
    if re.search(r'验收：|运行.*validator|审计并修复|同时执行下游',body): errors.append(f'GENERATOR_AUDIT_LEAK:{r["task_id"]}')
    if not r['write_scope']: errors.append(f'NO_WRITE_SCOPE:{r["task_id"]}')
    if not r['read_scope']: errors.append(f'NO_READ_SCOPE:{r["task_id"]}')
    if contract.is_file():
        try:
            contract_data=json.loads(contract.read_text(encoding='utf-8'))
            payload_schema=contract_data.get('payload_schema')
            if payload_schema and not (ROOT/payload_schema).is_file():
                errors.append(f'MISSING_PAYLOAD_SCHEMA:{r["task_id"]}:{payload_schema}')
        except Exception as e: errors.append(f'BAD_TASK_CONTRACT:{r["task_id"]}:{e}')
expected={'power','location','existence','skill','item','faction','relation','goal','desire','cognition','charm'}
actual={p.stem.removeprefix('BASE_').lower() for p in (ROOT/'prompts').glob('BASE_*.md') if p.name!='BASE_LINE_CORE.md'}
if actual!=expected: errors.append(f'BASE_DIMENSIONS_MISMATCH:{actual}')
core=(ROOT/'prompts'/'BASE_LINE_CORE.md').read_text(encoding='utf-8')
core_body=core.split('**只遵守两项核心限制：**',1)[-1].split('**输出：**',1)[0]
core_count=len(re.findall(r'^\d+\. ',core_body,re.M))
if core_count!=2: errors.append(f'BASE_CORE_CONSTRAINT_COUNT:{core_count}')
for p in (ROOT/'prompts').glob('BASE_*.md'):
    if p.name=='BASE_LINE_CORE.md': continue
    body=p.read_text(encoding='utf-8').split('---',2)[-1]
    n=len(re.findall(r'^\d+\. ',body,re.M))
    if not 1<=n<=BASE_DIMENSION_MAX_CONSTRAINTS: errors.append(f'BASE_DIMENSION_LIMITS:{p.name}:{n}')
for f in ['schemas/EXECUTION_ENVELOPE.md','schemas/ARTIFACT_ENVELOPE.md','workflows/WORKFLOW.md','HOST_SCHEDULER_REQUIREMENTS.md','NARRATIVE_DECISION_AND_EVENT_SYNTHESIS_V150.md']:
    if not (ROOT/f).is_file(): errors.append(f'MISSING:{f}')
for p in (ROOT/'schemas').rglob('*.json'):
    try: json.loads(p.read_text(encoding='utf-8'))
    except Exception as e: errors.append(f'BAD_JSON:{p.name}:{e}')
paths=[r['output_contract_file'] for r in rows]
if len(paths)!=len(set(paths)):
    allowed={'schemas/BASE_LINE_SCHEMA.json'}
    dup={x for x in paths if paths.count(x)>1}
    if dup-allowed: errors.append(f'UNEXPECTED_SHARED_CONTRACTS:{sorted(dup-allowed)}')
# R-06.2 §4.11 expression-taskbook must remain one global matrix task.
p06=next((r for r in rows if r['task_id']=='P06'),None)
if not p06 or p06['call_unit']!='one_global_expression_taskbook': errors.append('P06_NOT_GLOBAL_TASKBOOK')
if p06 and p06['output_contract_file']!='schemas/EXPRESSION_TASKBOOK_MATRIX_SCHEMA.json': errors.append('P06_WRONG_MATRIX_CONTRACT')
if (ROOT/'prompts'/'P06_PURPOSE_ONE_ENTITY_DIMENSION.md').exists(): errors.append('STALE_PER_DIMENSION_P06_PROMPT')
row_schema=json.loads((ROOT/'schemas'/'EXPRESSION_TASKBOOK_SCHEMA.json').read_text(encoding='utf-8'))
expected_row_fields={'entity_id','dimension','theme_role','emotion_curve','structural_function','purpose_contract','withholding_intents','skip_generation'}
if set(row_schema.get('required_fields',[]))!=expected_row_fields: errors.append('EXPRESSION_TASK_ROW_SCHEMA_DRIFT')
wi_fields={'payload','constituent_elements','release_mode','release_chapter','release_event_anchor','prerequisite_entry_ids'}
if set(row_schema.get('withholding_intent_fields',[]))!=wi_fields: errors.append('WITHHOLDING_INTENT_SCHEMA_DRIFT')

# V1.3.0: dimension registry and source-model contract checks.
regfile=ROOT/'schemas'/'BASE_DIMENSION_REGISTRY.csv'
if not regfile.is_file(): errors.append('MISSING_BASE_DIMENSION_REGISTRY')
else:
    drows=list(csv.DictReader(regfile.open(encoding='utf-8-sig',newline='')))
    if {r['dimension'] for r in drows} != expected or len(drows)!=11: errors.append('BASE_DIMENSION_REGISTRY_MISMATCH')
    for r in drows:
        p=ROOT/'prompts'/f"BASE_{r['dimension'].upper()}.md"
        if p.is_file() and r['state_format'] not in p.read_text(encoding='utf-8'): errors.append(f'BASE_STATE_FORMAT_MISSING:{r["dimension"]}')
base_schema=json.loads((ROOT/'schemas'/'BASE_LINE_SCHEMA.json').read_text(encoding='utf-8'))
if base_schema.get('title')!='BASELineDraft': errors.append('BASE_SCHEMA_NOT_CANONICAL_DRAFT')
if base_schema.get('properties',{}).get('dimension',{}).get('enum') != sorted(expected):
    # JSON schema enum order need not be sorted; compare set below.
    if set(base_schema.get('properties',{}).get('dimension',{}).get('enum',[])) != expected: errors.append('BASE_SCHEMA_DIMENSIONS_MISMATCH')
if base_schema.get('$defs',{}).get('TimelineNode',{}).get('properties',{}).get('state',{}).get('type')!='string': errors.append('BASE_STATE_MUST_BE_STRING')
# Split-audit tasks must exist; stale combined prompts must not be routed.
for tid in ['P19A1','P19A2','P19C1','P19C2','P19C3']:
    if tid not in ids: errors.append(f'MISSING_SPLIT_AUDIT_TASK:{tid}')
for stale in ['P19A','P19C']:
    if stale in ids: errors.append(f'COMBINED_AUDIT_TASK_REMAINS:{stale}')
# Output models must match the uploaded source models where declared.
expected_models={'P05C':'VoiceCard','P14A':'Event','P15A':'ChapterOutline','P16A':'VariableLine','P16B':'AckNode','P18':'ChapterContent'}
for tid,model in expected_models.items():
    cpath=ROOT/f'schemas/output_contracts/{tid}.json'
    if cpath.is_file():
        c=json.loads(cpath.read_text(encoding='utf-8'))
        if c.get('payload_model')!=model: errors.append(f'SOURCE_MODEL_MISMATCH:{tid}:{c.get("payload_model")}')
# Mechanism definition must declare critical semantic dimensions.
ms=json.loads((ROOT/'schemas/MECHANISM_RULE_SCHEMA.json').read_text(encoding='utf-8'))
for field in ['randomness','load_bearing_state_variables','state_transitions','cost','layering','contact_semantics']:
    if field not in ms.get('properties',{}): errors.append(f'MECHANISM_SCHEMA_MISSING:{field}')
for field in ['layering','contact_semantics']:
    if field not in ms.get('required',[]): errors.append(f'MECHANISM_SCHEMA_NOT_EXPLICIT:{field}')
# No validators or predecessor metadata in model-facing prompt body.
for r in rows:
    txt=(ROOT/r['prompt_file']).read_text(encoding='utf-8')
    if any(x in txt for x in ['validators','predecessor','运行 validator','运行校验器']): errors.append(f'ROUTING_LEAK_V13:{r["task_id"]}')

# V1.3.0: compact prompt/body and registry accounting checks.
for r in rows:
    txt=(ROOT/r['prompt_file']).read_text(encoding='utf-8')
    actual_count=len(re.findall(r'^\d+\. ',txt,re.M))
    if actual_count!=int(r['active_constraints_count']): errors.append(f'REGISTRY_PROMPT_COUNT_MISMATCH:{r["task_id"]}:{actual_count}!={r["active_constraints_count"]}')
    if len(txt)>MAX_PROMPT_CHARS: errors.append(f'PROMPT_BODY_OVER_LIMIT:{r["task_id"]}:{len(txt)}')
    if '**只遵守本任务的限制：**' not in txt: errors.append(f'NONSTANDARD_LIMIT_HEADING:{r["task_id"]}')
    if any(x in txt for x in ['请同时审计','同时修复并更新','完成本轮后运行','predecessor','validators']): errors.append(f'MIXED_TASK_OR_ROUTING:{r["task_id"]}')
    if r['task_id'].startswith('BASE-') and int(r['effective_semantic_constraints_count'])!=3: errors.append(f'BASE_EFFECTIVE_BUDGET:{r["task_id"]}')
# BASE registry must contain only compact format information, no duplicate dimension-purpose instructions.
if regfile.is_file():
    expected_base_fields={'dimension','type_class','state_format','node_semantics','runtime_note'}
    if set(drows[0].keys())!=expected_base_fields: errors.append(f'BASE_REGISTRY_NOT_COMPACT:{set(drows[0].keys())}')


# V1.4: workflow and data-consumption regression invariants.
required_v14={'P12B','P13B','P14C0','P18D','P19D2'}
if not required_v14.issubset(set(ids)): errors.append(f'MISSING_V14_TASKS:{sorted(required_v14-set(ids))}')
byid={r['task_id']:r for r in rows}
for tid,expected_dep in {
 'P08':'P14C','P14C':'P14C0','P14C0':'P17A','P17A':'P14B',
 'P18':'P17C=READY','P18D':'P17C=BLOCKED+explicit_test_authorization',
 'P19D2':'P18'
}.items():
 if tid in byid and byid[tid]['predecessor']!=expected_dep: errors.append(f'V14_DEPENDENCY_ORDER:{tid}:{byid[tid]["predecessor"]}!={expected_dep}')
if 'P13B' in byid and 'P13' not in byid['P13B']['predecessor']: errors.append('P13B_MUST_FOLLOW_P13')
for tid,need in {'P12':['CREATE_ASSET','NO_ASSET'],'P14B':['FEASIBLE','FEASIBLE_WITH_CONDITIONS','BLOCKED','UNASSESSED'],'P17A':['READY','CONDITIONAL_READY','BLOCKED','NOT_RUN','NOT_APPLICABLE'],'P17C':['READY','BLOCKED','NOT_APPLICABLE'],'P19D':['PASS','CONDITIONAL_PASS','REVISE','BLOCKED','NOT_APPLICABLE']}.items():
 c=ROOT/f'schemas/output_contracts/{tid}.json'
 if c.is_file():
  vals=json.loads(c.read_text(encoding='utf-8')).get('decision_values',[])
  if not set(need).issubset(vals): errors.append(f'V14_DECISION_ENUM:{tid}:{need}:{vals}')
# P14A must be one event opportunity cluster, not one asset.
p14a=(ROOT/byid['P14A']['prompt_file']).read_text(encoding='utf-8')
if 'EventOpportunityCluster' not in p14a or '一条 StoryAsset' in p14a: errors.append('P14A_NOT_CLUSTER_BASED')
# No empty voice calls; diagnostic Writer is segregated.
if 'NOT_APPLICABLE_INPUT_MISSING' not in (ROOT/'prompts/P05C_VOICE_CARD_ONE.md').read_text(encoding='utf-8'):
 errors.append('P05C_EMPTY_INPUT_SKIP_MISSING')
if 'DIAGNOSTIC_ONLY' not in (ROOT/'prompts/P18D_DIAGNOSTIC_RENDER_ONE_CHAPTER.md').read_text(encoding='utf-8'): errors.append('P18D_NOT_DIAGNOSTIC_ONLY')
if 'P17C=READY' not in (ROOT/'prompts/P18_WRITER_ONE_CHAPTER.md').read_text(encoding='utf-8'): errors.append('P18_NOT_GATED_BY_P17C')
if '不输入正文' not in (ROOT/'prompts/P19D_EVENT_CAUSAL_AUDIT.md').read_text(encoding='utf-8'): errors.append('P19D_SCOPE_UNCLEAR')
if '正文片段' not in (ROOT/'prompts/P19D2_RENDERED_EVENT_CAUSAL_AUDIT.md').read_text(encoding='utf-8'): errors.append('P19D2_NO_TEXT_INPUT')
# P08 must not count unfiltered event candidates; Writer input pack must use dependency closure.
p08=(ROOT/'prompts/P08_RHYTHM_INTENT_ONE_CHAPTER.md').read_text(encoding='utf-8')
if '不重新统计候选事件' not in p08: errors.append('P08_UNFILTERED_EVENT_COUNT_RISK')
policy=(ROOT/'INPUT_PACK_POLICY.md').read_text(encoding='utf-8')
if '先读当前成果的显式 refs，再递归解析其依赖' not in policy or '禁止仅按 `domain` 粗筛' not in policy: errors.append('INPUT_PACK_DEPENDENCY_CLOSURE_MISSING')
# Chapter plan finalizer must be present and compile.
materializer=ROOT/'tools/materialize_event_units.py'
if not materializer.is_file(): errors.append('MISSING_EVENT_MATERIALIZER')
usage_tracer=ROOT/'tools/trace_artifact_consumption.py'
if not usage_tracer.is_file(): errors.append('MISSING_ARTIFACT_USAGE_TRACER')
finalizer=ROOT/'tools/finalize_chapter_plan.py'
if not finalizer.is_file(): errors.append('MISSING_CHAPTER_PLAN_FINALIZER')
else:
 import py_compile
 try:
  py_compile.compile(str(finalizer),doraise=True)
  py_compile.compile(str(materializer),doraise=True)
  py_compile.compile(str(usage_tracer),doraise=True)
 except Exception as e: errors.append(f'FINALIZER_COMPILE:{e}')


# V1.5.0: actor decisions, causal interactions and phase-level asset deployment.
required_v150={'P05E','P13C'}
if not required_v150.issubset(set(ids)): errors.append(f'MISSING_V150_TASKS:{sorted(required_v150-set(ids))}')
for tid in ['P05E','P13C']:
    if tid in byid and len((ROOT/byid[tid]['prompt_file']).read_text(encoding='utf-8'))>MAX_PROMPT_CHARS: errors.append(f'V150_PROMPT_BUDGET:{tid}')
# P12B's phase schedule must have an explicit JSON schema, not only prose fields.
p12b_contract=json.loads((ROOT/'schemas/output_contracts/P12B.json').read_text(encoding='utf-8'))
if p12b_contract.get('payload_schema')!='schemas/ASSET_PORTFOLIO_DECISION_SCHEMA.json': errors.append('P12B_PHASE_SCHEMA_REFERENCE_MISSING')
if not (ROOT/'schemas/ASSET_PORTFOLIO_DECISION_SCHEMA.json').is_file(): errors.append('P12B_PHASE_SCHEMA_MISSING')
if 'personality' in expected or len(expected)!=11: errors.append('V150_BASE_DIMENSION_DRIFT')
for rel in ['schemas/CHARACTER_DECISION_PROFILE_SCHEMA.json','schemas/EVENT_DECISION_PLAN_SCHEMA.json','schemas/EVENT_OPPORTUNITY_PLAN_SCHEMA.json','schemas/ASSET_PORTFOLIO_DECISION_SCHEMA.json','tools/validate_narrative_logic.py','tools/materialize_no_agent_decision.py','tests/test_v150_narrative_logic.py']:
    if not (ROOT/rel).is_file(): errors.append(f'MISSING_V150_ARTIFACT:{rel}')
for tid,needle in {'P05E':'不是 BASE','P13C':'全部主要决策者','P14A':'P13C','P19D':'决策计划'}.items():
    if tid in byid and needle not in (ROOT/byid[tid]['prompt_file']).read_text(encoding='utf-8'):
        errors.append(f'V150_PROMPT_SEMANTIC_RULE_MISSING:{tid}:{needle}')
p13b_contract=json.loads((ROOT/'schemas/output_contracts/P13B.json').read_text(encoding='utf-8'))
assignment_rule=p13b_contract.get('assignment_rule','').lower()
if ('payload_schema' not in p13b_contract or 'asset_id' not in assignment_rule or 'phase' not in assignment_rule
        or 'multiple event' not in assignment_rule and 'multiple' not in assignment_rule):
    errors.append('V150_P13B_PHASE_MAPPING_CONTRACT_MISSING')
p10_contract=json.loads((ROOT/'schemas/output_contracts/P10.json').read_text(encoding='utf-8'))
if 'CAUSAL_INTERACTION_CONFIRMED' not in p10_contract.get('decision_values',[]): errors.append('V150_P10_CAUSAL_ROUTE_MISSING')
# compile new deterministic validator and preflight.
try:
    py_compile.compile(str(ROOT/'tools/validate_narrative_logic.py'),doraise=True)
    py_compile.compile(str(ROOT/'tools/preflight_task_call.py'),doraise=True)
    py_compile.compile(str(ROOT/'tools/materialize_no_agent_decision.py'),doraise=True)
    py_compile.compile(str(ROOT/'tools/materialize_event_units.py'),doraise=True)
except Exception as e: errors.append(f'V150_TOOL_COMPILE:{e}')

# V1.6.0: explanation proposals are separate from factual revision and asset use.
for tid in ('P11A', 'P11B'):
    if tid not in byid:
        errors.append(f'MISSING_V160_TASK:{tid}')
for rel in ('schemas/CONFLICT_EXPLANATION_SCHEMA.json',
            'schemas/CONFLICT_EXPLANATION_REVIEW_SCHEMA.json',
            'tools/conflict_resolution.py',
            'CONFLICT_EXPLANATION_POLICY_V160.md'):
    if not (ROOT/rel).is_file():
        errors.append(f'MISSING_V160_ARTIFACT:{rel}')
if p10_contract.get('routing', {}).get('MUTUALLY_EXCLUSIVE_ERROR') != 'P11A':
    errors.append('V160_EXPLANATION_ROUTE_MISSING')
try:
    py_compile.compile(str(ROOT/'tools/conflict_resolution.py'), doraise=True)
except Exception as e:
    errors.append(f'V160_TOOL_COMPILE:{e}')

# Real-test opening-state and planning-stage boundary checks.
base_plan_tool = ROOT/'tools/validate_base_plan.py'
if not base_plan_tool.is_file():
    errors.append('MISSING_BASE_PLAN_VALIDATOR')
else:
    try:
        py_compile.compile(str(base_plan_tool), doraise=True)
    except Exception as e:
        errors.append(f'BASE_PLAN_TOOL_COMPILE:{e}')
if 'opening_anchor' not in core or 'proposed' not in core:
    errors.append('BASE_OPENING_PLANNING_POLICY_MISSING')

# V1.4.1: lifecycle registry, preflight and dataflow controls.
life_path=ROOT/'TASK_LIFECYCLE_REGISTRY.csv'
if not life_path.is_file(): errors.append('MISSING_TASK_LIFECYCLE_REGISTRY')
else:
    lrows=list(csv.DictReader(life_path.open(encoding='utf-8-sig',newline='')))
    if {r.get('task_id') for r in lrows} != set(ids): errors.append('LIFECYCLE_TASK_ID_MISMATCH')
    if any(not r.get('preflight_rule') for r in lrows): errors.append('LIFECYCLE_PREFLIGHT_RULE_MISSING')
    if len(lrows)!=len(ids): errors.append('LIFECYCLE_ROW_COUNT_MISMATCH')
preflight=ROOT/'tools/preflight_task_call.py'
if not preflight.is_file(): errors.append('MISSING_TASK_PREFLIGHT_TOOL')
else:
    try: py_compile.compile(str(preflight),doraise=True)
    except Exception as e: errors.append(f'PREFLIGHT_COMPILE:{e}')
preflight_schema=ROOT/'schemas/PREFLIGHT_DECISION_SCHEMA.json'
if not preflight_schema.is_file(): errors.append('MISSING_PREFLIGHT_SCHEMA')
# Avoidable-call regressions from the real TR-20261104 run.
for tid,needle in {'P04B':'已注册实体','P05C':'没有语言样本时','P19F':'没有批准 VoiceCard 时 preflight SKIP'}.items():
    if tid in byid and needle not in byid[tid]['notes'] and needle not in byid[tid]['read_scope']:
        errors.append(f'AVOIDABLE_CALL_SKIP_POLICY_MISSING:{tid}')
# Writer pack must be de-duplicated: P17C receives the dependency manifest, not full pack.
if byid.get('P17C') and ('不得加载完整 Writer Pack' not in byid['P17C']['read_scope']): errors.append('P17C_FULL_WRITER_PACK_NOT_PROHIBITED')
if byid.get('P18') and ('只注入一次' not in byid['P18']['read_scope']): errors.append('P18_WRITER_PACK_SINGLE_INJECTION_NOT_REQUIRED')
if '不接收完整 WriterInputPack' not in (ROOT/'prompts/P17C_CHAPTER_INPUT_PREFLIGHT.md').read_text(encoding='utf-8'): errors.append('P17C_FULL_PACK_PROMPT_NOT_PROHIBITED')
if '只提供一次' not in (ROOT/'prompts/P18_WRITER_ONE_CHAPTER.md').read_text(encoding='utf-8'): errors.append('P18_PACK_DUPLICATION_PROMPT_NOT_PROHIBITED')
# Dependency evidence is host-only and does not expand the model prompt budget.
for needed in ('tools/writer_dependency_gate.py', 'schemas/WRITER_DEPENDENCY_GATE_SCHEMA.json',
               'WRITER_DEPENDENCY_POLICY.md', 'tests/test_writer_dependency_gate.py'):
    if not (ROOT/needed).is_file():
        errors.append(f'MISSING_WRITER_DEPENDENCY_GATE:{needed}')
try:
    py_compile.compile(str(ROOT/'tools/writer_dependency_gate.py'), doraise=True)
except Exception as e:
    errors.append(f'WRITER_DEPENDENCY_GATE_COMPILE:{e}')
p17c_contract=json.loads((ROOT/'schemas/output_contracts/P17C.json').read_text(encoding='utf-8'))
if p17c_contract.get('host_evidence_policy') != 'WRITER_DEPENDENCY_POLICY.md':
    errors.append('P17C_CURRENT_DEPENDENCY_POLICY_MISSING')
for needed in ('tools/canon_context.py', 'schemas/CANON_CONTEXT_MANIFEST_SCHEMA.json',
               'CANON_CONTEXT_POLICY.md', 'tests/test_canon_context.py'):
    if not (ROOT/needed).is_file():
        errors.append(f'MISSING_CANON_CONTEXT_HOST_INTERFACE:{needed}')
for tid in ('P16A', 'P16B', 'P16C'):
    contract=json.loads((ROOT/f'schemas/output_contracts/{tid}.json').read_text(encoding='utf-8'))
    if (contract.get('host_validator') != 'tools/canon_context.py::validate_p16'
            or contract.get('host_policy') != 'CANON_CONTEXT_POLICY.md'):
        errors.append(f'P16_HOST_CONTRACT_MISSING:{tid}')
try:
    py_compile.compile(str(ROOT/'tools/canon_context.py'), doraise=True)
except Exception as e:
    errors.append(f'CANON_CONTEXT_TOOL_COMPILE:{e}')
for needed in ('tools/index_transaction.py', 'schemas/INDEX_TRANSACTION_SCHEMA.json',
               'INDEX_TRANSACTION_POLICY.md', 'tests/test_index_transaction.py',
               'tools/line_lifecycle.py', 'tests/test_line_lifecycle.py',
               'schemas/LINE_LIFECYCLE_DECISION_SCHEMA.json', 'P16_LIFECYCLE_POLICY.md',
               'tests/test_canon_registration.py', 'tools/writer_dispatch.py',
               'tests/test_writer_dispatch.py', 'tests/test_schema_registry.py'):
    if not (ROOT/needed).is_file():
        errors.append(f'MISSING_INDEX_TRANSACTION_HOST_INTERFACE:{needed}')
try:
    py_compile.compile(str(ROOT/'tools/index_transaction.py'), doraise=True)
    py_compile.compile(str(ROOT/'tools/line_lifecycle.py'), doraise=True)
    py_compile.compile(str(ROOT/'tools/writer_dispatch.py'), doraise=True)
except Exception as e:
    errors.append(f'INDEX_TRANSACTION_TOOL_COMPILE:{e}')
for needed in ['tools/preflight_task_call.py','tools/audit_input_pack_efficiency.py','case_studies/TR-20261104_MULTILINE_DATAFLOW_USAGE_REPORT.md']:
    if not (ROOT/needed).is_file(): errors.append(f'MISSING_V141_ARTIFACT:{needed}')
# 0-1 clean archive: sample-specific interface checks removed with the test novel/evidence.

# A passing workspace is insufficient if its release manifest omits executable coverage.
manifest_path = ROOT/'SHA256SUMS.txt'
if not manifest_path.is_file():
    errors.append('MISSING_RELEASE_CHECKSUM_MANIFEST')
else:
    manifest_lines = manifest_path.read_text(encoding='utf-8-sig').splitlines()
    listed = set()
    for line in manifest_lines:
        if not re.fullmatch(r'[0-9a-f]{64}  .+', line):
            errors.append('INVALID_RELEASE_CHECKSUM_ENTRY')
        else:
            rel = line[66:]
            if rel in listed:
                errors.append(f'DUPLICATE_RELEASE_CHECKSUM_ENTRY:{rel}')
            listed.add(rel)
    for folder in ('tools', 'tests', 'schemas', 'prompts', 'workflows', 'regression'):
        for path in (ROOT/folder).rglob('*'):
            if (path.is_file() and '__pycache__' not in path.parts
                    and path.suffix in ('.py', '.json', '.md', '.csv', '.txt')):
                rel = path.relative_to(ROOT).as_posix()
                if rel not in listed:
                    errors.append(f'RELEASE_SOURCE_NOT_LISTED:{rel}')

if errors:
    print('FAIL',len(errors)); [print('-',e) for e in errors]; sys.exit(1)
print('PASS')
print('task_prompts:',len(rows))
print('prompt_files:',len(registered)+1)
print('active_constraints_max:',max(int(r['active_constraints_count']) for r in rows))
print('active_constraints_avg:',round(sum(int(r['active_constraints_count']) for r in rows)/len(rows),2))
print('effective_semantic_constraints_max:',max(int(r['effective_semantic_constraints_count']) for r in rows))
print('prompt_body_chars_max:',max(len((ROOT/r['prompt_file']).read_text(encoding='utf-8')) for r in rows))
print('base_dimensions:',len(expected))
print('output_contracts_resolved:',len(rows))
print('scope: static prompt-budget/registry/schema checks only; no LLM quality claim')
