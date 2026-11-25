# 宿主调度器要求 V1.6.0（本文件不注入模型；继承既有数据流规则）

1. 每个模型任务必须使用独立会话、单独输入包、单独输出路径；历史对话不继承，除非任务合同显式允许。
2. 按 TASK_REGISTRY 的 read_scope 与依赖闭包物理组装输入；不能把全项目文件传入后只靠提示词要求忽略。
3. 任务的 predecessor、validators、权限、状态、审批规则只留在宿主，不注入模型上下文。
4. FROZEN 成果只读；每次写入必须使用新路径。目标路径已存在时拒绝覆盖。
5. P04B 决策通过宿主提交 EntityRegistry；已注册实体不重复执行注册审计。
6. P06B Anchor 覆盖结果必须成为对应 BASE 签约任务的门禁；未覆盖不得静默继续。
7. 没有语言样本不调用 P05C；无 VoiceCard 不调用 P19F，记录 NOT_EVALUABLE。
8. P12 的单条资产决定由 P12B 组合选择；未 SELECTED 的资产不得进入 P13/P14A/Writer。
9. P13B 对资产聚类；P14A 按机会组生成 Event，而不是一资产一事件。
10. P14B/P17A 使用固定 decision enum；只有 READY/CONDITIONAL_READY 可进入 P14C0。
11. P14C0 事件去重/节点归属方案经校验后由 `tools/materialize_event_units.py` 物化新 Event 版本；默认强制严格叙事溯源，每个源 Event 对应一个 P13C 决策计划/无主体终态回执；manifest 保留决策/资产阶段/因果关系 refs，源 Event 保留不改。仅迁移 V1.4.1 历史产物时显式使用 `--allow-legacy-provenance`。
12. P14C 必须执行拓扑排序；存在环或 blocking dependency 时阻断。
13. P08 必须在 P14C 后运行，且不接受未筛选事件候选计数。
14. `tools/finalize_chapter_plan.py` 是章节计划批准/ID/冻结的确定性门禁；只接受有效 P14C、P15A/P15B、依赖清单与审批状态。冻结副本存在时拒绝覆盖。
15. P17C 必须核验 dependency-closure manifest 的完整性；缺少 EntityRegistry/purpose/secrets/精确 WC 引用、资产引用或状态审批时 BLOCKED。
16. P18 正式 Writer 只在 P17C=READY 时可调用；诊断模式必须走 P18D，产物标记 DIAGNOSTIC_ONLY 且与生产目录分离。
17. P19D 只审事件设计因果；P19D2 必须接收正文片段，负责审正文实际呈现。
18. 每个输出都记录消费关系：被哪个下游任务引用、作为何种证据、是否被排除/合并/延期/取代。无消费者且无诊断目的的任务应跳过。
19. 调用前执行生命周期预检：如果 skip_precondition 未满足（如实体已注册、无语言样本、无 SELECTED 资产、无机制调用），宿主记录 NOT_APPLICABLE/SKIPPED，不调用模型。
20. 每个任务调用前必须在 `TASK_LIFECYCLE_REGISTRY.csv` 找到预期消费者或合法终态；没有任何消费者且没有诊断/门禁目的时，不得启动调用。
21. 调用后必须写消费追踪：区分完整 Artifact ID 引用、领域对象 ID 引用、门禁/审计终态、提案待批准、诊断用途、被取代版本和无消费者待审。
22. 不得把“输入包包含某 ID”直接等同于语义使用；需要记录下游输出中的 source_refs/decision_refs/selected_refs，并在最终事件/正文中再次追踪。
23. 正式 Writer 输入包不得重复内嵌 P14B/P17A 完整审计报告；仅传决策、阻断项、必要证据引用和审批回执。
24. `P17C=BLOCKED` 后，P18 必须跳过；只有显式测试授权可调用 P18D，且结果只能供审计器诊断使用。
25. 每轮结束运行 `tools/trace_artifact_consumption.py`；所有未引用产物必须被分类为合法终态或 ORPHAN_REVIEW，不能静默忽略。


## V1.4.1：调用前置检查与数据消费闭环

1. 每个任务调用前读取 `TASK_LIFECYCLE_REGISTRY.csv` 的 `preflight_rule`。
2. 先执行 `tools/preflight_task_call.py --input-pack <pack.json>`。`NEEDS_HOST_CHECK` 仍需宿主确认依赖、权限、版本和消费者；不得直接视为 READY。
3. 将 SKIP/BLOCKED/NOT_APPLICABLE 作为宿主确定性记录保存，不生成空模型输出。
4. 每份输出在任务结束时登记 `artifact_id`、`source_artifact_ids`、`domain_refs`、`disposition`、`expected_consumer`。允许终态包括 USED、TERMINAL_AUDIT、NO_ASSET、PROPOSAL_PENDING、SUPERSEDED、DIAGNOSTIC_ONLY、REJECTED、DEFERRED。
5. 每轮运行结束执行 `tools/trace_artifact_consumption.py`。未引用输出先标记 `ORPHAN_REVIEW`，不自动删除；审核后决定是有效终态还是无消费者生成。
6. 生成 `WRITER_PACK` 前先检查其唯一消费者。P17C 只校验依赖 manifest；P18 才接收完整 Writer Pack。禁止将同一完整 Writer Pack 同时嵌入 P17C 和 P18。
7. 生产 P18 必须等待 P17C=READY；若 BLOCKED，只能用单独授权的 P18D，输出永久标记 DIAGNOSTIC_ONLY。
8. 输入包超过 300 KiB 必须有 `input_budget_exception`；没有例外批准时宿主阻断调用。


Preflight JSON 输出必须符合 `schemas/PREFLIGHT_DECISION_SCHEMA.json`。宿主不得把 `NEEDS_HOST_CHECK` 解释为 READY。


## V1.5.0 新增宿主要求
24. P05E 只为真实决策主体生成；同一 entity_id + profile_version 已 VALIDATED/FROZEN 时复用，不重复调用。P05E 不是 BASE 线，不得写入 BASE 快照。
25. P09 输出的因果交互只是候选；只有 P10=CAUSAL_INTERACTION_CONFIRMED 才可进入 P13B。候选必须保留 source/target line-node refs 与方向。
26. P13B 资产分配按 `(asset_id, phase)` 校验，而非按 `asset_id` 唯一；允许一个资产的 SETUP/REVEAL/PAYOFF 分布在不同事件。每个阶段在一个 scope 内必须唯一归属或显式延期/不适用。
27. P13B 没有 selected assets 但有 confirmed causal interactions 时可运行；两者皆无则 NOT_APPLICABLE。每个 event cluster 至少有一个真实 driver_ref。
28. P13C 每个事件机会组一次调用，列出该事件所有主要决策者；不按每个角色各调用一次。缺少关键决策 profile/状态证据时 BLOCKED 或 CONDITIONAL，不允许由 P14A 猜测。
29. P14A 必须读取 P13C 只读成果；Event 仍严格遵循 R-06.2 `Event` 模型，不添加自定义模型字段。P13C 意图不等于实际结果。
30. P14C0 合并时额外检查主要人物选择路径与后果是否等价；若合并会消去独立选择/代价，必须分开。
31. P19D 对每个主要行动者核对“当时知道什么、想得到什么、可选什么、为何选择/放弃”；不以事后结果倒推人物当时必然知道。
32. V1.4.1 的旧 P13B asset-level 单次分配产物不得自动升级为 V1.5.0 phase-level mapping；需要重新规划阶段的资产应重跑 P13B。
33. P13C 若 preflight=NOT_APPLICABLE（无 `decision_required` 主体），宿主必须调用 `tools/materialize_no_agent_decision.py` 物化只读终态 `EventDecisionPlan`：`decision=NO_AGENT_DECISION_REQUIRED`、`actor_decisions=[]`、绑定当前 `cluster_id` 与输入 hash；P14A/P17A/P19D 读取该终态回执，不得要求模型为无行动主体伪造人物决策。
34. P05E `INSUFFICIENT_BASIS` 不等于冻结 profile。若主体 `profile_required=true`，宿主阻断 P13C；只有该主体 `VALIDATED/FROZEN` profile 才能满足决策卡前置。

35. P09 的 pair-local `candidate_local_id` 必须由宿主规范化为全局 `candidate_ref = scan_artifact_id::candidate_local_id`；P10 的输入/输出和 P13B 引用都保留此值，不得重新编号。P10 缺少 canonical ref 或原始节点/证据引用时 BLOCKED。

36. P14A/P14C0/ChapterOutline.event_records 必须以 `schemas/EVENT_MODEL_SCHEMA.json` 为准；不得输出旧 Skill 草图 `EVENT_SCHEMA.json` 中的 `state_deltas` 字段。P14A 将 BASE/DERIVED 变化写入 Event.variable_changes，并在 Event.causal_links 中保留原 driver_refs；READER_COGNITION 只作为揭示义务传至 P15A beat payload，不能混同角色 knowledge_changes。

37. P13B 的每个 event_cluster 只有一个 target_chapter；所有 asset_deployments.target_chapter 必须等于该 cluster target_chapter，并逐项复制 P12B `selected_assets[].phase_chapters[phase]` 原值。宿主 preflight 缺少阶段章时 BLOCKED；不得为聚类方便而改排资产铺垫/揭示/回收章。

38. P14A、P17A、P19D 的宿主 preflight 必须检查其上游产物 `status` 为 VALIDATED/FROZEN；仅有 `decision=READY/FEASIBLE` 不构成批准。P13C 与 P14A 必须校验 opportunity `target_chapter`，P17A 校验 Event.chapter 与该目标章一致。
39. `tools/materialize_event_units.py` 必须按 `EVENT_MODEL_SCHEMA.json` 拒绝非 R-06.2 Event 顶层字段（例如 `state_deltas`）和无效 event_type；不得通过重写/覆盖 chapter 字段来掩盖上游目标章漂移。
40. P12B 输出必须满足 `ASSET_PORTFOLIO_DECISION_SCHEMA.json`；`phase_chapters` 按 SETUP=introduce_chapter、REVEAL=reveal_chapter、PAYOFF=payoff_chapter 精确复制，P13B 只能消费原值。
41. P05E 的 VALIDATED/FROZEN 倾向卡按 entity_id + profile_version 复用；P13B 的 EventOpportunityPlan 必须通过结构 validator 后冻结，P13C 才能调用；P13C EventDecisionPlan 必须通过决策结构 validator 后冻结，P14A 才能调用。结构 validator 不替代人物心理真实性审读。
42. P14B、P17A、P14C0、P14C 等审批型产物必须先由对应 validator/确定性检查验证内容，再由宿主将状态从 DRAFT/REVIEW 晋级；只有 VALIDATED/FROZEN 才能被下游消费。验证器不得要求输入先是 FROZEN，否则形成验证-冻结死锁。

## V1.6.0 冲突资源化门禁
43. 当前互斥先进入 P11A/P11B；显式不探索或无有效解释可走 P11。证据不足的探索需要明确授权；不把候选当成缺失证据。
44. P11B 的 PASS/PROPOSE_REVISION 不自动批准事实。复用 P01B/P21A、P20、BASE 三项审计和规则接口复检；受影响实体对重新 P09/P10。
45. 按 `CONFLICT_EXPLANATION_POLICY_V160.md` 绑定原始命题、来源 hash、选中解释、新完整工件及复检 hash。批准文件和激活记录必须受保护，模型无写权限；hash 绑定不代替身份认证。
46. 物化器只支持完整 BASE_LINE/WORLD_RULE 新版本。激活新索引并使 P20 范围内旧消费者/旧审读失效由宿主执行，不能把 FROZEN 包存在视为已经切换索引。
47. 对 P12、P13B、P17C、P18 等消费任务，从可信索引解析传递性解释来源，填充输入包顶层 `resolution_dependencies`，并运行 preflight。每项包含原 candidate_ref、冻结 manifest 路径/hash、包内新 relationship_decision 和 activation 回执；检查不通过即 BLOCKED。
48. 不得隐瞒解释依赖，也不得将模型自产 ACTIVATED/APPROVED 作为授权。所有生产消费者仍需版本一致性及完整依赖闭包检查；本工具只能检查声明的范围。
49. 保留唯一 BASE/DERIVED 状态机、完整稀疏变量线和只读冻结版本。解释与批准索引是控制元数据；Writer 无权在正文临时发明承重机制。

## 实测修复：开场锚与计划审读
50. BASE 调用前从可信初始资料建立本维度 `opening_anchor`：entity_id、dimension、state、source_ref、source_sha256、source_pointer、source_value。state 是宿主审定的维度格式化状态，不是模型自选开场；未知部分明确 UNKNOWN。锚只绑定已有事实，不预定未来剧情。
51. 运行 `tools/validate_base_plan.py --line <line.json> --anchor <anchor.json> --source <source.json>`。来源字节 hash、JSON Pointer 原值、首节点状态、顺序、有效区间及未批准 ID 任一不符则阻断。该工具不自动证明格式化 state 忠实于源文本；宿主必须审查映射及本维度语义。历史线不能未经复核自动升级。
52. 首节点 established，未来设计节点 proposed；同章可用 seq 区分开场和计划变化。稳定状态前向携带，信息揭示、物品用途或他方认可不自动构成本维度状态变化。P07A/B/C 即使 PASS 也不能覆盖确定性失败。
53. P06 acceptance_criteria 限于本线计划可验条件，expected_effects 留正文后验。P07A 将后验效果记待验证；P07B 仅确认有具体来源的遗漏，同时检查开场改写。不得把未提供的未来来源或未生成正文当成覆盖失败，也不得强迫 PASS；范围错误应修订契约。
54. 修复通过新版本失效受影响 BASE、P07 及下游消费者；原失败请求、响应、用量和判定保留。正文效果仍须由 P19G/P19D2 等后验，不因前置 PASS 宣称已兑现。
55. 单线隔离限制的是读取权限及既有事实断言，不禁止在白名单规则和本线功能内设计未来 proposed 节点。完整线不得只留开场等待事件来补齐；变化前件以语义描述给出，P09/P10 再校验跨线兼容，P14A 后绑定实际计划事件。未知历史不能伪造成未来提案的已证实前件。
56. P07C 的 input_manifest 必须解析至实际允许的来源内容及条目 ID，不能只传字段名后要求审读器猜测引用是否获准。registered_relation_targets 只允许登记对象身份，不带入对侧状态；opening_anchor 属注册资料的宿主投影，allowed_locations 属世界场馆资料，均需有来源。
57. 新 P07B 输出须包含 blocking_findings；运行 `validate_coverage_audit` 核对判定与阻断条目一致。PASS 携带阻断项、或 REVIEW/CONFIRMED_MISSING/NOT_YET_VERIFIABLE 无具体范围内阻断项，均退回审读任务而非改判。条目真实性仍须核对来源；旧合同审读保留原版本，不伪补字段。
58. `compact_redundant_nodes` 只可在新版本中折叠逐字相同、幅度零、非锚点且无动作/情绪/前件/绑定等载荷的 proposed 重复节点。保存原响应 hash、完整删除清单及新工件 hash；状态前向携带须等价，新版重新 P07A/B/C。不得用压缩移除有意义的幅度零变化或改判旧审读。
59. P12B 批准前运行 `validate_narrative_logic.validate_portfolio(plan, candidate_payloads)`，并完成 JSON Schema 校验。领域 asset_id 必须恰好得到一种处置；禁止占位 ID、选中与延期/拒绝重复、漏项及重复 canonical。merged_groups 的来源集合须与对应 selected_assets 一致；不兼容源阶段不得被合并后悄悄改期。输出具体缺失/重复/未知 ID 退回原任务，保留失败证据；结构通过不证明容量筛选具有实际叙事增量。
60. P13C 输入快照与倾向卡只含当前 primary_actors，提供当前部署阶段载荷；世界真实事实不自动授权角色知悉。unresolved_gaps 须说明对本次选择/执行的影响，历史谜团、无关未来偏好及非必需的时间换算未知不自动阻断；真实条件缺失仍阻断，不由宿主改判。五章实测入口 `tools/real_novel_test.py <stage> --work-dir <已有运行目录> --token-budget <已授权累计上限>` 默认200万，扩大需用户确认。并行调用预留输入字节上界及最大输出，所有失败/中断保留；已送达但无响应的请求账单用量可能未知。
61. 同章多个 seq 变化不得被章末快照抹去。P13B/P13C/P14A 除开场/终点外，必须读取所承载的本章完整变化节点与前件；它们仍为 proposed，不自动成为开场已知。需要先听、先查、先获授权的知识或资源应由本事件实际动作或已批准前驱承载；稳定倾向卡、角色能力和事件自写前件不能证明已完成获知。跨线时序仍以实际因果为准，不比较不同线 seq 大小。
62. P13C CONDITIONAL、P14B FEASIBLE_WITH_CONDITIONS、P17A CONDITIONAL_READY 保留原判定，不改为无条件通过。非阻断限制沿物化、节拍、P17C 与 Writer Pack 显式传递；真实阻断、未满足必需条件仍停止。P19D CONDITIONAL_PASS 仅按第64条核验纯后续呈现义务，否则停止。条件不得只留在被省略的长审计报告中；正文后必须核验实际遵守情况。
63. P13B 冻结和每次重用前，运行 `validate_opportunity(plan, assets, confirmed_links, base_nodes)`；base_nodes 来自完整只读 BASE，每行含 source_node_ref 与原 node。BASE_LINE 目标必须存在、属于本簇章节且为 proposed；本线 seq 不得按章重置或虚构。CLI 使用 `--base-nodes`；未提供时的旧兼容模式不证明 BASE 绑定。已冻结但校验失败的计划保留原文件，写独立失效回执并阻断全部依赖事件，不因旧模型 PASS 放行。
64. P19D 只验证设计因果：proposed BASE 节点是事件后计划状态，不是事件起始知识。读者揭示义务须绑定现有 Event.actions 的可观察载体；未生成 P15A 或正文不自动失败，真实因果缺项仍退回。旧 CONDITIONAL_PASS 不重标，新审读使用 PLAN3 版本，保留 PLAN2 结构错误作为修订输入。`validate_causal_continuation` 仅允许非阻断 READER_BEAT_EMBODIMENT、owner_stage=P15A、既有READER_COGNITION target及合法动作索引；CONDITIONAL finding 与条件一一对应，REVISE/BLOCKED/资源或知识缺项一律停止。条件原样传至节拍；`validate_render_obligations` 要求恰好一个payload beat绑定条件ID、目标ref和required_elements，缺失时不得进入Writer。P19D2仍须核验实际呈现，beat登记不证明文学效果。
65. 五章完整规划修订使用 `validate_planned_node_coverage`，对授权范围内每个原 proposed 变化登记恰好一个事件所有者；完整 BASE 不因机会遗漏而删减。P14A 用 `validate_event_target_coverage` 保留本簇目标及原 driver；覆盖通过不证明动作语义忠实。单源物化不得改写 canonical Event，排序须精确覆盖批准 ID，节拍须原样保留 Event 并绑定合法 ID。
66. Writer 启动前复核来源文件、冻结章节文件及批准回执字节 hash；完整变量线与全书冻结事件作为设计上下文，当前角色可知边界仍按获知时点限制。相邻接口只引用共同冻结计划，不消费相邻 Writer 正文。追踪每次产生独立快照并纳入失败尝试；没有五个字数达标的正式 Writer 结果，不导出五章合订稿。正文审读完成与文学质量批准分开记录。
67. 事件结算须明确实际裁决与行动分支，不能让 Writer 补定承重结果。五章入口 `settlement` 只在全章节包未冻结、正文未启动前修订第五章，保留原 BASE、规则及其他事件，重新审读后输出独立新事件包。
68. 节拍 ID 只从 outline.beats 定义表分配；转折点、节奏图、钩子及文字引用使用同一映射，不在引用位置新造 ID 或覆盖整段描述。重复定义与未绑定占位引用阻断。旧错误冻结副本保留，新版本重新冻结与输入检查。
69. 五章宿主的 P18 输入去重仅为无损包内引用：保留完整原输入和冻结来源，发送前还原全部引用并核对，引用只指向本包完整载荷。实际请求字节包含引用说明开销；不得将字节降幅表述为 token/账单或文学质量降幅。真实模型对引用的消费仍需后续对照验证。
70. 调用级强化 Schema 必须与回包验证一致，并在请求前检查 Schema 及其传递本地依赖。缓存和返修绑定执行指纹；缺指纹或指纹变更不得默认复用旧缓存，不删除历史工件、不因此自动重生成全书。
71. 同一执行指纹的自动与续跑请求合计最多四次；相同错误无可测改善则持久 BLOCKED。字数误差缩小可继续，返回同样正文或改措辞但相同错误未减则停止。修订输入必须新版本；修订合同或说明须记录新指纹。预算与调用次数授权仍独立限制全部版本，不因换指纹变成无限预算。
72. P08 的全书时间窗不等于每章跨度，opening_date 不等于每章重新开场；本章端点未获批时保留未定。Writer 前读取 hash 绑定的 P08 来源并汇总明确的章节端点，发现前章终点晚于后章起点时停止，不用“已冻结”覆盖接口冲突。修订必须新版本批准与冻结，不修改旧 P08、旧章节包或原 BASE。
73. P18 注入有来源的 WriterBoundaryContract，保留已批准时段、未定时段、事实来源与 UNKNOWN；未来 proposed 和其他角色私知不能成为当前 POV 事实。允许感知、情绪、比喻、质疑和人物谎言；承重证据、程序、机制与知识变化须由获批设计承保。
74. Writer 本章明确时间越界在回包检查中阻断，五章完成后独立汇总时间锚点，跨章时间回退不写成合格 Writer 包。已有 Writer 包也须离线复检，不因存在文件就跳过新门禁。汇总审读读取所有正文不等于将相邻正文注入任何 Writer。
75. 正则检测的录音位置、声音识别、底账历史和制度线索只记 REVIEW_REQUIRED，不自动判为事实矛盾；P19D2 收到世界事实、原 Event 与候选上下文作语义审读。正常动作耗时、背景声识别、问题、回忆与假设不得机械升级为新证据。空扫描不是忠实性批准，批准解释仍须回写后激活。
76. 五章运行的功能覆盖与修稿准备使用 `tools/audit_run_readiness.py --work-dir <已有运行目录> --out <运行目录外的新报告>`，不读取配置、不调用模型、不写回运行目录。分别统计有响应尝试、已保存工件、下游请求引用和输出显式来源引用；引用不证明语义使用，validated 路径中的 DRAFT 不自动批准。P14C0 的单源复制回执单列，不计为模型调用。未调用任务保留 NO_RUN_EVIDENCE，不补造 SKIP/NOT_APPLICABLE。
77. 离线修稿清单保持 PENDING_REVIEW，绑定源文件 hash、原始疑点与报告位置。先核验章节依赖、批准回执及冻结字节；来源变化即阻断。时间设计、正文时间、事实候选、文学意见和 Event 忠实性复核分别登记，后两类模型意见仍须核对原文。清单不改变批准事实、完整 BASE 或激活索引，不代表全图失效管理、完整修稿闭环或出版质量批准；修订必须使用新版本并完成实际审读与重新冻结。
78. 五章入口的 `timing` 只生成 P08 TIME1 候选，不调用 freeze，不替换 chapters-r2 或激活索引。输入为本章批准事件摘要、开场日及相邻批准事件顺序，不注入其他候选或任何正文。结构化 start_phase/end_phase 可为 null；精确时段提案须标为 PROPOSED_NOT_APPROVED。先为整个阶段及可能的两次尝试检查累计预算，仍受既有授权约束。
79. `timing_repair --timing-review <宿主审读文件>` 校验原候选包hash、原工件hash及逐项引用原文，只重生成明确 REVISE 的章节为 TIME2，其余候选原样复用。审读文件不是审批授权，hash不是审读者身份认证；保存范围意见不代表独立人类盲审。端点结构检查不覆盖span/description的完整自然语言，语义矛盾须保留独立意见并返修。零显式端点冲突与全部null均不能自动批准时间可行性，旧版本仍只读。
80. 项目级同日时间段使用 `tools/timing_approval_gate.py`，合同见 `TIMING_APPROVAL_POLICY.md`。每章候选、事件序列及大纲必须精确绑定ID/hash；语义审读覆盖事件顺序、耗时、转场、文字一致性及未定端点处理，批准记录绑定整个审读主体与审读hash。可信批准者由调用者权限策略显式传入，不从模型或manifest自动读取；hash和issuer字符串不替代身份认证。
81. 含P08/rhythm或已声明时间依赖的 `finalize_chapter_plan.py` 输入必须提交timing_gate及可信批准者。冻结器重新校验当前章源快照和项目/版本范围，不允许换hash复用旧审读，不允许删去真实依赖绕过门禁。新冻结记录保存时间证据与Writer限制，但完整依赖重建、索引激活和P17C仍须宿主另行完成。当前门禁支持任意章数的同日顺延段，不推断跨日或倒叙；旧无时间依赖夹具兼容分支不构成生产豁免。
82. 生产P18使用 `preflight_task_call.py --require-writer-dependency-gate`，按 `WRITER_DEPENDENCY_POLICY.md` 提供宿主控制的完整依赖图、实时索引、冻结回执及当前P17C。来源文件变化、索引切换/撤销均沿消费边失效；旧文件仍完好不代表当前有效。必需根节点失效即阻断，不相关节点变化单列。受控索引的完整性、权限及并发锁仍由宿主保证，不接受模型自产索引。
83. 新P17C绑定当前依赖主体hash和冻结回执hash，实际执行记录由宿主留存；禁止在旧审读上补hash冒充新审读。Writer输入里的P17C必须与绑定文件完整内容一致。门禁通过仍为NEEDS_HOST_CHECK，不批准索引激活、语义真实性或实际Writer载荷。新冻结副本显式写入版本和FROZEN状态，历史文件不改写；五章旧运行入口不是此通用生产合同的自动迁移器。
84. 按 `CANON_CONTEXT_POLICY.md` 使用 `tools/canon_context.py` 从受保护的当前索引读取完整CanonIndex与所需BASE。相关DERIVED按有效窗口内scope×dimension整簇保留，到期/逾期ACK及其引用不可静默裁剪；共享ACK线只计一次，必需簇超预算阻断。线数不是token预算，BASE不参与此裁剪，检索结果不自动成为POV可知事实或新的记忆权威。
85. 生产P16/P17C/P18使用 `--require-canon-context`；P16大纲必须与实际冻结来源完整内容一致。P16A/B输出由 `canon_context.py::validate_p16` 校验实体/维度、完整注册表、计划引用、单项义务及proposed/pre-registered状态；P16C仅校验注册线、cause、依赖与生效章，生命周期语义另审。结构校验不批准CanonIndex提交；无目标终态不调用模型。
86. P17C只消费canon_context_summary，不复制记忆全载荷；P18所消费CanonIndex、BASE及已绑定大纲的源ID/hash必须进入已核验的Writer闭包。宿主先审定稳定ID的P15计划快照，P16消费该快照，之后才冻结最终章节包；不得要求P16等待包含自身的最终包，也不得用占位节拍替代稳定ID。
87. ACK执行前运行 `validate_ack_fulfillment`，要求当前ACK/正文hash、完整义务引文、无阻断的审读及绑定正文和审读的SHIPPED回执。哈希与PASS不认证身份或证明语义充分；实际审读及出厂权限由受信宿主验证。该检查函数不写回旧ACK；已批准履行按下述有限事务提交，不迁移五章付费入口。
88. `tools/index_transaction.py` 按 `INDEX_TRANSACTION_POLICY.md` 提交ACK_EXECUTE、DERIVED_REGISTER及ACK_REGISTER。宿主先初始化完整dependencies、generation与transactions，旧简化索引不能自动迁移。预览绑定候选hash、请求主体及当前索引hash，受信批准者另行授权；可信issuer由宿主权限策略传入，不从模型取值。
89. 提交使用内核锁及索引hash CAS，发布不可覆盖的旧索引、新Canon与intent；一次原子替换同时提交Canon指针、传递INVALIDATED状态、generation及幂等回执。不相关节点不失效，历史源文件保持原字节。同tx_id同请求只重放，换请求或复用历史版本阻断，提交点前后崩溃可用同一请求恢复。
90. 所有索引写入及Writer最后预检/派发必须共享锁或等效租约。事务不阻止不合作写入者的最终竞争，不取消已派发模型任务，不保证Windows断电或网络文件系统持久性；后续交付须核验版本。COMMITTED不授予Writer激活，重建受影响审读、冻结与新P17C仍由宿主执行，P16C变更尚无提交接口。
91. DERIVED_REGISTER/ACK_REGISTER分别绑定P16A/P16B当前候选、输入包及generator检索manifest，重验完整来源闭包和真实大纲。登记保留proposed/pre-registered，不默认为事实发生或义务兑现；完整候选和旧历史不裁剪。登记来源保存为历史证据，不给新Canon添加指向旧消费者大纲的实时边；宿主保证历史文件不可变，并重建失效消费者。
92–95. 样章专用审计、重建、计划、授权工具不随本归档提供；如需使用，由宿主在外部证据上实现。
96. 模型格式合格输出保持DRAFT。内部编辑接受另存绑定响应及实际事件的复核记录和VALIDATED副本，不改模型原字节，不冒称独立人类审读。引用编号纠正需显式保存并传入下游；有阻断、事件不匹配、请求来源改变时禁止接受，非阻断条件不能因状态变化消失。