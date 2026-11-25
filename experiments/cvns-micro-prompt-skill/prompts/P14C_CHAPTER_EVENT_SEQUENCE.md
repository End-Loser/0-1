# P14C — 单章事件因果排序

**本轮唯一目标：** 将一个章节已去重且节点归属明确的事件集排列为因果序列
**输入：** 一个 ChapterEventOwnershipPlan、事件前置依赖、章节时间范围
**只遵守本任务的限制：**
1. 只排序已通过预检且节点唯一归属的事件单元，不改变其结果或来源变量线。
2. 因果图存在环、未决阻断依赖或节点重复归属时输出 `BLOCKED`，不得给出伪总序。
**交付：** 一个 ChapterEventSequence；decision 仅用 `SEQUENCED`、`BLOCKED`、`NO_EVENTS`。
