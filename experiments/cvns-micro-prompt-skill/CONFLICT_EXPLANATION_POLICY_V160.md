# V1.6.0 冲突解释与受控修订

## 目标

把纵向变量线之间的不一致作为设计来源：先探索解释，生成有限、可推演的机制或剧情，再形成一致的计划。不是把真实矛盾换个名字直接交给Writer。

保留11维、单线完整调用、稀疏变化点、唯一状态机和冻结版本。新增工件是提案/审读/批准索引，不是第12维或第二套事实库。

## 分类与路由

- P10的五种枚举保持兼容。`MUTUALLY_EXCLUSIVE_ERROR`指当前已批准规则下不可同真，不断言所有合理的新设计都不可能。
- 互斥先进入P11A。明确不探索或解释失败，可走原P11最小修复；普通错误允许只有最小修复对照。
- PENDING_EVIDENCE默认挂起。只有宿主明确授权且绑定原始命题时，才允许探索解释；解释不能冒充缺失证据。
- P11A为一个冲突生成候选集，先复用既有规则，再考虑认知/身份/时间差异或新机制。
- P11B一次比较该冲突的全部候选，不按候选反复追加模型调用。
- P11B=PROPOSE_REVISION仅意味着值得提出修订；不是规则生效、跨线事实成立或文学质量证明。
- 修订后产生新的P09扫描和P10判定。新candidate_ref由新扫描规范化；原candidate_ref只作为来源保存在修订manifest中，不原位改名。
- 只有新的批准张力可进入P12；新的确认因果可进入P13B；NO_MATERIAL_RELATION不强制资产化。

## 合理性判据

P11B每个候选必须覆盖：命题覆盖、无暗改、题材边界、规则一致性、因果可执行性、作用边界、跨线影响、读者揭示公平性、持续叙事收益。

每项记录PASS/FAIL/UNKNOWN、理由、证据，并给出同机制作用于其他人物/时点的具体反例。FAIL/UNKNOWN不能获选。原始证据与候选推论应分清；解释必须说明哪些命题被保留、重解释或修订。

边界不等于惩罚配额。无代价的机制也可以由触发条件、范围、资源或反制限制；不能靠新增任意例外解决每个难题。复用机制有时比增加机制更有价值。

读者可以晚知道，角色不能提前使用未知事实；公平线索不等于提前解释全部规则。

## 调用与结构检查

先运行preflight，输出NEEDS_HOST_CHECK仍需宿主确认权限、消费者和版本。

```powershell
python tools/preflight_task_call.py --input-pack <pack.json>
python tools/conflict_resolution.py --candidates <P11A.json> --claims <host_claim_refs.json>
python tools/conflict_resolution.py --review <P11B.json> --candidate-set <P11A.json>
```

`jsonschema`是新增工具的依赖；缺失时阻断，不静默跳过schema检查。本轮环境已具备该依赖。

P11A/P11B模型提示词分别为554/532字符、各5项主动限制，均在新静态上限（3000字符/15项）内；预算放宽不等于允许合并生成、审计与批准。字段合同与本政策由宿主按需附送相关部分，不把整个项目或所有审读提示词注入模型。

## 批准与物化协议

1. P11A/P11B完成结构检查及必要语义审读。
2. 新机制用P01B，既有规则修订用P21A，相关机制接口用P01C/P21B。
3. P20分析获选候选的影响。`change_ref`使用`候选集artifact_id::hypothesis_id`；affected_artifacts、invalidated_reviews、retest_scope、unaffected_scope均使用ID数组。
4. 宿主按已批准修订创建新的BASE/机制工件；该工具不代替模型或作者生成这些内容。
5. 改动的BASE分别重做P07A/P07B/P07C；机制接口按已声明相关规则做复检。
6. 受影响关系重做P09/P10。对外提供标准化复检回执和完整的新工件。
7. 宿主/作者签发受保护的批准回执，绑定manifest和全部控制文件hash。
8. `conflict_resolution.py`检查并物化新目录。全程不覆盖任何原文件。
9. 宿主激活新版本索引、使受影响下游与旧审读失效；之后才能消费。冻结包已写出，不等于宿主已完成激活。

首轮物化器支持BASE_LINE与WORLD_RULE。DERIVED、实体、场馆或全局日历等复杂修订仍走既有宿主修订流程；不得伪装成已被本工具自动处理。

### manifest必需字段

```json
{
  "resolution_id": "RESOLUTION-001",
  "source_candidate_ref": "SCAN-V1::C1",
  "selected_hypothesis_id": "H1",
  "candidates_path": "P11A.json",
  "review_path": "P11B.json",
  "impact_path": "P20.json",
  "approval_path": "host-approval.json",
  "scan_path": "P09-V2.json",
  "classification_path": "P10-V2.json",
  "sources": [],
  "claim_bindings": [],
  "revisions": [],
  "rechecks": [],
  "dependency_closure_complete": true
}
```

空数组只展示结构，不是可批准的示例。完整可运行合成案例见`tests/test_v160_conflict_resolution.py`。

- sources条目：artifact_id/path/sha256，读取已批准的原P10及原始事实快照。
- claim_bindings条目：claim_ref/artifact_id/json_pointer，指向实际原始文本，例如`/content/change_nodes/0/state`。命题集合必须等于P11A的source_claim_refs，且属于原P10 evidence_refs。
- revisions条目：artifact_id/role/path/sha256/source_artifact_id。已有BASE必须指向旧版本并保持实体/维度/线ID；新规则可用null来源。
- 新BASE文件使用Artifact Envelope，content是完整BASE_LINE_SCHEMA；机制content是MECHANISM_RULE_SCHEMA。状态为VALIDATED/FROZEN。
- rechecks条目：task_id/path/sha256。回执包含artifact_id/task_id/status/decision/checked_artifact_hashes，哈希精确绑定实际新版本。
- BASE三个回执decision=PASS。规则接口回执额外有pair_rule_refs；无相关规则时显式NOT_APPLICABLE、空pair_rule_refs，不发空模型调用。
- 新P09使用source_refs引用新修订artifact IDs，并包含新candidate_local_id；P10保留规范化新candidate_ref及原合同全部条件字段。
- P20的影响清单会进入包的invalidated_artifact_ids；原文件不被删除或改成SUPERSEDED，索引失效由宿主执行。

### 宿主批准回执

包含decision=APPROVED、manifest_sha256、control_hashes。manifest_sha256是`manifest_digest()`对完整manifest的规范JSON哈希；control_hashes为candidates/review/impact/scan/classification五个文件的SHA-256。

批准文件必须由宿主/作者签发，模型无写权限。工具验证绑定关系，不实现身份认证或数字签名；不能把模型自行写出的APPROVED当成真实授权。

```powershell
python tools/conflict_resolution.py --manifest <manifest.json>
python tools/conflict_resolution.py --manifest <manifest.json> --out-dir <new-frozen-directory>
```

物化采用新目录、协作锁、暂存及最后发布；文件逐字节复制，保留完整字段。输出存在则拒绝覆盖，失败不发布冻结包。

## 下游消费

P12必须接收已批准`relationship_decision=TENSION_ASSET_CANDIDATE`。

解释来源额外传宿主元数据：

- resolution_origin_candidate_ref；
- resolution_manifest_path及resolution_manifest_sha256；
- resolution_activation回执：decision=ACTIVATED、resolution_id、manifest_sha256、active_revision_ids、invalidated_artifact_ids。

preflight验证冻结包及所有成员hash、新判定一致性，以及激活回执覆盖。激活回执是受信宿主记录，不是工具独立证明文件系统索引已切换。

P13B、P17C及Writer同样通过依赖闭包解析新来源。宿主在输入包顶层附`resolution_dependencies`数组，每项包含以上四组元数据和包内新的`relationship_decision`；通用preflight核验全部条目，确认因果分支不例外。数组由可信索引递归生成，不得隐瞒或移除来源。工具无法从无标记的载荷中独立发现遗漏的依赖。

修订manifest和激活门禁留在宿主，模型只读当前批准的事实、机制和必要表达材料。未选择的解释、旧矛盾与候选长报告不得进入Writer。

宿主须沿依赖图检查所有受影响实体对/消费者。工具只检查manifest声明的来源和复检；不能独立证明整个2000章依赖图完整。

## 验证边界

测试覆盖候选完整性、明确修复退路、UNKNOWN阻断、来源/hash绑定、命题实际定位、复检、新P09/P10、无覆盖物化及下游激活门禁。

这证明流程约束可执行，不证明模型解释合理、剧情新颖、出版质量提升或2000章并行成立。下一阶段需用异类可解释冲突与普通错误做实际模型和成稿对照。
