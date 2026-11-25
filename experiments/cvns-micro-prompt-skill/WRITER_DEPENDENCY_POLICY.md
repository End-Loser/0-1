# Writer 依赖失效与当前 P17C 门禁

## 范围

`tools/writer_dependency_gate.py` 只读取宿主证据，传播已声明依赖中的失效，并校验当前版本 P17C。它不调用模型、不批准事实、不修改旧成果、不切换索引，也不证明文学质量。

宿主必须从受保护的当前索引解析全部真实依赖，按 `schemas/WRITER_DEPENDENCY_GATE_SCHEMA.json` 创建新 manifest。禁止从模型输出取得索引或通过删掉依赖规避检查。该工具不是完整整书调度器，无法发现宿主未登记的依赖。

## 依赖图

manifest 使用以下字段：

| 字段 | 含义 |
| --- | --- |
| project_id / chapter / version | 当前拟写章节及冻结版本 |
| complete | 宿主确认依赖闭包完整，必须为 true |
| nodes | 工件 ID、版本、相对路径、原字节 sha256、depends_on |
| root_ids | Writer 必需根节点，必须包括冻结回执的每一份工件 |
| current_index.path | 宿主控制的实时索引，相对 manifest 目录 |
| frozen | 当前 APPROVAL_MANIFEST.json 的相对路径和字节 hash |
| p17c | 针对当前闭包执行的新 P17C 的相对路径和字节 hash |

`depends_on` 指向来源工件 ID；不允许重复 ID、重复文件路径、未登记边或依赖环。所有文件必须处于 manifest 目录以内，可放在其子目录；不接受目录逃逸。每个节点的来源 JSON 必须有匹配的 artifact_id、version 和 VALIDATED/FROZEN 状态。

新冻结器会在新副本中明确写入 `version` 与 `status=FROZEN`，不会原位升级旧副本。旧成果缺少版本字段时不能靠改 hash 迁移，需使用新的审定成果和新冻结版本。

## 当前索引

当前索引为宿主维护的可变控制文件，格式如下：

```json
{
  "project_id": "PROJECT",
  "artifacts": {
    "ARTIFACT-ID": {
      "version": "V2",
      "sha256": "当前工件的实际字节SHA256",
      "status": "FROZEN"
    }
  }
}
```

P17C 也必须登记在此索引。来源被撤销、删除、切换版本或替换 hash，即使旧来源文件仍然完好，也会失效。节点失效沿反向消费关系传播至审读、冻结工件和 Writer 根节点；仅根节点的闭包失效才阻断该章。不相关节点的变化单独报告，不强制全书重建。

索引内容不固定到整份 manifest 的历史 hash，否则一次不相关更新也会使所有章节误失效。工具读取当前索引并保存本次字节快照，结束前复查；来源或索引检查期间改变即阻断。检查结束后的并发激活必须由宿主锁或事务保证，工具本身不提供锁。

受控ACK提交使用 `INDEX_TRANSACTION_POLICY.md` 的内核锁、CAS及单索引提交点，使Canon新指针和消费者INVALIDATED状态同步可见。Writer最后预检与派发须由宿主使用同一锁或版本租约；只读工具不自动持锁，不保证派发后旧任务被取消。事务不会重新批准已失效的P17C或自动重建章节。

## 新 P17C

先对不含 `p17c` 链接的完整 manifest 调用 `subject(manifest)`，获得确定性主体 hash；宿主将它和当前冻结回执字节 hash 作为本次 P17C 的受控输入绑定。实际执行该次 P17C 并完成宿主验证后，在新证据中记录：

- project_id、chapter、version、artifact_id、task_id=P17C。
- status=VALIDATED/FROZEN、decision=READY。
- checked_dependency_subject_sha256。
- checked_frozen_manifest_sha256。
- missing_refs=[]、version_mismatches=[]。

最后才将新 P17C 路径/hash写入 manifest 并登记当前索引。排除 p17c 链接只是避免 hash 循环，不排除依赖图、冻结回执或索引路径。不能在旧审读上手工补 hash 冒充新审读；实际任务输入、执行指纹、独立响应和批准真实性由宿主留存验证。工具校验的是证据绑定，不是审读者身份或语义真实性。

## Writer 接入

生产宿主执行：

```powershell
python tools/preflight_task_call.py --input-pack <输入包> --require-writer-dependency-gate
```

输入包在宿主控制层提供 project_id、version、frozen_manifest_sha256 及 `writer_dependency_gate={path,sha256}`；inputs.chapter 必须匹配，inputs.p17c_preflight 必须与被绑定的新 P17C 完整 JSON 内容一致。控制层字段不注入 Writer 模型。

也可单独检查并生成不覆盖历史的回执：

```powershell
python tools/writer_dependency_gate.py --manifest <manifest> --sha256 <实际hash> --project-id <项目> --chapter <章号> --version <版本> --out <不存在的回执>
```

声明门禁、要求门禁或携带时间批准回执的 P18 自动执行检查；显式生产 CLI 参数不能被输入包的 false 标记关闭。未声明的旧无时间夹具保留 NEEDS_HOST_CHECK 行为，它从来不是正式批准，生产宿主不得使用此兼容分支。

依赖通过仍只返回 NEEDS_HOST_CHECK。完整 Writer 载荷必须由宿主从这些快照组装并核验，包括完整变量线、事实权限、时间限制、条件义务和信息隔离。本轮没有改造五章付费测试入口，也没有让任何已有小说或旧时间候选重新获批。时间语义批准仍使用独立时间门禁，当前工具不替代它。

## 派发与交付租约

`tools/writer_dispatch.py`提供Python宿主接口，不自行调用模型或批准语义。`prepare(root, pack_path, pack_hash)`只读生成批准主体，强制P18同时通过依赖和Canon门禁；两个manifest的实际索引都必须为同一root/index.json，门禁路径为绝对路径，避免拿错锁。

受信宿主独立批准记录须包含decision=AUTHORIZE_WRITER_DISPATCH、approved_by、lease_id和subject_sha256，主体绑定完整输入包字节hash、项目/版本/章号、依赖主体、来源闭包、冻结回执、当前P17C及Canon来源和检索内容。预算、会话隔离、真实P17C执行和Writer完整载荷仍由宿主核验，不是租约工具自动认证。

`dispatch(root, pack_path, pack_hash, lease_id, approval_binding, trusted_issuers, submit)`在与索引事务相同的内核锁下重新预检、核验独立批准，发布不可覆盖的leases/<lease_id>/intent.json，然后调用submit(pack, intent)。submit必须是受信、非阻塞且持久的入队函数，不得持锁等待模型全文。回调收到深拷贝，不可通过修改对象替换原输入包。回调结束再次核验来源。

intent不证明模型已经提交。提交前后进程崩溃或队列返回异常可能导致未知状态；已有同lease_id必须由宿主查询队列对账，工具拒绝重复调用submit，不因重试浪费付费请求。不同lease_id的总请求预算仍由宿主计数，不能借换ID无限重试。

`deliver(root, lease_path, lease_hash, prose, output_path, trusted_issuers)`在同一锁下验证原包、当前P17C、Canon与来源闭包，再发布不可覆盖的正文JSON。来源变更或撤销时拒绝旧在途正文；不相关索引更新不必作废该章。输出标记VERSION_VALIDATED_NOT_SHIPPED和quality_approval=NOT_GRANTED，尚须事实、时间、人物与文学审读，不能拿它作为ACK履行的SHIPPED回执。索引、旧工件和已有输出均不覆盖。

该模块尚未接入五章付费入口，也不保证队列或供应商exactly-once、自动取消在途任务、身份认证或防范恶意同权限写入；所有实际提交/交付和索引写入者必须遵守同一锁及权限策略。

## 通用消费者替换

`tools/consumer_rebuild.py` 可按已登记依赖顺序复检新候选、完整来源闭包、语义审读和独立批准，使用同一root锁原子替换消费者并失效未重建下游；详见 `CONSUMER_REBUILD_POLICY.md`。它不自行调用生成器或模型，不修改BASE/Canon，不授予Writer权限。

`verify_projection` 仅检查待提交索引，成功标记为PROJECTED_DEPENDENCY_VERIFIED_NOT_ACTIVE，普通 `verify` 和生产预检仍只读取实际索引。P17C替换另需新冻结绑定、完整模型侧引用摘要和原请求/响应/计费凭证；不能把投影检查或旧READY用于Writer派发。
