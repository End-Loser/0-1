---
name: cvns-r062-microtask-variable-line-skill
description: CVNS R-06.2 微任务专项提示词 Skill，V1.7.0。
version: 1.7.0
---

> **0-1 归档说明（2026-11-25）**：本目录只包含设计文档、提示词、工具、测试与问题记录；**不包含任何测试小说正文、样章、修订候选或样本输出**。详细设计书见 [DESIGN.md](DESIGN.md)，已知问题见 [PROBLEMS.md](PROBLEMS.md)，版本变更见 [CHANGELOG.md](CHANGELOG.md)。设计书是提案，不保证正确；高质量小说生成仍需讨论与对照实验。

# CVNS R-06.2 Micro-Task Variable-Line Skill 1.7.0

本版本继承 V1.6.0，新增修炼体系与战斗规则提案、叙述声音指南和长篇压力测试 sidecar，并按 30 章压力测试优化包升级 27 份既有提示词。保留完整变量线作为全书规划上下文，不把未解决矛盾直接交给 Writer。

## V1.7.0 当前更新
- 新增 `P01E` 修炼体系提案与 `P01F` 战斗规则提案：只产出 PROPOSAL，须经 P21A 修订与宿主批准才可写入 WorldConstants。
- 新增 `P05F` 叙述声音指南：作为既有写作说明注入 Writer，不修改 Schema 或 BASE 结构。
- 新增 `P26` 长篇压力测试 sidecar：冻结基线、逐章状态、滚动审查与 P20 回归，不进入正式 Canon。
- 静态提示词预算调整为最长 3000 字符、最多 15 项限制；仍保持一任务一调用与生成/审计/批准分离。
- 详见 `SKILL.md`、`PROMPT_DESIGN_STANDARD.md`、`PROMPT_BUDGET_REPORT.md`。

## V1.6.0 当前更新
- P10 当前规则互斥先 P11A/P11B；解释不成立或明确不探索才走 P11 最小修复。
- 每个候选覆盖原始命题，说明规则边界、跨线后果、反例和叙事收益；有 FAIL/UNKNOWN 的候选不能获选。
- `tools/conflict_resolution.py` 校验来源/hash、批准绑定和复检，将完整新 BASE/规则复制到新冻结包，拒绝覆盖旧成果。
- 宿主激活索引并使旧消费者/审读失效后，新的张力或确认因果才可被消费。P12 与通用 `resolution_dependencies` preflight 均核验回执。
- 协议见 `CONFLICT_EXPLANATION_POLICY_V160.md`；合成案例与失败门禁见 `tests/test_v160_conflict_resolution.py`。结构回归不证明真实小说质量改善。

## 这次修复的核心问题
- 5条资产全部变成5个同质事件：增加 P12B 资产组合筛选、P13B 事件机会聚类。
- 事件争用同一组 BASE 变化节点：增加 P14C0 去重与唯一节点归属。
- P08 读取未筛选事件导致事件数量不一致：调整为 P14C 后运行。
- 章节计划无批准/冻结步骤：增加 `tools/finalize_chapter_plan.py`。
- P17C 缺少依赖闭包：更新输入包策略。
- P17C BLOCKED 后仍使用生产 Writer：分离 P18 生产与 P18D 诊断。
- P19D 没有正文输入却承担正文因果检查：增加 P19D2。
- 无语言样本仍调用 P05C：改为调度层跳过。

## V1.5.0 人物决策与事件合成
- P05E 不生成第 12 条 BASE 线，而是从角色设计与可定位依据提取稳定决策倾向：价值排序、风险/不确定性处理、信任策略、冲突压力反应及盲点；新角色的未验证倾向标为假设，不虚构经历。
- P13C 对一个事件机会组内的所有主要决策者一次调用，比较实际可行选项、选择理由、放弃方案和代价；性格是倾向而非行为命令。
- P09/P10 区分张力、互斥、定向因果交互和无实质关系；只对确认的因果关系建立事件驱动。
- P13B 按资产 SETUP/REVEAL/PAYOFF 阶段聚类，不再按资产数量机械生成事件。

关键设计原则和验收判据详见 [`NARRATIVE_DECISION_AND_EVENT_SYNTHESIS_V150.md`](NARRATIVE_DECISION_AND_EVENT_SYNTHESIS_V150.md)，包括变量线关系类型、P05E 稳定倾向、P13C 人物决策、机会聚类、资产阶段、Event 源模型和合并验收条件。

## 使用步骤
1. 先读 `workflows/WORKFLOW.md`。
2. 按 `TASK_REGISTRY.csv` 选择一个 task_id。
3. 新会话只加载该提示词和 `read_scope` 最小输入包。
4. 生成、审计、修订和批准分别执行。
5. 冻结成果用新版本管理，禁止覆盖。

## 运行静态验证
```bash
python tools/validate_skill.py
pytest -q
python -m unittest discover -s tests -p "test_*.py" -v
python tests/test_v150_narrative_logic.py
```

`materialize_event_units.py` 将 P14C0 canonical Event 提案物化为新 Event 文件，不修改原事件；默认严格要求每个源 Event 一条决策计划/无主体回执，并保留资产阶段、因果关系 refs。仅迁移 V1.4.1 历史事件时显式使用 `--allow-legacy-provenance`。

`finalize_chapter_plan.py` 的输入 manifest 见脚本头部示例。它不会修改源成果，冻结目录已存在时拒绝覆盖。

## 重要边界
本 Skill 是提示词与宿主流程规范，不是原 R-06.2 Python 工程的源码替代。真实文件权限、模型会话隔离和依赖闭包需要宿主实现。

## V1.4.1：继承的数据流修复
- 增加调用前置 preflight，已注册实体、空语言样本、无 VoiceCard 基线等直接跳过。
- 明确输出使用分类，区分下游引用、终端审计、提案、被替代、诊断和无消费者。
- P17C 不再接收完整 Writer Pack；P18 只接收一次，消除同一 Writer Pack 的三份重复物化。
- 增加输入包去重与预算门禁、数据消费追踪报告。
- 本次历史归档中，73 份模型输出文件有 60 份具备下游 ID 引用（82.2%）；按修正后的实际使用口径，5 次调用可明确避免（3 次重复 P04B、1 次空输入 P05C、1 次无批准 VoiceCard 的 P19F）。实际子代理为 74 个会话，另有 1 次 P07C 重试未进入 73 行 call_log。费用账单：子代理 ¥23.27 + 主会话 ¥7.34 = API 合计 ¥30.61；该次只有一份约 4,628 汉字诊断草稿，正式批准章节 0，不是完整小说生成成本。

## 本次实测数据分析
- [TR-20261104-MULTILINE 数据消费与输入包效率报告](case_studies/TR-20261104_MULTILINE_DATAFLOW_USAGE_REPORT.md)
- [产物消费追踪 CSV](case_studies/TR-20261104_MULTILINE_artifact_usage_trace.csv)
- [输入包大小与重复子树报告](case_studies/TR-20261104_MULTILINE_input_pack_efficiency.json)
- [调用前置检查工具](tools/preflight_task_call.py)
- [测试回归案例](regression/TR-20261104_MULTILINE_REGRESSION.md)

- [历史输入包 preflight 重放结果](case_studies/TR-20261104_MULTILINE_preflight_replay.json)


## V1.5.0：人物决策与因果事件建模升级
- 新增 P05E：稳定决策倾向卡（sidecar），不是第 12 条 BASE 线；临时认知/欲望/情绪仍由既有维度承载。
- P09/P10 从单纯冲突扫描扩展为张力、互斥和有方向证据的因果交互分类。共鸣仍按 R-06.2 P6 默认关闭。
- P13B 改为资产阶段级多对多部署：同一资产可在不同事件中分别 setup/reveal/payoff；源目标章相同且阶段功能确实独立时，也可由同一事件承载多个阶段。
- 新增 P13C：一个事件机会一次调用，集中推演所有主要决策者的独立选择逻辑，避免按角色逐人调用造成成本膨胀。
- P14A 只把决策计划转成原始 R-06.2 Event 字段；P19D 专项审查选择是否符合人物状态与对手理性。
- 新增确定性结构验证器 `tools/validate_narrative_logic.py`，验证资产阶段/确认因果关系全覆盖、目标章不漂移、决策者覆盖、可行选项、profile 冻结状态和反事实检查字段。P12B 另有 `ASSET_PORTFOLIO_DECISION_SCHEMA.json` 固化源资产阶段目标章。
- 修正 Event 兼容：不向 R-06.2 `Event` 增加 `state_deltas` 等字段；P13B/P13C 的详细计划和来源引用由 sidecar/物化 manifest 保存。
- 新增 `tools/materialize_no_agent_decision.py`：无决策主体的事件由宿主生成终态回执，不发起空的 P13C 模型调用。
- P14A/P17A/P19D preflight 核验上游正式冻结状态；P14C0 物化器默认严格要求每个源 Event 对应决策计划/无主体回执并保留所有资产阶段、因果关系 refs。

## 版本兼容
V1.5.0 的 P09/P10/P13B/P14A 输入输出合同有版本变化。V1.4.1 的旧产物保留为历史版本，不得直接伪装成 V1.5.0 的 VALIDATED/FROZEN 产物。旧 P13B 的 asset-level 单次分配只可作为迁移参考；需要跨事件分阶段兑现的资产必须重新执行 P13B。


### 成本控制注意
- P05E 按重要主体生成一次并复用冻结版本；不是每章/每事件重做。
- P13C 是每个事件机会组一次调用，所有主要决策者合在同一输入中推演；禁止按每个人物追加调用。
- 无决策主体时宿主本地物化 `NO_AGENT_DECISION_REQUIRED`，不调用模型。
- P10 仅对 P09 输出的 material candidates 运行；共现/主题相似/弱关系不进入 P10。新增决策质量保障可能增加一定调用费用，必须以新一轮实际 Token 账单测量，不预先宣称省钱。
