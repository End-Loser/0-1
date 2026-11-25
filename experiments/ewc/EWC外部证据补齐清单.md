# EWC外部证据补齐清单

日期：2026-11-18。用于解除完整验收的必要外部依赖，不是采购单。
以下请求尚未发给厂商，不表示取得授权或性能承诺。
可由厂商授权模型，或具有条件、不确定度和可追溯原始数据的实测证据补齐；
典型规格表、CAD外形和未标注条件的曲线不能直接替代完整动态模型。

## 1. 收发与反馈：G4 / R01～R06、R10～R14

向ADI请求ADL5375和ADL5380适用的可执行、可合法使用动态模型：

- 明确型号后缀、模型版本、求解器及许可，不改库参数或逆向二进制。
- 覆盖2.5～3.5 GHz待核查频带、本振驱动和分配负载。
- 有符号基带、DC偏置/共模、输入输出阻抗、压缩/失真、建立与过载恢复。
- 说明噪声、温度、供电、负载及工艺覆盖范围，以及模型与测量误差。
- 明确C比较器的真实输出能否驱动反相抵消路径，所需驱动、电平转换与等待。

仅提供稳态S参数或链路预算工具时，仍保留瞬态反馈和符号恢复缺项。
无法提供模型时，需原始幅相/瞬态/噪声数据及可识别模型的误差界，
不能把拟合后的典型模型当作原厂全条件保证。

## 2. 实际接头：G3 / R07、R09、P04

候选Amphenol RF 132139及901-9211均未达到fixture准入。
两候选各8项完整全波输入缺项，详见
[实际SMA核查](射频端口验证/实际接头核查/实际SMA馈通模型可用性报告.md)。

需当前修订内部剖面/多实体电气CAD、所有导体/介质/空隙分域、
有限导电率和磁性、镀层厚度与粗糙度、PTFE复介电参数、
内部/安装公差、配合及电气参考面、复数S参数和去嵌入条件、不确定度。
901-9211网页黄铜与图纸不锈钢/铍铜及耐久冲突须先澄清。
外形STEP不等于电气模型；最高频率不等于装入腔体后的频响保证。

还需项目明确腔壁孔、壁厚、安装接触/扭矩、探针长度/位置、焊接和回流路径。
本清单不替用户冻结两款之一，不绕过403或登录限制。

## 3. 材料与制造：G2 / R08、P03～P05

- Rogers RT/duroid 5880指定板厚、批次、铜箔处理面与粗糙度，
  2.5～3.5 GHz复介电参数及温度范围、测试方法和不确定度。
- C11000实际材料电导率/温度、表面处理、壁厚、接缝接触和装配公差。
- 介质/金属因果宽带模型或足以验证模型的频域数据，不把10 GHz典型值改成3 GHz保证。
- 项目实际PCB截面、支撑介质、连接与屏蔽结构，明确是否有接缝/开孔/阻尼。

## 4. 调理、采集与供能：G4～G5 / R12～R15、C01～C07、P01～P06

THS4551和TLV3501已有原厂典型模型，但不是全温、噪声或启动验收。
需跨求解器/原始实测对照及实际接收源阻抗、输入幅度、参考和输出负载。
ADS8866时序合同已有独立复核，仍缺模拟驱动/参考、残留、噪声和硬件边沿。
细调/DAC必须给真实量化、幅相传递、负载建立、毛刺及供电。
本振、驱动、检测、判决、反馈、控制、参考、稳压和必要主机均计入供能边界。

## 5. 收件准入

每份证据记录：提供方、型号/批次/修订、日期、原件SHA-256、授权、
有效频段/温度/幅度/负载、测试参考面、典型或保证性质及误差/不确定度。
附件可放入本工作区后再核查；受限文件只在授权环境使用，不公开重分发。
证据不足的条目保持未通过。G6一位及G7四位依赖以上条件，
不能用代理模型、静态真值表或软件测试数代替真实联合验收。

## 6. 供电分层后的支持证据

双轨分层九配置已全部取得完整波形，八个真实轨接口失败。
另七次单稳压作用域对照复现原成功网表，但六个电阻夹具失败；
三个顶层/封装配对全列相同，不能把作用域或6 V输入认定为根因。

需原厂支持PSpice 17.2-2016 S050或经确认等价环境下的TPS7A20参考工程，
含电阻/电流负载、使能初态与启停、输出开关控制、负载反向电流适用性，
以及原始工作点、完整波形和模型版本。当前约1 MΩ表观输出耦合仅为诊断。
同时需实际使能/信号源供能和启动安全门控；理想控制关系没有测量耗能。
真实缓冲负轨超范围样本不用于功能性能验收。

还需细步复核五组外部电容积分失败；不能把末窗口KCL闭合视为全时间通过。
见[供电分层报告](器件验证/供电分层联接/供电分层联接报告.md)及
[作用域对照报告](器件验证/稳压作用域对照/稳压作用域对照报告.md)。
此补充不表示已发出申请、取得授权或更改BOM，G4/G5和G6/G7仍未关闭。

## 7. 目标频带与供电细步续查

两个新增Rogers官方网页只保存工具转换文本；
共用频率表头不证明5880的3 GHz适用，5880LZ不是5880。
指定批次/板厚/铜箔复介电和不确定度、原始方法仍缺，不更换材料路线。
见[材料续查报告](材料验证/公开频带续查/5880目标频带公开续查报告.md)。

上节五组供电电容积分失败已另档50/20 ns共十次核对：
8完整、2截断；3条记账通过，但没有一组两细步都通过，接口全失败。
两20 ns约104.016 μs未到2 ms，返回码0也不接受；
buffer_real_rails两细步输出电容积分仍约1.12%，缓冲负轨超范围保持。
还需受支持原厂工程与完整时间步配对，实际供能/启动证据缺项不撤销。
见[供电细步报告](器件验证/供电电容细步复核/供电电容细时间步验证报告.md)。
没有发出厂商申请、改库、接受新许可或绕安全策略，七包仍0/7。

## 8. 完整LDO输出通路补充

完整实例观测已补齐：原失败电阻夹具精确零控制下输出通路1 MΩ；
原成功电流夹具末窗口约±1e-16 V控制下高低阻交替，原输出子项通过
不能支持开关边界、启动或全供电可靠性结论。原波形列逐值差0，
模型和求解器未改，原生locdata仍未导出，showmod未匹配。
后续支持环境需提供零控制端点、舍入附近正负控制、原负载/使能
历史及完整内部端点/电流原件对照，而非仅给一个末窗口输出成功。
见[完整稳压内部状态报告](器件验证/完整稳压内部状态复核/完整稳压内部状态复核报告.md)。
本节是证据请求的补强，不代表已申请、取得授权、编译修复或实物验收。

## 9. DAC原厂参考成功证据

SBAM485已有两份PSpice文本日志均失败/中止；包内`.dat`不作完成证明。
需原厂或适用支持环境提供原库完整成功算例，绑定执行库/求解器SHA、
15 ms声明末时刻、实际码/参考/负载和原始波形，并保留失败对照。
原厂全16位同STIM的100 nF夹具不能直接当作实际SPI、
2 kΩ/200 pF建立或EWC参考供电联接。
本轮只归档既有失败及适用范围，没有发送申请或替换原工程。
详见[DAC原厂工程报告](校准验证/DAC80501原厂工程复核/DAC80501原厂工程失败与适用范围报告.md)。

## 10. 原铜箔表澄清后的材料缺项

已有S03原表图像已确认五条列有5880的普通铜箔Sq/厚度单位为微米，
介质侧/顶侧及典型多批次均值性质已单独记录，不再使用文本毫米误读。
仍需指定铜箔/板厚/批次及处理侧、实际截面、目标频带幅相损耗、
形貌/温度/电导率和误差；单一Sq不能识别Huray形貌或粗糙度相位模型。
还需澄清表1/表2四分之一盎司9/8 μm差异与体电阻率印刷单位，
本轮没有据此修改C11000腔壁参数、改BOM或发出厂商申请。
见[原铜箔表复核](材料验证/铜箔原表复核/铜箔原表单位与适用范围复核报告.md)。
这是已有官方来源的有效补强，不是G2/G3关闭或G6/G7进入证明。

## 11. ADL5380共模公开表与DC负载

已有Rev. C原件第3页表1开路共模写VS−1.2 V，
第21页表4开路写约3.2 V，5 V名义供电下两种表述尚未协调。
两页原图均已查看，需厂商澄清修订/工况、实际共模范围及ADJ传递，
不能默认选3.2 V或把近似表当温度/批次保证。
每脚12 mA、ENBL低245 mA/高145 mA都在Typ列；
需实际共模与差模负载能力、供电及噪声/失真条件，尤其真实FDA的DC吸收电流。
原固定链四次DC调用无有效向量；此前资源保护未调用的20项已另档结束，
共24项稳定归档，但原ngspice exe缺失使正式全批复核返回1，未恢复或跳过检查。
既有部分快照只有3.2 V/111一份单行有限导出，
但幅度/共模/REF/DC KCL失败，REF ESR电流约26.967 mA，不是合格DC点。
尚需真实驱动条件、实际共模/差模负载与稳定工作点/动态证据。
原瞬态末窗口或Transient op回退导出不能作为严格DC平衡或ADL动态模型补齐。
没有发送ADL厂商申请或修改BOM，G4/G5及G6/G7仍未关闭。
见[ADL原表与DC留出](器件验证/ADL共模接口留出/ADL5380共模原表与固定链DC留出报告.md)。
新增[DC续行部分验收](器件验证/ADL共模接口留出_未执行20项/DC续行部分验收报告.md)
保留原失败及续行证据；已结束执行不等于完整原件复核通过。
用户授权准备PSpice for TI，已亲自提交申请，官方确认页显示待审。
尚无批准下载/软件许可完成/安装/83字段授权能力的证据；
不代接受许可或绕过安全、审批、人机验证及探测限制。
见[PSpice安装准备与边界](PSpice%20for%20TI安装准备与证据边界.md)及阶段报告第38节。

## 12. 结构性能力缺口（非数据缺口）

2026-11-18 阶段复核（`EWC全目标阶段复核结果.json`，73 个 job）有 12 个非零返回码。
逐条打开各 job 日志后，失败原因不是“缺一个测量值”，而是“验证器强制要求一个已被操作系统清理的
`%TEMP%` 原始捕获”，或“验证器每次运行都会重写自己的证据文件”。按台账第 7 条登记为能力缺口；
按第 4、5 条不删除、不改名、不重跑成“通过”。12 个 job 的 `source_revision_unchanged` 全为 `True`，
可排除“源码被改动导致失败”。

| job（返回码 1） | 异常 | 落点 | 被钉死的路径 | `manifest.unrecoverable` 摘要 |
|---|---|---|---|---|
| `sma` | `ValueError: 严格复核缺少TEMP响应` | `射频端口验证/实际接头核查/verify_sources.py:77`（`--require-temp`） | `ewc-sma-audit-lb263e49\S1..S5` | `amphenol-http-403` 5 条：`60019606…`(385 B)、`952a4d26…`(419 B)、`44460157…`(419 B)、`a3e26d10…`(419 B)、`fe7d735f…`(419 B) |
| `public_sources` | `FileNotFoundError` | 直接读原始响应，无回退解析 | `ewc-public-models-4eiy0i3s\ref_page.raw` | `cf94f646fc037063359c967a1616e8f40a5f600c42cede499ddd764a7f7e1415`，615,263 B |
| `linked_initial` / `linked_scoped` / `linked_gear` / `linked_nodeset` | `FileNotFoundError`（4 个 job 同一路径） | 直接读原始响应，无回退解析 | `ewc-buffer-official-elyzuldr\buffer_page.raw` | `582f026544da5655a1e50161cc31cf359e5024bfeb5321807792b0d4be3a0a07`（`live-vendor-page`） |
| `compatibility_build` / `compatibility_buildtests` | `FileNotFoundError`；后者 `FAILED (errors=1)` 为同源异常的测试包装 | 读工具链二进制，无回退 | `ewc-build47\msys64\usr\bin\bash.exe` | `c33c40ebb006cdb5f0f651672d577fdf7c785f1141ba7ccdff9d6a1e32001417`（本机无字节副本） |
| `dac_specs` / `dac_tests` | `FileNotFoundError`；后者 `FAILED (errors=5)` 为同源异常的测试包装 | 直接读原始响应，无回退解析 | `ewc-dac80501-official-74n3kb5l\product.html` | `39ec2ed9857ab7c3892c66d79436ad276f77bdfc8a49bb38735c5b1f925de0f4`，342,574 B |
| `sealed_original` | `FileNotFoundError` | 直接读原始响应，无回退解析 | `ewc-dc-official-82hpty56\ths4551_page.html` | `6781304499304292a889b9fc16406ebd612757c326bc0a9f9ca11bb105752824`（`live-vendor-page`） |
| `bias_decoupling` | `AssertionError: …\真实参考供电联接\batch_audit.json` | `器件验证/参考节点去耦/verify_bias_decoupling.py:78` | 项目内派生文件（不依赖 `%TEMP%`） | 见 12.2 |

### 12.1 A 类：验证器强制要求已登记 `unrecoverable` 的 `%TEMP%` 原始捕获

上表前 11 个 job 属此类。它们的 `manifest.unrecoverable` 条目都以明确理由登记为不可复原
（`live TI product page; re-fetch cannot reproduce the archived bytes`；
`Amphenol RF part page rejected the capture with HTTP 403`；
`build-tool binary from a deleted toolchain`），但验证器仍把该路径当作存在前提：

- `verify_sources.py` 要求 5 份 403 响应原件在 `%TEMP%` 内，并在 `:77`
  `ensure(not require_temp or not temp_missing, "严格复核缺少TEMP响应")`；
  驱动方传入 `--require-temp`，于是 5 条 `missing` 直接判失败。
- 其余 6 个锚点由各自验证器在读取阶段直接 `open()`，无 `exists()` 分支、无项目内副本回退，
  缓存被清理后一律抛 `FileNotFoundError`。

结论：这是验证器的设计缺陷（把已判定不可复原的外部锚点设为硬前提），不是“数据还没补”，
也不是“结论不可信”。既有的 `collection.json` / `verification.json` 快照仍可复核当时的
403 状态、字节数和哈希，但无法让这批 job 在缓存缺失时重新返回 0。

### 12.2 B 类：验证器从 `%TEMP%` 缓存重新派生并原地覆写自身证据（`bias_decoupling`）

`器件验证/真实参考供电联接/audit_batches.py` 对同一份封存输入产出过三个不同摘要，
差别只在 `%TEMP%` 缓存状态：

```
810621ab81f25f79e93ff1576b4487721f52676e3a4de256b967b88bf5792d5c  ← 封存权威值
246309423ef48912396b7d0369dac42aa96ba8632fca8d664b3d8e2ea70330ed  ← 缓存清理后的重现值
ba1aa793d55bc33190964583f7ea99996f51d8ae4e45be8392e9c61c44a884ba  ← 放回 1 个 wave.txt 后的重跑值
```

- `810621ab…` 被 11 个 `openEMS验证/阶段复核封存/stage_*.json` 与
  `器件验证/参考节点去耦/summary.json:1069` 共同钉住，是本项目的权威摘要。
- 成因在 `audit_batches.py:49`：`partial = Path(record["cache"]) / run["id"] / "wave.txt"`，
  `retained_partial` 是否存在取决于 `%TEMP%` 缓存；`:65` 又把结果原地写回
  `batch_audit.json`。缓存被 OS 清理后，同一脚本对同一封存输入必然产出不同字节。
- 下游 `verify_bias_decoupling.py:78` 校验 `summary.json` 的 355 条摘要，
  实测唯一 `BAD` 就是这条 `batch_audit.json`（记录 `810621ab…`，现值 `24630942…`）。
  该现值已于 2026-11-18 由封存包字节复原为 `810621ab…`，处置见 §12.4。

“缺失的 `wave.txt` 是否可确定性复原”已用同一 `ngspice_con.exe`
（`22d5cae2bd32b2e39157a8d27bf457122f68285b72a9ebefdf41551b628233ab`）实跑 5 个冻结网表判定：

```
linked_buffered_bias  rc 0  log_equal False  wave 有  (16,563,771 B, 24,758 行, 末时刻 208.07 ns)
linked_real_rails     rc 0  log_equal True   wave 无
linked_real_rails_5ps rc 0  log_equal True   wave 无
linked_ratio_0p1pct   rc 0  log_equal True   wave 无
linked_startup        rc 0  log_equal True   wave 无
```

- 后四个配置“无波形”是确定性可复现的真实失败（日志逐字节相同，确实不产出 `wave.txt`）。
- 唯一产出波形的 `linked_buffered_bias` 不可逐字节复现：重跑日志与封存日志在
  第 1071 行首次分叉（`Reference value : 4.16346e-09` 对 `4.25346e-09`，约 2.2%），
  收敛轨迹随后发散；重跑只到 208 ns、24,758 行，封存那次有 100,308 行。
  因此 `batch_audit.json` 的 `810621ab…` 不能由任何重跑重建。

### 12.3 附注：验证器覆写/派生自身证据的实例

同一模式在本项目至少出现在以下位置，均属验证器自身缺陷，不改写为数据结论：

- `器件验证/参考反馈电流复核/verify_feedback_audit.py:114-124` 从 `%TEMP%` 缓存读
  `wave.txt` 生成 `partial_waveform`，`:137` 原地写 `verification.json`。
- `器件验证/真实参考供电联接/audit_batches.py:49`（`%TEMP%` 缓存依赖）与 `:65`（原地覆写）。
- `openEMS验证/run_goal_stage_verification.py:508-509` 原地覆写
  `EWC全目标阶段复核结果.json`；重跑前需先封存，否则下游钉住的摘要必然失配。

处置边界：以上缺口不新增“通过”，也不撤销既有 A/B 级结论；`full_system_gates_closed`
仍为 0/7，`goal_complete` 仍为 `false`。要真正消解 A 类缺口，只能在有网络/授权条件时
重新取得可归档的原件并另建新目录，而不是恢复或伪造旧 `%TEMP%` 路径。

`原始资料/manifest.json` 的“1 字节往返差异”已查清：文件为 CRLF + 行尾换行，122,914 B，
SHA-256 `f4e7596a3e6689182edc0cb2f7940e2ee78fe5294bd7726fe169d9df394c7963`；
转为 LF 后为 120,443 字符，而 `json.dumps(indent=2)` 重排为 120,442 字符。
唯一差异在偏移 22,736：文件写作 `}\n,\n`，`json.dumps` 写作 `},\n`。内容是合法 JSON、
语义完全相同，且本仓库没有任何校验器钉住该摘要，故不重写该文件，仅在写入时要求保留原字节。

### 12.4 处置结果（2026-11-18）

§12 的 12 个非零返回码 job 已全部处置为 `returncode=0`；处置只改验证器前提与取证路径，
不新增任何物理“通过”。

**A 类未钉 5 条（旧输出无下游 pin，原地改造）**

| job | 脚本 | 新增字段（实测） |
|---|---|---|
| `sma` / `sma_tests` | `射频端口验证/实际接头核查/verify_sources.py` | `temp_responses_registered_unrecoverable=[S1..S5]`、`environment_missing=true` |
| `compatibility_build` / `compatibility_buildtests` | `器件验证/开关兼容性源码复核/verify_build_evidence.py` | `environment_missing=true`、7 条 `unverified_anchors` |
| `dac_specs` / `dac_tests` | `校准验证/DAC80501适用性/verify_dac.py` | `environment_missing=true`、`product.html` 与 `datasheet_layout.txt` 明细 |

放行条件：缺失锚点的 sha256 必须已在 `原始资料/manifest.json` 的 `unrecoverable` 中登记；
未登记的缺失或字节不符仍硬失败。`datasheet_layout.txt` 路径存在但字节不同，按三态模板记
`present_bytes_sha256` 与 `note`。

**A 类被钉 6 条（v2 并行谱系，旧脚本/旧输出字节不动）**

| job | 新脚本 | 新输出 |
|---|---|---|
| `public_sources` | `器件验证/公开模型补齐/verify_public_sources_v2.py` | `source_verification_env_missing.json` |
| `linked_initial` | `器件验证/真实参考供电联接/verify_linked_reference_v2.py` | `verification_v2.json` |
| `linked_scoped` | `.../verify_scoped_linkage_v2.py` | `scoped/verification_v2.json` |
| `linked_gear` | `.../verify_gear_linkage_v2.py` | `gear/verification_v2.json` |
| `linked_nodeset` | `.../verify_nodeset_linkage_v2.py` | `nodeset/verification_v2.json` |
| `sealed_original` | `器件验证/调理时间步收敛/verify_original_batch_v2.py` | 复核临时目录的 `original_batch_verification.json` |

- `public_sources` v2：5 份下载与 36 个 ZIP 成员实校，8 条已登记缺失放行，
  `whole_chain_passed=false` 保持。
- `linked_*` v2：全部 rail/R-C KCL、边沿 cross、delay、`checks==saved` 断言逐字保留；
  官方源 3/4 实校，仅 `buffer_page.raw`（`582f0265…`）放行；scoped/gear/nodeset 各自的
  等价/`.options`/`.nodeset` 断言保留。
- `sealed_original` v2：46 条封存件、83 条严格 pin、61 条原器件文件摘要全部硬断言保留；
  仅 `ths4551_page.html`（`67813044…`）放行；8 个 run 独立重算，`whole_chain_passed=false` 保持。

**B 类（`bias_decoupling`）**

- 漂移复现值 `ba1aa793…`（20,261 B）逐字节保留为负证据：
  `器件验证/真实参考供电联接/批次复核漂移负证据_20261118/batch_audit.ba1aa793_负证据.json`，
  同目录 `provenance.json` 记录成因与权威来源。
- `batch_audit.json` 由封存包 `EWC_project_full_20261118_100850.zip` 同名路径字节复原为
  `810621ab81f25f79e93ff1576b4487721f52676e3a4de256b967b88bf5792d5c`（20,554 B）。
- `audit_batches_v2.py` 不再原地覆写：结果写 `batch_audit_runs.json`（`wrote_sealed_batch_audit=false`），
  实测 `sealed batch_audit.json intact: True`。统一入口 `linked_audit` job 已切换到 v2。

**复核与封存**

- `openEMS验证/run_goal_stage_verification.py` 已切换 6 条 v2 job、`audit_batches_v2.py` 与
  `verify_original_batch_v2.py`，evidence 清单新增 6 条 v2 输出。
- 2026-11-18 重跑：**74/74 job `returncode=0`**，`executable_checks_passed=true`、
  `stage_checks_complete=true`、`runner_revision_unchanged=true`。
- 新结果 130,294 B / SHA-256 `52015ff2d82e2b731bb869bae0dd63e241544253e0f0830bff458cd0c5b2ee2b`，
  逐字节归档为 `openEMS验证/阶段复核封存/stage_75_with_anchor_restore_and_v2_lineage.json`。
- 处置边界不变：`full_system_gates_closed` 仍 **0/7**、`goal_complete` 仍 **`false`**；
  本节不构成“外部原件已补齐”，也不撤销既有 A/B 级结论。
