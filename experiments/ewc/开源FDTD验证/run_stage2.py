"""Phase-controlled subtraction and fixed-decoder robustness with flaport/fdtd."""

import argparse
import json
from pathlib import Path

import numpy as np

from run_fdtd import OP, relerr, solve, write_csv


def fixed_decode(signal, operation, unit_template, operator_template):
    centered = signal - OP[operation] * operator_template
    coordinate = float(np.dot(unit_template, centered) / np.dot(unit_template, unit_template))
    return coordinate, int(np.rint(coordinate))


def configurations():
    configs = [{"name": "nominal", "kind": "nominal", "value": 0, "parameters": {}}]
    for angle in (-20, -10, -5, -3, -1, 1, 3, 5, 10, 20):
        configs.append({"name": f"phase_B_{angle}_deg", "kind": "phase_B_degrees",
                        "value": angle, "parameters": {"phases": (0, np.deg2rad(angle), 0)}})
    for percent in (-5, -3, -1, 1, 3, 5):
        configs.append({"name": f"gain_B_{percent}_percent", "kind": "gain_B_percent",
                        "value": percent, "parameters": {"source_gains": (1, 1 + percent / 100, 1)}})
    for percent in (-5, -3, -1, -0.1, 0.1, 1, 3, 5):
        configs.append({"name": f"epsilon_{percent}_percent", "kind": "epsilon_percent",
                        "value": percent, "parameters": {"epsilon_r": 2.1 * (1 + percent / 100)}})
    for conductivity in (0.0002, 0.002, 0.02):
        configs.append({"name": f"conductivity_{conductivity}_S_m", "kind": "conductivity_S_m",
                        "value": conductivity, "parameters": {"conductivity": conductivity}})
    for dx in (-5, 5):
        configs.append({"name": f"source_B_dx_{dx}_mm", "kind": "source_B_dx_mm",
                        "value": dx, "parameters": {"positions": ((40, 50), (120 + dx, 50), (80, 25))}})
    for axis, rx, ry in (("dx", 75, 75), ("dx", 85, 75), ("dy", 80, 70), ("dy", 80, 80)):
        value = rx - 80 if axis == "dx" else ry - 75
        configs.append({"name": f"receiver_{axis}_{value}_mm", "kind": f"receiver_{axis}_mm",
                        "value": value, "parameters": {"receiver_mm": (rx, ry)}})
    return configs


def main(args):
    args.output.mkdir(parents=True, exist_ok=True)
    baseline = np.load(args.baseline / "receiver_arrays.npz")
    ea0, eb0, eo0 = [baseline[name] for name in ("basis_A", "basis_B", "basis_operator")]
    unit = ea0 / 15
    arrays = {"time": baseline["time"]}
    direct_rows, direct_count = [], 0

    # Operation selects a phase, not an arithmetic answer.
    for a in range(16):
        print(f"Direct FDTD phase-controlled subtraction A={a}, B=0..15", flush=True)
        for b in range(16):
            result = solve((a / 15, b / 15, OP["subtract"]), phases=(0, np.pi, 0))
            direct_count += 1
            actual = result["field"]
            expected_wave = (a / 15) * ea0 - (b / 15) * eb0 + OP["subtract"] * eo0
            coordinate, decoded = fixed_decode(actual, "subtract", unit, eo0)
            direct_rows.append({
                "A": a, "B": b, "phase_B_degrees": 180,
                "receiver_coordinate": coordinate, "decoded": decoded,
                "target": a - b, "correct": decoded == a - b,
                "relative_waveform_reconstruction_error": relerr(actual, expected_wave),
                "method": "independent upstream FDTD run",
            })
            arrays[f"subtract_{a}_{b}"] = actual
    write_csv(args.output / "subtraction_256_direct_runs.csv", direct_rows)
    configs, robustness_rows, summaries, spot_rows = configurations(), [], [], []
    selected_spots = {"nominal", "phase_B_10_deg", "gain_B_5_percent",
                      "epsilon_5_percent", "conductivity_0.02_S_m", "source_B_dx_5_mm"}
    for config in configs:
        print(f"FDTD perturbed basis responses: {config['name']}", flush=True)
        params = config["parameters"]
        responses = [
            solve(amplitudes, **params)["field"]
            for amplitudes in ((1, 0, 0), (0, 1, 0), (0, 0, 1))
        ]
        direct_count += 3
        ea, eb, eo = responses
        for label, response in zip(("A", "B", "OP"), responses):
            arrays[f"{config['name']}_{label}"] = response
        for operation, sign in (("add", 1), ("subtract", -1)):
            subset = []
            for a in range(16):
                for b in range(16):
                    signal = (a / 15) * ea + sign * (b / 15) * eb + OP[operation] * eo
                    coordinate, decoded = fixed_decode(signal, operation, unit, eo0)
                    target = a + b if operation == "add" else a - b
                    row = {
                        "configuration": config["name"], "kind": config["kind"],
                        "value": config["value"], "operation": operation, "A": a, "B": b,
                        "receiver_coordinate": coordinate, "decoded": decoded,
                        "target": target, "correct": decoded == target,
                        "absolute_error": abs(coordinate - target),
                        "method": "measured FDTD basis reconstruction; fixed nominal decoder",
                    }
                    robustness_rows.append(row)
                    subset.append(row)
            summaries.append({
                "configuration": config["name"], "kind": config["kind"], "value": config["value"],
                "operation": operation, "cases": 256,
                "passed": sum(row["correct"] for row in subset),
                "accuracy": sum(row["correct"] for row in subset) / 256,
                "max_absolute_error": max(row["absolute_error"] for row in subset),
                "response_gain_A": float(np.dot(unit, ea / 15) / np.dot(unit, unit)),
                "response_gain_B": float(np.dot(unit, eb / 15) / np.dot(unit, unit)),
                "operator_residual_bias_result_units": float(
                    OP[operation] * np.dot(unit, eo - eo0) / np.dot(unit, unit)),
            })
            if config["name"] in selected_spots:
                for a, b in ((0, 15), (15, 15), (5, 3)):
                    actual_params = dict(params)
                    phases = list(actual_params.get("phases", (0, 0, 0)))
                    if operation == "subtract":
                        phases[1] += np.pi
                    actual_params["phases"] = phases
                    actual = solve((a / 15, b / 15, OP[operation]), **actual_params)["field"]
                    direct_count += 1
                    prediction = (a / 15) * ea + sign * (b / 15) * eb + OP[operation] * eo
                    coordinate, decoded = fixed_decode(actual, operation, unit, eo0)
                    target = a + b if operation == "add" else a - b
                    spot_rows.append({
                        "configuration": config["name"], "operation": operation,
                        "A": a, "B": b, "decoded": decoded, "target": target,
                        "correct": decoded == target,
                        "reconstruction_error": relerr(actual, prediction),
                        "method": "independent upstream FDTD spot run",
                    })
    write_csv(args.output / "fixed_decoder_robustness.csv", robustness_rows)
    write_csv(args.output / "robustness_summary.csv", summaries)
    write_csv(args.output / "robustness_direct_spots.csv", spot_rows)
    np.savez_compressed(args.output / "phase_and_perturbation_waveforms.npz", **arrays)
    summary = {
        "solver": "flaport/fdtd 0.3.5", "scope": "2D TMz linear model",
        "phase_control": "B=0 degrees for add, B=180 degrees for subtract",
        "operator_receiver_and_phase_switch": "Ideal commanded phase; control hardware not simulated.",
        "decoder": "Fixed nominal matched projection and operation-specific fixed offset; no operands.",
        "new_direct_fdtd_runs": direct_count,
        "direct_subtraction_cases": 256,
        "direct_subtraction_correct": sum(row["correct"] for row in direct_rows),
        "direct_subtraction_max_error": max(abs(row["receiver_coordinate"] - row["target"])
                                            for row in direct_rows),
        "robustness_configurations": len(configs), "basis_reconstructed_cases": len(robustness_rows),
        "direct_robustness_spots": len(spot_rows),
        "max_spot_reconstruction_error": max(row["reconstruction_error"] for row in spot_rows),
        "parameters": configs, "results": summaries,
        "limitations": ["Fixed, not time-varying phase/gain changes", "No temperature material model",
                        "PEC conductor, ideal sources and receiver", "No physical operator control circuit",
                        "No continuous-task reset or memory test",
                        "Perturbation sweeps use linear-response reconstruction, not all direct runs"],
    }
    (args.output / "phase_robustness_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(11, 4), constrained_layout=True)
    matrix = np.array([row["decoded"] for row in direct_rows]).reshape(16, 16)
    image = axes[0].imshow(matrix, origin="lower", cmap="RdBu_r")
    axes[0].set(title="256 direct FDTD runs: phase-controlled subtraction", xlabel="B", ylabel="A")
    fig.colorbar(image, ax=axes[0], label="Decoded result")
    times = baseline["time"] * 1e9
    for name in ("subtract_5_3", "subtract_4_4"):
        axes[1].plot(times, arrays[name] - OP["subtract"] * eo0, label=name)
    axes[1].set(title="Previously colliding inputs are now distinguishable",
                xlabel="Time (ns)", ylabel="Operator-offset-removed Ez")
    axes[1].legend()
    axes[1].grid(alpha=0.25)
    fig.savefig(args.output / "phase_subtraction.png", dpi=150)
    plt.close(fig)
    fig, axes = plt.subplots(1, 3, figsize=(13, 4), constrained_layout=True)
    for ax, kind, xlabel in zip(
        axes, ("phase_B_degrees", "gain_B_percent", "epsilon_percent"),
        ("Static relative phase error (degrees)", "B amplitude error (%)", "Permittivity error (%)"),
    ):
        for operation in ("add", "subtract"):
            subset = [row for row in summaries if row["kind"] == kind and row["operation"] == operation]
            subset.append(next(row for row in summaries
                               if row["kind"] == "nominal" and row["operation"] == operation))
            subset.sort(key=lambda row: row["value"])
            ax.plot([row["value"] for row in subset],
                    [row["accuracy"] * 100 for row in subset], "o-", label=operation)
        ax.set(xlabel=xlabel, ylabel="Accuracy (%)", ylim=(0, 105))
        ax.legend()
        ax.grid(alpha=0.25)
    fig.savefig(args.output / "fixed_decoder_robustness.png", dpi=150)
    plt.close(fig)
    failed_configs = sorted(set(row["configuration"] for row in summaries if row["passed"] < 256))
    table = "\n".join(
        f"| {row['configuration']} | {row['operation']} | {row['passed']}/256 | "
        f"{row['max_absolute_error']:.5g} |" for row in summaries
    )
    report = f"""# 第二轮相位减法与固定解码鲁棒性验证

## 结论

使用现有开源 flaport/fdtd 0.3.5，不改动场推进方程。
将减法操作映射为 **B 数据源相位反转 180°**，两个输入各为 0～15，
256 次独立 FDTD 运行正确解码 {summary['direct_subtraction_correct']}/256，
最大接收坐标误差 {summary['direct_subtraction_max_error']:.3e}。
原先混淆的 5−3 和 4−4 在这个结构下可以区分。

这验证的是“理想相位控制＋线性电磁响应”的减法机制。
**第三束运算符波的识别、控制线路和实际相移器并未仿真**；
相位由配置接口理想指定，不能据此宣称普通线性腔体会自行理解减号。

## 编码和解码

输入仍为 A/15、B/15，并未在输入端先计算 A−B。
加法的 B 源相位为 0°，减法为 180°。
接收端使用上轮基准模板，只做固定运算符偏置移除、匹配投影和整数量化；
不读取两个输入数值，不逐输入查表。
负结果使用有符号接收坐标，验证范围为 −15～15。

## 扰动条件

固定上轮标定，不随扰动重新校准，检查：

- B 源固定相位误差 ±1、3、5、10、20°。
- B 源幅度误差 ±1、3、5%。
- 介电常数变化 ±0.1、1、3、5%。
- 介质电导率 0.0002、0.002、0.02 S/m；使用项目自带 AbsorbingObject。
- B 源 x 位置移动 ±5 mm，以及接收器 x／y 位置移动 ±5 mm。

这些是数值参数变化，不是材料温漂实测模型；相位误差是固定偏置，不是随机相位噪声。
移动量为一个粗网格单元，不用于声称毫米以下定位精度。

共 {len(configs)} 个条件，{len(robustness_rows)} 个加减输入映射通过测得基响应重建；
另外 {len(spot_rows)} 个独立 FDTD 组合检验重建，
最大波形相对误差 {summary['max_spot_reconstruction_error']:.3e}。
第二轮本脚本共 {direct_count} 次直接 FDTD，
**重建扫描不等于逐组合直接全波运行**。

## 全部条件结果

| 条件 | 运算 | 正确数 | 最大接收坐标误差 |
|---|---|---|---|
{table}

有失败输入的条件数为 {len(failed_configs)}，详见 robustness_summary.csv。
这意味着“相同输入重复输出”之外，还需要保证物理参数及标定稳定。
失败只针对当前固定标定，不否定重新标定、补偿电路或其他结构。
robustness_summary.csv 同时记录 A／B 响应增益与运算符残留偏置，
可分辨误差主要来自数据增益还是第三路波的固定偏置失配。
部分幅度误差条件有样例恰好落在半整数判决阈值；
其正确数受舍入规则和浮点微小误差影响，不能视为真实器件的精确错误概率。

## 数值和硬件边界

仍是上轮二维理想源、理想接收、PEC 外壁模型，不是三维器件。
上轮粗细网格波形约 13% 的变化未通过本轮消除，因此绝对电磁响应尚不能作为器件性能预测。
损耗材料模型只有给定常数 ε 和 σ，无真实频散、温度曲线或非线性。
没有验证连续任务的残留记忆、复位、除零、电路级相移器和真实功耗。

## 文件

- subtraction_256_direct_runs.csv：256 次独立减法验证。
- fixed_decoder_robustness.csv：全部重建输入及误差。
- robustness_summary.csv：每个条件的正确率和最大误差。
- robustness_direct_spots.csv：独立直接仿真对重建的检查。
- phase_and_perturbation_waveforms.npz：全部直接减法和扰动基波形。
- phase_subtraction.png、fixed_decoder_robustness.png：结果图。
- phase_robustness_summary.json：模型参数与明确限制。
"""
    (args.output / "相位与鲁棒性报告.md").write_text(report, encoding="utf-8")
    if summary["direct_subtraction_correct"] != 256:
        raise RuntimeError("Nominal phase-controlled subtraction did not pass.")
    if summary["max_spot_reconstruction_error"] > 1e-10:
        raise RuntimeError("Perturbed response reconstruction failed.")
    print(json.dumps({key: value for key, value in summary.items()
                      if key not in ("parameters", "results")}, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, default=Path(__file__).parent / "结果")
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "第二轮结果")
    main(parser.parse_args())
