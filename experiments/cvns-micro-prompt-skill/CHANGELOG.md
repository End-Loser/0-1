# 变更记录（提示词、代码与测试）

> 本记录只覆盖设计、提示词、工具、测试与流程变更；**不包含测试小说正文、样章、修订候选或样本输出**。V1.3.0–V1.5.0 的原始版本记录见 `changelogs/`；V1.6.0–V1.7.0 的细节见 `README.md` 与 `SKILL.md`。

## 2026-11-25 — 新增原始思路记录

- 归档作者提供的 `最初思想：.txt` 到 `docs/origin/最初思想.txt`，并新增 `docs/origin/ORIGIN.md` 说明与原文。
- 该记录只描述最初设想，不是设计结论或质量证明。

## V1.7.0 — 长篇压力测试与成长/战斗提案

- 新增 P01E 修炼体系提案、P01F 战斗规则提案：只输出 PROPOSAL，须经 P21A 修订与宿主批准才可写入 WorldConstants；Schema 不足时标 `BLOCKED_SCHEMA_GAP`。
- 新增 P05F 叙述声音指南：作为既有写作说明注入 Writer，不修改 Schema 或 BASE 结构。
- 新增 P26 长篇压力测试 sidecar：冻结基线、逐章状态、滚动审查、主角缺席/删除测试与 P20 回归；不进入正式 Canon。
- 27 份既有提示词按 30 章压力测试包升级；静态预算上限调整为最长 3000 字符、最多 15 项限制。
- `tools/validate_skill.py` 的门槛改为可配置常量，并新增输出契约与注册表行检查。

## V1.6.0 — 冲突解释闭环

- 互斥先由 P11A/P11B 探索解释；无有效解释或明确不探索才走 P11 最小修复。
- 新增/强化 `tools/conflict_resolution.py`：解释结构校验、批准 hash 绑定、新版本物化；不自动证明解释合理。
- 宿主需要使旧消费者/旧审读失效；P12 与通用 `resolution_dependencies` preflight 核验回执。
- 解释未批准、回写、复检与重新分类前，不得资产化。

## V1.5.0 — 人物决策与因果事件语义升级

- 新增 P05E 稳定决策倾向卡与 P13C 事件机会组决策计划；两者都有独立 schema 与生命周期规则。
- P13B 改为资产阶段（SETUP/REVEAL/PAYOFF）级唯一分配；同一资产可以跨事件部署。
- 新增 `NARRATIVE_DECISION_AND_EVENT_SYNTHESIS_V150.md`、`ASSET_PORTFOLIO_DECISION_SCHEMA.json`、`CHARACTER_DECISION_PROFILE_SCHEMA.json`、`EVENT_DECISION_PLAN_SCHEMA.json`。
- 新增 `tools/validate_narrative_logic.py`、`tools/materialize_event_units.py`、`tools/materialize_no_agent_decision.py`。
- 修正 Event 兼容：不向 R-06.2 Event 增加 `state_deltas` 等字段；详细计划与来源引用由 sidecar/manifest 保存。

## V1.4.1 — 数据流问责与上下文去重

- 新增 `tools/preflight_task_call.py`：已注册实体重复审查、空语言样本、无批准 VoiceCard、无机制调用、未放行 Writer 等在调用前 SKIP/BLOCKED；未知条件返回 NEEDS_HOST_CHECK，不自动批准。
- `TASK_LIFECYCLE_REGISTRY.csv` 增加 `preflight_rule`，逐任务记录调用条件、预期消费者和无消费者处置。
- P17C 只读依赖闭包与审批回执；P18 完整 Writer Pack 只出现一次；事件/审计任务只接收必要切片。
- 增加输入包预算与重复子树审计；字节数只作 token 代理，避免虚报节省量。
- 固化历史数据消费分类与输入包重复分析，作为后续回归案例；测试小说与样本输出不随本归档提供。

## V1.4.0 — 多变量线测试驱动的修复

- 新增 P12B 同范围资产组合筛选、P13B 事件机会聚类、P14A 一机会组一事件、P14C0 事件去重与 BASE 状态节点唯一所有权。
- P14C 只排序有效事件；循环/冲突时阻断。
- P08 调整为 P14C 后运行，避免候选事件数与放行事件数不一致。
- P17A 使用固定决策枚举；仅 READY/CONDITIONAL_READY 进入后续。
- 新增确定性章节计划批准/稳定 ID/冻结工具，禁止覆盖；P17C 强制完整依赖闭包与批准清单。

## V1.3.0 — 微任务化

- 一次调用只承担一个自然认知任务，通常 1–2 项、最多 3 项语义限制；生成、审计、修复、批准严格分开。
- 清除模型可见提示词中的 `predecessor`、`validators`、调度命令和下游验收指令；这些只保留在 TASK_REGISTRY 和宿主说明。
- 移除提示词 YAML/调度元数据重复；任务正文统一为唯一目标、最小输入、限制和单一输出。
- 将 POV 归属、人物知识、物品流转、身体/存在、场馆位置拆为独立审计提示词。
- BASE 维度注册表去掉与提示词重复的语义描述；一次只注入当前维度紧凑状态格式行。
- 修正 P18 Writer 注册表的限制计数。

## 详细原始记录

- [V1.3.0](changelogs/CHANGELOG_V1.3.0.md)
- [V1.4.0](changelogs/CHANGELOG_V1.4.0.md)
- [V1.4.1](changelogs/CHANGELOG_V1.4.1.md)
- [V1.5.0](changelogs/CHANGELOG_V1.5.0.md)
- V1.6.0 / V1.7.0：见 `README.md` 与 `SKILL.md` 的版本章节。
