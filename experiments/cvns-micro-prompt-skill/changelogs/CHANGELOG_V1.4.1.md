# V1.4.1 更新记录：Dataflow Accountability & Context De-dup

## 由 TR-20261104-MULTILINE 实测驱动
- 新增 `tools/preflight_task_call.py`，在 LLM 调用前拦截已注册实体重复审查、空语言样本、无批准 VoiceCard、无机制调用及未放行 Writer 等情况。未知条件返回 `NEEDS_HOST_CHECK`，不会自动批准。
- `TASK_LIFECYCLE_REGISTRY.csv` 增加 `preflight_rule`，逐任务记录调用条件、预期消费者和无消费者处置。
- 强化输入包投影：P17C 只读依赖闭包与审批回执；P18 的完整 Writer Pack 只出现一次；事件和审计任务仅接收必要切片。
- 增加输入包预算与重复子树审计；字节数仅作 token 代理，避免虚报节省量。
- 固化历史数据消费分类与输入包重复分析：73条call_log记录、74个子代理会话（1次P07C重试未记入日志）、74个JSON输出文件（其中1份为P19B确定性NOT_APPLICABLE记录）；作为后续回归案例。
- 记录5个 P14A 候选最终未形成批准事件序列的原因：一资产一事件造成同质事件、状态节点重复所有权、事件依赖循环。

## 不改变的原则
- R-06.2 的11条 BASE、P06全局矩阵、P09实体对扫描与既有流程不变。
- 不删除历史成果；只在调用前跳过无必要任务，或通过输入投影减少重复上下文。
- 60/73份模型输出文件具备下游ID引用，是可追踪引用证据，不等于语义采用；审计和合法终态不要求进入 Writer。按后续修正的preflight口径，5次调用可明确避免（3次P04B重复、1次P05C空输入、1次P19F缺VoiceCard）。
- 本版本是 Skill/宿主流程规范，不修改原 Python 项目源码。
