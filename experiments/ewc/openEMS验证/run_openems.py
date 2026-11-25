"""Configure and test a cavity using the official openEMS FDTD solver.

This is model configuration and result analysis, not an electromagnetic solver.
Arithmetic targets are used only for offline validation, never as field sources.
"""

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time

import numpy as np


F0 = 3e9
FC = 1.5e9
MAX_INPUT = 15
OP_CODES = {"add": 0.25, "subtract": 0.5, "multiply": 0.75, "divide": 1.0}
SOURCE_URL = "https://github.com/thliebig/openEMS-Project/releases/tag/v0.37.0"
ARCHIVE_SHA256 = "e4f14fd6e9be7d0c1fd9c244768c21c1c2b62b8aa81f08253ebf6e06040cda5a"


def relative_error(actual, expected):
    return float(np.linalg.norm(actual - expected) /
                 max(np.linalg.norm(expected), np.finfo(float).tiny))


def configure_openems(root):
    root = root.resolve()
    dll_dirs = [root, root / "bin", root / "lib"]
    handles = []
    for folder in dll_dirs:
        if folder.is_dir():
            os.environ["PATH"] = str(folder) + os.pathsep + os.environ["PATH"]
            if hasattr(os, "add_dll_directory"):
                handles.append(os.add_dll_directory(str(folder)))
    os.environ["CSXCAD_INSTALL_PATH"] = str(root)
    os.environ["OPENEMS_INSTALL_PATH"] = str(root)
    return handles


def solve_case(folder, amplitudes, spacing_mm, duration_ns):
    from CSXCAD import ContinuousStructure
    from openEMS import openEMS

    folder.mkdir(parents=True, exist_ok=True)
    fdtd = openEMS(NrTS=30000, EndCriteria=0, MaxTime=duration_ns * 1e-9)
    fdtd.SetGaussExcite(F0, FC)
    fdtd.SetBoundaryCond(["PEC"] * 6)
    csx = ContinuousStructure()
    fdtd.SetCSX(csx)
    mesh = csx.GetGrid()
    mesh.SetDeltaUnit(1e-3)
    for axis, size in zip("xyz", (160, 100, 60)):
        mesh.SetLines(axis, np.arange(0, size + spacing_mm / 2, spacing_mm))

    # A generic linear dielectric, not a measured commercial material model.
    dielectric = csx.AddMaterial("linear_dielectric", epsilon=2.1, kappa=0.002)
    dielectric.AddBox([60, 35, 20], [100, 65, 40])
    geometry = [
        ([35, 45, 0], [45, 55, 10]),   # data A
        ([115, 45, 0], [125, 55, 10]), # data B, mirror of A
        ([75, 20, 0], [85, 30, 10]),   # operator transmitter
        ([75, 65, 0], [85, 75, 10]),   # single receiver
    ]
    ports = []
    for index, (start, stop) in enumerate(geometry):
        amplitude = amplitudes[index] if index < 3 else 0
        ports.append(fdtd.AddLumpedPort(
            index + 1, 50, start, stop, "z",
            excite=amplitude, priority=10, edges2grid="xyz",
        ))
    csx.Write2XML(str(folder / "model.xml"))
    started = time.perf_counter()
    fdtd.Run(str(folder), cleanup=False, verbose=0, numThreads=2)
    elapsed = time.perf_counter() - started
    receiver = ports[3]
    receiver.CalcPort(str(folder), np.array([F0]))
    times = np.asarray(receiver.u_time)
    voltage = np.asarray(receiver.ut_tot)
    if len(times) < 100 or not np.all(np.isfinite(voltage)):
        raise RuntimeError("Invalid or insufficient FDTD receiver samples.")
    if np.max(np.abs(voltage)) == 0:
        raise RuntimeError("The receiver is silent; check port geometry.")
    np.savez_compressed(
        folder / "response.npz", time=times, voltage=voltage,
        frequency_voltage=receiver.uf_tot, runtime=elapsed,
    )
    return {
        "time": times, "voltage": voltage,
        "frequency_voltage": complex(receiver.uf_tot[0]),
        "runtime_seconds": elapsed, "spacing_mm": spacing_mm,
        "amplitudes": list(amplitudes), "folder": str(folder),
    }


def align(case, reference):
    return np.interp(reference["time"], case["time"], case["voltage"])


def decode_add(signal, unit_response, operator_response):
    """One fixed offset, matched-filter gain and integer quantization.

    No operand values are provided to this decoder.
    """
    centered = signal - OP_CODES["add"] * operator_response
    coordinate = float(np.dot(unit_response, centered) /
                       np.dot(unit_response, unit_response))
    return coordinate, int(np.rint(coordinate))


def write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def run(args):
    dll_handles = configure_openems(args.solver_root)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import openEMS as openems_module

    args.output.mkdir(parents=True, exist_ok=True)
    args.raw_dir.mkdir(parents=True, exist_ok=True)
    definitions = {
        "basis_a": (1, 0, 0),
        "basis_b": (0, 1, 0),
        "basis_operator": (0, 0, 1),
        "add_5_3": (5 / 15, 3 / 15, OP_CODES["add"]),
        "add_4_4": (4 / 15, 4 / 15, OP_CODES["add"]),
        "add_5_4": (5 / 15, 4 / 15, OP_CODES["add"]),
        "add_5_3_repeat": (5 / 15, 3 / 15, OP_CODES["add"]),
        "subtract_5_3": (5 / 15, 3 / 15, OP_CODES["subtract"]),
        "subtract_4_4": (4 / 15, 4 / 15, OP_CODES["subtract"]),
        "multiply_1_4": (1 / 15, 4 / 15, OP_CODES["multiply"]),
        "multiply_2_3": (2 / 15, 3 / 15, OP_CODES["multiply"]),
        "divide_6_2": (6 / 15, 2 / 15, OP_CODES["divide"]),
        "divide_4_4": (4 / 15, 4 / 15, OP_CODES["divide"]),
    }
    cases = {}
    for name, amplitudes in definitions.items():
        print(f"FDTD {name}: {amplitudes}", flush=True)
        cases[name] = solve_case(
            args.raw_dir / name, amplitudes, 5.0, args.duration_ns,
        )
    reference = cases["basis_a"]
    ea = reference["voltage"]
    eb = align(cases["basis_b"], reference)
    eo = align(cases["basis_operator"], reference)
    unit_response = ea / MAX_INPUT

    direct_rows = []
    max_superposition_error = 0
    for name, case in cases.items():
        actual = align(case, reference)
        a, b, operator = case["amplitudes"]
        predicted = a * ea + b * eb + operator * eo
        error = relative_error(actual, predicted)
        max_superposition_error = max(max_superposition_error, error)
        direct_rows.append({
            "case": name, "excitation_A": a, "excitation_B": b,
            "excitation_operator": operator,
            "relative_waveform_superposition_error": error,
            "runtime_seconds": case["runtime_seconds"],
        })
    write_csv(args.output / "direct_fdtd_cases.csv", direct_rows)

    # These are linear-response reconstructions, NOT 256 separate FDTD runs.
    sweep_rows = []
    for a in range(MAX_INPUT + 1):
        for b in range(MAX_INPUT + 1):
            signal = (a / MAX_INPUT) * ea + (b / MAX_INPUT) * eb
            signal += OP_CODES["add"] * eo
            coordinate, decoded = decode_add(signal, unit_response, eo)
            sweep_rows.append({
                "A": a, "B": b, "operator": "add",
                "receiver_coordinate": coordinate,
                "decoded_result": decoded, "expected_result": a + b,
                "correct": decoded == a + b,
                "method": "FDTD basis response reconstruction",
            })
    write_csv(args.output / "addition_sweep.csv", sweep_rows)

    direct_add = []
    for name in ("add_5_3", "add_4_4", "add_5_4", "add_5_3_repeat"):
        coordinate, decoded = decode_add(
            align(cases[name], reference), unit_response, eo,
        )
        a, b, _ = cases[name]["amplitudes"]
        expected = int(round(MAX_INPUT * (a + b)))
        direct_add.append({
            "case": name, "coordinate": coordinate,
            "decoded_result": decoded, "expected_result": expected,
            "correct": decoded == expected,
        })

    collision_definitions = [
        ("subtract", "subtract_5_3", "subtract_4_4", 2, 0),
        ("multiply", "multiply_1_4", "multiply_2_3", 4, 6),
        ("divide", "divide_6_2", "divide_4_4", 3, 1),
    ]
    collisions = []
    for operation, left, right, answer_left, answer_right in collision_definitions:
        difference = relative_error(
            align(cases[left], reference), align(cases[right], reference),
        )
        collisions.append({
            "operator": operation, "case_1": left, "case_2": right,
            "correct_answer_1": answer_left, "correct_answer_2": answer_right,
            "relative_receiver_waveform_difference": difference,
            "below_1e_minus_6": difference < 1e-6,
        })
    write_csv(args.output / "collision_tests.csv", collisions)

    rng = np.random.default_rng(20261117)
    noise_rows = []
    coordinates = np.array([row["receiver_coordinate"] for row in sweep_rows])
    targets = np.array([row["expected_result"] for row in sweep_rows])
    for sigma in (0, 0.05, 0.1, 0.2, 0.5, 1.0):
        measured = coordinates[:, None] + rng.normal(0, sigma, (256, 100))
        decoded = np.rint(measured).astype(int)
        accuracy = float(np.mean(decoded == targets[:, None]))
        noise_rows.append({
            "sigma_in_one_result_level": sigma, "trials": 25600,
            "accuracy": accuracy,
            "model": "Gaussian noise on receiver scalar; not FDTD material noise",
        })
    write_csv(args.output / "receiver_noise_sweep.csv", noise_rows)

    print("FDTD fine-mesh basis_a and direct add_5_3", flush=True)
    fine_a = solve_case(args.raw_dir / "fine_basis_a", (1, 0, 0), 2.5, args.duration_ns)
    fine_sum = solve_case(
        args.raw_dir / "fine_add_5_3", definitions["add_5_3"],
        2.5, args.duration_ns,
    )
    frequency_relative_change = float(abs(
        fine_a["frequency_voltage"] - reference["frequency_voltage"]
    ) / abs(fine_a["frequency_voltage"]))
    fine_sum_relative_change = float(abs(
        fine_sum["frequency_voltage"] - cases["add_5_3"]["frequency_voltage"]
    ) / abs(fine_sum["frequency_voltage"]))

    repeat_error = relative_error(
        align(cases["add_5_3_repeat"], reference),
        align(cases["add_5_3"], reference),
    )
    all_add_correct = all(row["correct"] for row in sweep_rows)
    summary = {
        "solver": "openEMS official FDTD, CPU, two threads",
        "solver_python_module": str(openems_module.__file__),
        "release_url": SOURCE_URL, "archive_expected_sha256": ARCHIVE_SHA256,
        "direct_fdtd_run_count": len(cases) + 2,
        "duration_ns": args.duration_ns,
        "geometry_mm": [160, 100, 60],
        "boundary": "PEC, all six faces",
        "dielectric": {"epsilon_r": 2.1, "conductivity_S_per_m": 0.002},
        "port_R_ohm": 50, "pulse_center_Hz": F0, "pulse_width_Hz": FC,
        "max_input": MAX_INPUT, "operator_amplitude_codes": OP_CODES,
        "receiver_samples": len(ea),
        "data_source_mirror_symmetry_error": relative_error(ea, eb),
        "maximum_direct_superposition_error": max_superposition_error,
        "repeat_waveform_error": repeat_error,
        "reconstructed_addition_cases": len(sweep_rows),
        "reconstructed_addition_all_correct": all_add_correct,
        "direct_addition_results": direct_add, "collision_tests": collisions,
        "receiver_scalar_noise_tests": noise_rows,
        "mesh_check": {
            "coarse_mm": 5.0, "fine_mm": 2.5,
            "basis_A_frequency_response_relative_change": frequency_relative_change,
            "add_5_3_frequency_response_relative_change": fine_sum_relative_change,
            "note": "Two-grid diagnostic, not proof of mesh convergence.",
        },
        "raw_dir": str(args.raw_dir),
        "limitations": [
            "Linear time-invariant material and ports; no nonlinear devices.",
            "Operator is a third additive wave, not a boundary or phase controller.",
            "Noise is a receiver scalar statistical test, not a physical noise simulation.",
            "PEC walls, ideal sources, zero initial fields and synchronized inputs.",
            "Only 15 direct FDTD runs; the 256-case sweep uses linear reconstruction.",
            "No arbitrary-cavity impossibility or full four-operation validation claim.",
        ],
    }
    summary_path = args.output / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    wave_rows = []
    displayed = ["basis_a", "basis_b", "add_5_3", "add_5_4",
                 "multiply_1_4", "multiply_2_3"]
    for index, t in enumerate(reference["time"]):
        row = {"time_s": float(t)}
        row.update({name: float(align(cases[name], reference)[index]) for name in displayed})
        wave_rows.append(row)
    write_csv(args.output / "receiver_waveforms.csv", wave_rows)

    fig, axes = plt.subplots(2, 1, figsize=(10, 7), constrained_layout=True)
    for name in ("add_5_3", "add_4_4", "add_5_4"):
        axes[0].plot(reference["time"] * 1e9, align(cases[name], reference), label=name)
    axes[0].set(title="Direct FDTD: receiver waveforms for addition",
                xlabel="Time (ns)", ylabel="Receiver voltage (V)")
    for name in ("multiply_1_4", "multiply_2_3"):
        axes[1].plot(reference["time"] * 1e9, align(cases[name], reference), label=name)
    axes[1].set(title="Different products, indistinguishable receiver waveforms",
                xlabel="Time (ns)", ylabel="Receiver voltage (V)")
    for axis in axes:
        axis.legend()
        axis.grid(alpha=0.25)
    fig.savefig(args.output / "waveform_comparison.png", dpi=160)
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(10, 4), constrained_layout=True)
    matrix = np.array([row["decoded_result"] for row in sweep_rows]).reshape(16, 16)
    image = axes[0].imshow(matrix, origin="lower", interpolation="nearest")
    axes[0].set(title="Fixed decoder: addition over 0..15", xlabel="B", ylabel="A")
    fig.colorbar(image, ax=axes[0], label="Decoded result")
    axes[1].plot([row["sigma_in_one_result_level"] for row in noise_rows],
                 [100 * row["accuracy"] for row in noise_rows], "o-")
    axes[1].set(title="Post-processing noise test (not full-wave noise)",
                xlabel="Receiver scalar noise sigma (result levels)", ylabel="Accuracy (%)")
    axes[1].grid(alpha=0.25)
    fig.savefig(args.output / "addition_and_noise.png", dpi=160)
    plt.close(fig)

    report = f"""# openEMS 开源全波仿真验证结果

## 验证对象与结论

使用官方 openEMS v0.37.0 的 FDTD 求解器，配置三路发射、一路接收的线性腔体。
没有自行实现电磁求解器，也没有把加减乘除答案写进激励或材料响应。

- 固定解码的 0～15 整数加法：由全波基响应重建的 256 个输入组合，正确数为 {sum(row['correct'] for row in sweep_rows)}。
- 独立全波加法样例：{json.dumps(direct_add, ensure_ascii=False)}。
- 两路数据源镜像响应差异：{summary['data_source_mirror_symmetry_error']:.3e}。
- 直接全波运行的最大叠加相对误差：{max_superposition_error:.3e}。
- 同输入、同初始条件重复运行波形差异：{repeat_error:.3e}。

**加法结果支持你的“物理信号可用固定规则代表数值”思路；它不验证完整的加减乘除计算机。**

## 模型与可复现条件

腔体为 160×100×60 mm，六面理想 PEC 边界。内部线性介质块相对介电常数
2.1、电导率 0.002 S/m；这是通用参数，不是测量所得的具体材料数据。
四个端口均为 50 Ω 集总端口；A、B 镜像布置，运算符和接收器位于对称面。
输入使用共同的 3 GHz 中心、1.5 GHz 宽度高斯激励，数值编码为源幅度 A/15、B/15。
运算符编码为第三个发射端口的幅度：加 0.25、减 0.5、乘 0.75、除 1.0。
每次从零场开始，仿真时间约 {args.duration_ns:g} ns。

粗网格 5 mm，细网格 2.5 mm。共 {len(cases) + 2} 次直接全波运行。
256 个加法组合是利用全波测得的三路基响应在线性条件下重建，**不是 256 次独立全波运行**。

## 固定解码如何工作

先以 A=1、B=0、运算符关闭测出一个数值单位的接收模板 r1；
以仅运算符激励测出运算符模板 ro。之后固定：

```text
z = <r1, 接收波形 - 0.25 ro> / <r1, r1>
输出 = round(z)
```

解码器不读取 A、B，也不执行 A+B；它只做固定偏置移除、固定匹配投影和量化。
由于对称线性结构把两路数据汇合为“和”的响应，z 直接对应和。
这属于模拟幅度加法与数字量化，不是逐位进位、电平恢复或可级联数字加法器。

## 减乘除为什么未通过这个模型

运算符在本模型中只作为第三束波注入，没有调节边界、相移或非线性器件。
因此相同运算符下，接收响应主要由 A+B 决定。下面是独立全波运行：

| 运算 | 两个输入样例 | 正确结果 | 接收完整波形相对差异 |
|---|---|---|---|
| 减法 | 5−3 与 4−4 | 2 与 0 | {collisions[0]['relative_receiver_waveform_difference']:.3e} |
| 乘法 | 1×4 与 2×3 | 4 与 6 | {collisions[1]['relative_receiver_waveform_difference']:.3e} |
| 除法 | 6÷2 与 4÷4 | 3 与 1 | {collisions[2]['relative_receiver_waveform_difference']:.3e} |

这些样例在单一接收器处的完整时间波形，而非仅一个幅度值，出现数值精度内的混淆。
更换信号名称不能从同一个可观测响应中读出两个不同答案。
这里的原因是所选对称结构、编码与线性机制；**不证明其他几何、编码或非线性方案不可能实现减乘除。**

## 噪声、重复性与网格检查

重复运行只检验确定性数值程序，未模拟真实温漂、机械扰动或相位噪声。
在解码前的接收标量上额外加入高斯噪声，标准差以“一档结果间距”为单位；
每个噪声条件 25600 次统计测试，详见 receiver_noise_sweep.csv。
它是统计后处理，不是 openEMS 内建材料噪声或实物可靠性结果。

3 GHz 处细、粗网格复数响应的相对变化：

- A 基响应：{frequency_relative_change:.3%}。
- 5＋3 直接响应：{fine_sum_relative_change:.3%}。

两档网格只能作敏感性诊断，不能证明收敛。如果变化较大，不应相信绝对电压、
共振细节或据此预测硬件精度；线性叠加和镜像对称结论也不等同于器件级性能验证。
固定仿真时间只定义了此次脉冲观测窗口，没有证明腔体已经完全复位或进入稳态。

## 尚未验证

非线性器件、运算符控制电路、真实导体损耗、材料色散与温漂、相位同步误差、
连续输入下的残留记忆、除零处理、扩大位宽、级联、速度与整机能耗均未验证。
当前模型是对原始构想一个明确子集的基线测试，不是现有实物设计的数字孪生。

下一步应使用开源电路求解器或支持目标非线性材料的全波工具，加入具体的相移控制、
混频与反馈结构；不能用行为表达式“输出=A×B”来声称验证了材料或腔体的乘法能力。

## 输出文件

- summary.json：机器可读参数、误差及限制。
- direct_fdtd_cases.csv：各次直接全波运行。
- addition_sweep.csv：256 个重建输入的固定解码结果。
- collision_tests.csv：不同答案的波形混淆检查。
- receiver_noise_sweep.csv：接收标量统计噪声测试。
- receiver_waveforms.csv：来自直接全波运行的接收时间波形。
- waveform_comparison.png、addition_and_noise.png：数据图。
- 原始 XML 与端口探针位于：`{args.raw_dir}`。

## 开源项目来源

openEMS 官方发布：{SOURCE_URL}

官方端口和几何 API；模型配置参考官方 Parallel_Plate_Waveguide.py、
Rect_Waveguide.py 的激励、网格和端口使用方式。
求解器发布包预期 SHA-256：`{ARCHIVE_SHA256}`。
本脚本仅配置求解器与分析数据，不修改 openEMS 源码。
"""
    (args.output / "验证报告.md").write_text(report, encoding="utf-8")
    if not all_add_correct or not all(item["correct"] for item in direct_add):
        raise RuntimeError("Addition did not pass; inspect the saved report.")
    if max_superposition_error > 1e-5:
        raise RuntimeError("Direct FDTD superposition check failed.")
    print(json.dumps(summary, ensure_ascii=True, indent=2), flush=True)
    return dll_handles


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--solver-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "结果")
    parser.add_argument("--raw-dir", type=Path,
                        default=Path(tempfile.gettempdir()) / "ewc-openems" / "fdtd")
    parser.add_argument("--duration-ns", type=float, default=15)
    arguments = parser.parse_args()
    run(arguments)
