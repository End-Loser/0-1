"""Report the validated extraction milestone, including unmet physical gates."""

import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).parent


def main():
    folder = ROOT/"第十一轮结果"
    summary_bytes = (folder/"loaded_em_summary.json").read_bytes()
    refinement_bytes = (folder/"loaded_em_refinement_summary.json").read_bytes()
    summary, refinement = json.loads(summary_bytes), json.loads(refinement_bytes)
    audit = json.loads((folder/"loaded_em_verification.json").read_text(encoding="utf-8"))
    assert audit["data_integrity"] == "PASS"
    assert audit["summary_sha256"] == hashlib.sha256(summary_bytes).hexdigest()
    assert audit["refinement_summary_sha256"] == hashlib.sha256(refinement_bytes).hexdigest()
    assert audit["case_count"] == len(summary["cases"])+len(refinement["cases"])
    rows = []
    for row in audit["finite_bandwidth_holdout_changes"]:
        rows.append(f"| {row['left']} | {row['right']} | "
                    f"{100*row['relative_readout_waveform_change']:.3f}% |")
    benchmarks = "\n".join(
        f"| {b['dx_mm']} | {b['time_refinement']} | {b['fitted_Hz']/1e9:.9f} | "
        f"{b['relative_analytic_error']*100:.6f}% | {b['relative_discrete_error']:.3e} |"
        for b in summary["benchmarks"])
    holdout = "\n".join(
        f"| {r['dx_mm']} | {r['relative_holdout_error']:.3e} |"
        for r in audit["holdout_checks"])
    sensitivity = "\n".join(
        f"| {name} | {value*100:.3f}% |"
        for name, value in audit["load_material_sensitivities"].items())
    report = f"""# 第十一轮第一阶段：三维有负载电磁接口响应提取

## 结论

完成{audit['case_count']}次三维受激场仿真和{audit['benchmark_count']}次空腔基准。
原始归档、有限输入下的因果线性重建和解析基准独立核验为 **PASS**。
**真实端口与电路联合仿真进入条件尚未满足，完整硬件门槛未通过。**
本轮不是一位全加器完整电磁/电路联合仿真，也没有运行四位性能比较。

这一步把旧二维、无接收负载的场响应推进为三维、含介质损耗和分布式接收负载的模型。
同时发现网格和观察窗口的不确定性不能忽略，不能直接把提取响应换算成器件电压，
或据此重申20 ns、真实能耗、硬件成本与CPU性能优势。
未通过进入条件不代表方案不可能，也不推翻第十轮限定代理模型的正确性结果。

## 模型

- 腔体截面与高度为160×100×60 mm，六面理想PEC；仅冻结切向电场，保留法向电场。
- 介质块为x=60～100、y=35～65、z=20～40 mm；
  假设相对介电常数2.1、电导率0.002 S/m，使用体积分数描述边界。
- 四个10×10×10 mm源/读出体中心为
  (40,50,30)、(120,50,30)、(80,25,30)、(80,75,30) mm。
- 接收体沿z方向另加0.02 S/m电导；它确实改变场演化，
  **但不是经过验证的50欧姆端口、真实天线或检测器**。
- 驱动是固定物理体积内的归一化电流密度激励；读出是体积加权平均Ez。
  不将这些归一化量宣称为端口伏特、瓦特、功率损耗或V/V增益。
- 粗、细网格为5和2.5 mm；补充2 mm网格的第一源脉冲与组合激励。
  2 mm并未提取全部四源矩阵，三档网格也不能自动视为收敛证明。
- Courant数0.5；独立时间细化使用0.25。基础观察窗口12 ns，
  第一源另做24 ns延长测试。全部从零场开始，不代表连续长期运行。

实际求解器为flaport/fdtd {summary['version']}，NumPy后端。
使用上游Grid、LineSource、AbsorbingObject及step推进场；
配置脚本设置有限体积源分布和预先指定的输入，不自行实现Maxwell更新方程。
没有重试受系统策略阻止的openEMS，也没有修改安全策略。
材料、源和接收体是明确的研究模型，还不是可直接采购的完整实物设计。

## 数值与响应核验

空腔TM110模式直接推进，与连续解析频率和已知Yee离散频率比较：

| 网格mm | 时间细化倍率 | 拟合频率GHz | 相对连续解析差异 | 相对离散解析差异 |
|---|---:|---:|---:|---:|
{benchmarks}

驱动体在z方向有限，实际受激场包含非Ez电场分量；没有沿用二维TMz不变假设。
基准仅验证一个解析模式，不是所有三维模式、接头与材料模型的独立验证。

每档基础网格提取四路单步脉冲到四路读出的响应矩阵。
另一次直接求解同时输入三条不同频率、相位和包络的有限带宽激励，
使用归档脉冲作线性因果卷积重建，不注入算术答案：

| 网格mm | 独立直接激励与重建相对差异 |
|---|---:|
{holdout}

互易矩阵相对差异：5 mm为{audit['reciprocity_relative_difference']['5']:.3e}，
2.5 mm为{audit['reciprocity_relative_difference']['2.5']:.3e}。
独立核验用直接线性卷积，检查初始零输出、输入源、时间轴、PEC切向边界、
文件SHA-256及重复运行窗口一致性；不是循环FFT拼接或逐输入答案查表。
这些结果支持本模型的提取流程，不构成全加器真值验收。

## 尚未收敛的范围

12 ns截断脉冲响应在2.5～3.5 GHz频段，5与2.5 mm网格的复响应相对差异为
**{audit['band_2p5_to_3p5GHz_relative_mesh_change']*100:.3f}%**。
第一源2.5与2 mm的同频段相对差异为
**{audit['source0_band_relative_mesh_change_2p5_to_2mm']*100:.3f}%**。
这不是无穷时间稳态S参数，也不是物理误差上界；谐振位置、离散色散和截断残留都可能影响它。
不能据此把物理接口误差放进第十轮的±1%有效系数预算。

有限带宽直接组合激励的接收波形比较如下，按共同观察时间插值，
与单步宽带脉冲的高频敏感性分开记录：

| 原设置 | 细化设置 | 接收波形相对差异 |
|---|---|---:|
{chr(10).join(rows)}

单步宽带脉冲减半时间步的归一化波形变化为
{audit['time_refinement_relative_waveform_change']*100:.3f}%。
宽带离散脉冲包含远高于目标载频的分量，该变化不能直接当作3 GHz器件误差。
反过来，有限带宽结果较小也不能消除所有网格和脉冲截断问题。

24 ns运行的前12 ns与原运行逐点一致，后12 ns接收响应L2范数占完整24 ns响应L2范数的
**{audit['after_12ns_extended_response_norm_fraction']*100:.3f}%**。
这是采样响应范数比，不是硬件能量比；说明12 ns截断不能当成已复位或响应完全结束。

介质与负载敏感性，均为第一源完整12 ns接收波形比较：

| 变化 | 相对基础波形差异 |
|---|---:|
{sensitivity}

没有把这些波形变化直接等同于逻辑误码率、静态增益误差或实测温漂。

## 下一步与停止条件

暂不把本响应接入第十轮电路来宣称“真实联合仿真通过”，原因是端口量纲、
收敛、截断和实际检测器均未解决。

后续优先顺序：

1. 明确物理端口、馈电结构及器件频段；选择支持经过验证的电压/电流端口的求解工具，
   或先独立验证当前工具的端口实现。分布式电导不能直接冒充集总阻抗。
2. 针对目标载频与调制带宽，继续网格、时间和观察窗口收敛；检查尾部误差，
   必要时评估阻尼、耦合或结构调整，不能靠截掉尾部取得通过。
3. 提取带有端口负载、互耦、反射和正确单位的多端口模型；
   验证因果性、互易性、被动性与留出激励。当前互易检查不替代被动性与能量核验。
4. 加入实际增益、相位控制、混频/检测、采样及校准器件模型，
   验证驱动和检测负载对电磁响应的反作用，再重测一位全加器。
5. 一位联合模型达到明确正确性与误差门槛后，才进行四位性能对照。

本轮不采购硬件，没有生成可直接购买的BOM。
此前硬件成本与四位80 ns只是粗略讨论，不是本轮测量结论。

## 数据与复现

- `loaded_em_summary.json`：基础15次受激仿真和3次解析基准的参数与归档哈希。
- `loaded_em_refinement_summary.json`：补充3次网格/有限带宽时间细化及来源哈希。
- `loaded_em_verification.json`：独立核验、未满足的进入条件。
- `loaded_frequency_response.csv`：截断归一化响应，不是Touchstone或实际S参数。
- `*.npz / *.json`：输入、时间轴、四路读出、最终完整三维E/H场及配置。
- `loaded_em_diagnostics.png`：直接有限带宽响应与脉冲残留图。

```powershell
$env:PYTHONPATH = Join-Path $env:TEMP 'ewc-openems\\fdtd-package'
$env:PYTHONPYCACHEPREFIX = Join-Path $env:TEMP 'ewc-openems\\pycache'
python "开源FDTD验证\\run_loaded_em_interface.py"
python "开源FDTD验证\\refine_loaded_em_interface.py"
python "开源FDTD验证\\verify_loaded_em_interface.py"
python "开源FDTD验证\\report_loaded_em_interface.py"
```

本轮不覆盖前十轮结果。数据完整性PASS与真实硬件进入门槛未通过是两个不同结论。
"""
    fig, axes = plt.subplots(2, 1, figsize=(10, 7), constrained_layout=True)
    for name in ("holdout_5mm", "holdout_2.5mm", "holdout_2mm"):
        with np.load(folder/(name+".npz")) as data:
            axes[0].plot(data["time"]*1e9, data["readout"][:, 3], label=name)
    with np.load(folder/"duration_double.npz") as data:
        axes[1].plot(data["time"]*1e9, data["readout"][:, 3], lw=.7)
    axes[0].set(title="Finite-bandwidth independent excitation", ylabel="Mean Ez (normalized)")
    axes[0].legend()
    axes[1].axvline(12, color="red", ls="--", label="Original archive end")
    axes[1].set(title="Impulse response beyond the original window",
                xlabel="Time (ns)", ylabel="Mean Ez (normalized)")
    axes[1].legend()
    for ax in axes:
        ax.grid(alpha=.25)
    fig.savefig(folder/"loaded_em_diagnostics.png", dpi=140)
    plt.close(fig)
    path = folder/"三维有负载接口提取与联合仿真前置报告.md"
    path.write_text(report, encoding="utf-8")
    print(path)


if __name__ == "__main__":
    main()
