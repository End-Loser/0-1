# R-06.2 v7.6 架构对齐与本版调整

## 保留的硬架构
1. 数据流：WorldBuilder → Theme → SubTheme → Anchor → EntityDiscovery → NarrativeArchitect → ExpressionTaskbook → BASE → Conflicts → StoryAssets → NarrativeLens → Events → Outlines → Global Time Axis → DERIVED → Dry Run → Writer → Audit。
2. BASE = entity×dimension；11维，不含 time；每条线一次独立调用，覆盖完整有效区间。
3. BASE 输入白名单物理隔离；只加载当前线与其专属表达任务书；完整覆盖全部真实变化点。
4. Anchor 只描述抽象状态目标，不是预先写死的具体事件。
5. 表达任务书是 NarrativeArchitect 后的**全局单次逻辑任务**：ThemeSpec × character_functions → 每实体×维度的表达任务书矩阵。按实体批次处理只属于运行时资源调度，不得改成逐维度独立规划。
6. 冲突按实体对扫描；当前互斥可探索解释，再修订为一致计划；未解决矛盾不得直接成为资产。
7. Writer 是计划层纯渲染器；读取同一冻结计划可支持章节并行，不要求继承相邻 Writer 正文。源代码的正式串行执行不等于本架构必须串行。

## 本版提示词粒度
每次调用只有一个主要认知目标，语义限制为 1–3 项。一个全局矩阵任务可以输出多行，因为行记录共同完成同一项全局分配任务；不能把“行数多”误判为“认知任务多”。BASE 则严格按 entity×dimension 单线单调用。

## 表达任务书的源模型对齐
本包 `EXPRESSION_TASKBOOK_SCHEMA.json` 对齐上传源码 `cvns/models/data_models.py` 中的 `ExpressionTaskEntry`、`VariablePurposeContract`、`WithholdingIntent` 字段：`payload`、`constituent_elements`、`release_mode`、`release_chapter`、`release_event_anchor`、`prerequisite_entry_ids` 等。全局输出根结构为 `{"entries": [...]}`，与 `ExpressionDecomposer` 的解析入口一致。引擎运行时 meta 由代码计算，不要求模型编造。

## 与真实 Python 运行时代码的已知差异
上传的 R-06.2 ZIP 中观察到：
- `main.py` 仍有 TimeGenerator 路径；设计目标已退役 per-entity TIME。
- `ActionPackGenerator` 仍可能合并 skill/item/faction；设计目标要求三条线独立。
- DesireSpec sidecar 尚不等同完整 desire BASE 线。
- BASE chain 生成仍可见固定里程碑数量上限，与设计书的全变化点覆盖不一致。
- `ExpressionDecomposer` 对大量实体有按实体批次的运行时预算；本包保留全局任务语义，不把每个维度改成独立的全局目的规划。

本 Skill 仅生成提示词和执行手册，不修改这些 Python 路径，也不声称自动兼容当前运行时。


## V1.5.0 增量架构对齐
- `P05E CharacterDecisionProfile` 是实体级稳定决策倾向 sidecar，不属于 R-06.2 BASE/DERIVED 变量线，不改变 11 维。临时状态仍在 BASE 线。
- `P09/P10` 扩展关系扫描/判定类型。只有有向因果候选经过 P10 确认，才进入事件机会规划；确认因果交互不冒充 `Conflict` 模型。
- `P13B` 的资产事件映射单位改为 `asset_id × phase`；资产可以跨不同事件逐步兑现。V1.4.1 的旧 asset-level partition 不可自动声称满足此合同。
- `P13C EventDecisionPlan` 是事件级决策 sidecar，每个事件机会一次调用；P14A 仍输出原 R-06.2 `Event` 模型，不增加自定义 Event 字段。
- V1.5.0 仍是 Skill 工作流，不修改 CVNS Python 生产源码；真实运行时适配、冻结和宿主权限依旧需要宿主。


### V1.5.0 源模型与阶段时间对齐补充
- `StoryAsset` 的阶段章沿用原模型字段：`introduce_chapter`/`reveal_chapter`/`payoff_chapter`；P12B 必须原值复制，P13B 不能重排或改写。
- `Event` 的实际字段没有 `state_deltas`、`decision_plan_refs` 或 `asset_deployments`。P14A 通过现有 `variable_changes`、`state_at_start/end`、`knowledge_changes`、`goal_changes`、`relation_changes`、`item_transfers`、`rule_changes`、`cognitive_impact` 等表达；来源与完整决策推演留在 Envelope/sidecar。
- `setup_payoff_links` 按原提示词只表达 setup/payoff；REVEAL 的人物知识变化进入 `knowledge_changes`，读者信息兑现落在 P15A beat payload，再由读者认知线按原机制推导。
- P14C0 canonical Event 仍必须符合 Event 模型；V1.5.0 的 decision plan、asset phase、relationship 引用存入物化 manifest。

## V1.5.0 Event 字段映射补充
- P14A/P14C0/ChapterOutline.event_records 的生产对象以 `schemas/EVENT_MODEL_SCHEMA.json` 为准。`schemas/EVENT_SCHEMA.json` 是旧 Skill 级草图，不是源 Pydantic Event。
- P13B `expected_state_changes` 是机会规划侧工件，不是 Event 字段。P14A 将 BASE/DERIVED/world-scope line 变化转为 Event.variable_changes，并以 Event.causal_links 保存 `driver_refs`、target_ref 与结果来源。
- READER_COGNITION 不属于人物 knowledge_changes；其揭示义务经 Event.causal_links 传至 P15A，再映射到受解禁条件保护的 OutlineBeat payload。
- Event 的顶层结构不新增 `state_deltas`、`expected_state_changes`、`driver_refs`、`decision_plan_refs` 或 `asset_deployments`。


### V1.5.0 语义总则
详见 `NARRATIVE_DECISION_AND_EVENT_SYNTHESIS_V150.md`：全覆盖扫描不等于全量合成；张力、互斥、因果、待证据和无实质关系分流；人物稳定倾向不替代动态 BASE；P13C 只生成各方决策意图，P14A 才根据行动互动推导 Event 结果；资产阶段按源章部署；事件合并不得抹掉独立选择路径和状态后果。

## V1.6.0 解释生成一致计划
- 保留完整稀疏变量线、11维、单线单调用和唯一事实状态机。
- 新 P11A/P11B 将跨线冲突变成有边界的解释候选；获选仅提出修订，经批准的新规则/线、复检、新 P09/P10 后才进入资产或事件。
- 解释、批准和激活记录都是控制 sidecar，不是第二套事实；Writer 不读取失败候选或自行补承重机制。
- R-06.4 原包复核与历史源码差异见 `R06.4原项目与当前Skill对照分析.md`；本轮没有修改源 ZIP 或生产 Python 工程。
