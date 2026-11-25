# 成果封装与版本保护

每次调用输出一份独立成果，最小字段：

```yaml
artifact_id: "<unique>"
task_id: "<registered task id>"
version: "V1"
status: "DRAFT | REVIEW | VALIDATED | FROZEN | SUPERSEDED | REJECTED"
read_refs: ["<input artifact id@version>"]
write_target: "<this task's own artifact>"
content: {}
validation: "NOT_RUN | PASS | FAIL | PASS_WITH_RISK"
```

## 权限规则
- 所有输入只读；任务只写自己的输出文件。
- FROZEN 成果只能读取、引用或合成，不允许原位修改；宿主调度器应拒绝对冻结文件的写入。
- 修改必须创建新版本并保留旧版本；先执行依赖影响分析，再重验受影响成果。
- 提示词只能提出修改建议；真实文件权限、版本号和冻结状态应由调度器/文件系统强制执行。
- 手动在聊天窗口执行时，冻结是流程约定而非技术权限；使用者必须自行另存版本，不能把文字提醒误认为文件系统锁。
