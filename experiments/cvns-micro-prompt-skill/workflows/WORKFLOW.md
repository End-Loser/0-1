# V1.7.0 微任务提示词工作流（基于 R-06.2 v7.6）

## 执行总则
每次调用前按 `TASK_LIFECYCLE_REGISTRY.csv` 检查 skip_precondition 和预期消费者。无输入、无目标或无下游消费者时，优先由宿主记录 SKIPPED/NOT_APPLICABLE，不发起模型调用。
每次模型调用只执行 TASK_REGISTRY 中一个自然任务。每次调用使用独立会话、独立输入包、独立输出路径。前序成果通过只读引用传递；冻结成果不可原位修改。

## 顺序
1. `P00` 项目契约 → 用户确认冻结。
2. `P01A` 按领域整理世界事实；每条机制分别执行 `P01B`；机制对执行 `P01C`；`P01D` 确定 opening_date。题材承诺修炼成长或能力对抗时，分别执行 `P01E` 修炼体系提案与 `P01F` 战斗规则提案；两者仅为 PROPOSAL，须经 `P21A` 修订与宿主批准后才可写入 WorldConstants。
3. `P02A` 核心主题问题 → `P02B` 对立立场对 → `P02C` 主题回应条件。
4. 每个子主题分别执行 `P03A`；每个子主题分别执行 `P03B` 抽象 Anchor。Anchor 不是具体剧情。
5. 循环执行 `P04A` 一个实体候选、`P04B` 一个必要性判断。已在 EntityRegistry 注册的实体不得重复调用 P04B；P04B=REGISTER/REUSE/REJECT 由宿主确定性提交/拒绝。每个 Anchor 执行 `P04C` 覆盖检查。需求队列为空后冻结 EntityRegistry。
6. `P05A` 故事弧形状；每个实体分别 `P05B` 叙事功能。对 CORE/MAJOR 或确实承担重要选择的 SUPPORTING 决策主体执行 `P05E`，生成稳定决策倾向卡；非决策主体跳过。只有存在用户提供或批准的语言样本时才调用 `P05C`；无样本跳过。执行 `P05D` 情绪曲线。项目要求稳定叙述声音时执行一次 `P05F` 生成写作指南；它只注入 Writer 与风格复核，不改 Schema。
7. `P06` 一次生成全局 ExpressionTaskBook 矩阵。它是一个不可拆的全局认知任务。每个候选 entity×dimension 恰好一项，字段服从 R-06.2 原始合同。
8. 每个 Anchor 单独执行 `P06B`。只有覆盖通过或明确 `NO_LINE_REQUIRED` 的相关 Anchor，才允许其关联 BASE 线进入生成；缺口返回新版本 P06 提案，不原位改写。
9. 对每个已签约 entity×dimension，加载对应 `BASE_<DIMENSION>` 与 `BASE_LINE_CORE`，从可信初始资料绑定本维度 opening_anchor，一次生成完整 BASE 线；运行 `validate_base_plan.py` 核对来源、首节点与计划身份，语义映射另审。禁止按章节截断；11维不含 time。
10. 每条 BASE 线分别执行 P07A 目的、P07B 变化覆盖、P07C 输入隔离审计。只有统一决策枚举通过的线才冻结；REVIEW/REJECTED 不得假装通过。
11. 每对实体（含实体内部）执行 P09 全线关系扫描；宿主将 candidate_local_id 规范化为 scan_artifact_id::candidate_local_id；每个 material candidate 执行 P10 并保留该 ID。缺少节点/证据时 BLOCKED。互斥先 P11A 候选集、P11B 合理性比较；明确不探索或解释失败才 P11 最小修复。获选解释经机制设计/修订、P20、新完整工件、复检、新 P09/P10、宿主批准及物化/激活后，新的张力进入 P12，新的确认因果进入 P13B。证据不足默认挂起，显式授权才可探索。原判定不改名，提案不直接修改冻结线。P09 全覆盖不强制产生关系。
12. 对同一子主题/章节范围的 P12 结果执行一次 P12B 资产组合筛选。每个资产必须被选中、合并、延期或拒绝；不得默认全部保留。批准前运行 `validate_narrative_logic.validate_portfolio` 及 schema 校验，核对领域 ID 完整分区、源阶段章和合并映射；失败退回任务，不由宿主补占位或改判。
13. 每个子主题/故事范围执行 P13 NarrativeLens；只引用 P12B 的 SELECTED 资产。
14. 每个范围执行一次 P13B，将 SELECTED 资产的 SETUP/REVEAL/PAYOFF 阶段需求与 P10 已确认因果交互组织为事件机会组。同一资产可在不同事件机会中承担不同阶段；同一阶段在本范围只能有一个归属。仅相同地点/主题/章节不构成聚类依据；必须有共享核心行动、互动决策和兼容状态变化。没有真实行动/结果的材料可延期或标记本范围不发生。P13B 完成及重用前运行 `validate_narrative_logic.py --opportunity-plan ... --base-nodes ...`，从完整BASE绑定原节点，BASE_LINE目标须存在且同章proposed；通过后由宿主冻结，失败不得进入P13C，旧冻结与旧模型PASS不覆盖来源失败。
15. 每个 EventOpportunityCluster 执行一次 P13C（有主要决策者时），一次调用推演该事件中全部主要决策者的独立选项、选择理由、替代方案、代价和反事实敏感性；没有决策者时不调用模型，由 `tools/materialize_no_agent_decision.py` 生成 FROZEN 的 NO_AGENT_DECISION_REQUIRED 终态回执。有决策者时，运行 profile/decision-plan validator；VALIDATED/FROZEN 且决策状态 READY/CONDITIONAL 才可交给 P14A。然后每个机会组执行一次 P14A，一个机会组最多生成一个 Event；一个 Event 可承载多个资产阶段/因果交互。每个 Event 分别执行 P14B 可行性检查；通过规则/状态核验后由宿主晋级为 VALIDATED/FROZEN，P14B 未批准不得进入 P17A。
16. 每个 Event 分别执行 P17A 正文前预检；同时核对 P13C 决策计划或 NO_AGENT_DECISION_REQUIRED。decision 仅允许 READY、CONDITIONAL_READY、BLOCKED、NOT_RUN、NOT_APPLICABLE。只有 READY/CONDITIONAL_READY 可继续；结构检查通过后由宿主晋级为 VALIDATED/FROZEN，条件必须随事件向下游传递。
17. 每章对所有可继续事件执行 P14C0 去重与 BASE 节点唯一所有权分配。无法合并、无法唯一归属或存在循环时 BLOCKED。P14C0 方案须完成 schema/节点归属校验并冻结后，批准的合并方案由 `tools/materialize_event_units.py`（默认强制完整叙事溯源；仅迁移 V1.4.1 历史事件时显式使用 `--allow-legacy-provenance`）物化为新的 Event 版本；decision plan、asset phase、relationship refs 写入独立 manifest，源 Event 保留。
18. 每章执行 P14C 对去重后事件单元进行拓扑排序。存在 BLOCKING 依赖、循环或总序不成立时 BLOCKED，不给伪序。
19. 每章执行 P08 节奏意图（仅在 P14C 有效排序后）。P08 不读取未筛选候选数量，不决定事件数量。日期轴由确定性程序计算。
20. 每章执行 P15A 节拍图；需要钩子时执行 P15B。Beat ID 与 Artifact ID 由宿主确定性生成，不允许占位符进入后续输入。
21. 宿主先物化P15稳定ID并审定只读计划快照，再按 `CANON_CONTEXT_POLICY.md` 建立generator记忆视图，执行需要的P16A DERIVED线、P16B ACK、P16C生命周期任务。每条单线/单项独立调用，生产预检使用 `--require-canon-context`，输出执行 `canon_context.py` 来源与结构校验；不以包含自身的最终章节包作为P16前置。无需任务的终态不调用模型，校验不自动提交索引。
22. 宿主运行 `tools/finalize_chapter_plan.py`：检查 P14C、P15A/P15B、依赖清单、事件节点所有权及所有批准状态；通过后发放稳定 ID 并生成章节计划冻结包。失败则不进入正式 Writer。
23. 每章组装Writer Input Pack，按依赖闭包加载全部必需引用及writer记忆视图。P17C仅接收闭包与记忆hash/来源摘要；P18生产预检使用 `--require-writer-dependency-gate --require-canon-context`，记忆源hash必须在已核验闭包内。仅当前版本P17C=READY且宿主全部检查通过才可进入P18，BLOCKED时禁止调用生产Writer。
24. P18 每章一次正式渲染。若需专门测试审计器，可在明确测试授权下调用 P18D；P18D 产物永久标记 DIAGNOSTIC_ONLY。
25. 正文后按对象分别运行 P19A1、P19A2、P19B、P19C1、P19C2、P19C3、P19D（事件设计复核）、P19D2（正文事件呈现）、P19E、P19F、P19G、P19H、P19I。没有样本/机制/基线时由宿主记录 NOT_APPLICABLE 或 NOT_EVALUABLE，不调用模型生成空报告。
26. P23 润色默认关闭；一次只修一段、一个目标。修改冻结成果先执行 P20 影响分析并创建新版本。
27. 每轮结束运行 `tools/trace_artifact_consumption.py` 生成引用追踪；对未被引用的产物区分重复调用、空输出、合法终态、提案待批、诊断用途、被取代版本和真正孤儿产物。

## 核心调用粒度
- 一个 BASE 变量线：一个 entity×dimension，一次调用，完整有效区间。
- P09：一对实体一次调用，读取双方全部已签约线。
- P11A/P11B：一个冲突分别一次候选集与一次全候选比较；不按候选拆调用。
- P12：一条张力一次调用；P12B：一个范围内资产组合一次调用。
- P13B：一个故事范围内资产阶段×已确认因果交互的机会聚类一次调用。
- P13C：一个事件机会组一次调用，批量推演该事件全部主要决策者（不按人物逐个调用）。
- P14A/P14B/P17A：一个事件机会/事件一次调用。
- P14C0/P14C/P08/P15A/P15B/P17C/P18：一章一次调用或确定性处理。
- P01E/P01F：一个项目各一次提案调用，不按境界或招式拆分；提案须经 P21A 审批才生效。
- P05F：一个项目一次，形成版本化写作指南后复用。
- P26：一个冻结基线与测试章节范围一次，输出测试 sidecar，不进入正式 Canon。
- 专项审计：一个自然审计对象一次调用。

## 状态和使用规则
- `NO_ASSET`、`NO_EVENT`、`DEFERRED`、`NOT_APPLICABLE` 都是有效终态，不得为提高产出率强制生成。
- 候选输出不等于获批资产；资产不等于事件；事件候选不等于已批准事件；诊断正文不等于交付正文。
- 每个输出必须登记上游引用与预期下游消费者；没有消费者的任务应跳过或标记为诊断任务。
- `GENERATED/LOADED/REFERENCED/SELECTED/MATERIALIZED/RENDERED/VERIFIED` 是不同消费阶段，禁止用一个“已使用”字段概括。
- 下游任务不能覆盖上游成果；修改只能创建新版本。


## V1.4.1 继承：最小调用与消费者先行

每次 LLM 调用前：
1. 查 `TASK_LIFECYCLE_REGISTRY.csv`，确认本任务有明确消费者或合法终态。
2. 运行 `tools/preflight_task_call.py`；SKIP/BLOCKED/NOT_APPLICABLE 不调用模型。
3. 按 `INPUT_PACK_POLICY.md` 只构建本任务输入投影。P17C 只拿依赖闭包摘要；P18 完整 Writer Pack 只出现一次。
4. 调用后登记输出消费状态。未进入下游的输出不得立即称为废弃，先判定是否是合法终态、提案、诊断或版本替代。
5. 每轮完成运行 `trace_artifact_consumption.py` 和 `audit_input_pack_efficiency.py`。


## V1.5.0 人物选择与事件真实意义
- 事件不是资产清单；它是参与者在具体时间/地点/规则限制下作出选择、采取行动并造成状态变化的因果过程。
- 不能为了兑现资产而强迫角色行动；当人物的最优/可行选择不会产生预定剧情时，应改变计划或接受 NO_EVENT。
- 每个主要行动者必须有自己的目标、知识边界、可行选项和代价；对手有独立目的，不得静止等待主角。
- P13C 只输出决策意图和选择理由；P14A 才合成交互行动及实际计划结果；P14B 检查可行性；P19D 检查人物/因果合理性。
- P19D 核对原节点章/seq与状态内容；读者义务只查可观察的计划载体及知识边界，不索要未生成正文。载体缺失仍须指出；旧条件判定不重标，正文实际兑现由P19D2核验。
- 事件合并不得抹掉不同决策路径、独立结果或资产阶段；P14C0 不能把“同一主题/同一场景”作为合并充分条件。


## V1.5.0 Event 源模型映射
- `P13B EventOpportunityPlan` 与 `P13C EventDecisionPlan` 是独立计划 sidecar；二者不扩展 R-06.2 `Event`。
- 资产原始阶段章严格取 `StoryAsset.introduce_chapter/reveal_chapter/payoff_chapter`。P13B 每个 cluster 对应一个 target_chapter 和一个 Event；不同阶段目标章不同，不得强行聚为同一 Event。
- P14A 将状态结果映射到 Event 已有字段：`variable_changes`、`state_at_start/end`、`knowledge_changes`、`goal_changes`、`relation_changes`、`item_transfers`、`rule_changes`、`cognitive_impact`、`setup_payoff_links` 等。禁止新增 `state_deltas`、`decision_plan_refs` 或 `asset_deployments`。
- REVEAL 的读者 payload 由 P15A 落到获批 beat 的 payload；读者认知线仍按 R-06.2 机制从节拍承保 payload 推导。Event 的 `cognitive_impact` 不替代读者认知线。
- Event 合并后，P14C0 物化 manifest 必须保留所有 P13C decision plan refs、P13B asset phase refs 和 P10 relationship refs；这些 sidecar 引用不塞进 canonical Event JSON。

## V1.6.0 解释修订与并行消费
- 详细协议见 `CONFLICT_EXPLANATION_POLICY_V160.md`。解释可以异类，但必须有稳定的因果、条件/范围边界、跨线影响和揭示依据。
- P11B 获选只是修订提案。`conflict_resolution.py` 只复制批准的完整 BASE/规则到新冻结目录，不激活索引、不证明文学合理性。
- 宿主沿依赖图传播解释来源，给 P12、P13B、P17C、P18 等消费者附宿主 `resolution_dependencies`，preflight 检查每个冻结包及激活/失效回执。不能因走确认因果分支而省略此门禁。
- 全部章节可以读取同一冻结计划版本并行渲染；不要求等待相邻 Writer 正文。是否能实际全章并行仍取决于宿主调度和跨章接口验证。
