"""Surrogate end-to-end route smoke: source -> copper cavity -> ports -> load ->
detection -> decision -> calibration. New evidence only; never overwrite.

This is NOT an openEMS run and NOT a G6/G7 closure. Ports are 2x2 delta-gap
edge clusters on the bottom face, loads are shunt 50 ohm conductances inside the
same midpoint operator, detection is a phase-locked readout of the gap voltage,
and the decision/calibration stages are evaluated offline on those readouts.
"""

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np
from scipy.sparse import diags
from scipy.sparse.linalg import splu

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from finite_copper_cavity import CopperCavity  # noqa: E402
from copper_driven_midpoint import DrivenMidpoint  # noqa: E402

FILES = ["finite_copper_cavity.py", "finite_pec_cavity.py",
         "copper_midpoint_cg.py", "copper_driven_midpoint.py",
         "厚铜边界基准结果/passive_surface_models.json"]
SHAPE = (12, 9, 7)
LENGTHS = (0.16, 0.10, 0.06)
F0 = 299792458.0 / 2 * np.hypot(1 / LENGTHS[0], 1 / LENGTHS[1])
DT = 5e-12
RL = 50.0
GL = 1.0 / RL
STEPS = 30000
PERIODS = 20
PORTS = [(1, 1), (6, 1), (10, 1), (1, 5), (6, 5), (10, 5)]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def cpx(value):
    value = complex(value)
    return [float(value.real), float(value.imag)]


def build(report):
    model = next(row for row in json.loads(
        (HERE / "厚铜边界基准结果/passive_surface_models.json").read_text(
            encoding="utf-8"))["models"] if row["terms"] == 64)
    r = np.asarray(model["R_ohm"], float)
    l = np.asarray(model["L_H"], float)
    grid = CopperCavity(SHAPE, LENGTHS)
    es2, low = grid.pec.es[2], int(grid.pec.eoff[2])
    gaps = np.array([[low + np.ravel_multi_index((i0 + di, j0 + dj, 0), es2)
                      for di in range(2) for dj in range(2)]
                     for i0, j0 in PORTS])
    assert np.isin(gaps, grid.pec.active).all(), "port edges outside active set"
    assert len(np.unique(gaps)) == gaps.size, "port clusters overlap"
    report["grid"] = dict(shape=SHAPE, lengths=LENGTHS, ne=int(grid.ne),
                          nf=int(grid.nf), frequency_Hz=float(F0),
                          port_edges=gaps.tolist(), dt_s=DT, load_ohm=RL)
    return grid, r, l, gaps


def drive_vector(grid, gaps, port):
    d = np.zeros(grid.ne)
    d[gaps[port]] = 1.0 / gaps.shape[1]
    return d


def frequency_domain(grid, r, l, gaps, load_ports, sources, freq):
    _, per_record, total = grid.admittance(freq, r, l)
    loaded = total.copy()
    for p in load_ports:
        np.add.at(loaded, gaps[p], GL)
    s = 2j * np.pi * freq
    operator = (grid.stiffness + diags(s * s * grid.m + s * loaded)).tocsc()
    t0 = time.perf_counter()
    factor = splu(operator, permc_spec="MMD_AT_PLUS_A")
    lu_seconds = time.perf_counter() - t0
    rows = {}
    for p in sources:
        drive = np.zeros(grid.ne, complex)
        drive[gaps[p]] = 1.0 / gaps.shape[1]
        t1 = time.perf_counter()
        e = factor.solve(s * drive)
        solve_seconds = time.perf_counter() - t1
        p_src = 0.5 * float(np.real(np.vdot(e, drive)))
        p_wall = 0.5 * float(np.sum(per_record.real *
                                    np.abs(e[grid.wall_edges]) ** 2))
        p_load = 0.5 * GL * float(sum(np.sum(np.abs(e[gaps[q]]) ** 2)
                                      for q in load_ports))
        rows[p] = dict(amplitude=[cpx(np.mean(e[gaps[m]])) for m in range(len(PORTS))],
                       z_in=cpx(np.vdot(e, drive)),
                       abs_s11=float(abs((np.vdot(e, drive) - RL) /
                                         (np.vdot(e, drive) + RL))),
                       p_source_W=p_src, p_wall_W=p_wall, p_load_W=p_load,
                       power_closure=float((p_wall + p_load) / p_src)
                       if p_src > 0 else None,
                       load_fraction=float(p_load / p_src) if p_src > 0 else None,
                       solve_seconds=solve_seconds)
    return dict(frequency_Hz=float(freq), load_ports=list(load_ports),
                lu_seconds=lu_seconds, rows=rows)


class LoadedDriven(DrivenMidpoint):
    """Midpoint CG with extra shunt conductances on selected edge clusters."""

    def __init__(self, grid, dt, r, l, load_edges, load_g):
        super().__init__(grid, dt, r, l)
        conductance = np.bincount(self.edges, weights=1 / self.effective,
                                  minlength=grid.ne)
        for edges in load_edges:
            conductance = conductance + np.bincount(
                edges, weights=np.full(len(edges), float(load_g)),
                minlength=grid.ne)
        operator = (diags(2 * grid.m / dt + conductance) +
                    dt / 2 * grid.stiffness).tocsr()
        self.operator = operator
        self.invroot = 1 / np.sqrt(operator.diagonal())
        scale = diags(self.invroot)
        self.scaled = (scale @ operator @ scale).tocsr()
        self.load_g = float(load_g)


def time_domain(grid, r, l, gaps, load_ports, sources, freq, steps, ref_phase=0.0):
    solver = LoadedDriven(grid, DT, r, l, [gaps[p] for p in load_ports], GL)
    drives = {p: drive_vector(grid, gaps, p) for p, _, _ in sources}
    wt = 2 * np.pi * freq
    window = int(round(PERIODS / freq / DT))
    acc = np.zeros(len(PORTS), complex)
    e = np.zeros(grid.ne)
    b = np.zeros(grid.nf)
    state = np.zeros_like(solver.rr)
    t0 = time.perf_counter()
    iters, residual = 0, 0.0
    for n in range(steps):
        tm = (n + 0.5) * DT
        source = np.zeros(grid.ne)
        for p, phase, amp in sources:
            source += amp * np.sin(wt * tm + phase) * drives[p]
        e, b, state, _, iters, residual = solver.step(e, b, state, source)
        if n >= steps - window:
            kernel = np.exp(-1j * wt * tm)
            for m in range(len(PORTS)):
                acc[m] += kernel * np.mean(e[gaps[m]])
    elapsed = time.perf_counter() - t0
    return dict(amplitude=[cpx(2 * value / window) for value in acc],
                steps=steps, step_seconds=elapsed / steps,
                total_seconds=elapsed, last_iterations=int(iters),
                last_residual=float(residual), ref_phase=float(ref_phase))


_GRID = {}


def get_grid():
    if not _GRID:
        report = {}
        grid, r, l, gaps = build(report)
        _GRID.update(dict(grid=grid, r=r, l=l, gaps=gaps))
    return _GRID["grid"], _GRID["r"], _GRID["l"], _GRID["gaps"]


def run_case(payload):
    index, name, source_ports, load_ports, freq, ref_phase, amps, steps = payload
    grid, r, l, gaps = get_grid()
    td = time_domain(grid, r, l, gaps, load_ports,
                     [(p, ref_phase, a) for p, a in zip(source_ports, amps)],
                     freq, steps)
    rows = frequency_domain(grid, r, l, gaps, load_ports, source_ports, freq)
    return dict(index=int(index), name=name, source_ports=list(source_ports),
                source_amplitudes=[float(a) for a in amps],
                load_ports=list(load_ports), frequency_Hz=float(freq),
                ref_phase=float(ref_phase), time_domain=td,
                frequency_domain=rows)


def amplitudes(row):
    return np.array([complex(*pair) for pair in row])


def decode(basis, measurement):
    matrix = np.vstack([basis.real, basis.imag])
    vector = np.concatenate([measurement.real, measurement.imag])
    coeff, _, rank, singular = np.linalg.lstsq(matrix, vector, rcond=None)
    residual = np.linalg.norm(matrix @ coeff - vector) / np.linalg.norm(vector)
    condition = float(singular[0] / singular[-1]) if singular[-1] > 0 else np.inf
    return coeff, float(residual), int(rank), condition


def run(out, steps, workers=1):
    version = {name: sha(HERE / name) for name in FILES}
    runner = sha(Path(__file__).resolve())
    report = dict(created=time.strftime("%Y-%m-%dT%H:%M:%S"),
                  scope="surrogate end-to-end route smoke: FIT copper cavity, "
                        "six 2x2 delta-gap ports, shunt 50 ohm loads, phase-locked "
                        "readout, differential decision, four-bit calibration decode",
                  evidence_level="A (local full-wave time and frequency domain, "
                                 "cross-checked) for the surrogate interface only",
                  limitations=[
                      "ports are axial delta-gap edge clusters, not real SMA/coaxial feeds",
                      "loads are shunt conductances inside the same operator, not a physical termination",
                      "detection and decision are offline post-processing of gap voltages, no RF detector or comparator",
                      "single fixed geometry, single frequency, ten-port cluster set; no hardware, no BOM",
                      "no eight-state/twenty-four-transition long history, no 512-combination G7 matrix",
                      "cannot be used to claim real 20 ns switching speed or system level 0.5 percent accuracy"],
                  not_claimed=dict(full_system_gates_closed=0, total_system_gates=7,
                                   goal_complete=False),
                  steps_per_case=steps, periods_used=PERIODS, workers=int(workers),
                  source_sha256=version, runner_sha256=runner)
    grid, r, l, gaps = build(report)

    window = int(round(PERIODS / F0 / DT))
    if steps <= 2 * window:
        raise SystemExit("steps=%d must exceed two lock-in windows (%d)"
                         % (steps, 2 * window))
    report["lockin_window_steps"] = window
    report["warmup_steps"] = steps - window

    load_path = out / "configurations.json"
    load_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                         encoding="utf-8")

    cases = []
    g6 = [("g6_src_p0", [0], [1, 2, 3, 4, 5], F0, 0.0),
          ("g6_src_p0_ref90", [0], [1, 2, 3, 4, 5], F0, np.pi / 2),
          ("g6_src_p0_detune", [0], [1, 2, 3, 4, 5], F0 * 1.002, 0.0),
          ("g6_src_p1", [1], [0, 2, 3, 4, 5], F0, 0.0),
          ("g6_src_p4", [4], [0, 1, 2, 3, 5], F0, 0.0)]
    g7_sources = [0, 1, 3, 4]
    g7_loads = [2, 5]
    g7_codes = [(1, 1, 1, 1), (1, 0, 1, 0), (0, 1, 0, 1), (0, 0, 1, 1)]
    specs = [(name, ports, loads, freq, phase, [1.0] * len(ports))
             for name, ports, loads, freq, phase in g6]
    specs += [("g7_basis_p%d" % p, [p], g7_loads, F0, 0.0, [1.0])
              for p in g7_sources]
    # Each code bit is encoded as the source phase: +1 keeps phase 0, -1 adds pi.
    specs += [("g7_code_%d%d%d%d" % code, list(g7_sources), g7_loads, F0, 0.0,
               [1.0 if bit else -1.0 for bit in code])
              for code in g7_codes]

    payloads = [(index, name, ports, loads, freq, phase, amps, steps)
                for index, (name, ports, loads, freq, phase, amps)
                in enumerate(specs)]
    if workers > 1:
        import concurrent.futures as futures
        with futures.ProcessPoolExecutor(max_workers=workers) as pool:
            pending = {pool.submit(run_case, payload): payload[0]
                       for payload in payloads}
            for future in futures.as_completed(pending):
                case = future.result()
                cases.append(case)
                print("done %d/%d %s" % (len(cases), len(payloads), case["name"]),
                      flush=True)
                cases.sort(key=lambda row: row["index"])
                (out / "cases.json").write_text(
                    json.dumps(cases, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    else:
        for payload in payloads:
            print("running", payload[1], flush=True)
            cases.append(run_case(payload))
            (out / "cases.json").write_text(
                json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")

    by_name = {case["name"]: case for case in cases}

    def td_amp(name):
        return amplitudes(by_name[name]["time_domain"]["amplitude"])

    def fd_amp(name, port):
        return amplitudes(by_name[name]["frequency_domain"]["rows"][port]["amplitude"])

    # Cross-domain scale from every single-source case.
    singles = [c for c in cases if len(c["source_ports"]) == 1]
    numerator = 0j
    denominator = 0.0
    for case in singles:
        port = case["source_ports"][0]
        a_fd = fd_amp(case["name"], port) * np.exp(1j * case["ref_phase"])
        a_td = td_amp(case["name"])
        numerator += np.vdot(a_fd, a_td)
        denominator += float(np.vdot(a_fd, a_fd).real)
    alpha = numerator / denominator
    agreement = []
    for case in singles:
        port = case["source_ports"][0]
        predicted = alpha * np.exp(1j * case["ref_phase"]) * fd_amp(
            case["name"], port)
        measured = td_amp(case["name"])
        # Compare only the driven port and ports above 0.1 percent of the
        # strongest readout; near-null ports otherwise dominate the error.
        mask = np.abs(measured) > 1e-3 * np.abs(measured).max()
        mask[port] = True
        agreement.append(dict(
            case=case["name"], ports_compared=int(mask.sum()),
            relative_error=float(np.linalg.norm(measured[mask] - predicted[mask]) /
                                 np.linalg.norm(predicted[mask])),
            phase_error_deg=float(np.degrees(np.angle(
                np.vdot(predicted[mask], measured[mask])))),
            amplitude=[cpx(v) for v in measured],
            predicted=[cpx(v) for v in predicted]))

    # G6 one-bit differential decision at receivers 1 and 4.
    def bit(amp):
        return int(abs(amp[1]) > abs(amp[4]))

    def phase_bit(amp):
        return int(np.angle(amp[1] / amp[4]) > 0)

    decisions = []
    for name in ["g6_src_p0", "g6_src_p0_ref90", "g6_src_p0_detune",
                 "g6_src_p1", "g6_src_p4"]:
        case = by_name[name]
        port = case["source_ports"][0]
        a_td = td_amp(name)
        a_fd = alpha * np.exp(1j * case["ref_phase"]) * fd_amp(name, port)
        decisions.append(dict(
            case=name, td_magnitude_bit=bit(a_td), fd_magnitude_bit=bit(a_fd),
            td_phase_bit=phase_bit(a_td), fd_phase_bit=phase_bit(a_fd),
            magnitude_match=bool(bit(a_td) == bit(a_fd)),
            phase_match=bool(phase_bit(a_td) == phase_bit(a_fd)),
            td_ratio=float(abs(a_td[1]) / abs(a_td[4])),
            td_relative_phase_deg=float(np.degrees(np.angle(a_td[1] / a_td[4]))),
            fd_ratio=float(abs(a_fd[1]) / abs(a_fd[4])),
            fd_relative_phase_deg=float(np.degrees(np.angle(a_fd[1] / a_fd[4])))))

    # G7 four-bit calibration decode: basis runs then phase-coded combinations.
    basis_td = np.column_stack([td_amp("g7_basis_p%d" % p) for p in g7_sources])
    basis_fd = np.column_stack([alpha * fd_amp("g7_basis_p%d" % p, p)
                                for p in g7_sources])
    codes = []
    for code in g7_codes:
        name = "g7_code_%d%d%d%d" % code
        a_td = td_amp(name)
        desired = np.array([1.0 if b else -1.0 for b in code])
        coeff_td, residual_td, rank_td, condition = decode(basis_td, a_td)
        coeff_fd, residual_fd, rank_fd, _ = decode(basis_fd, a_td)
        recovery_td = np.linalg.norm(basis_td @ desired - a_td) / np.linalg.norm(a_td)
        recovery_fd = np.linalg.norm(basis_fd @ desired - a_td) / np.linalg.norm(a_td)
        codes.append(dict(
            case=name, input_bits=list(code), desired_coefficients=desired.tolist(),
            recovered_td_coefficients=coeff_td.tolist(),
            recovered_td_bits=[int(v > 0) for v in coeff_td],
            recovered_fd_bits=[int(v > 0) for v in coeff_fd],
            td_bits_match=bool(all((v > 0) == bool(b) for v, b in zip(coeff_td, code))),
            fd_bits_match=bool(all((v > 0) == bool(b) for v, b in zip(coeff_fd, code))),
            decode_residual_td=residual_td, decode_residual_fd=residual_fd,
            linear_prediction_residual_td=float(recovery_td),
            linear_prediction_residual_fd=float(recovery_fd),
            basis_rank_td=rank_td, decode_condition=condition))

    summary = dict(
        status="surrogate_route_smoke_complete",
        created=time.strftime("%Y-%m-%dT%H:%M:%S"),
        scale_alpha=dict(value=cpx(alpha), modulus=float(abs(alpha)),
                         phase_deg=float(np.degrees(np.angle(alpha)))),
        cross_domain_agreement=agreement,
        g6_one_bit=dict(decisions=decisions,
                        all_magnitude_match=bool(all(r["magnitude_match"] for r in decisions)),
                        all_phase_match=bool(all(r["phase_match"] for r in decisions)),
                        reference_phase_invariant=bool(
                            bit(td_amp("g6_src_p0")) == bit(td_amp("g6_src_p0_ref90")) and
                            phase_bit(td_amp("g6_src_p0")) == phase_bit(td_amp("g6_src_p0_ref90"))),
                        detuned_decision_holds=bool(
                            bit(td_amp("g6_src_p0")) == bit(td_amp("g6_src_p0_detune")) and
                            phase_bit(td_amp("g6_src_p0")) == phase_bit(td_amp("g6_src_p0_detune")))),
        g7_four_bit=dict(cases=codes,
                         all_td_bits_match=bool(all(r["td_bits_match"] for r in codes)),
                         all_fd_bits_match=bool(all(r["fd_bits_match"] for r in codes)),
                         max_linear_prediction_residual_td=float(max(
                             r["linear_prediction_residual_td"] for r in codes))),
        full_system_gates_closed=0, total_system_gates=7, goal_complete=False)
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    print("done", flush=True)
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(HERE / "端到端路线冒烟结果"))
    parser.add_argument("--steps", type=int, default=STEPS)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    out = Path(args.out)
    if out.exists():
        raise SystemExit("Refusing to overwrite existing evidence: %s" % out)
    out.mkdir(parents=True)
    summary = run(out, args.steps, args.workers)
    print(json.dumps(summary, ensure_ascii=False, indent=2)[:4000])


if __name__ == "__main__":
    main()
