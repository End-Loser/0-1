# 单次调用运行卡

每次模型调用复制一张运行卡。不得在一轮里粘贴多个 task_id 的提示词。

```yaml
run_id: "RUN-<unique>"
task_id: "<one task id>"
prompt_file: "<exact registry path>"
prompt_version: "1.3.0"
output_contract_file: "<registry output_contract_file>"
input_refs:
  - artifact_id: "<id>"
    version: "<version>"
    scope_note: "<why this input is allowed>"
write_target: "<one new output artifact path>"
source_artifacts_read_only: true
expected_status: "DRAFT"
```

执行前核对：
1. 当前只加载一条执行信封、这一份 prompt、这一份输出结构合同和白名单输入。
2. 不把 registry 中的 predecessor/validators 或其他任务提示词注入当前模型。
3. 输出写入新文件；不得覆盖输入文件或 FROZEN 成果。
4. 未单独执行的验证必须标 `NOT_RUN`。
