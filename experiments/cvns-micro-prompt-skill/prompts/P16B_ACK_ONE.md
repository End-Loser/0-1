# P16B — 单项 ACK 义务登记

**本轮唯一目标：** 登记一项由已批准计划产生、尚待正文履行的可核验义务
**本轮输入：** 一个已批准事件或节拍中的承诺/待完成动作、相关变量线 ID
**只遵守本任务的限制：**
1. 只登记一项可用正文判断履行与否的义务。
2. 初始状态必须为 pre-registered；正文出厂审计通过前不得声称 executed。
**交付：** 一个 AckNode。
**本轮输出格式：** 按 ACK_NODE_SCHEMA.json 输出；字段使用 id/type/status/target_chapter/related_lines/ack_ref/obligations/scene_pressure。
