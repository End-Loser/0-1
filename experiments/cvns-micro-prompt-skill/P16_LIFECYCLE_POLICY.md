# P16C生命周期受控合同

## 范围与职责

`tools/line_lifecycle.py`定义一条已登记DERIVED线的终止、迁移与升级边界；`canon_context.validate_p16`只读检查；`index_transaction`的LIFECYCLE_APPLY通过独立语义审读与宿主批准提交。没有模型调用、任意patch、旧线删除、BASE改写或自动Writer激活。

P16C仍只判断一个源线的一个变化。迁移/升级关闭源线并追加一个新ID后继，是同一条实体变量线的连续版本，不是同时修订其他已登记线。不能把迁移和升级混为一次变化，也不能借P16A登记绕过生命周期合同。

故事合理性仍由受信宿主审读：代码不能证明一句解释真实成立，issuer字符串也不是身份认证。原任务目标与单一事实源不变。

## 决策载荷

`schemas/LINE_LIFECYCLE_DECISION_SCHEMA.json`要求：

- line_id：本次inputs.line_id对应、实际检索到的DERIVED线；BASE只读，不接受生命周期变更。
- decision：TERMINATE、TRANSFER或UPGRADE。没有变化应由可信宿主将lifecycle_change_required设false，不调用P16C，不提交空事务。
- effective_chapter、effective_seq：当前获批章中的生效位置，非负seq不能用布尔值；触发事件/节拍、动作顺序是否确实支持该位置由语义审读核验。
- previous_status固定active；new_status依次为terminated、transferred、upgraded，不接受任意字符串。
- cause_ref引用当章冻结大纲中的事件或节拍；dependency_refs无重复且包含源线及cause，所有引用都在实际批准输入中。
- reason非空；successor_line必须显式为null或完整新DERIVED对象。

外层仍是宿主工件封装，具备非空artifact_id、对应task、project、当前Canon版本、当前章以及VALIDATED/FROZEN状态。模型只输出载荷，不能自产批准后直接提交。

## 状态与时间边界

源线必须尚无lifecycle闭合记录，且valid_to为null，或恰好等于本次生效章。已有其他终止章不自动提前、延期或清除；已结束的线不能重复终止/迁移/升级。

全部旧节点必须严格早于生效位置。未来节点或同位置节点存在时阻断，不裁掉冲突节点来让提案通过。需要修订原计划时走正常来源审读、批准、重新冻结流程。

终止仅在新Canon快照中将valid_to设为生效章，并追加lifecycle元数据。旧文件、全部节点、cause、purpose、别名与未知扩展不变。源线始终留在登记表与审计历史。

本合同处于P16计划层：lifecycle.causal_status始终proposed，记录execution_status=PLANNED_NOT_SHIPPED。terminated/transferred/upgraded描述获批计划中的生命周期结果，不是正文已执行或角色已知的证明。提交不能自动提升节点因果状态；实际履行仍需出厂和相应证据门禁。

已有章级半开区间不原位重解释。闭合章仍向Writer提供完整线，lifecycle.effective_seq给出章内闭合位置。同章出生和闭合仅在显式lifecycle边界严格晚于全部节点时合法；没有该证据的valid_from=valid_to依然非法。

## 迁移与升级

后继必须同时满足：

1. 新ID未在完整BASE/DERIVED注册表中出现；predecessor_line_id等于源线ID。
2. subject_ref与源线中已批准的稳定实体/物件身份相同。源线缺该身份时不能由模型猜测或补一个值后迁移，必须先走来源审定；终止本身不要求subject_ref。
3. dimension不变；valid_from为当章、valid_to为null，只含一个当章开头节点，节点seq等于闭合位置，causal_status保持proposed。
4. 新cause引用同一获批触发事件/节拍；purpose中的引用均有真实来源。
5. 不得带另一个lifecycle记录，不得夹带未来节点或提前将新状态确认为历史事实。

TRANSFER要求scope改变、状态文本与源线最后节点一致，不能借交接同时升级。UPGRADE要求scope不变、状态确有差异并提供非空state_delta；是否符合世界规则、成本和能力边界必须审读。

完整Canon中，同一subject_ref×dimension的生效窗口不能重叠。检查包括尚未检索的其他scope及未来线，不只看当前相关视图。后继链须前驱/后继互相绑定、边界相接；断链、孤立前驱、分叉、假身份或额外当前事实均阻断检索。

## 义务与消费者

所有ACK对象原样保留，不执行、取消、延期或重定向。源线仍被pre-registered ACK引用时，语义审读逐项说明如何保留义务；涵盖尚未到期、通常未放进generator上下文的未来ACK。出厂履行仍需独立ACK_EXECUTE合同。

本章闭合线及后继、相关实体/world线、显式必需线和到期ACK引用线的连续后继链共同保护。跨scope迁移不能让可选预算裁剪只留下旧持有人。预算不足整包阻断，不砍半条实体链。

Writer后章如因ACK需要保留已闭合旧线，同时带入可用后继；line_lifecycle_context将历史线与本章闭合线单列，不把两个完整历史对象当同时有效的当前状态。audit保留全部DERIVED与ACK。

## 来源、审读和提交

LIFECYCLE_APPLY使用公共事务字段，另需candidate、input_pack、registration_context和lifecycle_review。registration_context沿用已有字段名，语义为当前generator来源manifest；不得混入ACK_EXECUTE的prose/review/ship/ack_id字段。

实际Canon/完整BASE/冻结大纲及其依赖闭包必须与受保护当前索引一致。生命周期请求的base_lines必须覆盖Canon上游中全部BASE工件，不能仅因ACK未引用BASE就省略完整属性线。输入canon_context和chapter_outline需匹配重新编译的完整对象hash；候选项目、版本、章号及task保持一致。

独立lifecycle_review需decision=PASS、status=VALIDATED/FROZEN、reviewed_by、project_id、chapter，以及：

- candidate_object_sha256、input_pack_object_sha256、context_object_sha256、previous_line_object_sha256。
- blocking_findings为空。
- transition_checks恰好包含cause、continuity、time、base_consistency、single_fact_source、ack_obligations、downstream_impact；每项PASS和非空basis。这要求实际逐项审读，不意味着代码认证理由充分。
- ack_evidence逐条包含ack_id、ack_object_sha256、decision=PRESERVE_PENDING与非空basis；无重复、漏项或假履行。没有相关待履行ACK时也显式给空数组。

只读preview核验上述合同、真实来源和完整历史，返回候选hash及请求主体hash，但不授予激活。受信宿主另行签发绑定请求主体、候选和旧索引hash的APPROVED记录，不能把模型PASS改为宿主批准。

commit还要求reviewed_by和approved_by都在宿主提供的可信issuer配置中；可信配置及身份认证由宿主负责。使用已有同root内核锁、CAS、不可覆盖旧索引/新Canon快照和一次index.json原子替换。lifecycle_records保留候选、输入、审读、来源闭包hash、前对象hash、边界、后继ID和未取消ACK清单。

新Canon、generation、事务幂等回执及下游INVALIDATED共用提交点。历史依据不变成新Canon的实时大纲上游边，不产生“Canon消费大纲、大纲消费Canon”的环。受影响大纲、审读、冻结包和P17C需正常重建/复核，不能只换hash。

重放只认同一最终请求及可信批准/审读者，已提交快照和旧索引存档仍需完好；不重复变化或增加generation。快照发布前后崩溃按现有事务恢复规则处理。Windows断电、恶意同权限写入及网络文件系统仍不在已证明范围内。

## 验证限制

离线测试和合成来源能证明宿主合同、历史保留和提交行为，不证明真实故事的因果、义务履行、文学效果或出版标准。不会给第一章虚构P16C调用；第一章尚无注册线时不制造生命周期任务来消耗API预算。
