# BASE-ITEM — 专项提示词

**本轮唯一目标：** 追踪本实体完整有效区间内的随身普通物品持有变化
**本轮输入：** 当前 entity×dimension 的表达任务书、当前实体注册资料、当前实体 character_functions、WorldConstants、仅本维度的状态格式注册表行；必要时读取 opening_date/推演日历表。
**state 格式：** `物品名: 品质 (condition)`（具体取值与特殊语义以本次唯一注入的 BASE_DIMENSION_REGISTRY 对应行和已冻结项目资料为准）。
**只遵守本任务的限制：**
1. 每个变化节点输出完整持有清单；跨章承载设定/剧情的关键物品改由 DERIVED 线承载，此线只保留指针。
**交付：** 只输出一条完整的 `item` BASELineDraft；稀疏节点格式见 BASE_LINE_SCHEMA.json。
