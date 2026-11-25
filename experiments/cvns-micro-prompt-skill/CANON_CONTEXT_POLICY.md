# CanonIndex记忆检索与P16消费合同

## 唯一事实来源

本轮以原项目CanonIndex的完整DERIVED线与ACK表作为结构化记忆，不另造“记忆卡事实库”。人物属性仍由完整BASE线和已有倾向卡承载；检索结果只是有来源的设计视图，不能替代原变量线或自动授权POV角色知悉。

`tools/canon_context.py`负责只读检索、P16结构/引用校验及ACK履行证据检查。它不调用模型、不更改CanonIndex、不生成真实批准，也不自动终止、转移、升级变量线。

## 输入与来源

宿主按 `schemas/CANON_CONTEXT_MANIFEST_SCHEMA.json` 建立新manifest：

- project_id、version、chapter、mode，mode为generator/writer/audit。
- canon绑定一份完整CanonIndex快照，至少含derived_lines及ack_nodes。
- base_lines绑定本次需保留的完整BASE工件；未提供的真实必需BASE仍须被宿主完整依赖门禁拦截。
- outline绑定实际批准、ID已稳定化的大纲文件；P16生产消费必须提供。
- current_index.path指向宿主受保护的实时工件索引，格式沿用Writer依赖门禁。
- entity_ids、required_line_ids明确当前相关对象和不可省略的线。
- budget_lines可选，不设默认固定容量；它是DERIVED线数量上限，不是token预算或API授权。

每个绑定记录artifact_id、version、相对path及实际字节sha256。CanonIndex与BASE使用工件封装的content字段；大纲支持content、payload或冻结器的顶层结构。封装必须有project_id、artifact_id、version和status=FROZEN，并与当前索引完全一致。所有证据路径限制在manifest目录以内。

旧快照缺版本、状态或来源时不手工补字段冒充批准。由宿主审定新快照，旧文件继续只读。新冻结副本已明确记录项目、版本及FROZEN状态。

## 检索规则

- 完整保留被选中DERIVED线的全部节点及元数据，不删除同章seq，也不将proposed自动改为established。
- 相关scope/world、必需引用及到期ACK引用的线按当前检索窗口内的scope×dimension簇共同保留。Writer可裁剪不相关可选簇，但不能只留半簇。
- 存活区间沿用valid_from≤章号<valid_to；valid_to对应的终止章仍进入Writer。未生效线不作为当前线检索，显式引用未出生线则阻断。
- 尚未履行且已到期的ACK全部保留；逾期单列，不自动取消、延期或假定履行。其引用的历史线仍保留为追踪证据，不冒充当前状态。
- 同一条线被多个ACK引用只注入一次。注册表ID保留全量，包含本次没有检索的记录，防止P16重复登记。
- 生命周期边界与跨scope后继按`P16_LIFECYCLE_POLICY.md`共同保护：必须保留的旧线不能留下而裁掉可用后继。断链或同一subject_ref×dimension窗口重叠阻断；历史、当前章内闭合与proposed后继显式区分，不能把全量历史当成两个当前事实。
- BASE不参与此线数裁剪，输入中的每条完整BASE原样保留。audit模式保留完整DERIVED及ACK历史，不裁剪；超预算仍报告阻断。
- 真实token预算、全量来源完整性、角色信息隔离和后续语言效果仍由宿主控制。没有自动语义摘要、别名解析、显著度评分或旧线删除。

## P16调用与输出

生产调用前执行：

```powershell
python tools/preflight_task_call.py --input-pack <包> --require-canon-context
```

输入包的宿主控制层提供project_id、version及canon_context_gate路径/hash；inputs.chapter、inputs.canon_context与本次检索结果一致。P16还须提供与被绑定来源完全一致的inputs.chapter_outline。

- P16A提供entity_id及dimension。新线只能记录指定实体/维度、本章proposed状态，cause与purpose引用必须存在；无新增可返回NO_DERIVED，不能夹带线ID或节点。
- P16B提供obligation_candidate，含source_ref及单项obligation。生成结果必须恰好登记该义务、引用获批事件/节拍并保持pre-registered；没有目标章批准大纲不能自定未来期限。
- P16C提供line_id。校验指定DERIVED线、当前生效章/seq、已批准cause及包含线和cause的dependency_refs。明确TERMINATE/TRANSFER/UPGRADE状态转换、完整旧节点和按需单一后继；具体合理性仍需独立宿主生命周期审读，结构通过不提交。BASE不接受此操作。
- derived_state_required/obligation_required/lifecycle_change_required为false时只形成无任务终态，不启动空调用；这些标记必须来自可信宿主，不是模型自产授权。

生成后执行只读结构校验：

```powershell
python tools/canon_context.py --input-pack <包> --sha256 <包字节hash> --candidate <输出> --task-id P16A --out <新验证回执>
```

task-id也可为P16B/P16C。该命令重新核验来源和运行时输入，不修改候选，只返回P16_STRUCTURE_VALIDATED或BLOCKED。成果封装、独立语义审读、审批、索引提交及依赖失效仍由宿主完成。

P16需要的是已审定且ID稳定的P15计划快照，不是已完成全部P16的最终章节包。宿主先物化P15稳定ID并审定只读快照，再运行P16，最后冻结最终章节包，避免“P16等待最终包、最终包等待P16”的循环。冻结器接受显式required_status=FROZEN的大纲来源，并生成新的最终副本；不原位改写前置快照。

## Writer与P17C

检索命令：

```powershell
python tools/canon_context.py --manifest <manifest> --sha256 <实际hash> --out <新检索回执>
```

P17C只接收canon_context_summary，其中包含context_object_sha256与source_hashes；不得复制完整canon_context。P18接收一次完整检索载荷，并同时运行 `--require-writer-dependency-gate --require-canon-context`。

CanonIndex、BASE及已绑定大纲的每个源ID/hash必须进入P17C检查过的Writer依赖闭包。检索通过但来源未登记到闭包仍阻断；运行时换掉记忆状态、漏义务或伪造FROZEN大纲也阻断。通过只意味着NEEDS_HOST_CHECK，不授予Writer激活或文学质量批准。

## ACK回收边界

宿主调用 `validate_ack_fulfillment(ack, chapter_text, review, ship_receipt)`：

- ACK仍为pre-registered，审读绑定完整ACK对象hash及当前正文UTF-8 hash。
- 每项义务有PASS、理由和精确原文区间/引文；不能只靠全局PASS或缺少证据就标履行。
- 审读须VALIDATED/FROZEN、无阻断项，章号一致；SHIPPED回执绑定当前正文和该义务审读对象hash。

该函数只验证证据合同，不证明引文真正充分兑现义务，也不认证审读者或出厂者身份。受信宿主核验实际审读及权限后才能追加新的执行记录、提交新CanonIndex版本并失效受影响消费者。原ACK、正文和索引不由本工具改写。

只读工具本身不提交索引。已批准ACK履行可按 `INDEX_TRANSACTION_POLICY.md` 使用独立ACK_EXECUTE事务，将新Canon、消费者失效与幂等记录一起提交；不会修改原ACK文件。新DERIVED/ACK可通过DERIVED_REGISTER/ACK_REGISTER事务登记。P16C可通过LIFECYCLE_APPLY提交，另须绑定真实生命周期语义审读、完整BASE/Canon和独立批准，保留所有ACK；详细合同见`P16_LIFECYCLE_POLICY.md`。通用整书宿主仍未实现。

没有真实模型记忆效果测试或旧五章入口自动迁移，之前未调用的P16任务不能记为已实测。提交后须重建受影响消费者并执行新P17C，不以COMMITTED代替Writer批准。
