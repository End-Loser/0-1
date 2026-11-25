# 通用消费者重建与受控替换

## 范围与当前结果

`tools/consumer_rebuild.py`实现索引消费者的重建顺序、候选来源复检和同一索引提交点的受控替换，不自行调用生成器或模型。请求遵守 `schemas/CONSUMER_REBUILD_SCHEMA.json`。它补齐消费者替换工程流程，不替代语义批准、真实任务执行、完整整书调度器或出版验收。

当前工作区pytest 1128项通过、37.50秒，unittest 45项通过、1.309秒，静态检查与compileall通过；相对1079基线新增49项。

独立源码/合同快照 `CONSUMER_REBUILD_CODE_20261105.zip` 已实际重提取：352条目（351项清单文件及SHA256SUMS.txt），hash零错配、无.env。包SHA256为 `bcf3aa61af3d7707cd52356f6b839af901266a8954ba600c6cb6f0758a812a2f`，提取目录为 `<本机临时目录>`。提取pytest 1128项通过、38.90秒；unittest 45项通过、1.345秒；静态与compileall通过。后验结果在打包后更新本报告/总表/验证JSON，不回写不可覆盖源码包。

实际第一章生产索引只读检查结果保存在 `CONSUMER_REBUILD_CURRENT_PLAN_20261105.json`：DEADSEAL当前没有INVALIDATED消费者，索引hash为 `903ccc15a25f8f43ee84a635a09cf7c9a375718959ae583b03428031e84d749e`，返回NO_INVALIDATED_CONSUMERS、NOT_GRANTED。本轮未在真实小说索引提交替换，未重检或交付正文。

实际预算重新核验仍为550次、7857092 token，剩余0次、242908 token；本轮新增付费调用0。不得以token余额继续发起模型请求。

## 准备与审读

`plan --root <runtime>`只读检查已登记的完整无环索引，按来源优先返回失效消费者；未选入的失效上游单独报告。没有失效消费者是合法空终态，不是Writer批准。旧工件缺少任务/项目/版本元数据时不能直接迁移为可提交成果。

宿主根据顺序实际重建各任务，分别保存新候选、完整输入包和来源绑定语义审读，再创建CONSUMER_REBUILD请求：

- 同一稳定artifact_id、新version、独立候选路径；旧源文件不修改。
- depends_on必须与已登记图完全相同，不能删依赖绕过门禁；需要改变设计依赖时应先走独立受控图修订。
- inputs保留原任务载荷。source_bindings放在宿主控制层，精确绑定完整上游闭包的路径、版本、hash、状态和扩展元数据，不默认把全部来源正文送给模型。
- 候选按当前TASK_REGISTRY的原合同与payload_schema复检，内外身份和status/decision枚举不得矛盾。
- 每项review的decision=ACCEPT_REBUILT_CONSUMER，绑定旧hash、新候选hash、输入包hash和完整上游绑定。source_fidelity、knowledge_isolation、time、obligations四项均为true；FROZEN候选另需freeze_authorized=true。
- 这些审读标记必须由受信宿主的真实验证流程签发；工具不生成它们。P13C知情隔离不因拥有全量来源闭包而取消。

BASE、Canon、P16变更、P18正文、核心世界设定和未分类源不由此工具替换。事实变更仍走原批准/Canon事务，正文仍走Writer租约与质量/出厂流程。历史版本号和历史索引引用过的路径不可再用；包括已退出当前索引的第一版。

## 新P17C

P17C另绑定p17c_manifest和execution。execution提供原始request、response及单项usage凭证的路径/hash；核对输入包、模型实际wire inputs、唯一完整stop响应的原始JSON内容与候选payload，以及任务/key/attempt/usage。不能在旧READY上补hash充当新执行。

模型输入仅包含新冻结批准、完整依赖摘要及其hash/版本，不能拿缺省空摘要放行。dependency_closure_manifest的required/resolved引用和每个来源版本/hash必须与待提交图的真实上游闭包完全一致。

提交前 `writer_dependency_gate.verify_projection` 使用待提交索引检查新P17C和新冻结绑定，返回PROJECTED_DEPENDENCY_VERIFIED_NOT_ACTIVE，绝不返回生产Writer认可的成功状态。普通 `verify` 始终检查实际索引；替换前它仍阻断旧图，提交后才可能通过。

投影验证不替代独立Canon门禁、生成器会话隔离、实际执行身份、全局调用账本和Writer租约。execution凭证真实性及usage来源唯一性由受信宿主核验，文件hash和issuer字符串不是签名认证。

## 预览、批准与提交

预览不读取approval、不创建锁或控制状态，不改变索引。它返回请求主体hash、候选索引object hash和validation_fingerprint；指纹绑定工具源码、合同、schema与任务注册表。宿主独立批准需记录decision=APPROVED、approved_by、project_id、tx_id，以及以上hash和expected_index_sha256。commit还要求每项reviewer在可信issuer名单内。

commit复用Canon事务的同root内核锁、CAS、flush/fsync、硬链接不可覆盖存档和一次index.json替换：

1. 重新复检完整请求、当前来源、候选、审读及独立批准。
2. 保存intent、原字节index.before.json和全部候选不可覆盖副本。
3. 复核来源和索引，原子切换已重建消费者的新版本，仍未重建的下游标INVALIDATED。
4. 同一次替换提交generation及transactions回执，保留未知元数据、原依赖图和其他工件。

索引引用获批候选的原路径，事务另存不可覆盖副本。宿主必须对这些候选路径执行只读权限/写入策略；后来篡改会被版本/hash门禁阻断，工具不提供对恶意同权限进程的文件防护。

同tx_id与同请求hash重放返回ALREADY_COMMITTED，不重复增加generation；不同内容占用同tx_id拒绝。旧索引或候选存档丢失/损坏时不偷偷再执行。替换前进程退出可复用暂存证据再提交；替换后退出从索引回执识别已提交。事务目录禁止逃逸root，报告输出必须在runtime之外且不覆盖已有文件。

## 验证与限制

49项新增回归覆盖顺序、局部重建、未选入失效上游、缺/重复ID、依赖删改、CAS、source/input/review绑定、候选合同、审读授权、核心源拒绝、历史版本/路径、元数据保留、共享锁冲突、新P17C原始执行与摘要、投影/实际门禁区分、source竞态、无网络/API、副作用边界和真实子进程os._exit恢复。

验证夹具中的生成、语义审读和P17C执行凭证为明确合成数据，证明绑定及工程行为，不证明真实小说已重新审读。Windows本机进程恢复不等于断电/网络盘/硬件故障保证；所有写入者必须遵守同一锁与权限策略。

第一章文学问题、原16项修稿处置及独立编辑仍未完成，第二章未激活，全部目标不能据此结项。
