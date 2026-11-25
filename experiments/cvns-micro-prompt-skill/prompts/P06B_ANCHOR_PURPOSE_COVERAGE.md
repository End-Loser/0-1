# P06B — 单个 Anchor 承载覆盖判断

**本轮唯一目标：** 判断一个 Anchor 是否已被必要的已签约变量线承载
**输入：** 一个 Anchor、ExpressionTaskBook 中与该 Anchor 对应的条目
**只遵守本任务的限制：**
1. 只检查该 Anchor 的必要功能是否已有变量线承载，不要求每个维度都签约。
2. 无需额外变量线时选 `NO_LINE_REQUIRED`；覆盖不足选 `UNCOVERED`，证据不足选 `REVIEW`。
**交付：** `COVERED`、`NO_LINE_REQUIRED`、`UNCOVERED` 或 `REVIEW`；结果由宿主门禁映射到相关 BASE 任务。
