# 最小输入包策略 V1.6.0（宿主调度器使用，不注入模型）

## 组包原则
- 不把整本项目、全部提示词或所有历史成果传给模型。
- 每次只加载当前任务 read_scope 所需的最小依赖闭包。
- 先读当前成果的显式 refs，再递归解析其依赖；禁止仅按 `domain` 粗筛 WorldConstants。
- 每个注入项在 `input_manifest.json` 记录 source_id、version、hash、reason_included、consumed_by_task。
- 同一个来源在一个输入包只出现一次；大文件只摘录当前任务引用的必要段落并保留行/ID来源。
- 规则引用必须精确按 WC-ID 加载；如正文/事件引用 WC-02，即使当前审计属于 geography，也必须包含 WC-02。
- 输出包只提供被选择资产、事件或实体的载荷；不得把同范围所有候选和已拒绝内容塞入 Writer 输入。
- P14B/P17A 审计随附只提供精简 decision + blockers + evidence refs，不复制整份长报告。

## 依赖闭包
以下内容如被事件、变量线、节拍或主题透镜引用，必须进入输入包或提供可解析、带版本的只读引用：
- EntityRegistry 对应实体条目；
- ExpressionTaskBook 对应 purpose contract；
- 当前事件涉及的秘密/证据注册表条目；
- 被引用的 WorldConstants 精确规则；
- 已批准 StoryAsset 的最小必要字段；
- 参与者在事件时点的 BASE/DERIVED 状态快照；
- 相关日历与时间锚点；
- 前置事件和必要状态转移。

## 不得注入
- 当前 BASE 任务以外的 BASE 线（P09 例外：一对实体全部已签约线）；
- 与当前任务无关的 VoiceCard、记忆、物品和全部历史报告；
- 未选中/已拒绝资产的完整载荷；
- 生成任务的 validators、predecessors、返修历史和下游审计提示词；
- 诊断成果冒充批准成果。

## 长上下文例外
- P06：全局 ExpressionTaskBook 矩阵是一个全局认知任务。
- BASE：一条 entity×dimension 完整线一次调用。
- P09：一对实体全部已签约线一次调用。
- P12B/P13B/P14C0：各自对同一范围内的一组相关资产/事件执行一个单一组合决策，不扩展为全项目扫描。
- P18：一章已批准节拍与最小必要输入；确需分场时由宿主预切已批准场景包。

## 输入包去重与注意力预算（新增）
- 先计算当前任务的最小依赖闭包，再决定是否需要载荷；不得把“可能有用”当成默认加载理由。
- 一个上游 artifact 在同一输入包只允许出现一个完整载荷。其他位置只保留 artifact_id 和字段级引用，禁止重复内嵌完整对象。
- 审计结果优先以 `decision + blocking_findings + evidence_refs + source_artifact_id` 的短回执传递；除非下游任务要重新审读，否则不复制整份审计报告。
- P17C 主要读取依赖闭包清单、审批状态、引用解析结果和阻断项，不读取全部资产全文、全部 P14B/P17A 长报告及全部历史审计。
- P18 只读取已冻结章节计划、当前章事件序列、当前章所需实体快照、已选资产的本章切片、实际引用的 WorldConstants、必要 VoiceCard 与钩子；不重复加载 P17C 的全量输入或全部审计正文。
- 同一资产跨章节只传当前章节需要的 setup/payoff 片段，并保留 source_refs；不得把该资产全部未来发展复制到每章。
- 以目标模型 tokenizer 计算实际 input tokens。若无法取得 tokenizer，记录 UTF-8 字节数作为代理指标，并标明不是 token 数。
- 输入包体量超过该任务预算时，先去重、缩减非当前窗口、改传状态回执；不得删掉当前任务的必需因果证据来满足预算。
- 每份 input_manifest 必须记录：artifact_id、version、hash、payload_mode（FULL/EXCERPT/STATUS_RECEIPT/REF_ONLY）、reason_included、consumer_task；省略的上游候选要记录 omission_reason。

## 最小数据消费记录
每个产物必须记录生命周期状态：GENERATED、VALIDATED、FROZEN、SELECTED、MERGED、MATERIALIZED、RENDERED、VERIFIED，或明确终态 REJECTED、DEFERRED、NO_ASSET、NO_EVENT、NOT_APPLICABLE、PROPOSAL_PENDING、DIAGNOSTIC_ONLY、SUPERSEDED、BLOCKED。

“在输入包中出现”只能证明被加载；“在输出中以 source_ref/asset_ref/event_ref 引用”只能证明存在引用证据；“被选中/物化/写入正文/独立验证”必须分别记录，不得合并为一个 USE=TRUE。


## V1.4.1：按任务消费者构建输入包（强制）

### 1. 先做 preflight，再调用模型

- 宿主必须先运行 `tools/preflight_task_call.py` 或实现等价规则。
- `SKIP` / `NOT_APPLICABLE`：不调用 LLM，写入确定性终态记录。
- `BLOCKED`：不调用 LLM，先修复阻断条件。
- `NEEDS_HOST_CHECK`：不是 READY；宿主仍须验证依赖闭包、批准状态、版本、权限和下游消费者。
- 禁止把空输入送给模型，再期待模型生成空卡、空报告或猜测缺失资料。

### 2. 不复制完整大包给不需要它的任务

- P17C 只接收：冻结章节计划 ID/hash、依赖闭包 manifest、审批状态回执、版本一致性结果、必要缺失项清单。不得嵌入完整 `writer_input_pack` 或整份 `approved_events_with_checks`。
- P18 只接收一份 Writer Pack。若 `chapter_outline` 已在 Writer Pack 内，禁止在顶层再次复制。
- P19D 单事件审计只接收 focal event、必要前驱/后继事件和相关状态切片，不接收整章全部事件。
- P14B/P17A 按一个 Event 运行，只传该事件涉及实体的必要状态节点，不传完整无关变量线。
- P10/P12 按单一冲突运行，只传该冲突的 source refs 与相关变化节点，不重复附带两条实体全量变化节点。
- P07A/P07B/P07C 保持独立会话，但分别构造任务投影；不得因为会话隔离而把同一份全量 BASE JSON 无差别复制给三个审计。
- P09 是一对实体的全线扫描，允许读取这对实体已签约的完整 BASE 线；不加载其他实体线。

### 3. 输入包去重和预算

- 同一输入包内同一 artifact/version 只能物化一次；其他位置使用引用。
- 默认 150 KiB 预警，200 KiB 需要复核，300 KiB 以上必须附 `input_budget_exception`，说明无法进一步裁剪的原因。
- 目标模型 tokenizer 可用时，以 token 数作为预算依据；不可用时才用 UTF-8 字节数代理，并在 manifest 标记 `budget_unit=bytes_proxy`。
- 每项输入都要有 `why_needed`、`consumer_task_id`、`source_artifact_id` 和 `projection`；无消费者的材料不得注入。
- 审计包引用原文证据时只携带必要片段及可回溯 offset/hash；不得为方便把整章反复复制。

### 4. 输入去重不等于历史删除

源成果、冻结版本、被拒绝的候选和诊断报告继续保留。只减少调用时重复注入模型上下文，不删除历史工件。


## V1.5.0：决策与资产阶段输入投影
- P05E 只读取单个决策主体注册资料与 character_functions；不得读取其他实体 BASE 线。决策倾向卡只作为稳定行为倾向引用，不代替时点状态。
- P13C 只读取当前机会组的主要决策者、对应时点状态快照、必要 P05E 卡和确切 WC 规则；同一机会组的全部主要决策者放入一次调用，避免逐人调用。
- P13C 不读取完整资产未来轨迹；只注入当前部署阶段的 payload ceiling、依赖和允许揭示内容。
- P14A 只读取当前 EventOpportunityCluster 与对应 P13C decision plan；其他事件、非相关资产、未到揭示期的未来 payoff 不注入。
- P19D 只读取该事件及因果链所需的决策计划/profile 证据，不加载全书所有人物卡。
- 决策卡是 sidecar；稳定倾向不写入 BASE state。动态 goal/desire/cognition/relation 等仍按时点快照读取。
- 若 P13C preflight 为 NOT_APPLICABLE（事件没有需要决策的 agent），宿主生成带 cluster_id/input hash 的 `NO_AGENT_DECISION_REQUIRED` 终态回执；这不是模型生成的空白计划。

## V1.6.0：冲突解释输入与事实投影
- P11A 读取一条批准的当前规则判定、原始命题/节点、相关规则及题材边界；P11B 读取同一候选集和原始证据，一次比较全部候选。
- 命题引用由宿主绑定原文件、版本、hash 和 JSON Pointer。候选的保留/重解释/修订声明不能代替源文本。
- P12 读取批准的张力判定。解释形成的新判定必须有批准冻结包及宿主激活回执；旧互斥不得改名后直接投影为资产。
- 宿主在输入包顶层附 `resolution_dependencies` 数组，每项使用政策规定的来源、冻结包、判定、激活字段。沿资产/因果/事件/节拍/Writer 的依赖闭包传播，不允许删除来源标记绕过门禁。preflight 无法自行发现被宿主隐瞒的依赖。
- 此数组只用于宿主检查，不注入模型。模型读取当前批准版本的事实、规则、事件与必要揭示材料，不读取全部解释竞争报告或失效版本。
- 完整变量线作为全书规划上下文保留；当前任务投影只减少无关载荷，不将完整线改造成逐章生成，不默认注入相邻章节正文。

## 五章宿主：无损引用与有界返修
- `tools/call_optimization.py` 仅对同一输入内字节规范化后完全相同的大对象建立 `$cvns_input_ref`；指向 `inputs` 内已经保留的完整载荷，不是要求模型读取本地文件。
- `tools/real_novel_test.py` 目前只对 P18 启用此投影。原 `input_pack.inputs`、完整 BASE、冻结工件和 canonical Event 不变；发送前递归还原并核对全部输入，引用目标另有内容 SHA256。
- 同 ID 不同内容不得合并。保留第一次完整出现，其余引用；计入说明与来源清单开销后没有净字节收益则不采用。字节节省不是实际 token、金额或质量提升。
- 调用级强化 Schema 同时用于发送和回包验证；调用前检查 Schema 本身及本地引用。缓存与续跑核对输入摘要、提示词/合同/传递 Schema/校验器/宿主版本、范围说明和输出参数指纹。
- 缺合同指纹的旧缓存不能自动认定为当前合同成果；保留作历史证据，不静默迁移，不自动触发整轮重生成。新版本任务须显式登记。
- 同一执行指纹最多四次请求，每次入口最多两次。相同错误且没有可测改善则写独立 BLOCKED 回执并停止；重复启动或只提高预算不能重新打开该指纹。字数距合法范围缩小视为改善，不能因仍有字数错误就过早阻断。
- 返修只附一份最接近合格的失败底稿和当前错误，不注入全部失败历史。修复后仍必须通过全部校验和原语义门禁，不将暂停重试当成批准。
- `tools/benchmark_call_optimization.py --work-dir <原始证据目录> --out <新报告路径>` 只读原请求与用量，离线测量完整请求体净字节变化；不读取配置、不调用模型、不写回原始证据。

## 时间与事实边界
- P18 的 `WriterBoundaryContract` 从原 clock、世界事实、完整章节接口和 hash 绑定的 P08 来源编译。已批准的节奏计划在包内保留，不用外部文件引用替代实际载荷；具体时段未定则保持未定，不由宿主擅自补出钟点。
- P08 输入的 `target_time_span` 标注为全书外边界；单章端点不能把全书“清晨至黄昏”重复当作本章跨度。已冻结端点发生顺序冲突时，在任何新 Writer API 请求前停止。
- 完整变量线与全书设计上下文仍保留；事实权限明确区分当前事实、未来节点、未知事项和本章获批揭示。候选线索不增加世界事实，人物猜测或谎言不自动变成叙述者真相。
- `tools/writer_boundaries.py --work-dir <证据目录> --out <新报告>` 为只读离线诊断，不访问配置或 API。时间按明确主线锚点检测；有限事实模式只提供待语义核验线索。未检出问题不代表语义合格。
- 跨章汇总发生在生成后；每个 Writer 仍只消费共同冻结计划，不读取相邻 Writer 正文。旧生成包的离线边界检查也不修改其字节或批准状态。

## 当前依赖证据
- 生产P18调用前执行 `--require-writer-dependency-gate`；宿主控制层保留project_id、version、frozen_manifest_sha256与writer_dependency_gate路径/hash，不注入模型。
- `WRITER_DEPENDENCY_POLICY.md` 的完整依赖图与实时索引只供宿主检查；P17C仍只读取闭包摘要和批准回执，不接收整书载荷。输入里的P17C须与当前绑定证据完整内容一致，不添加伪FROZEN标记覆盖原状态。
- 来源或索引改变须失效实际消费者，重新执行受影响审读、冻结和P17C。门禁不代替Writer实际载荷的快照组装、事实权限校验及宿主并发控制，不以旧READY字符串放行。

## CanonIndex与P16
- 依照 `CANON_CONTEXT_POLICY.md` 从hash绑定的完整CanonIndex建立相关记忆视图；保留选中线的全部节点、当前窗口内完整相关簇、到期ACK及全量注册ID，不创建第二套人物事实来源。
- P16接收generator视图与真实冻结P15快照；P17C只接收writer视图的hash/来源摘要；P18接收一次writer载荷，并核验其来源在当前依赖闭包内。运行时替换视图或大纲会被阻断。
- 实时索引、检索manifest和门禁证据留在宿主，不注入模型；model_context中的proposed不升级为当前事实，完整BASE保留，未检索的旧线只省略载荷、不删除索引记录。
- budget_lines是显式DERIVED线数限制，不能作为token/API预算；必需簇或ACK超过容量则停止。无实际任务目标不调用P16；ACK履行、生命周期变更与真实记忆效果须另行审读和受控回写。
