# 时间候选批准与冻结门禁

## 范围

`tools/timing_approval_gate.py`是宿主证据校验器，不是模型审读任务，也不会签发批准、重写时间计划或激活Writer。支持任意章数、明确按顺序推进的同日时间段；跨日、跳时与回溯需要独立时钟合同，当前拒绝自动套用同日阶段排序。

输入文件须处于宿主控制的证据目录。调用者从自身权限策略传入可信批准者，不得从模型输出或待审manifest读取“可信名单”。issuer字符串和SHA256不是数字签名，不能认证任意第三方文件的作者身份。

## 门禁输入

新建一个时间门禁manifest，包含：

| 字段 | 内容 |
| --- | --- |
| project_id / version | 项目及拟冻结的新版本 |
| chapter_order | 严格递增、不重复的正整数字段，明确本时间段覆盖范围 |
| same_day | 当前必须为true |
| global_window | start_phase与end_phase，使用P08日内阶段枚举 |
| candidates | 每章恰好一个P08候选，绑定artifact_id、chapter、path、sha256 |
| context | 每章的event_sequence与outline各一份，额外记录role，并绑定ID、章节、路径、hash |
| review | 宿主提供的语义审读文件路径及hash |
| approval | 宿主提供的明确批准文件路径及hash |

所有文件路径相对门禁manifest目录，禁止逃逸目录。候选和上下文ID不得重复，文件身份必须与绑定记录一致。上下文必须为VALIDATED/FROZEN；大纲event_records顺序须对应EventSequence的event_order。P08来源只可引用本章或相邻覆盖章节的事件ID、序列或大纲ID，不接受候选引用自身作为来源。

新时间候选须采用结构化target_anchor，包含可为null的start_phase/end_phase。日内端点必须在global_window内，已知阶段跨未定章节前向携带，不允许明确时间回退。

## 审读与批准

语义审读文件须包含：

- project_id、version、decision=PASS、unresolved_blockers=[]。
- checked_artifact_hashes：完整且精确对应全部候选和上下文ID/hash，禁止沿用旧大纲审读。
- checks中event_order、duration、transitions、text_consistency、unknown_time_policy各一项，每项有decision=PASS和非空evidence。
- 任意端点未定时，另提供非空writer_restrictions，说明不能从未定字段补造哪些时间事实。

校验器只检查意见存在且绑定正确，不证明自然语言意见真实、独立或充分。null并不自动成为PASS；实际语义审读与独立性由宿主负责。

批准文件须包含：

```json
{
  "issuer": "由宿主权限策略指定的批准者",
  "decision": "APPROVED",
  "scope": "FREEZE_ONLY",
  "subject_sha256": "approval_subject函数计算的实际hash",
  "review_sha256": "语义审读文件的实际字节hash"
}
```

`approval_subject(manifest)`对除approval字段以外的完整manifest使用sort_keys、无空格分隔符及UTF-8序列化计算hash。排除批准文件自身的路径/hash链接，避免循环签名；批准文件字节仍由manifest.approval.sha256绑定。宿主生成批准记录后，不得原位修改候选、上下文、审读或版本。

## 冻结接入

包含P08/rhythm工件、已声明P08引用、requires_timing_approval=true或timing_gate字段的章节冻结输入，必须提供时间证据。未声明时间依赖的历史无时间夹具保留原行为；宿主不得故意删去真实时间依赖来走兼容分支。

章节冻结manifest增加：

```json
{
  "requires_timing_approval": true,
  "timing_gate": {
    "path": "相对章节manifest目录的时间门禁manifest.json",
    "sha256": "实际字节hash"
  }
}
```

阶段检查命令：

```powershell
python tools/timing_approval_gate.py --manifest <时间门禁manifest> --trusted-issuer <宿主批准者> --out <不存在的新证据回执>
python tools/finalize_chapter_plan.py --manifest <章节manifest> --trusted-timing-issuer <同一宿主批准者>
```

冻结器重新验证门禁，不接受仅凭旧PASS回执放行。当前章必须有P08、事件序列和大纲，其ID/hash与批准证据精确匹配；项目、版本和章节也必须匹配。冻结后的APPROVAL_MANIFEST包含timing_approval_gate证据及Writer限制，但不自动解除P17C、不激活Writer。

缺少证据、来源改变或版本错配均在创建冻结输出目录前BLOCKED；来源读取保存字节快照，不用后来的文件版本冒充原输入来源。旧来源不修改，新冻结目录不得覆盖。

## 后续职责

新时间证据通过只支持本次冻结。完整依赖图失效、相邻章节重建、索引激活、批准者身份认证及新P17C仍由宿主执行。本工具未提供通用整书调度器、记忆卡系统或批处理。
