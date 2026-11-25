# V1.3.0 调用示例

## BASE cognition 一次调用
读取：`BASE_LINE_CORE.md` + `BASE_COGNITION.md` + `BASE_DIMENSION_REGISTRY.csv` 中 cognition 精简格式行（不加载其他维度或重复的语义规则） + 当前 entity×cognition 表达任务书 + 该实体注册资料/character_functions + WC + 必要 opening_date 日历表 + `BASE_LINE_SCHEMA.json`。
不读取：其他 BASE 线、其他实体变量线、P07 审计提示词、validators/predecessor。
写入：新的 `BASELineDraft` 版本文件。

## P06 一次全局矩阵调用
读取：ThemeSpec、全部 character_functions、EntityRegistry、候选 entity×dimension 矩阵、一个 ExpressionTaskBook schema。
输出：一个完整 entries 矩阵。不能逐 entity×dimension 分别调用。

## P09 一次实体对调用
读取：实体 A/B 全部已签约线（所有维度，不过滤、不截断；有无损种子协议时可压缩）、必要 WC 规则和一个 ConflictPairScan 合同。
输出：候选冲突报告。P10 分类是另一轮调用。

## 审计示例
P19A1 只看 POV 归属；P19A2 只看人物知识；P19C1 只看一个物品；P19C2 只看一个实体身体/存在；P19C3 只看一个实体场馆位置。每份审计报告单独输出，不修改被审计正文或底账。


## P13B→P13C→P14A 事件示例
读取：一个范围内 SELECTED assets 的阶段字段、P10 已确认因果交互、参与者时点状态、需要的 decision profile。
P13B 输出：`ASSET-1/SETUP` 放入机会簇 A，`ASSET-1/REVEAL` 放入机会簇 B，`ASSET-1/PAYOFF` 延后；`REL-3` 作为簇 A 的因果驱动。
P13C：对簇 A 中林晚、母亲分别列出已知/相信/未知、目标/欲望、可行选项、选择理由、放弃替代方案、代价，并做一次反事实敏感性检查；一次调用覆盖两名主要决策者。缺必需冻结 profile 则 preflight BLOCKED。
P14A：把选择互动转成一个 R-06.2 Event；不可提前兑现 ASSET-1 的 REVEAL/PAYOFF。
这不是一资产三次机械调用：如果某个阶段没有当前可执行事件，就延期；若没有真实行动，输出 NO_EVENT。
