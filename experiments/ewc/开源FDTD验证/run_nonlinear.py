"""Static nonlinear circuit validation with the upstream ahkab solver.

No behavioral multiplier/divider and no custom circuit solver are used.
This is not an RF model or an EM/circuit co-simulation.
"""

import argparse
import contextlib
import importlib.metadata
import json
from pathlib import Path
import tempfile

import numpy as np
import scipy.signal as signal
from scipy.signal import windows

# ahkab 0.18 imports legacy window names during package initialization.
# Only relocated API names are restored; solver and device equations are untouched.
WINDOW_ALIASES = ("bartlett", "hann", "hamming", "blackman",
                  "blackmanharris", "gaussian", "kaiser")
for name in WINDOW_ALIASES:
    if not hasattr(signal, name):
        setattr(signal, name, getattr(windows, name))

import ahkab

from run_fdtd import write_csv

UNIT_CURRENT = 1e-6
SATURATION_CURRENT = 1e-12


def make_circuit(a, b, operation, series_resistance=0,
                 output_ideality=1, reference_scale=1, temperature=300,
                 input_functions=None):
    """Inputs drive current sources; only linear voltages combine diode drops."""
    circuit = ahkab.Circuit(f"diode log-exp {operation} A={a} B={b}")
    for label, n in (("LOG", 1), ("EXP", output_ideality)):
        circuit.add_model("diode", label, {
            "IS": SATURATION_CURRENT, "N": n,
            "RS": series_resistance, "TEMP": 300,
        })
    for label, value in (("a", a * UNIT_CURRENT),
                         ("b", b * UNIT_CURRENT),
                         ("r", UNIT_CURRENT * reference_scale)):
        circuit.add_isource("I" + label, "0", label, dc_value=value,
                            function=(input_functions or {}).get(label))
        circuit.add_diode("D" + label, label, "0", "LOG", T=temperature)
    circuit.add_vcvs("EA", "sum_a", "0", "a", "0", 1)
    if operation == "multiply":
        circuit.add_vcvs("EB", "sum_b", "sum_a", "b", "0", 1)
        circuit.add_vcvs("ER", "drive", "sum_b", "r", "0", -1)
    elif operation == "divide":
        circuit.add_vcvs("EB", "sum_b", "sum_a", "b", "0", -1)
        circuit.add_vcvs("ER", "drive", "sum_b", "r", "0", 1)
    else:
        raise ValueError(operation)
    circuit.add_diode("Dout", "drive", "sense", "EXP", T=temperature)
    circuit.add_vsource("Vsense", "sense", "0", dc_value=0)
    return circuit


def solve_static(circuit):
    # Upstream new_op uses temporary files outside the workspace.
    results = ahkab.run(circuit, ahkab.new_op(verbose=0))["op"]
    if results is None:
        raise RuntimeError("ahkab failed to converge.")
    current = float(np.asarray(results["I(Vsense)"]).item())
    values = {"output_current_A": current}
    for label in ("a", "b", "r", "drive"):
        values["V_" + label] = float(np.asarray(results["V" + label]).item())
    if not all(np.isfinite(value) for value in values.values()):
        raise RuntimeError("Non-finite nonlinear circuit result.")
    return values


def main(args):
    args.output.mkdir(parents=True, exist_ok=True)
    calibration = solve_static(make_circuit(1, 1, "multiply"))["output_current_A"]
    if calibration <= 0:
        raise RuntimeError("Invalid fixed current decoder calibration.")
    scenarios = [
        ("ideal_matched_multiply", "multiply", {}),
        ("ideal_matched_divide", "divide", {}),
        ("diode_RS_1_ohm", "multiply", {"series_resistance": 1}),
        ("diode_RS_10_ohm", "multiply", {"series_resistance": 10}),
        ("output_N_plus_0_1_percent", "multiply", {"output_ideality": 1.001}),
        ("reference_current_plus_1_percent", "multiply", {"reference_scale": 1.01}),
        ("all_diodes_310_K", "multiply", {"temperature": 310}),
    ]
    rows, summaries = [], []
    for name, operation, params in scenarios:
        start = len(rows)
        for a in range(16):
            print(f"ahkab DC: {name}, A={a}", flush=True)
            for b in range(1 if operation == "divide" else 0, 16):
                result = solve_static(make_circuit(a, b, operation, **params))
                coordinate = result["output_current_A"] / calibration
                target = a * b if operation == "multiply" else a / b
                decoded = int(np.rint(coordinate)) if operation == "multiply" else coordinate
                error = abs(coordinate - target)
                theory = np.nan
                if not params:
                    ia, ib, ir = a * UNIT_CURRENT, b * UNIT_CURRENT, UNIT_CURRENT
                    if operation == "multiply":
                        theory = ((ia + SATURATION_CURRENT) * (ib + SATURATION_CURRENT)
                                  / (ir + SATURATION_CURRENT) - SATURATION_CURRENT)
                    else:
                        theory = ((ia + SATURATION_CURRENT) * (ir + SATURATION_CURRENT)
                                  / (ib + SATURATION_CURRENT) - SATURATION_CURRENT)
                rows.append({
                    "scenario": name, "A": a, "B": b, "operation": operation,
                    **result, "receiver_coordinate": coordinate,
                    "target": target, "decoded": decoded,
                    "absolute_error_result_units": error,
                    "ideal_shockley_reference_current_A": theory,
                    "error_to_shockley_reference_A": abs(result["output_current_A"] - theory),
                    "pass": decoded == target if operation == "multiply" else error <= 1e-3,
                })
        subset = rows[start:]
        summaries.append({
            "scenario": name, "operation": operation, "parameters": params,
            "cases": len(subset), "passed": sum(row["pass"] for row in subset),
            "accuracy": sum(row["pass"] for row in subset) / len(subset),
            "maximum_absolute_error": max(row["absolute_error_result_units"] for row in subset),
        })
    write_csv(args.output / "nonlinear_circuit_all_cases.csv", rows)
    netlist_checks = []
    for operation in ("multiply", "divide"):
        path = args.output / f"diode_{operation}_example.cir"
        lines = str(make_circuit(5, 3, operation)).splitlines()
        # Upstream __str__ uses value=, while its parser requires idc=/vdc=.
        for index, line in enumerate(lines):
            if line[:1].lower() in ("i", "v"):
                dc_keyword = "idc=" if line[:1].lower() == "i" else "vdc="
                lines[index] = line.replace("value=", dc_keyword)
        path.write_text(
            "\n".join(lines)
            + "\n.model diode LOG IS=1e-12 N=1 RS=0 TEMP=300"
            + "\n.model diode EXP IS=1e-12 N=1 RS=0 TEMP=300"
            + "\n.op\n.end\n", encoding="utf-8",
        )
        parsed, _, _ = ahkab.netlist_parser.parse_circuit(str(path))
        result = solve_static(parsed)
        expected = next(row for row in rows if row["scenario"] == f"ideal_matched_{operation}"
                        and row["A"] == 5 and row["B"] == 3)
        netlist_checks.append({
            "operation": operation, "netlist": path.name,
            "output_current_A": result["output_current_A"],
            "difference_from_API_current_A": abs(result["output_current_A"] - expected["output_current_A"]),
        })
    benchmark_error = max(row["error_to_shockley_reference_A"] for row in rows
                          if row["scenario"] in ("ideal_matched_multiply", "ideal_matched_divide"))
    summary = {
        "solver": "ahkab", "version": ahkab.__version__, "license": "GPL-2.0",
        "url": "https://github.com/ahkab/ahkab",
        "analyses": "nonlinear DC operating points",
        "scope": "Static hypothetical diode circuit, not 3 GHz RF or EM/circuit co-simulation",
        "calibration_output_current_A": calibration,
        "nominal_input_unit_A": UNIT_CURRENT,
        "nominal_diode_IS_A": SATURATION_CURRENT,
        "nominal_diode_N": 1, "nominal_temperature_K": 300,
        "solver_runs": len(rows) + 1 + len(netlist_checks), "scenarios": summaries,
        "max_ideal_shockley_benchmark_error_A": benchmark_error,
        "netlist_round_trip_checks": netlist_checks,
        "compatibility": "Legacy scipy.signal window names alias scipy.signal.windows only.",
        "ideal_elements": ["Three unity-gain linear VCVS buffers", "Zero-ohm current sensor",
                           "Ideal input/reference current sources"],
        "excluded": ["Junction capacitance", "Transit time", "Finite bandwidth and supply rails",
                     "Antenna coupling", "EM feedback", "Division-by-zero detection"],
        "dependency_versions": {key: importlib.metadata.version(key)
                                for key in ("ahkab", "numpy", "scipy")},
    }
    (args.output / "nonlinear_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8",
    )
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(11, 4), constrained_layout=True)
    ideal = np.array([row["receiver_coordinate"] for row in rows
                      if row["scenario"] == "ideal_matched_multiply"]).reshape(16, 16)
    image = axes[0].imshow(ideal, origin="lower")
    axes[0].set(title="ahkab static diode multiplication", xlabel="B", ylabel="A")
    fig.colorbar(image, ax=axes[0], label="Fixed-decoded current")
    multipliers = [item for item in summaries if item["operation"] == "multiply"]
    axes[1].bar(range(len(multipliers)), [100 * item["accuracy"] for item in multipliers])
    axes[1].set_xticks(range(len(multipliers)),
                      ["Ideal", "Rs 1 ohm", "Rs 10 ohm", "N +0.1%", "Iref +1%", "310 K"],
                      rotation=35, ha="right")
    axes[1].set(title="Same fixed decoder; hypothetical device perturbations",
                ylabel="Integer multiplication accuracy (%)")
    fig.savefig(args.output / "nonlinear_results.png", dpi=150)
    plt.close(fig)
    table = "\n".join(
        f"| {item['scenario']} | {item['passed']}/{item['cases']} | "
        f"{item['maximum_absolute_error']:.6g} |" for item in summaries
    )
    report = f"""# 二极管非线性乘除机制验证

## 结果与边界

实际使用开源 **ahkab {ahkab.__version__}** 的非线性 DC 工作点求解器。
共 {summary['solver_runs']} 次工作点求解。没有自行编写牛顿求解器，没有行为乘法或除法源。
这与二维 FDTD 是分开运行的两种模型，**不是电磁／电路联合仿真，也不证明 3 GHz 器件可用**。
理想条件下与包含 “−1” 项的 Shockley 电路解析关系比较，
最大电流差异为 {benchmark_error:.3e} A；示例网表也通过重新解析并独立求解检查。

| 条件 | 通过／总数 | 最大绝对误差（结果单位） |
|---|---|---|
{table}

乘法通过标准：输出电流经同一个固定增益转换、四舍五入后等于整数乘积。
除法通过标准：同一增益输出与商的绝对误差不超过 0.001。
除法只测试 B=1～15；B=0 的检测、报错和保护电路没有实现。

## 电路机制

两个数据输入分别设置理想电流源 IA=A×1 μA、IB=B×1 μA；
参考电流为 1 μA。匹配二极管把这些电流转换为电压 VA、VB、VR。
三个线性受控电压源表示理想缓冲／求和级，它们只进行线性电压组合：

```text
乘法：Vdrive = VA + VB - VR
除法：Vdrive = VA - VB + VR
Vdrive → 指数响应输出二极管 → 接收电流
```

非线性来自 ahkab 自带的二极管器件方程，不是模型中直接写入 A×B 或 A÷B。
输出只用一次 (1,1) 校准得到的电流增益，随后所有输入和扰动条件保持不变。
目标乘积／商仅在求解结束后用于验收，不进入输出源或器件模型。

## 为什么理想匹配时有效

理想 Shockley 模型的匹配指数特性允许对数电压组合对应电流乘除。
由于二极管的 “−1” 项，零输入及很小电流不是严格的纯对数；
本次仍直接求解包含该项的器件模型，没有把零输入的答案强制设为零。

假设参数：IS=1 pA、N=1、300 K，基准串联电阻为零。
这些是通用模型参数，不是某款材料或商业器件的测量拟合。
输出传感器是理想零伏电压源，缓冲级没有电源轨、带宽、失调或噪声。

## 非理想结果应如何理解

增加串联电阻、输出二极管理想因子失配、参考电流漂移时，仍使用基准固定解码器。
准确率下降表示这套未补偿实现对这些因素敏感，不表示其他校准或电路方案不可能成功。
全部二极管同温变化与单管温差不是同一问题；本次只测试了匹配共同温度变化。
没有把失败输入逐个查表修正，也没有用各输入目标重新校准。

## 对原始构想的意义

支持的判断：具体非线性器件加线性控制级可以承担乘除映射，固定结果编码是可行接口。
不支持的判断：一束“乘号波”在普通线性腔体中就能自动乘法，或此电路已是 GHz 全波计算机。
实现这种机制需要匹配器件、参考源和控制／缓冲级，架构比三束波简单叠加更复杂。

需要下一阶段验证：结电容与载流子时间、带宽、动态建立时间、失配补偿、
真实供电和输出负载、天线耦合以及除零检测。

## 复现和文件

- nonlinear_circuit_all_cases.csv：全部工作点、二极管电压、电流和误差。
- nonlinear_summary.json：参数、条件和限制。
- diode_multiply_example.cir、diode_divide_example.cir：配置对应的示例网表。
- nonlinear_results.png：输入映射与扰动准确率。
- run_nonlinear.py：调用现有求解器的电路配置与验收脚本。

旧版 ahkab 初始化导入的七个 SciPy 窗函数名已移动到 scipy.signal.windows，
脚本只恢复名称别名，不改动求解器、二极管方程或数值收敛算法。
开源项目：https://github.com/ahkab/ahkab ，GPL-2.0。
"""
    (args.output / "非线性验证报告.md").write_text(report, encoding="utf-8")
    if summaries[0]["passed"] != 256 or summaries[1]["passed"] != 240:
        raise RuntimeError("Ideal static arithmetic did not pass; inspect saved results.")
    if benchmark_error > 1e-9 or any(item["difference_from_API_current_A"] > 1e-12
                                    for item in netlist_checks):
        raise RuntimeError("Nonlinear analytic or netlist round-trip check failed.")
    print(json.dumps(summary, ensure_ascii=True, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "第二轮结果")
    arguments = parser.parse_args()
    main(arguments)
