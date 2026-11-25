"""EWC 腔体计算仿真原型 v1。

功能：输入 4 位码 -> 四个源端口以相位 +1/-1 注入铜腔 -> 六个端口读取复幅度
-> 用四条基向量线性解码 -> 输出识别出的 4 位码；同时给出 1 位判决演示。

边界：端口是 delta-gap 边簇、负载是同一算子内的并联电导、检测与判决是离线的
锁相读出后处理，没有真实接头、探测器、比较器与硬件。七包门槛仍为 0/7。

证据规则：只写新目录；目录已存在则拒绝运行；所有结论挂源文件哈希与用时。
"""

import os

for _key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
             "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_key, "1")

import argparse  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import run_end_to_end_smoke as smoke  # noqa: E402

SOURCES = [0, 1, 3, 4]
LOADS = [2, 5]
CODES = [tuple(int(c) for c in format(value, "04b")) for value in range(15, -1, -1)]
STEPS = 12000


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def code_name(code):
    return "code_" + "".join(str(int(bit)) for bit in code)


def payloads(steps):
    rows = []
    for port in SOURCES:
        rows.append((len(rows), "basis_p%d" % port, [port], LOADS,
                     smoke.F0, 0.0, [1.0], steps))
    for code in CODES:
        rows.append((len(rows), code_name(code), list(SOURCES), LOADS,
                     smoke.F0, 0.0, [1.0 if bit else -1.0 for bit in code], steps))
    return rows


def td_amp(case):
    return smoke.amplitudes(case["time_domain"]["amplitude"])


def fd_amp(case, port):
    return smoke.amplitudes(case["frequency_domain"]["rows"][port]["amplitude"])


def run(out, steps, workers):
    report = dict(
        name="EWC 腔体计算仿真原型 v1",
        created=time.strftime("%Y-%m-%dT%H:%M:%S"),
        scope="四条基向量 + 全 16 组四位码，时域锁相读出与频域算子解双路解码",
        evidence_level="A（本机全波时域/频域实跑，同一离散算子，交叉对照）",
        grid=dict(shape=smoke.SHAPE, lengths_m=smoke.LENGTHS,
                  frequency_Hz=float(smoke.F0), dt_s=smoke.DT,
                  steps=steps, load_ohm=smoke.RL),
        encoding="四个源端口同时注入，幅度 +1/-1 表示比特 1/0（相位 0 或 pi）",
        decode="六端口复幅度对四条基向量做最小二乘，系数符号即比特",
        workers=int(workers),
        source_sha256={name: sha(HERE / name) for name in smoke.FILES},
        runner_sha256=sha(Path(__file__).resolve()),
        limitations=[
            "端口是 delta-gap 边簇，不是真实 SMA/同轴馈口",
            "负载是同一算子内的并联电导，不是实体端接",
            "检测/判决是锁相读出的离线后处理，没有射频检波器或比较器",
            "解码是线性叠加的码本回收，不是通用逻辑门或进位运算",
            "单一几何、单频点，无硬件、无 BOM、无温度与老化",
            "不能据此声称真实开关速度或系统级精度",
        ],
        not_claimed=dict(full_system_gates_closed=0, total_system_gates=7,
                         goal_complete=False),
    )
    grid, r, l, gaps = smoke.build(report)
    out.joinpath("configurations.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["port_edges"] = gaps.tolist()

    window = int(round(smoke.PERIODS / smoke.F0 / smoke.DT))
    if steps <= 2 * window:
        raise SystemExit("steps=%d 必须大于两个锁相窗（%d）" % (steps, 2 * window))
    report["lockin_window_steps"] = window

    specs = payloads(steps)
    cases, started = [], time.perf_counter()
    import concurrent.futures as futures
    with futures.ProcessPoolExecutor(max_workers=workers) as pool:
        pending = {pool.submit(smoke.run_case, spec): spec for spec in specs}
        for future in futures.as_completed(pending):
            cases.append(future.result())
            print("done %d/%d %s" % (len(cases), len(specs), future.result()["name"]),
                  flush=True)
    elapsed = time.perf_counter() - started
    cases.sort(key=lambda row: row["index"])
    out.joinpath("cases.json").write_text(
        json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")

    by_name = {case["name"]: case for case in cases}
    basis_td = np.column_stack([td_amp(by_name["basis_p%d" % port]) for port in SOURCES])

    # 跨域复尺度：用全部单源基向量例做最小二乘，频域基向量必须先乘 alpha
    # 才能与时域测量共用同一套最小二乘解码（见 run_end_to_end_smoke.py）。
    numerator, denominator = 0j, 0.0
    for port in SOURCES:
        a_fd = fd_amp(by_name["basis_p%d" % port], port)
        a_td = td_amp(by_name["basis_p%d" % port])
        numerator += np.vdot(a_fd, a_td)
        denominator += float(np.vdot(a_fd, a_fd).real)
    alpha = numerator / denominator
    basis_fd = np.column_stack([alpha * fd_amp(by_name["basis_p%d" % port], port)
                                for port in SOURCES])

    rows = []
    for code in CODES:
        case = by_name[code_name(code)]
        measured = td_amp(case)
        coeff_td, residual_td, rank_td, condition = smoke.decode(basis_td, measured)
        coeff_fd, residual_fd, _, _ = smoke.decode(basis_fd, measured)
        bits_td = [int(value > 0) for value in coeff_td]
        bits_fd = [int(value > 0) for value in coeff_fd]
        rows.append(dict(
            input_bits=list(code), td_bits=bits_td, fd_bits=bits_fd,
            td_match=bool(bits_td == list(code)), fd_match=bool(bits_fd == list(code)),
            td_coefficients=[float(value) for value in coeff_td.real],
            fd_coefficients=[float(value) for value in coeff_fd.real],
            td_min_abs_coefficient=float(np.min(np.abs(coeff_td.real))),
            td_decode_residual=float(residual_td), fd_decode_residual=float(residual_fd),
            basis_rank_td=int(rank_td), decode_condition=float(condition),
            td_seconds=float(case["time_domain"]["total_seconds"]),
            fd_lu_seconds=float(case["frequency_domain"]["lu_seconds"])))

    # 一位判决：单源 p0 / p4 时，比较接收端口 1 与 4 的幅度。
    def decision(port):
        amp = td_amp(by_name["basis_p%d" % port])
        ratio = float(abs(amp[1]) / abs(amp[4]))
        return dict(source_port=port, amplitude_port1=float(abs(amp[1])),
                    amplitude_port4=float(abs(amp[4])), ratio=ratio,
                    bit=int(ratio > 1.0), margin=float(abs(ratio - 1.0)))

    summary = dict(
        status="v1_simulation_prototype_complete",
        created=time.strftime("%Y-%m-%dT%H:%M:%S"),
        parameters=dict(steps_per_case=steps, lockin_window_steps=window,
                        periods=smoke.PERIODS, frequency_Hz=float(smoke.F0),
                        dt_s=smoke.DT, shape=smoke.SHAPE, lengths_m=smoke.LENGTHS,
                        source_ports=SOURCES, load_ports=LOADS,
                        load_ohm=smoke.RL, workers=int(workers)),
        codes=rows,
        totals=dict(cases=len(rows),
                    td_match=int(sum(row["td_match"] for row in rows)),
                    fd_match=int(sum(row["fd_match"] for row in rows)),
                    all_td_match=bool(all(row["td_match"] for row in rows)),
                    all_fd_match=bool(all(row["fd_match"] for row in rows)),
                    min_abs_coefficient=float(min(row["td_min_abs_coefficient"]
                                                  for row in rows)),
                    max_td_decode_residual=float(max(row["td_decode_residual"]
                                                     for row in rows)),
                    max_fd_decode_residual=float(max(row["fd_decode_residual"]
                                                     for row in rows)),
                    decode_condition=float(rows[0]["decode_condition"]),
                    basis_rank_td=int(rows[0]["basis_rank_td"])),
        one_bit=dict(decisions=[decision(1), decision(4)]),
        scale_alpha=dict(real=float(alpha.real), imag=float(alpha.imag),
                         modulus=float(abs(alpha)),
                         phase_deg=float(np.degrees(np.angle(alpha)))),
        solve_seconds=dict(twenty_cases_wall=float(elapsed),
                           per_case_mean=float(np.mean([row["td_seconds"] for row in rows]))),
        source_sha256=report["source_sha256"], runner_sha256=report["runner_sha256"],
        full_system_gates_closed=0, total_system_gates=7, goal_complete=False)
    out.joinpath("summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    out.joinpath("report.md").write_text(render(report, summary), encoding="utf-8")
    return summary


def render(report, summary):
    params, totals = summary["parameters"], summary["totals"]
    lines = [
        "# EWC 腔体计算仿真原型 v1",
        "",
        "> 结论边界：这是**仿真原型**。端口为 delta-gap 边簇、负载为并联电导、"
        "检测判决为离线后处理；七包门槛仍为 0/7，不得当作硬件验证。",
        "",
        "## 一、这一版做了什么",
        "",
        "输入 4 位码，在四个源端口以 +1/-1 幅度（相位 0/π）同时注入铜腔，"
        "在六个端口用锁相方式读出复幅度，再对四条基向量做最小二乘解码，"
        "由系数符号还原比特。全部 16 组码都实跑，没有抽样。",
        "",
        "## 二、参数与用时",
        "",
        "| 项 | 值 |",
        "|---|---|",
        "| 网格 | %s |" % (tuple(params["shape"]),),
        "| 腔体尺寸 / m | %s |" % (tuple(params["lengths_m"]),),
        "| 频率 / Hz | %.6e |" % params["frequency_Hz"],
        "| 时间步 / s | %.3e |" % params["dt_s"],
        "| 每例步数 | %d |" % params["steps_per_case"],
        "| 并行进程 | %d |" % params["workers"],
        "| 20 例墙钟 / s | %.1f |" % summary["solve_seconds"]["twenty_cases_wall"],
        "| 单例平均 / s | %.1f |" % summary["solve_seconds"]["per_case_mean"],
        "| 跨域尺度因子 alpha | %.5f ∠ %.2f° |" % (
            summary["scale_alpha"]["modulus"], summary["scale_alpha"]["phase_deg"]),
        "",
        "## 三、16 组码的识别结果",
        "",
        "| 输入码 | 时域解出 | 频域解出 | 时域一致 | 最小系数 | 解码残差(时域) |",
        "|---|---|---|---|---|---|",
    ]
    for row in summary["codes"]:
        lines.append("| %s | %s | %s | %s | %.4f | %.2e |" % (
            "".join(str(bit) for bit in row["input_bits"]),
            "".join(str(bit) for bit in row["td_bits"]),
            "".join(str(bit) for bit in row["fd_bits"]),
            "是" if row["td_match"] else "否",
            row["td_min_abs_coefficient"], row["td_decode_residual"]))
    lines += [
        "",
        "汇总：时域 %d/%d 组一致，频域 %d/%d 组一致；基向量秩 %d，条件数 %.3f；"
        "时域最大解码残差 %.2e，频域最大解码残差 %.2e。" % (
            totals["td_match"], totals["cases"], totals["fd_match"], totals["cases"],
            totals["basis_rank_td"], totals["decode_condition"],
            totals["max_td_decode_residual"], totals["max_fd_decode_residual"]),
        "",
        "## 四、一位判决演示",
        "",
        "单个源在端口 1、4 之间做差分幅度比较：比值 > 1 判 1，否则判 0。",
        "",
        "| 源端口 | 端口1幅度 | 端口4幅度 | 比值 | 判为 | 距阈值 |",
        "|---|---|---|---|---|---|",
    ]
    for row in summary["one_bit"]["decisions"]:
        lines.append("| p%d | %.4f | %.4f | %.4f | %d | %.4f |" % (
            row["source_port"], row["amplitude_port1"], row["amplitude_port4"],
            row["ratio"], row["bit"], row["margin"]))
    lines += [
        "",
        "## 五、复现与证据",
        "",
        "```powershell",
        "python ewc_v1.py --out <新目录> --steps %d --workers 2" % params["steps_per_case"],
        "```",
        "",
        "源文件 SHA256（前 12 位）：" + "、".join(
            "%s=%s" % (name.split("/")[-1], digest[:12])
            for name, digest in summary["source_sha256"].items()),
        "",
        "运行器 SHA256（前 12 位）：%s" % summary["runner_sha256"][:12],
        "",
        "## 六、明确不声明",
        "",
    ]
    lines += ["- " + item for item in report["limitations"]]
    lines += [
        "- 七包门槛 0/7，`full_system_gates_closed=0`，`goal_complete=false`。",
        "",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(HERE / "EWC_v1仿真原型结果"))
    parser.add_argument("--steps", type=int, default=STEPS)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    out = Path(args.out)
    if out.exists():
        raise SystemExit("拒绝覆盖已存在的证据目录：%s" % out)
    out.mkdir(parents=True)
    summary = run(out, args.steps, args.workers)
    print(json.dumps(summary["totals"], ensure_ascii=False, indent=2))
    print("输出：%s" % out)


if __name__ == "__main__":
    main()
