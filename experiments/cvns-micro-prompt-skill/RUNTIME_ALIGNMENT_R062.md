# R-06.2 提示词输出与实际 Pydantic 模型对齐报告

## 已按实际模型字段对齐
- P05C → `VoiceCard`：entity_id、entity_name、sentence_style、oral_marks、word_domain、rhythm、signature_gesture、forbidden。
- P14A → `Event`：输出字段与 `data_models.py::Event` 对齐，event_type 只允许五种枚举。
- P15A → `ChapterOutline` + `OutlineBeat`：使用原模型字段；全局 beat_id 由代码发放，LLM 不铸造。
- P16A → `VariableLine` / `CauseRef` / `Purpose`：DERIVED cause 禁 `user_input`，purpose 至少有 setup/reveal/payoff 一项。
- P16B → `AckNode`：字段和 `AckType`/`AckStatus` 枚举与源模型对齐；executed 由正文出厂后回写。
- P18 → `ChapterContent`：使用 chapter/title/text/word_count/source_outline_id/improvised_nouns；audit_records 留给审计程序。
- P06 → `ExpressionTaskBook` / `ExpressionTaskEntry` / `VariablePurposeContract` / `WithholdingIntent`：字段以用户上传源模型为准，全局矩阵单次逻辑任务。

## Skill 级规范化工件（当前源代码没有同名 Pydantic class）
- `MechanismRuleDraft`：源 `WorldConstants` 有 `rule_interfaces`，但没有独立 MechanismRule 类。机制规则应单独持久化，再由项目适配层维护与 WC 的关联。
- `BASELineDraft`：Skill 输出稀疏 BASE 变化点；源 `Variable.timeline` 仍要求逐章 dense 序列，须按 `BASE_RUNTIME_ADAPTER.md` 程序展开。
- ConflictPairScan / ConflictDecision、各专项 Audit 结果属于 Skill 工作流工件，不可误称为源 Pydantic 模型，除非实际项目另有适配模型。

## 仍未被本 Skill 修复的 Python 实现差异
1. `VariableType.TIME` 枚举仍存在；设计书 v7.1 已退役 BASE time。
2. `ActionPackGenerator` 仍可能合并 skill/item/faction；设计目标要求独立线。
3. `DesireSpec` sidecar 尚不等于 desire BASE 线。
4. BASE chain 实现仍可见固定里程碑数量上限；设计要求所有真实变化点覆盖。
5. 设计书描述 `seed_protocol.py`，用户上传目录中的冲突引擎引用该模块，但本次上传文件集中未包含该独立源文件；本 Skill 的 P09 需要使用完整无损实体对种子输入，当前 ZIP 不提供 Python 转换器。

以上差异属于源码实现/适配问题，不应通过修改提示词假装已经解决。
