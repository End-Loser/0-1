# BASE-GOAL — 专项提示词

**本轮唯一目标：** 追踪本实体完整有效区间内的表层目标与深层驱动力
**本轮输入：** 当前 entity×dimension 的表达任务书、当前实体注册资料、当前实体 character_functions、WorldConstants、仅本维度的状态格式注册表行；必要时读取 opening_date/推演日历表。
**state 格式：** `{surface} | {deep}`（具体取值与特殊语义以本次唯一注入的 BASE_DIMENSION_REGISTRY 对应行和已冻结项目资料为准）。
**只遵守本任务的限制：**
1. deep 必须由 DESIRE 或既有背景/行动支持；资料不足时标明待确认，不制造隐秘创伤或秘密。
**交付：** 只输出一条完整的 `goal` BASELineDraft；稀疏节点格式见 BASE_LINE_SCHEMA.json。
