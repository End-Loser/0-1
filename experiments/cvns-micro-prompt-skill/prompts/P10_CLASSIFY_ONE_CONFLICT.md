# P10 — 单条跨线关系判定

**本轮唯一目标：** 判定一条 P09 关系候选的真实性质，并原样保留宿主规范化的 `candidate_ref`
**输入：** 一条候选、双方原始节点、时间/规则证据及候选类型
**只遵守本任务的限制：**
1. 只选 `TENSION_ASSET_CANDIDATE`、`MUTUALLY_EXCLUSIVE_ERROR`、`CAUSAL_INTERACTION_CONFIRMED`、`PENDING_EVIDENCE`、`NO_MATERIAL_RELATION`。
2. 张力须可同真且造成选择代价；互斥按当前批准规则，不预判新解释永远不可能；因果须有方向、前件/时序及作用路径。seq仅是本线序号，不证明跨线先后；effect_node_refs引用结果节点。
3. 证据不足时挂起；不得把共现、相似、主题呼应或心理矛盾冒充因果/互斥。
**交付：** RelationshipDecision；张力进入 P12，互斥先探索解释或显式选最小修复；未解决矛盾不得资产化。
