# CanonIndex受控提交与消费失效事务

## 当前范围

`tools/index_transaction.py`支持ACK_EXECUTE、DERIVED_REGISTER、ACK_REGISTER及LIFECYCLE_APPLY。已登记义务经真实正文审读和出厂后可申请执行；P16A/P16B候选经来源核验后可申请登记；P16C单线终止/迁移/升级按`P16_LIFECYCLE_POLICY.md`提交。四者均须独立宿主批准，生成新的CanonIndex快照，并原子切换当前索引、失效实际消费者。

它不改BASE、不批准世界事实，不调用模型。P16C执行另须来源绑定的生命周期语义审读，不凭任意status直接patch。审批者身份认证、审读语义、完整依赖图及文件权限仍由受信宿主保证。提供issuer字符串和hash不是身份认证。

## 受控状态目录

宿主显式指定独立root，使用其 `index.json`；不扫描项目目录寻找索引，不自动迁移或激活已有小说。

事务索引在原工件表上增加以下字段：

| 字段 | 要求 |
| --- | --- |
| project_id | 与工件、请求及履行证据一致 |
| generation | 非负整数，每次实际提交加1 |
| artifacts | ID对应version、sha256、status；Canon及其上游还须有相对path |
| dependency_graph_complete | 宿主确认完整登记，必须true |
| dependencies | 每个工件ID均有一组来源ID；无重复、未知边或依赖环 |
| transactions | 已提交tx_id对应的幂等回执；初始为空对象 |

只读Writer与记忆门禁兼容这些新增字段，但旧简化索引不能直接提交。宿主必须在独立审定步骤完成依赖登记及初始化，禁止补一个true绕过真实完整性检查。

Canon当前工件须FROZEN，完整来源JSON须有project_id、artifact_id、version、status及content。提交前核验Canon及其已登记上游文件的真实ID、版本、状态和字节hash，不能只信索引状态。

## 请求与批准

请求遵守 `schemas/INDEX_TRANSACTION_SCHEMA.json`：

- operation=ACK_EXECUTE、tx_id、project_id、chapter、canon_id、ack_id。
- expected_index_sha256是申请时当前索引原字节hash，作为compare-and-swap前提。
- canon绑定当前Canon快照；new_version必须非空，不能重复当前或事务历史已用版本。
- prose、review、ship分别绑定当前正文、逐项义务审读及出厂证据。
- base_lines可提供完整BASE绑定，必须存在于Canon上游闭包；ACK引用BASE线时不能缺失。
- approval绑定独立宿主批准记录。所有路径限制在root以内，tx_id只允许安全目录字符。

先保存含approval占位链接的请求，执行只读预览：

```powershell
python tools/index_transaction.py preview --root <独立状态目录> --request <请求> --sha256 <请求字节hash>
```

预览不读批准记录、不创建锁或状态文件，只返回主体hash、候选Canon实际hash及旧索引hash。ACK、正文引文、出厂绑定或来源无效时预览也阻断。

主体hash排除approval字段，避免循环；其余请求、来源hash、新版本及旧索引hash全部绑定。受信宿主另行签发批准记录：

```json
{
  "decision": "APPROVED",
  "approved_by": "受信宿主批准者",
  "project_id": "项目",
  "tx_id": "请求事务ID",
  "request_subject_sha256": "预览主体hash",
  "candidate_sha256": "预览候选hash",
  "expected_index_sha256": "申请时索引hash"
}
```

然后将批准文件实际hash写入请求，计算最终请求hash。批准文件不能由模型自行生成并视为有效；可信issuer列表来自宿主权限策略。

## P16登记合同

DERIVED_REGISTER对应P16A，ACK_REGISTER对应P16B。请求保留公共事务字段和完整base_lines，另绑定candidate、input_pack、registration_context；不得混入ACK_EXECUTE的ack_id、prose、review、ship字段。

registration_context是来源绑定的generator模式Canon manifest：项目、章号、Canon版本、当前索引hash必须一致，实际索引必须为事务root的index.json；Canon及BASE必须与事务请求相同，大纲必需。校验这些来源及其完整上游的实际文件、状态、项目、版本、路径和hash；不能只用FROZEN大纲字符串或检索回执放行。

input_pack的task_id、project_id、version及inputs.chapter必须一致；输入canon_context和chapter_outline完整对象必须匹配重新检索结果。candidate必须为当前项目、版本、章号及对应task的VALIDATED/FROZEN封装，具有非空artifact_id。再执行validate_p16检查目标、真实计划引用、完整注册ID表、proposed节点或单项pre-registered义务。NO_DERIVED是合法无调用终态，但不是可提交登记对象；不得借登记把规划节点提升成已发生事实或义务履行。

提交只追加完整候选线／义务及registration_records，保留所有旧节点和未知扩展。记录绑定候选、输入包、检索manifest以及当时完整来源闭包hash。大纲通常消费旧Canon，因此登记依据是不可变历史来源，不给新Canon添加指向该大纲的实时上游边，避免依赖环。宿主须保持这些历史文件只读；hash记录不是对后续任意篡改的权限保护。

四类事务共用preview、独立批准、内核锁、CAS、不可覆盖快照、失效传播和幂等提交点。登记和生命周期变化都会使旧大纲/P17C失效；必须重新审定受影响计划并真正执行新P17C，不把提交视为Writer激活。完整BASE不修改、不裁剪。

LIFECYCLE_APPLY使用candidate、input_pack、registration_context和独立lifecycle_review，禁止混入履行/出厂字段。只处理一个DERIVED源线：保留旧节点，关闭生效窗口；迁移/升级只追加同一subject_ref的一个新ID后继。完整Canon和BASE用于一致性审读，包含未来ACK的逐项保留，不取消义务。详细字段与边界见`P16_LIFECYCLE_POLICY.md`。

## 提交与恢复

```powershell
python tools/index_transaction.py commit --root <独立状态目录> --request <最终请求> --sha256 <最终请求hash> --trusted-issuer <受信批准者>
```

提交顺序：

1. 获取root内持久锁文件的内核排他锁。冲突立即阻断，不睡眠重试、不删除所谓“过期锁”。进程死亡自动释放内核锁。
2. 检查当前索引CAS、实际来源、完整ACK履行证据及宿主批准。
3. 在 `transactions/<tx_id>/` 发布不可覆盖的intent.json、原字节index.before.json及新canon.json。先flush/fsync临时文件，再以硬链接发布完整文件；不同内容占用相同路径即阻断。
4. 复查来源和当前索引，在内存构造同一份新索引。ACK_EXECUTE仅在新快照中改为executed并追加执行证据；登记操作仅追加候选和来源记录；LIFECYCLE_APPLY仅闭合源线、按需追加一个后继及来源记录，旧节点和ACK原样保留。其他线、节点和未知元数据不省略。
5. 沿已登记反向依赖标记消费者INVALIDATED，新Canon保持FROZEN；不相关节点不失效。索引generation与transactions回执同时更新。
6. 用一次os.replace提交完整index.json。新Canon、消费者失效状态和幂等记录共用一个提交点，不产生“新记忆生效但旧P17C仍有效”的可见中间态。

旧Canon、BASE、正文、审读及批准文件不覆盖。index.json是允许切换的控制文件，其旧字节保存在index.before.json。回执记录源索引hash、前后版本、快照路径/hash、失效ID及批准者。

同一tx_id及同一最终请求hash重放只返回ALREADY_COMMITTED，不再执行ACK或增加generation；tx_id换内容即阻断。已提交快照和旧索引存档缺失/损坏也阻断，不自动重执行或撤回历史。

提交前崩溃：旧索引仍有效；重放同一请求复用匹配的完整暂存文件，重新验证批准、来源及CAS后提交。原子替换后崩溃：事务记录已与新索引一起生效，重放识别为已提交。半写临时文件不会被索引引用；崩溃残留临时文件保留为未引用文件，不自动删除或发布。

## 并发与边界

所有索引写入者必须使用同一个root锁。宿主还须在同一锁下完成Writer最后预检和派发，或使用等效版本租约；当前只读门禁不会自行持锁到模型调用结束。不能在已持锁上下文再次调用commit，它不是可重入锁。

不合作的外部写入可能在最终CAS与替换之间竞争，普通文件系统没有原子“按内容hash替换”原语；中间复查是诊断，不替代互斥和权限。已经派发的Writer也不会被该事务自动取消，宿主必须阻断其旧版本交付。

本轮在本机文件系统验证进程崩溃与并发；不保证Windows断电持久性、网络文件系统、硬件故障恢复或恶意同权限文件替换。os.replace后报错可能表示提交已发生，必须重放同一请求查询，不根据异常盲目创建新事务。

当前路径不补造旧运行缺失的P16证据，也不提供任意事实注册器、事务垃圾回收或真实模型质量验证。消费者重建与原子替换由 `tools/consumer_rebuild.py` 按 `CONSUMER_REBUILD_POLICY.md` 单独处理，共用索引锁与事务账本，不并入Canon事实变更。新的Canon版本仍须重新冻结受影响成果并执行新P17C，COMMITTED不授予Writer激活。
