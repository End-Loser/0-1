# EWC 电磁波计算实验归档

本目录归档 EWC（Electromagnetic Wave Computing）项目的构想、可行性分析、代码和结果摘要。项目要检验的问题不是“能否立刻造出一台计算机”，而是：

> 受控电磁结构的输入—输出关系，能否稳定、可重复且可编程地承担计算映射？

- 状态：`status:experiment`（数值仿真与电路级验证；无硬件样机）
- 实验条目：[../ewc-electromagnetic-wave-computing.md](../ewc-electromagnetic-wave-computing.md)
- 想法条目：[../../ideas/electromagnetic-wave-computing.md](../../ideas/electromagnetic-wave-computing.md)
- 最后更新：2026-11-25

## 当前状态（据项目记录，截至 2026-11-19）

| 项目 | 状态 |
|---|---|
| 概念与可行性分析 | 已完成初版 |
| 二维开源 FDTD 验证 | 多轮报告已归档 |
| 三维 openEMS 铜腔验证 | 数值子项部分通过；六组长时算例未全部完成 |
| 器件 / 校准 / 端口 / 材料核查 | 部分子项通过，存在外部缺件与模型缺口 |
| v1 仿真原型 | 20 例实跑：时域 / 频域比特一致 16/16（30000 步版） |
| 七项整机验收门槛 | 0/7；`goal_complete=false` |
| 硬件样机与实测 | 未开始 |

**事实与边界**：本目录的电磁结果均来自数值仿真。v1 的“16/16 比特一致”指在 delta-gap 端口、并联电导负载和离线锁相判决下的仿真原型结果，不关闭任何整机验收门槛，也不构成硬件、速度或能效证据。详见[全任务验收台账](EWC全任务验收台账.md)与[项目诊断与收尾实施计划](EWC项目诊断与收尾实施计划.md)。

## 目录

| 路径 | 内容 |
|---|---|
| `构想.md` | 原始构想书 |
| `EWC系统实现可行性分析.md` | 物理边界、器件路线与分阶段实验建议 |
| `EWC全任务验收台账.md` | 七项主门槛、28 个功能项与专项证据 |
| `EWC全目标持续执行记录.md` | 长时算例、复核与外部依赖执行记录 |
| `EWC项目诊断与收尾实施计划.md` | 缺口、决策点与收尾路线 |
| `openEMS验证/` | 三维 openEMS 铜腔、端口、边界、长时窗与 v1 原型 |
| `开源FDTD验证/` | flaport/fdtd 二维 FDTD 与 ahkab 电路验证 |
| `器件验证/` | 运放、开关、供电与接口级 ngspice 验证 |
| `射频端口验证/` | 同轴端口归一化与参考面基准 |
| `材料验证/` | 介质与铜箔数据适用性核查 |
| `校准验证/` | ADC / DAC / 基准的时序与量化预算 |

## 复现入口

### 环境

- Windows 11，Python 3.11
- NumPy 1.26.4、SciPy 1.17.1、Matplotlib 3.10.8
- 二维 FDTD：`fdtd==0.3.5`（MIT）；电路：`ahkab==0.18`（GPL-2.0）
- 三维验证：openEMS（GPLv3）
- 器件验证：ngspice 47（BSD-3-Clause）及厂商公开模型

依赖清单见 `开源FDTD验证/requirements.txt`。部分脚本依赖本机安装的 openEMS、ngspice 和厂商模型库；仓库不分发这些第三方组件。

### 代表性命令

```powershell
python 开源FDTD验证\run_stage2.py
python openEMS验证\ewc_v1.py --out openEMS验证\EWC_v1仿真原型结果_复跑 --steps 30000 --workers 2
python 射频端口验证\run_coax_benchmark.py
python 材料验证\run_dielectric.py
python 校准验证\run_all.py
```

**注意**：

1. 原项目文档与部分脚本保留了原始 Windows 绝对路径（如 `D:\work\...`、`E:\...`、`G:\...`），复跑前需按本机目录调整；本仓库归档时未重新执行全部脚本。
2. 为控制仓库体积并遵守第三方许可，`*.npz`、`*.csv`、`*.cir`、`*.raw`、日志、第三方数据手册 / PDF、厂商源码与可执行文件**未纳入仓库**。依赖归档波形或预计算锚点的 `verify_*` 脚本不能直接运行，需先按 `run_*` 脚本重跑生成数据。
3. 器件验证中的 PSpice for TI 申请、厂商模型获取等外部依赖，可能在复现时仍不可得；缺少输入不等于物理结论。

## 许可与第三方

- 本项目原创代码采用仓库根目录 [MIT 许可证](../../LICENSE)。
- 文档、报告与整理的数据采用 [CC BY 4.0](../../LICENSE-CONTENT.md)，另有说明的除外。
- 第三方依赖保留各自许可证：fdtd（MIT）、ahkab（GPL-2.0）、openEMS（GPLv3）、ngspice（BSD-3-Clause）等。使用或再分发相关脚本时需同时遵守对应许可证。
- 仓库不分发厂商数据手册、SPICE 模型、工具安装包或厂商源码副本；相关结论仅引用公开来源。
- 本目录不含账号、密码、Token 或个人信息。
