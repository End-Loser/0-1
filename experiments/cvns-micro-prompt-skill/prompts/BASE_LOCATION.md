# BASE-LOCATION — 专项提示词

**本轮唯一目标：** 追踪本实体完整有效区间内的主要到场场馆变化
**本轮输入：** 当前 entity×dimension 的表达任务书、当前实体注册资料、当前实体 character_functions、WorldConstants、仅本维度的状态格式注册表行；必要时读取 opening_date/推演日历表。
**state 格式：** `{place} | {sub_location} | {condition}`（具体取值与特殊语义以本次唯一注入的 BASE_DIMENSION_REGISTRY 对应行和已冻结项目资料为准）。
**只遵守本任务的限制：**
1. 跨场馆行程登记对应节点；仅在已有批准节拍时绑定 beat_id，规划阶段不造占位 ID；同一场馆内部移动不生成独立 location 节点。
**交付：** 只输出一条完整的 `location` BASELineDraft；稀疏节点格式见 BASE_LINE_SCHEMA.json。
