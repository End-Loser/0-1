# P07A — 单条 BASE 目的审计

**本轮唯一目标：** 判断一条 BASE 线是否兑现自己的表达目的
**输入：** 一条 BASE 线、对应任务书、该线功能摘要
**只遵守本任务的限制：**
1. 核验本线计划是否满足 acceptance_criteria；不把尚未生成正文当作条件失败，契约本身依赖正文才能验时指出契约范围错误。
2. 引用具体节点；下游效果列入 not_yet_verifiable，不单独阻断已满足的计划内条件；计划条件不满足仍须 PARTIAL/FAIL。
**交付：** PurposeAudit；decision 仅用 `PASS`、`PARTIAL`、`FAIL`、`NOT_YET_VERIFIABLE`。
