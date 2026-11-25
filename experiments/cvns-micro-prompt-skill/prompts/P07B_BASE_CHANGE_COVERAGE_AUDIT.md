# P07B — 单条 BASE 变化覆盖审计

**本轮唯一目标：** 检查一条 BASE 线是否漏掉有来源的真实变化点
**输入：** 一条 BASE 线、该实体本维度允许的来源资料
**只遵守本任务的限制：**
1. 先核对首节点与已给开场事实，再寻找有具体来源却未承载的本维度变化；开场改写必须报告 REVIEW，不用故事需要解释。
2. 未来 proposed 是计划而非已发生历史；无证据的候选标 UNKNOWN，不算 confirmed missing，也不单独阻断已核验覆盖。来源本身缺失才 NOT_YET_VERIFIABLE。
**交付：** ChangeCoverageAudit；decision 仅用 `PASS`、`REVIEW`、`CONFIRMED_MISSING`、`NOT_YET_VERIFIABLE`。
