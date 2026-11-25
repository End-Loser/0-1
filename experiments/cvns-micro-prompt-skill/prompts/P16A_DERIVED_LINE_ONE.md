# P16A — 单条 DERIVED 线生成

**本轮唯一目标：** 为本章一个承重实体生成一条 DERIVED 状态线
**本轮输入：** 本章冻结大纲、相关 CanonIndex 检索、本章 BASE 快照、对应已批准事件
**只遵守本任务的限制：**
1. 只记录本章新产生且跨章/后续确需追踪的一个承重状态；普通单章细节不升级。
2. cause.kind 只能引用已存在的 event、line、asset 或 outline_beat；不得使用 user_input。
3. 不修改 BASE；无新增承重状态时输出 NO_DERIVED。
**交付：** 一条 VariableLine（layer=DERIVED）或 NO_DERIVED。
**本轮输出格式：** 按 DERIVED_LINE_SCHEMA.json 输出；节点 causal_status=proposed；purpose 至少一项，ref 必须来自已提供的计划，不虚构未来节拍。
