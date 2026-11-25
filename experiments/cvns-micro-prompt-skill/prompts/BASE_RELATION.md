# BASE-RELATION — 专项提示词

**本轮唯一目标：** 追踪本实体完整有效区间内的重要关系变化
**本轮输入：** 当前 entity×dimension 的表达任务书、当前实体注册资料、当前实体 character_functions、WorldConstants、仅本维度的状态格式注册表行；必要时读取 opening_date/推演日历表。
**state 格式：** `{target}→{status}({subtext}) | ...`（具体取值与特殊语义以本次唯一注入的 BASE_DIMENSION_REGISTRY 对应行和已冻结项目资料为准）。
**只遵守本任务的限制：**
1. 只记录本实体视角有依据的重要关系；不得代写对方未被观察到的内心或仅因同场推断关系。
**交付：** 只输出一条完整的 `relation` BASELineDraft；稀疏节点格式见 BASE_LINE_SCHEMA.json。
