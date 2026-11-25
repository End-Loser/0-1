# P06 — 专项提示词

**本轮唯一目标：** 根据 ThemeSpec 与 NarrativeArchitect 的 character_functions，为全部候选 entity×dimension 生成一份表达任务书矩阵
**输入：** genre、total_chapters、完整 ThemeSpec、EntityRegistry、全部 character_functions、R-06.2 当前候选 entity×dimension 列表、允许的趋势词
**只遵守本任务的限制：**
1. 只为输入候选矩阵中的每个 entity×dimension 决定签约或 skip；每格恰好一条，不增项、不漏项、不重复。
2. 仅复述注册资料的稳定背景就 skip，不以“没有本线会失去此背景”证明独立贡献。acceptance_criteria 只验状态计划，不要求文本呈现；正文/读者效果放 expected_effects。
3. 只设计本线的表达形状：主题角色/趋势曲线/结构功能/留白；不得预写具体事件、节拍坐标或点名对侧实体/维度。
**交付：** 只输出 ExpressionTaskBook（entries 矩阵；每个候选 entity×dimension 一条），按随附字段合同组织。
