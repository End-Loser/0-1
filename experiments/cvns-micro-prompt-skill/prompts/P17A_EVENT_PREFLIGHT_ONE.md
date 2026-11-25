# P17A — 单事件正文前预检

**本轮唯一目标：** 判断一个事件及其人物选择是否可以进入章节事件合成
**输入：** 一个通过 P14B 的 Event、P13C 决策计划或 NO_AGENT_DECISION_REQUIRED、参与实体状态与规则证据
**只遵守本任务的限制：**
1. 只输出 `READY`、`CONDITIONAL_READY`、`BLOCKED`、`NOT_RUN`、`NOT_APPLICABLE`；条件必须可传递且可验证。
2. 主要决策者缺计划、重要人物选择无状态/知识依据、关键前件缺失或状态互斥时 BLOCKED；不补造条件。
**交付：** EventPreflight；只有 READY/CONDITIONAL_READY 可进入 P14C0。
