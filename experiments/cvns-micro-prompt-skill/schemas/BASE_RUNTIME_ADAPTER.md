# BASELineDraft → R-06.2 Runtime Variable 适配说明

## 1. 为什么需要适配

本 Skill 按 R-06.2 设计书的 BASE 粒度生成稀疏 `change_nodes`：只输出真实变化点，保留完整有效区间，不设置节点数量上限。上传的 `data_models.py::Variable` 仍使用 `timeline: List[TimelineNode]`，并且其校验器要求 `timeline[i].chapter == i+1`。因此 Skill 输出不能直接作为当前 Pydantic `Variable.timeline` 使用。

## 2. 确定性适配规则

适配器由程序完成，LLM 不负责展开：
1. 读取 BASELineDraft 的 `valid_from/valid_to` 和按 `(chapter, seq)` 排序的 `change_nodes`。
2. 校验首个基线节点、章节范围、同章 `seq` 单调性、状态字符串格式和11维合法值。
3. 对每个故事章节生成一个 `TimelineNode`：命中变化节点时使用该节点；没有新变化时，将上一状态向前携带。
4. 前向携带只复制状态，不得创造新变化原因、变化幅度或新设定；节点来源标记由适配器记录。
5. 构造 `Variable` 的字段：`id/type/entity_id/entity_name/constraint_level/sub_level/importance/timeline/depends_on/description/layer/valid_from/valid_to/aliases`。
6. `VariableType.TIME` 仍存在于当前代码枚举，但 v7.6 设计已退役 per-entity TIME；新 BASELineDraft 的 dimension enum 不允许 `time`。时间交全局时间轴。

## 3. 禁止的错误适配

- 不得把稀疏节点直接塞进要求逐章连续的 `Variable.timeline`。
- 不得给缺失章节调用 LLM 补节点。
- 不得把 `change_reason` 改写成未发生的事件。
- 不得把 BASE 线 purpose/表达任务书复制成 Variable 未定义的 Pydantic 字段；关联通过独立 ExpressionTaskBook ID/外部索引维护。

## 4. 当前运行时差异

用户上传的 R-06.2 Python 版本尚未完整采用本 Skill 的稀疏 BASELineDraft 适配接口；此外 `VariableType.TIME`、ActionPack 三线合一、DesireSpec sidecar 和 BASE 固定里程碑帽仍可在源码中观察到。此 Skill 不修改 Python 程序，不声称这些差异已被自动解决。
