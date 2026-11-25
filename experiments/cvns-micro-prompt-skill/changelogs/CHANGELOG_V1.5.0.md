# V1.5.0 — 人物决策与因果事件语义升级

## 变更原因
V1.4.1 已缓解一资产一事件、重复事件与事件节点争用，但真实测试暴露更根本的语义缺口：冲突扫描偏重矛盾分类；事件前没有独立的人物决策计划；P13B 将资产整体只分配一次，无法跨事件规划 setup/reveal/payoff。

## 新增
- `NARRATIVE_DECISION_AND_EVENT_SYNTHESIS_V150.md`：固化变量线关系、人物决策、事件机会聚类、事件合成和归并验收判据。
- `ASSET_PORTFOLIO_DECISION_SCHEMA.json`：为 P12B 的分阶段资产组合结果补充机器可检验 Schema，三个阶段分别绑定源 StoryAsset 的 introduce/reveal/payoff 目标章。
- P05E：稳定决策倾向卡。只给重要决策主体生成；不是新 BASE 维度，且必须区分稳定倾向与动态状态。
- P13C：一个事件机会组一次调用，集中推演该事件的全部主要决策者。
- `CHARACTER_DECISION_PROFILE_SCHEMA.json`、`EVENT_DECISION_PLAN_SCHEMA.json` 与相应输出合同。
- `tools/validate_narrative_logic.py`：对决策 profile、资产阶段归属、确认因果交互全覆盖、决策者覆盖、可行选项与反事实检查作确定性结构校验；另拦截已选资产无 required phase 或阶段目标章缺失（包括延期阶段）。
- `tools/materialize_no_agent_decision.py`：没有决策主体的事件以宿主终态回执连接 P13C→P14A，不产生空模型调用。
- P12B 的资产阶段目标章新增专用 JSON Schema；严格要求原 StoryAsset 三个阶段均为有效章节整数；P13B 校验每个资产阶段严格沿用源目标章。
- Event 源模型 JSON Schema 关闭额外顶层字段，防止 `state_deltas` 等 Skill 草图字段混入生产 Event。

## 修改
- P09/P10 现在可以将有证据的定向因果交互与张力/互斥分开；因果交互必须由 P10 确认后才能被 P13B 使用。
- P13B 从 asset-level one-to-one 改为 asset-phase mapping；同一资产可跨事件分别兑现 setup/reveal/payoff。
- P14A 必须消费 P13C 决策计划，但仍只输出原 R-06.2 Event 模型。
- P14B 检查选择行动的可行性；P19D 检查人物选择与因果合理性。
- P14C0 合并门槛增加“不能抹掉独立决策路径/代价/结果”。

## 不变
- R-06.2 BASE 仍为 11 维，单条 entity×dimension 完整轨迹一次调用。
- P06 仍为全局表达任务书；P09 仍按实体对全线扫描；全覆盖检测不意味着所有线都生成冲突或事件。
- 互斥错误不得资产化；P6 共鸣资产默认关闭。
- 冻结产物不可原位覆盖；正文不得反向修改计划层。

## 兼容性
P09/P10/P13B 输出合同版本改变。V1.4.1 的旧 P13B 只能作为历史记录；若要进行分阶段兑现，需重跑 P13B。不得把旧的 asset-level assignment 自动迁移为多个阶段已被规划。Event 本体字段没有扩展。

## 补强的质量护栏
- P12B/P13B 阶段目标章逐级原样传递；事件机会不得跨章聚类，P13B 校验部署章与源 StoryAsset 的阶段章完全相等。
- P14A、P17A、P19D preflight 校验上游产物 VALIDATED/FROZEN 状态，阻止仅因 decision 字段写着 READY 就越过生命周期门槛。
- P14C0 事件物化默认启用严格叙事溯源：每个源 Event 一条 P13C/无主体回执引用；来源资产阶段/关系引用写入 manifest；事件章号不匹配直接阻断。旧版历史产物迁移必须显式使用 `--allow-legacy-provenance`。
- P13B 对每个资产阶段与每条已确认因果关系建立显式驱动引用；每条确认因果关系只能被一个事件簇承载，或写入有原因/重检条件的未分配清单。
- P13C 核对每个主要决策主体；必需的 P05E profile 必须是 VALIDATED/FROZEN；所选行动不可为 INFEASIBLE；仅一个可行选项时需说明客观约束；必须提供反事实敏感性检查。
- P14A/P17A/P19D preflight 阻止未冻结的上游产物越级；P14A 还要求机会簇目标章有效，P17A 检查 Event.chapter 不得偏离目标章。

## 结构回归结果（本交付包）
- pytest：74/74 PASS；unittest：43/43 PASS；人物决策专项直接回归脚本：27/27 PASS。
- `validate_skill.py`：77 个任务、78 个提示词文件、11 条 BASE 维度、最多 3 条任务局部语义限制，PASS。
- Python compileall：PASS；91 个 JSON 文件（其中 88 个 Schema JSON）解析错误为 0。
- 本轮没有执行真实 LLM 小说生成；以上均为结构、契约、生命周期和工具测试，不代表文学质量已验证。

## 验证边界
静态 validator、Schema 和回归测试证明文件、字段、阶段日程、来源保留及生命周期前置规则可检查；不证明 LLM 一定生成高质量人物或事件。尚需真实模型进行同主题 A/B 小说片段验证。
