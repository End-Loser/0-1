# BASE-CHARM — 专项提示词

**本轮唯一目标：** 追踪本实体完整有效区间内的读者吸引来源与阶段变化
**本轮输入：** 当前 entity×dimension 的表达任务书、当前实体注册资料、当前实体 character_functions、WorldConstants、仅本维度的状态格式注册表行；必要时读取 opening_date/推演日历表。
**state 格式：** `{source} | {phase} | {condition}`（具体取值与特殊语义以本次唯一注入的 BASE_DIMENSION_REGISTRY 对应行和已冻结项目资料为准）。
**只遵守本任务的限制：**
1. 只记录有角色行为或读者认知依据的变化；不得把魅力线写成无依据的好感分数或单调上升线。
**交付：** 只输出一条完整的 `charm` BASELineDraft；稀疏节点格式见 BASE_LINE_SCHEMA.json。
