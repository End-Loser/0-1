# V1.5.0 — Event 预期状态变化映射合同

本文件以 `schemas/EVENT_MODEL_SCHEMA.json`（R-06.2 `data_models.py::Event`）为唯一生产 Event 字段依据。`schemas/EVENT_SCHEMA.json` 是旧的 Skill 级草图，不得作为 P14A/P14C0/ChapterOutline.event_records 的生产输出合同。

| P13B target_kind | P14A 写入的真实 Event 字段 | 后续责任 |
|---|---|---|
| `BASE_LINE` | `variable_changes`；`causal_links` 保存 target_ref、change_intent 和原始 driver_refs | P14C0 唯一节点归属；审计校验对应 BASE 节点 |
| `DERIVED_LINE_PROPOSAL` | `variable_changes` 只记录计划变化；`causal_links` 保留驱动与提案身份 | 大纲冻结后由 P16A 正式生成 DERIVED 线；Event 不伪称该线已存在 |
| `WORLD_STATE` | 仅当 target_ref 是已注册 world-scope line 时写 `variable_changes`；否则保持 CONDITIONAL/UNRESOLVED，不能编造世界变量 | P14B/P19D 校验规则和状态依据 |
| `READER_COGNITION` | `causal_links` 登记揭示目标/驱动；`cognitive_impact` 仅可描述场景预期效果，不是读者信息集事实；不得写入角色 `knowledge_changes` | P15A 将揭示义务映射到满足解禁条件的 OutlineBeat `payload`/`required_elements`；ReaderCognition 由批准的大纲揭示节拍推导 |

## causal_links 最小来源记录
每条由 expected_state_changes 派生的 link 至少保留：
- `driver_refs`：原 P13B `cause_driver_refs`（ASSET_PHASE / CONFIRMED_CAUSAL_INTERACTION）；
- `target_kind`、`target_ref`、`change_intent`；
- `materialization_field`：Event 原生承载字段名称；
- `result_status`：PLANNED / MATERIALIZED / OUTLINE_PAYLOAD_REQUIRED / CONDITIONAL；
- `evidence_refs`：可定位的节点/规则/事件证据。

以上是 `Event.causal_links` 的内部对象结构，不增加顶层 Event 字段。

## 禁止
- 不得输出 `state_deltas`、`expected_state_changes` 或 `driver_refs` 作为 Event 顶层字段。
- 不得把“人物知道”与“读者知道”混为一谈。
- 不得把 DERIVED proposal 伪装成已经落地的正式变量线。
