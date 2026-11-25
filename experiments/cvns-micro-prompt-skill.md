# CVNS R-06.2 小说写作 Skill v1.7.0：设计、代码与问题记录

- 状态：`status:experiment`
- 类型：设计书 / 提示词库 / 工具与测试 / 问题记录
- 作者：End-Loser
- 许可：代码 MIT；设计文档、提示词、案例与整理数据 CC BY 4.0（见 [LICENSE-NOTE.md](cvns-micro-prompt-skill/LICENSE-NOTE.md)）
- 对应想法：[微任务变量线长篇小说写作（CVNS R-06.2）](../ideas/micro-task-variable-line-novel-writing.md)
- 项目归档：[experiments/cvns-micro-prompt-skill/](cvns-micro-prompt-skill/README.md)
- 设计书：[DESIGN.md](cvns-micro-prompt-skill/DESIGN.md) · [PROBLEMS.md](cvns-micro-prompt-skill/PROBLEMS.md) · [CHANGELOG.md](cvns-micro-prompt-skill/CHANGELOG.md)
- 最后更新：2026-11-25

## 目标

记录一个“微任务变量线”小说写作流程的**设计、已实现代码、代码变更和已知问题**，方便别人批评、复现或接手。

本条目**不验证文学质量**，也不声称设计书一定正确。高质量小说生成需要讨论、对照实验和人工审读。

## 归档范围

**包含**：

- 设计书：`DESIGN.md`（目标、架构、数据模型、流程、取舍、验证计划与不保证事项）
- 提示词库：83 个 `TASK_REGISTRY.csv` 任务行、84 个提示词文件（含 BASE 维度）
- 工具：27 个 Python 工具（preflight、校验、物化、索引事务、冲突解释、时间门禁、Writer 依赖等；含 real_novel_test / writer_length_contract 两个测试/契约工具；另有 18 个样章专用工具因依赖未上传测试证据而移除）
- 测试：24 个核心测试（契约、schema、生命周期、数据流、冲突解释、时间门禁等；另有 19 个样章专用测试已移除）
- schemas / output contracts / workflows / regression
- 数据流分析报告与问题记录（不含测试小说正文）
- `changelogs/`：V1.3.0–V1.5.0 历史变更原始记录
- `PROBLEMS.md`：已知问题、限制和下一步

**不包含**：

- 任何测试小说正文、样章、修订候选或样本输出
- 样章专用的 18 个工具与 19 个测试（依赖未上传的测试证据；real_novel_test / writer_length_contract 作为测试/契约代码保留）
- 原 R-06.2 Python 工程的完整运行时代码
- 宿主调度器（会话隔离、权限、审批、冻结、索引激活）
- 真实 API Key 或模型服务
- 对文学质量、商业效果或“整本书自动生成”的保证

## 设计要点

- **一次任务一次调用**：独立会话、最小输入包、独立输出；生成、审计、修订、批准、冻结分离。
- **变量线作为 canon**：一个 BASE 线是 entity × dimension 的完整时间轨迹，不按章节/字段切碎。
- **冲突先解释**：P09/P10 分类，P11A/P11B 探索解释；未批准的互斥不资产化。
- **事件与资产**：P12B 资产组合、P13B 阶段化事件机会、P13C 决策计划、P14C0 去重与唯一节点归属。
- **Writer 与审计分离**：P17C 未 READY 时 P18 阻断；诊断只能走 P18D。
- **确定性工具只做结构校验**：通过不代表文学质量通过。

详细设计与取舍见 [DESIGN.md](cvns-micro-prompt-skill/DESIGN.md)。

## 环境与复现

- Python 3.11；测试依赖见 [requirements-dev.txt](cvns-micro-prompt-skill/requirements-dev.txt)：`pytest`、`jsonschema`、`referencing`。
- 项目 README 给出的静态验证命令：

```bash
python tools/validate_skill.py
pytest -q
python -m unittest discover -s tests -p "test_*.py" -v
python tests/test_v150_narrative_logic.py
```

- **归档时未重新执行上述测试**；脚本、测试与证据来自 v1.7.0 clean 包，结果需在本地重跑。
- `tools/real_novel_test.py` 需要宿主提供 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_PRIMARY_MODEL`，并指向已有完整前置证据；本包不含密钥、测试小说或宿主调度器。
- 使用顺序：先读 [workflows/WORKFLOW.md](cvns-micro-prompt-skill/workflows/WORKFLOW.md)，按 [TASK_REGISTRY.csv](cvns-micro-prompt-skill/TASK_REGISTRY.csv) 选择 task_id，只加载该任务的最小输入包。

## 历史测试记录（不含小说正文）

项目 README 与 case_studies 记录了一次 **V1.4.1 历史归档**的小规模数据流实测：

- 73 份模型输出，60 份（82.2%）有下游 ID 引用；
- 5 份（6.8%）可避免调用：3 次重复 P04B、1 次空 P05C、1 次无批准 VoiceCard 的 P19F；
- 正式批准章节 **0**，只有一份约 4,628 汉字的 P18 诊断草稿；
- 成本记录：子代理 ¥23.27 + 主会话 ¥7.34 = API 合计 ¥30.61；
- 这不是完整小说生成成本，也不是 v1.7.0 的文学质量证明；
- **本归档不包含该测试的小说正文、样章、修订候选或样本输出**，因此不能只靠本包复现完整测试输入。

详见 [PROBLEMS.md](cvns-micro-prompt-skill/PROBLEMS.md) 与 [数据流分析报告](cvns-micro-prompt-skill/case_studies/TR-20261104_MULTILINE_DATAFLOW_USAGE_REPORT.md)。

## 已知问题

最关键的几条（完整列表见 [PROBLEMS.md](cvns-micro-prompt-skill/PROBLEMS.md)）：

- 宿主调度器未随包提供；没有宿主时无法完成冻结、权限、会话隔离和索引激活。
- 文学质量未被证明；静态测试只验证结构、预算、契约和回归。
- 历史实测样本很小，正式批准章节为 0；没有与简单长提示的对照基线。
- 输入包仍可能很大；150/200/300 KiB 阈值是工程建议，不是质量保证。
- 提示词预算（15 项 / 3000 字符）可能压缩复杂语义。
- 模型、tokenizer、费用和输出质量随 provider/model 变化。
- 设计书本身是提案，不保证正确；高质量小说生成仍需讨论与实验。

## 风险与边界

- 不用于违法、侵权、冒充或误导内容；案例与样本的版权/署名由使用者负责。
- 不要把 `status:experiment` 解读为已验证的写作能力或“自动写完整本小说”。
- 本包不含账号、密码或真实 API Key；测试中的 `test-only` / `not-a-real-key` 是占位符。
- 项目内容提到模型/费用记录；这些是项目运行记录，不是对任何模型身份的官方结论。

## 作者与许可

- 作者：End-Loser
- 代码 / 工具 / 测试：MIT；设计文档 / 提示词 / 案例 / 整理数据：CC BY 4.0；第三方依赖保留各自许可证。
- 引用时请注明 `status:experiment`、版本 v1.7.0，以及“设计书/流程规范，非文学质量证明”。

## 提交确认

- [x] 我确认未提交密钥、令牌、隐私数据或未经授权的内容。
- [x] 我确认实验不以攻击、诈骗或破坏为目的。
- [x] 我已说明第三方代码、依赖和许可证。
- [x] 我已区分事实、推断、猜测与未验证内容。
