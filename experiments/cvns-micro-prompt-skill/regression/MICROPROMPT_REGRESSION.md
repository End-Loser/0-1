# 微任务提示词回归案例

## MP01 单调用过载
输入一个提示词要求模型同时规划、生成、审计和返修。
预期：提示词 lint 失败，要求拆分任务。

## MP02 多项不相关规则
一个任务声明超过3项 active_constraints。
预期：注册表/验证器拒绝。

## MP03 BASE 合并
一次提示词要求同一人物同时生成 power/location/cognition。
预期：拒绝；必须按 entity×dimension 分开。

## MP04 BASE 截断
把全2000章有效期限制为5个节点。
预期：拒绝；节点应覆盖全部真实变化点。

## MP05 反向污染
后续冲突/事件任务直接覆盖冻结 BASE 文件。
预期：权限检查拒绝；只能产生 PROPOSAL 或新版本。

## MP06 全局规则过载
每次调用都附加完整世界规则、全部变量线和全部审计协议。
预期：input pack lint 失败，必须按 read_scope 物理裁剪。

## MP07 生成自审混合
Writer 同时写正文并声明本章审计通过。
预期：拒绝；Writer 只写正文，审计另行调用。

## MP08 结构通过冒充语义通过
JSON schema 通过即宣称线条目的和完整性通过。
预期：拒绝；语义目的/覆盖须独立检查。

## MP09 过度拆字段
将一条 BASE cognition line 按每个章节或每个记忆节点拆成多次调用。
预期：拒绝；完整变量线仍为一次调用。

## MP10 无目的变量线
为普通一次性杯子生成完整11维变量线。
预期：签约准入失败或维度 SKIP；实体注册不等于完整变量线。


## V1.3.0 回归案例
1. **BASE-STATE-FORMAT**：每个 BASE 输出的 state 必须是字符串，并符合本次注入的唯一维度注册行；`time` 不能出现在11维 BASE。
2. **BASE-EXISTENCE-VOCAB**：existence 状态使用 `alive / injury / fatigue / recovery / dead / missing / departed` 语义；dead 默认终态，复活无规则/事件则拒绝。
3. **BASE-LOCATION-MULTIVENUE**：跨场馆节拍必须有 beat_id 位置节点；同场馆内部移动不单独产生 location 节点。
4. **BASE-ITEM-DERIVED**：跨章关键物品不得同时以完整物品状态在 BASE 与 DERIVED 双写。
5. **MECHANISM-RANDOMNESS**：随机机制必须声明计划是否能指定结果；未声明不得默认为 Writer 可选定。
6. **MECHANISM-STATE-TRANSITIONS**：持有/接触/封存机制缺死亡、失能、强制取离或开封/复封迁移语义时，标 RULE_GAP。
7. **MODEL-EVENT-SHAPE**：P14A content 使用 Event 的真实字段与 event_type 五值。
8. **MODEL-OUTLINE-SHAPE**：P15A content 使用 ChapterOutline/OutlineBeat 字段；最终 beat_id 由代码发放。
9. **MODEL-DERIVED-ACK-SHAPE**：P16A/P16B 分别匹配 VariableLine/AckNode；DERIVED cause 不得 user_input，ACK 不得预先标 executed。
10. **MODEL-WRITER-SHAPE**：P18 content 使用 ChapterContent.text/word_count，audit_records 留给审计程序。
11. **AUDIT-SCOPE**：POV、知识、物品、身体、位置五种审计对象分开调用，不允许旧 P19A/P19C 的混合提示词重新引入。
12. **PROMPT-NO-ORCHESTRATION**：生成提示词中不得出现 validators/predecessor/下游运行命令；这些只在宿主注册表。


## V1.3.0 微任务注意力回归案例
13. **ONE-PRIMARY-OPERATION**：每个提示词只含一个主要目标；不得同时生成、审计、修复或批准。
14. **LOW-SEMANTIC-BUDGET**：语义限制默认1–3项、硬上限15项；公共 BASE 核心2项 + 维度规则1–5项。长任务（P18/P26）不得把多个任务伪装成多项限制。
15. **NO-METADATA-IN-CONTEXT**：predecessor、validators、write permission 和调度命令不得进入模型提示词。
16. **IMMUTABLE-ARTIFACT**：后续任务只能读取冻结成果或提出新版本，不得覆写原成果。
17. **ONE-ENTITY-ONE-DIMENSION**：BASE 按 entity×dimension 一次生成完整有效期轨迹，不按章节或单字段拆分。
18. **P06-GLOBAL-MATRIX**：表达任务书仍是一个全局矩阵逻辑任务，不得逐维度独立生成。
19. **P09-ENTITY-PAIR**：冲突扫描一次处理一对实体的全部维度，不做有损截断。
20. **AUDIT-OBJECT-SEPARATION**：POV、知识、物品、身体、位置审计不得重新合并成万能审计提示词。
