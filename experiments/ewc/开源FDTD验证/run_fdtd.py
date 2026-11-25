"""Cavity model and tests using flaport/fdtd 0.3.5, not a custom solver."""

import argparse
import csv
import importlib.metadata
import json
from pathlib import Path
import tempfile
import time

import fdtd
import numpy as np

F0 = 3e9
MAX_INPUT = 15
OP = {"add": 0.25, "subtract": 0.5, "multiply": 0.75, "divide": 1.0}
COURANT = 299792458 / (F0 * 30 * 0.005)


def relerr(a, b):
    return float(np.linalg.norm(a - b) / max(np.linalg.norm(b), 1e-300))


def write_csv(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def solve(amplitudes, dx_mm=5, duration_ns=5, phases=(0, 0, 0),
          epsilon_r=2.1, conductivity=0, positions=None,
          receiver_mm=(80, 75), source_gains=(1, 1, 1)):
    """Use only the upstream Grid, PointSource and LineDetector APIs."""
    dx = dx_mm * 1e-3
    nx, ny = int(round(160 / dx_mm)) + 1, int(round(100 / dx_mm)) + 1
    xs, ys = np.arange(nx) * dx_mm, np.arange(ny) * dx_mm
    eps = np.ones((nx, ny, 1, 3))
    block = ((xs[:, None] >= 60) & (xs[:, None] <= 100)
             & (ys[None, :] >= 35) & (ys[None, :] <= 65))
    eps[:, :, 0, :][block] = epsilon_r
    # For this invariant TMz model, frozen Ez on the perimeter is PEC.
    # No field update equation or custom boundary class is implemented here.
    eps[[0, -1], :, :, :] = np.inf
    eps[:, [0, -1], :, :] = np.inf
    grid = fdtd.Grid(
        shape=(nx, ny, 1), grid_spacing=dx,
        permittivity=eps, courant_number=COURANT,
    )
    if conductivity:
        x_ids, y_ids = np.where(np.any(block, axis=1))[0], np.where(np.any(block, axis=0))[0]
        grid[x_ids[0]:x_ids[-1] + 1, y_ids[0]:y_ids[-1] + 1, 0:1] = fdtd.AbsorbingObject(
            permittivity=epsilon_r, conductivity=conductivity, name="lossy_block",
        )
    positions = positions or ((40, 50), (120, 50), (80, 25))
    for index, ((x, y), amplitude) in enumerate(zip(positions, amplitudes)):
        # Scale the soft point source to keep dt/dx^2 source strength fixed
        # when refining the 2D mesh. No arithmetic result enters this source.
        grid[int(round(x / dx_mm)), int(round(y / dx_mm)), 0] = fdtd.PointSource(
            period=1 / F0, amplitude=amplitude * source_gains[index] * 5 / dx_mm,
            name=f"source_{index}", phase_shift=phases[index], pulse=False,
        )
    rx, ry = [int(round(value / dx_mm)) for value in receiver_mm]
    grid[rx:rx + 1, ry:ry + 1, 0:1] = fdtd.LineDetector(name="receiver")
    started = time.perf_counter()
    grid.run(float(duration_ns * 1e-9), progress_bar=False)
    elapsed = time.perf_counter() - started
    field = np.asarray(grid.receiver.E)[:, 0, 2]
    times = np.arange(len(field)) * grid.time_step
    if len(field) < 100 or not np.all(np.isfinite(field)):
        raise RuntimeError("Invalid receiver samples.")
    if np.max(np.abs(grid.E[:, :, :, :2])) != 0 or np.max(np.abs(grid.H[:, :, :, 2])) != 0:
        raise RuntimeError("The TMz invariant was violated.")
    if np.max(np.abs(grid.E[[0, -1], :, :, 2])) != 0 or np.max(np.abs(grid.E[:, [0, -1], :, 2])) != 0:
        raise RuntimeError("PEC perimeter invariant was violated.")
    return {
        "time": times, "field": field, "snapshot": grid.E[:, :, 0, 2].copy(),
        "runtime_s": elapsed, "dt_s": grid.time_step,
        "actual_frequency_Hz": 1 / (grid.source_0.period * grid.time_step),
        "grid_shape": grid.shape, "amplitudes": amplitudes,
    }


def decode(signal, unit_template, operator_template):
    """Fixed decoder: never accepts either operand."""
    centered = signal - OP["add"] * operator_template
    z = float(np.dot(unit_template, centered) / np.dot(unit_template, unit_template))
    return z, int(np.rint(z))


def empty_cavity_benchmark(dx_mm):
    """Advance an analytic initial mode with upstream FDTD and fit its frequency."""
    from scipy.optimize import minimize_scalar

    dx, lx, ly, c = dx_mm * 1e-3, 0.16, 0.10, 299792458
    nx, ny = int(round(lx / dx)) + 1, int(round(ly / dx)) + 1
    eps = np.ones((nx, ny, 1, 3))
    eps[[0, -1], :, :, :] = np.inf
    eps[:, [0, -1], :, :] = np.inf
    grid = fdtd.Grid((nx, ny, 1), grid_spacing=dx,
                     permittivity=eps, courant_number=COURANT)
    initial = (np.sin(np.pi * np.arange(nx)[:, None] / (nx - 1))
               * np.sin(np.pi * np.arange(ny)[None, :] / (ny - 1)))
    initial[[0, -1], :] = 0
    initial[:, [0, -1]] = 0
    grid.E[:, :, 0, 2] = initial
    rx, ry = nx // 2, ny // 2
    grid[rx:rx + 1, ry:ry + 1, 0:1] = fdtd.LineDetector(name="receiver")
    grid.run(15e-9, progress_bar=False)
    waveform = np.asarray(grid.receiver.E)[:, 0, 2]
    times_ns = np.arange(len(waveform)) * grid.time_step * 1e9
    exact_ghz = c / 2 * np.sqrt(1 / lx ** 2 + 1 / ly ** 2) / 1e9

    def fit_error(f_ghz):
        phases = 2 * np.pi * f_ghz * times_ns
        design = np.column_stack((np.cos(phases), np.sin(phases), np.ones(len(phases))))
        coefficients = np.linalg.lstsq(design, waveform, rcond=None)[0]
        return float(np.mean((design @ coefficients - waveform) ** 2))

    fit = minimize_scalar(fit_error, bounds=(0.99 * exact_ghz, 1.01 * exact_ghz),
                          method="bounded", options={"xatol": 1e-12})
    if not fit.success:
        raise RuntimeError("Empty-cavity frequency fit failed.")
    fitted = fit.x * 1e9
    return fitted, float(abs(fitted / (exact_ghz * 1e9) - 1))


def main(args):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    args.output.mkdir(parents=True, exist_ok=True)
    args.raw_dir.mkdir(parents=True, exist_ok=True)
    basis = {}
    for name, amplitude in (
        ("basis_A", (1, 0, 0)), ("basis_B", (0, 1, 0)),
        ("basis_operator", (0, 0, 1)),
    ):
        print(f"Upstream FDTD: {name}", flush=True)
        basis[name] = solve(amplitude, duration_ns=args.duration_ns)
    ea, eb, eo = [basis[name]["field"] for name in ("basis_A", "basis_B", "basis_operator")]
    if np.linalg.norm(ea) == 0:
        raise RuntimeError("Receiver is silent.")
    unit = ea / MAX_INPUT
    arrays = {name: item["field"] for name, item in basis.items()}
    direct_rows, addition_rows, selected = [], [], {}
    max_superposition_error = 0

    # Every input pair is independently advanced by the upstream FDTD solver.
    for a in range(16):
        print(f"Direct FDTD addition sweep A={a}, B=0..15", flush=True)
        for b in range(16):
            result = solve((a / 15, b / 15, OP["add"]), duration_ns=args.duration_ns)
            expected_wave = (a / 15) * ea + (b / 15) * eb + OP["add"] * eo
            error = relerr(result["field"], expected_wave)
            max_superposition_error = max(max_superposition_error, error)
            z, decoded = decode(result["field"], unit, eo)
            name = f"add_{a}_{b}"
            addition_rows.append({
                "A": a, "B": b, "operator": "add", "receiver_coordinate": z,
                "decoded_result": decoded, "expected_result": a + b,
                "correct": decoded == a + b,
                "relative_waveform_superposition_error": error,
                "runtime_s": result["runtime_s"], "method": "independent upstream FDTD run",
            })
            arrays[name] = result["field"]
            if (a, b) in ((5, 3), (4, 4), (5, 4)):
                selected[name] = result
    write_csv(args.output / "addition_256_direct_runs.csv", addition_rows)
    repeat = solve((5 / 15, 3 / 15, OP["add"]), duration_ns=args.duration_ns)
    repeat_error = relerr(repeat["field"], selected["add_5_3"]["field"])
    arrays["add_5_3_repeat"] = repeat["field"]

    collision_specs = [
        ("subtract", (5, 3), (4, 4), (2, 0)),
        ("multiply", (1, 4), (2, 3), (4, 6)),
        ("divide", (6, 2), (4, 4), (3, 1)),
    ]
    collisions = []
    for op, pair1, pair2, answers in collision_specs:
        results = []
        for a, b in (pair1, pair2):
            name = f"{op}_{a}_{b}"
            print(f"Upstream FDTD collision test: {name}", flush=True)
            result = solve((a / 15, b / 15, OP[op]), duration_ns=args.duration_ns)
            arrays[name] = result["field"]
            results.append(result)
        error = relerr(results[0]["field"], results[1]["field"])
        collisions.append({
            "operator": op, "input_1": str(pair1), "input_2": str(pair2),
            "correct_answer_1": answers[0], "correct_answer_2": answers[1],
            "relative_complete_waveform_difference": error,
            "below_1e_minus_10": error < 1e-10,
        })
    write_csv(args.output / "collision_tests.csv", collisions)

    # No EM noise claim: this is additional noise on the decoded analog coordinate.
    coordinates = np.array([row["receiver_coordinate"] for row in addition_rows])
    targets = np.array([row["expected_result"] for row in addition_rows])
    rng, noise_rows = np.random.default_rng(20261117), []
    for sigma in (0, 0.05, 0.1, 0.2, 0.5, 1):
        measured = coordinates[:, None] + rng.normal(0, sigma, (256, 100))
        accuracy = float(np.mean(np.rint(measured) == targets[:, None]))
        noise_rows.append({
            "sigma_result_levels": sigma, "trials": 25600, "accuracy": accuracy,
            "model": "Gaussian receiver coordinate noise, not full-wave material noise",
        })
    write_csv(args.output / "receiver_noise.csv", noise_rows)

    # Test analytic empty-cavity TMz eigenfrequency, using the discrete
    # Laplacian's known dispersion as an independent mesh-resolution check.
    # This is a diagnostic of the discretization, not another EM solver.
    mesh_rows = []
    c, lx, ly = 299792458, 0.16, 0.10
    exact_f11 = c / 2 * np.sqrt(1 / lx ** 2 + 1 / ly ** 2)
    for dx_mm in (5, 2.5, 1.25):
        print(f"Upstream FDTD empty-cavity benchmark: dx={dx_mm} mm", flush=True)
        measured_f11, benchmark_error = empty_cavity_benchmark(dx_mm)
        dx = dx_mm * 1e-3
        dt = COURANT * dx / c
        kterm = np.sqrt(np.sin(np.pi * dx / (2 * lx)) ** 2
                        + np.sin(np.pi * dx / (2 * ly)) ** 2)
        discrete_f11 = np.arcsin(COURANT * kterm) / (np.pi * dt)
        mesh_rows.append({
            "dx_mm": dx_mm, "analytic_empty_cavity_f11_Hz": exact_f11,
            "discrete_dispersion_f11_Hz": float(discrete_f11),
            "relative_dispersion_error": float(abs(discrete_f11 / exact_f11 - 1)),
            "upstream_fdtd_fitted_f11_Hz": measured_f11,
            "fdtd_vs_analytic_relative_error": benchmark_error,
            "fdtd_vs_discrete_relative_error": float(abs(measured_f11 / discrete_f11 - 1)),
        })
    write_csv(args.output / "empty_cavity_dispersion_check.csv", mesh_rows)

    print("Upstream FDTD: fine-grid basis, operator and addition sample", flush=True)
    fine_a = solve((1, 0, 0), dx_mm=2.5, duration_ns=args.duration_ns)
    fine_o = solve((0, 0, 1), dx_mm=2.5, duration_ns=args.duration_ns)
    fine_sum = solve((5 / 15, 3 / 15, OP["add"]), dx_mm=2.5, duration_ns=args.duration_ns)
    fine_z, fine_decoded = decode(fine_sum["field"], fine_a["field"] / 15, fine_o["field"])
    coarse_on_fine = np.interp(fine_a["time"], basis["basis_A"]["time"], ea)
    mask = fine_a["time"] <= basis["basis_A"]["time"][-1]
    mesh_change = relerr(coarse_on_fine[mask], fine_a["field"][mask])

    np.savez_compressed(args.output / "receiver_arrays.npz", **arrays,
                        time=basis["basis_A"]["time"])
    shown = ["basis_A", "basis_B", "add_5_3", "add_4_4", "add_5_4",
             "multiply_1_4", "multiply_2_3"]
    wave_rows = [
        {"time_s": float(t), **{name: float(arrays[name][i]) for name in shown}}
        for i, t in enumerate(basis["basis_A"]["time"])
    ]
    write_csv(args.output / "receiver_waveforms.csv", wave_rows)
    summary = {
        "solver": "flaport/fdtd", "solver_version": fdtd.__version__,
        "solver_license": "MIT", "solver_url": "https://github.com/flaport/fdtd",
        "solver_module": fdtd.__file__,
        "scope": "2D TMz only, not a 3D cavity or a material-specific digital twin",
        "direct_fdtd_runs": 3 + 256 + 1 + 6 + 3 + 3,
        "addition_direct_cases": 256,
        "addition_correct": sum(row["correct"] for row in addition_rows),
        "max_coordinate_absolute_error": float(np.max(np.abs(coordinates - targets))),
        "max_direct_superposition_error": max_superposition_error,
        "mirror_source_response_error": relerr(ea, eb),
        "repeat_same_initial_state_error": repeat_error,
        "geometry_mm": [160, 100],
        "epsilon_r_block": 2.1, "material_loss": 0,
        "boundary": "Ez=0 frozen perimeter, PEC in invariant 2D TMz",
        "receiver_quantity": "Ez in normalized solver field units, no antenna load",
        "source_model": "Ideal soft point source, amplitude A/15 or B/15",
        "operator_amplitude_codes": OP,
        "duration_ns": args.duration_ns, "source_frequency_Hz": F0,
        "actual_discrete_source_frequency_Hz": basis["basis_A"]["actual_frequency_Hz"],
        "coarse_grid_shape": basis["basis_A"]["grid_shape"],
        "coarse_dt_s": basis["basis_A"]["dt_s"],
        "receiver_samples": len(ea), "collisions": collisions, "noise": noise_rows,
        "empty_cavity_benchmarks": mesh_rows,
        "mesh_check": {
            "coarse_dx_mm": 5, "fine_dx_mm": 2.5,
            "relative_time_waveform_change": mesh_change,
            "fine_add_5_3_coordinate": fine_z, "fine_add_5_3_decoded": fine_decoded,
            "warning": "Two grids, point source and staircased material; not convergence proof.",
        },
        "dependency_versions": {name: importlib.metadata.version(name)
                                for name in ("numpy", "scipy", "matplotlib", "fdtd")},
        "openEMS_status": "Official archive verified; Windows application control blocked execution.",
        "openEMS_archive_sha256": "e4f14fd6e9be7d0c1fd9c244768c21c1c2b62b8aa81f08253ebf6e06040cda5a",
    }
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    fig, axes = plt.subplots(2, 1, figsize=(10, 7), constrained_layout=True)
    times = basis["basis_A"]["time"] * 1e9
    for name in ("add_5_3", "add_4_4", "add_5_4"):
        axes[0].plot(times, arrays[name], label=name)
    axes[0].set(title="Independent open-source FDTD: addition", ylabel="Ez (solver units)")
    for name in ("multiply_1_4", "multiply_2_3"):
        axes[1].plot(times, arrays[name], label=name)
    axes[1].set(title="Different products, indistinguishable receiver waveforms",
                xlabel="Time (ns)", ylabel="Ez (solver units)")
    for ax in axes:
        ax.grid(alpha=0.25)
        ax.legend()
    fig.savefig(args.output / "waveforms.png", dpi=150)
    plt.close(fig)
    fig, axes = plt.subplots(1, 2, figsize=(10, 4), constrained_layout=True)
    matrix = np.array([row["decoded_result"] for row in addition_rows]).reshape(16, 16)
    image = axes[0].imshow(matrix, origin="lower", interpolation="nearest")
    axes[0].set(title="256 independent FDTD addition runs", xlabel="B", ylabel="A")
    fig.colorbar(image, ax=axes[0], label="Decoded result")
    axes[1].plot([row["sigma_result_levels"] for row in noise_rows],
                 [row["accuracy"] * 100 for row in noise_rows], "o-")
    axes[1].set(title="Receiver coordinate noise (post-processing)",
                xlabel="Noise sigma (result levels)", ylabel="Accuracy (%)")
    axes[1].grid(alpha=0.25)
    fig.savefig(args.output / "addition_noise.png", dpi=150)
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(8, 5), constrained_layout=True)
    image = ax.imshow(selected["add_5_3"]["snapshot"].T, origin="lower",
                      extent=(0, 160, 0, 100), cmap="RdBu_r")
    for (x, y), label in zip(((40, 50), (120, 50), (80, 25), (80, 75)),
                             ("A", "B", "OP", "RX")):
        ax.plot(x, y, "ko")
        ax.text(x + 3, y + 3, label, color="black")
    ax.set(title="2D TMz cavity: field after the 5+3 run",
           xlabel="x (mm)", ylabel="y (mm)")
    fig.colorbar(image, ax=ax, label="Ez (normalized solver units)")
    fig.savefig(args.output / "cavity_field.png", dpi=150)
    plt.close(fig)

    collision_lines = "\n".join(
        f"| {row['operator']} | {row['input_1']} / {row['input_2']} | "
        f"{row['correct_answer_1']} / {row['correct_answer_2']} | "
        f"{row['relative_complete_waveform_difference']:.3e} |"
        for row in collisions
    )
    report = f"""# 开源 FDTD 电磁计算基线验证

## 先说明实际使用的工具

按要求使用现有开源项目，没有自行编写 Maxwell 方程求解器。
实际运行的是 **flaport/fdtd {fdtd.__version__}，MIT 许可证，NumPy 后端**。
官方 openEMS v0.37.0 已通过国内可访问镜像下载，SHA-256 与官方发布一致；
但其扩展模块被 Windows 应用控制策略阻止，独立程序也不能运行。
没有关闭安全策略。因此，**本报告不是 openEMS 结果，也不是三维全波验证结果**。

## 验证结果

在以下明确的二维线性模型中：

- 0～15 的两个整数输入，256 个组合全部独立调用开源 FDTD 求解器运行；
  正确解码 {summary['addition_correct']}/256，输出范围 0～30。
- 固定解码结果与目标和的最大绝对偏差：{summary['max_coordinate_absolute_error']:.3e}。
- 直接全波波形与基响应线性叠加的最大相对误差：{max_superposition_error:.3e}。
- 镜像数据源到接收点的波形相对差异：{summary['mirror_source_response_error']:.3e}。
- 5＋3 从相同零场初态重复运行的相对差异：{repeat_error:.3e}。

**这支持你的“电磁波输出可以按固定规则代表数值”思路，并验证了该模型的模拟加法。
它不证明已经实现完整加减乘除系统或通用计算机。**

## 模型假设

二维 TMz 模式，电场只有 Ez，磁场为 Hx、Hy；z 方向不模拟变化。
x、y 截面为 160×100 mm，边缘 Ez 冻结为零，对该模式表示 PEC 边界。
内部 60～100 mm、35～65 mm 区域设相对介电常数 2.1，无材料损耗。
这是通用线性介质模型，不是某种真实材料的实测数据。

数据源 A=(40,50) mm，B=(120,50) mm；运算符源=(80,25) mm；
接收点=(80,75) mm。发射源使用项目自带 PointSource，接收使用 LineDetector
单点采样，读取完整 Ez 时间序列，没有模拟真实天线阻抗、负载或测量电路。

三源共同使用 3 GHz 正弦，数值编码为源幅度 A/15、B/15。
第三源的运算符幅度编码：加 0.25、减 0.5、乘 0.75、除 1.0。
源是理想软点源，幅度表示求解器的场注入参数，不是实际发射功率或电压。
每次零场初始化，观察 {args.duration_ns:g} ns；不要求场达到稳态或在结束时消失。

粗网格 5 mm，CFL 数 {COURANT:.6f}，时间步长 {summary['coarse_dt_s']:.3e} s；
离散源实际频率 {summary['actual_discrete_source_frequency_Hz']:.6e} Hz。
总计 {summary['direct_fdtd_runs']} 次直接 FDTD 运行，无训练集查表代替波演化。

## 固定解码不是临时给每个输入指定答案

先测出 A 基响应及运算符基响应。令 r1 为一个整数单位的接收模板，ro 为运算符模板。
之后对全部加法输入保持同一个规则：

```text
z = <r1, 接收波形 - 0.25 ro> / <r1, r1>
输出数值 = round(z)
```

解码器不接收 A、B，不调用加法表；只做固定偏置移除、匹配投影和量化。
因为本模型的镜像线性结构产生与 A+B 对应的接收响应，这种简单解码有效。
这是整体幅度编码的模拟算术，不是逐位数字进位或可靠级联逻辑。

## 只加入运算符发射器，为什么减乘除仍不成立

同一运算符下，镜像输入使接收端只能看到与 A+B 对应的数据响应。
第三束波只是增加了一个运算符相关项，没有控制边界或引入非线性。
以下样例都是直接 FDTD 运行，比较的是完整接收时间波形：

| 运算 | 两个输入对 | 正确答案 | 波形相对差异 |
|---|---|---|---|
{collision_lines}

不同正确答案对应几乎同一个接收响应，固定解码无法消除这种信息混淆。
这是 **此结构、此编码、此接收模式** 的失败，不是所有电磁波方案的不可能性证明。
不同几何、编码、多特征读取或非线性控制仍可研究。

## 数值可信度与局限

细网格 2.5 mm 对 5＋3 重新校准基响应后，接收坐标为 {fine_z:.12g}，
解码为 {fine_decoded}。粗细网格 A 基响应完整时间波形的相对变化为 {mesh_change:.3%}。
点源、介质边界阶梯化和长时间相位误差会影响这项指标；
两档网格及重新校准并不证明绝对场强已经收敛，也不证明真实硬件精度。

空腔另做三次直接 FDTD 基准：以解析 TMz 基模初始化，记录自由演化，
拟合频率并与连续解析解、离散色散公式比较。
5、2.5、1.25 mm 网格相对连续解析频率的误差分别为
{mesh_rows[0]['fdtd_vs_analytic_relative_error']:.6%}、
{mesh_rows[1]['fdtd_vs_analytic_relative_error']:.6%}、
{mesh_rows[2]['fdtd_vs_analytic_relative_error']:.6%}。
详见 empty_cavity_dispersion_check.csv；这验证了空腔模式与数值色散，
不是对含介质、点源模型的完整独立验证。

接收标量上额外加入高斯噪声，各条件 25600 次统计；这属于后处理噪声测试，
不是全波材料噪声或天线噪声仿真。重复运行的零差异只说明确定性程序重复性，
不能据此声称真实器件无噪声、无温漂或无限可靠。

未验证：三维模式、导体损耗、材料色散、温漂、自由运行发射器相位漂移、
非线性材料、运算符控制电路、动态任务切换、连续输入残留、除零处理、
大位宽、级联、速度和整机能耗。

## 文件与复现

- addition_256_direct_runs.csv：256 次独立加法运行的原始解码与误差。
- receiver_waveforms.csv：代表性直接仿真的接收时间序列。
- collision_tests.csv：不同答案的接收信号混淆。
- receiver_noise.csv：统计后处理噪声测试。
- summary.json：模型参数、求解器版本、依赖版本与指标。
- waveforms.png、addition_noise.png、cavity_field.png：仿真数据图。
- 全部接收数组保存于 `{args.output / 'receiver_arrays.npz'}`。
- run_fdtd.py：模型配置和结果分析；求解器更新方程来自已安装的开源项目。

开源项目：https://github.com/flaport/fdtd

## 下一步

完整三维与材料验证尚未完成。要推进减乘除，应先指定运算符如何控制相位、
内部器件或非线性机制，再用允许执行的开源电磁与电路工具验证。
不能在行为源里直接写“输出=A×B”来声称验证了腔体或材料的乘法。
"""
    (args.output / "验证报告.md").write_text(report, encoding="utf-8")
    if summary["addition_correct"] != 256 or fine_decoded != 8:
        raise RuntimeError("Addition failed; report retained for inspection.")
    if max_superposition_error > 1e-10:
        raise RuntimeError("Numerical superposition test failed.")
    if any(row["fdtd_vs_discrete_relative_error"] > 1e-6 for row in mesh_rows):
        raise RuntimeError("Upstream solver disagrees with empty-cavity discrete dispersion.")
    print(json.dumps(summary, ensure_ascii=True, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "结果")
    parser.add_argument("--raw-dir", type=Path,
                        default=Path(tempfile.gettempdir()) / "ewc-fdtd-results")
    parser.add_argument("--duration-ns", type=float, default=5)
    main(parser.parse_args())
