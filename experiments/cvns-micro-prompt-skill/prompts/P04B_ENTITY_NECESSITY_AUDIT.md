# P04B — 单候选实体必要性判断

**本轮唯一目标：** 判断一个候选实体是否值得注册进 EntityRegistry
**输入：** 一个尚未注册的 EntityCandidate、产生它的需求、现有 EntityRegistry 摘要
**只遵守本任务的限制：**
1. 执行删除测试，说明删除该实体会失去的具体主题检验、因果承载或类型功能。
2. 若已有实体可承担同一功能选 `REUSE`；不可替代性不足选 `REJECT`。
**交付：** `REGISTER`、`REUSE` 或 `REJECT`，由宿主提交注册表变更。
