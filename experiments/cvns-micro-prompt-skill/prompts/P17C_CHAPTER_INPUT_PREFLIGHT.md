# P17C — 单章 Writer 依赖闭包预检

**本轮唯一目标：** 判断一个已冻结章节计划的依赖闭包是否满足正式写作条件
**输入：** 章节计划 ID/hash、DependencyClosureManifest、批准状态回执、版本一致性结果和缺失项摘要；不接收完整 WriterInputPack、完整事件正文或全部审计报告
**只遵守本任务的限制：**
1. 只核验必需引用是否全部解析、是否为允许状态、版本/hash是否一致。
2. 缺少任何必需批准成果、稳定 ID 或依赖时必须 BLOCKED；不得从未授权文件补齐。
**交付：** 一份 ChapterInputPreflight；decision 仅为 `READY`、`BLOCKED` 或 `NOT_APPLICABLE`。
