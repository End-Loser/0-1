# BASE-FACTION — 专项提示词

**本轮唯一目标：** 追踪本实体完整有效区间内的组织归属、等级与处境变化
**本轮输入：** 当前 entity×dimension 的表达任务书、当前实体注册资料、当前实体 character_functions、WorldConstants、仅本维度的状态格式注册表行；必要时读取 opening_date/推演日历表。
**state 格式：** `{affiliation} | {rank} | {standing}`（具体取值与特殊语义以本次唯一注入的 BASE_DIMENSION_REGISTRY 对应行和已冻结项目资料为准）。
**只遵守本任务的限制：**
1. 只引用已注册组织；无所属时保持无/独立，不为填线创建新组织或晋升/背叛事件。
**交付：** 只输出一条完整的 `faction` BASELineDraft；稀疏节点格式见 BASE_LINE_SCHEMA.json。
