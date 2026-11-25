---
name: cvns-r062-microtask-variable-line-skill
description: 用于按纵向变量线规划、写作与修订小说。生成完整BASE线，探索冲突解释并审读合理性，经批准新版本、资产筛选和事件编排后写作；不把未解决矛盾当作事实。
version: 1.7.0
---

# CVNS R-06.2 Micro-Task Variable-Line Skill 1.7.0

## 定位
本 Skill 是提示词库和执行流程，不是一次性运行全部任务的总提示词。每次模型调用只执行 TASK_REGISTRY 中一个自然任务。原 R-06.2 的 BASE/DERIVED、11维、P06全局矩阵、P09实体对扫描和阶段顺序继续保留。

## 核心规则
1. 每次模型调用使用独立会话、独立输入包和独立输出路径；调度元数据不进入模型提示词。
2. 一个 BASE 变量线仍是一个 entity×dimension 的完整时间轨迹，一次调用；11维不变，不按章节/字段切碎。
3. P05E 只为重要决策主体生成稳定决策倾向 sidecar；它不是第12条 BASE 线。临时目标、欲望、认知、关系和情绪仍由既有线记录。
4. P09 对实体对全部已签约线做全覆盖关系扫描；P10 再分类张力、互斥、确认因果、待证据或无实质关系。全覆盖不意味着所有线都必须合成。
5. 互斥按当前批准规则判定，先由 P11A/P11B 探索解释；无有效解释或明确不探索才走 P11 最小修复。解释未批准、回写、复检与重新分类前，不得资产化；张力进入 P12，确认因果进入 P13B。
6. P12B 判断资产组合边际价值；P13B 按 SETUP/REVEAL/PAYOFF 阶段规划部署。同一资产可以跨多个事件阶段兑现，但同一阶段在一个范围内只能有一个承载位置。
7. P13C 以一个 EventOpportunityCluster 为单位，一次推演该事件全部主要决策者的选择逻辑；不得为每个角色逐人追加调用。
8. P14A 才把决策计划转成 R-06.2 Event；P14B 检查可行性；P19D 检查人物选择与事件因果。P13C 的意图不等于 Event 实际结果。
9. P14C0 只能合并行动、决策路径、时空窗口和结果实质相同的事件；同地点、同主题或相似氛围不足以合并。
10. 无资产阶段、无真实因果驱动、无行动与状态/读者信息变化时，允许 DEFERRED/NO_EVENT/NOT_APPLICABLE；不得为了提高事件数强制生成。
11. P17C BLOCKED 时生产 P18 必须被宿主阻断；诊断草稿使用独立 P18D，并永久标记 DIAGNOSTIC_ONLY。
12. 下游只能读取、引用、合成或提出新版本；冻结成果不得原位修改。
13. 解释候选可提出异类机制，但必须说明命题覆盖、因果、边界和跨线后果。P11B通过只是修订提案；宿主批准、物化与激活分别执行，共鸣实验仍默认关闭。
14. P01E 修炼体系与 P01F 战斗规则只产出提案；未通过 P21A 单规则修订与宿主批准前，不得写入有效 WorldConstants，也不得被 BASE_POWER 当作既定等级。P26 只做压力测试 sidecar，不进入正式 Canon。
## 关键文件
- `TASK_REGISTRY.csv`：任务目标、输入白名单、输出和依赖。
- `NARRATIVE_DECISION_AND_EVENT_SYNTHESIS_V150.md`：变量线关系分类、人物决策和事件合成的语义判据。
- `workflows/WORKFLOW.md`：完整顺序。
- `INPUT_PACK_POLICY.md`：最小依赖闭包与去重。
- `HOST_SCHEDULER_REQUIREMENTS.md`：宿主执行、权限、审批和冻结要求。
- `case_studies/TR-20261104_MULTILINE_DATAFLOW_REVIEW.md`：本轮数据消费/排除分析。
- `tools/validate_skill.py`：静态结构与提示词预算验证。
- `tools/materialize_event_units.py`：从 P14C0 方案物化新的 Event 版本。
- `tools/materialize_no_agent_decision.py`：无决策主体时由宿主生成终态回执，不发起空模型调用。
- `tools/validate_narrative_logic.py`：校验资产阶段分配、源阶段章不漂移、因果驱动、人物决策与状态变化来源。
- `schemas/ASSET_PORTFOLIO_DECISION_SCHEMA.json`：约束 P12B 资产阶段目标章和组合处置字段。
- `tools/finalize_chapter_plan.py`：章节计划审批、ID稳定化和冻结。
- `TIMING_APPROVAL_POLICY.md`与`tools/timing_approval_gate.py`：同日时间段的来源、语义审读及宿主批准证据校验；含时间依赖的章节冻结必须通过门禁，不自动批准或激活Writer。
- `tools/trace_artifact_consumption.py`：保守追踪成果 ID 是否被下游输入引用。
- `CONFLICT_EXPLANATION_POLICY_V160.md`：冲突资源化、合理性判据和受控修订协议。
- `tools/conflict_resolution.py`：解释结构校验、批准hash绑定、新版本物化；不自动证明解释合理或激活宿主索引。
- `tools/validate_base_plan.py`：绑定开场来源、核验首节点与计划身份；具体语义仍需独立审读，见宿主要求50—54。
- `tools/real_novel_test.py`：在显式授权与累计预算内续跑已有五章实测；每任务独立调用、证据只增不改、Writer 共享冻结计划并行。不是通用整书宿主；使用 `--work-dir` 指向已有完整前置证据。

## 边界
本 Skill 不修改 R-06.2 Python 项目核心源码。冻结、写入权限和独立会话需要宿主调度器/文件系统支持。静态测试不证明真实文学质量。

## V1.4.1 继承的数据流与调用效率规则
- 所有 LLM 调用必须先通过 `tools/preflight_task_call.py`；该工具未给出 SKIP/BLOCKED 时也只返回 `NEEDS_HOST_CHECK`，不自动批准。
- 每份生成输出必须登记消费状态或合法终态；未引用先进入 ORPHAN_REVIEW，不自动删除。
- Writer Pack 每个任务输入包最多出现一次；P17C 只读取依赖闭包 manifest 和审批回执。
- 默认输入包超过 150 KiB 预警、200 KiB 复核、300 KiB 需例外批准。


## V1.5.0 版本重点
- 新增 `P05E` 与 `P13C`；两者均有独立 schema 和生命周期规则。
- P13B 不再使用“一资产只能进一个事件簇”的旧约束，改为“资产阶段（SETUP/REVEAL/PAYOFF）级唯一分配”，因此同一资产可以跨事件部署。
- P10 `CAUSAL_INTERACTION_CONFIRMED` 不是 Conflict 对象；它是可被 P13B 引用的定向关系工件。
- P12B→P13B 保留 StoryAsset 原始 SETUP/REVEAL/PAYOFF 目标章；一个事件簇仅对应一个目标章。
- P14A/P17A/P19D preflight 校验上游 VALIDATED/FROZEN 状态，P14C0 物化默认严格溯源并按 EVENT_MODEL_SCHEMA 拒绝自定义 Event 字段。
- `tools/validate_narrative_logic.py` 只做确定性结构校验，不替代语义审计或文学判断。

## V1.6.0 冲突解释闭环
- P10五种枚举不变；互斥改为先探索解释，而非只能最小修补。原始判定不可被改名为资产。
- P11A一个冲突一次候选集；P11B一次比较全部候选，允许修复、挂起或放弃，无机制/反转配额。
- 合理解释经P20、机制与变量线新版本、复检及新P09/P10后才可进入资产/事件；原来源保留。
- 新工具检查原始命题定位、批准与输入hash、复检版本、fresh scan/ref和禁止覆盖；支持BASE_LINE/WORLD_RULE的完整工件物化。
- P12及各消费者的宿主`resolution_dependencies`核验冻结包、激活和旧审读失效回执，覆盖确认因果及Writer分支。索引激活、完整依赖图与语义真实性仍需受信宿主执行。

## V1.7.0 长篇压力测试与成长/战斗提案
- 新增 `P01E` 修炼体系提案与 `P01F` 战斗规则提案：输出 `CultivationSystemProposal` / `CombatSystemProposal`，仅为 PROPOSAL；须经 P21A 单规则修订与宿主批准后才可映射为 WorldConstants，Schema 不足时标 `BLOCKED_SCHEMA_GAP`。
- 新增 `P05F` 叙述声音指南：作为既有写作说明注入 Writer，区分叙述者声音、POV 过滤与对白，不改任何 Schema。
- 新增 `P26` 长篇压力测试 sidecar：冻结基线、逐章状态提交、真实时间/揭示顺序双记录、每 5 章滚动审查、主角缺席与删除测试、P20 回归；不替代正式审计回执。
- 27 份既有提示词按 30 章压力测试包升级，强化成长资源循环、战斗能力边界、决策与因果细节；静态预算上限调整为 15 项 / 3000 字符。
- 校验器 `tools/validate_skill.py` 的门槛改为可配置常量，并新增 4 个输出契约与 2 个注册表行的检查。
