# P14C0 — 单章事件去重与节点归属

**本轮唯一目标：** 为一章确定去重事件单元及状态节点唯一承载者。
**输入：** 本章通过 P17A 的 Event、机会来源、人物决策摘要、`variable_changes`/`causal_links` 和 BASE 节点。
**只遵守本任务的限制：**
1. 仅当核心行动、关键决策互动、时空窗口、结果及状态变化实质相同才合并；主题/同场景相似不够。
2. 每个 BASE 变化节点本章最多归属一个事件单元；独立决策路径或后果必须保留。
3. 节点无法唯一归属、合并会抹掉人物选择或产生循环时 BLOCKED；只提新版本，不改源 Event。
**输出：** ChapterEventOwnershipPlan；canonical_event 必须符合 `EVENT_MODEL_SCHEMA.json`。
