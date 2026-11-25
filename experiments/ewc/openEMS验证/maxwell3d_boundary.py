"""Independent 3-D integral Maxwell grid, periodic y/z, open x terminals.

Spatially staggered E-edge voltages and B-face fluxes, implicit midpoint in
time. This is NOT openEMS, NOT Yee leapfrog, NOT a closed copper cavity.
"""

import numpy as np
from scipy.sparse import coo_matrix, diags
from scipy.sparse.linalg import splu

MU = 4e-7 * np.pi
C0 = 299792458.
EPS = 1 / (MU * C0**2)
ETA = np.sqrt(MU / EPS)


class Grid:
    def __init__(self, nx, ny, nz, dx, dy, dz):
        if min(nx, ny, nz) < 2 or min(dx, dy, dz) <= 0:
            raise ValueError("At least two cells per axis and positive spacing required.")
        self.shape = (nx, ny, nz)
        self.spacing = (dx, dy, dz)
        self.plane = ny * nz
        self.ex_count = nx * self.plane
        self.t_count = (nx + 1) * self.plane
        self.ne = self.ex_count + 2 * self.t_count
        self.nf = self.t_count + 2 * self.ex_count
        rows, cols, values = [], [], []

        def loop(face, edges):
            for sign, component, i, j, k in edges:
                rows.append(face)
                cols.append(self.e(component, i, j, k))
                values.append(sign)

        for i in range(nx + 1):
            for j in range(ny):
                for k in range(nz):
                    loop(self.f(0, i, j, k), (
                        (1, 1, i, j, k), (1, 2, i, j + 1, k),
                        (-1, 1, i, j, k + 1), (-1, 2, i, j, k)))
        for i in range(nx):
            for j in range(ny):
                for k in range(nz):
                    loop(self.f(1, i, j, k), (
                        (1, 2, i, j, k), (1, 0, i, j, k + 1),
                        (-1, 2, i + 1, j, k), (-1, 0, i, j, k)))
                    loop(self.f(2, i, j, k), (
                        (1, 0, i, j, k), (1, 1, i + 1, j, k),
                        (-1, 0, i, j + 1, k), (-1, 1, i, j, k)))
        self.curl = coo_matrix((values, (rows, cols)), shape=(self.nf, self.ne)).tocsr()
        vx = np.full(nx + 1, dx)
        vx[[0, -1]] /= 2
        self.m = np.r_[
            np.full(self.ex_count, EPS * dy * dz / dx),
            np.repeat(EPS * vx * dz / dy, self.plane),
            np.repeat(EPS * vx * dy / dz, self.plane),
        ]
        self.q = np.r_[
            np.repeat(vx / (MU * dy * dz), self.plane),
            np.full(self.ex_count, dy / (MU * dx * dz)),
            np.full(self.ex_count, dz / (MU * dx * dy)),
        ]
        self.left = np.array([self.e(c, 0, j, k)
                              for c in (1, 2) for j in range(ny) for k in range(nz)])
        self.wall = np.array([self.e(c, nx, j, k)
                              for c in (1, 2) for j in range(ny) for k in range(nz)])
        self.edge_length = np.repeat([dy, dz], self.plane)
        self.dual_width = np.repeat([dz, dy], self.plane)
        self.surface_scale = self.edge_length / self.dual_width

    def e(self, component, i, j, k):
        ny, nz = self.shape[1:]
        offset = (0, self.ex_count, self.ex_count + self.t_count)[component]
        return offset + i * self.plane + (j % ny) * nz + k % nz

    def f(self, component, i, j, k):
        ny, nz = self.shape[1:]
        offset = (0, self.t_count, self.t_count + self.ex_count)[component]
        return offset + i * self.plane + (j % ny) * nz + k % nz

    def divergence(self, flux):
        nx, ny, nz = self.shape
        bx = flux[:self.t_count].reshape(nx + 1, ny, nz)
        by = flux[self.t_count:self.t_count + self.ex_count].reshape(nx, ny, nz)
        bz = flux[self.t_count + self.ex_count:].reshape(nx, ny, nz)
        return (bx[1:] - bx[:-1] + np.roll(by, -1, axis=1) - by +
                np.roll(bz, -1, axis=2) - bz).ravel()

    def energy(self, e, b, states, inductance):
        return .5 * (self.m @ (e * e) + self.q @ (b * b) +
                      np.sum(inductance * states * states))


def simulate(grid, dt, duration, rbase, lbase, polarization="y", nonuniform=False):
    if not np.isfinite(dt) or dt <= 0 or duration <= 2.4e-9:
        raise ValueError("Positive finite timestep and duration beyond source cutoff required.")
    if polarization not in ("y", "z", "both"):
        raise ValueError("Unknown polarization.")
    rbase, lbase = np.asarray(rbase), np.asarray(lbase)
    if rbase.shape != lbase.shape or np.any(rbase <= 0) or np.any(lbase <= 0):
        raise ValueError("Matching positive RL arrays required.")
    steps = int(round(duration / dt))
    if not np.isclose(steps * dt, duration, rtol=1e-10, atol=1e-18):
        raise ValueError("Duration must be an integer number of steps.")
    r = grid.surface_scale[:, None] * rbase
    l = grid.surface_scale[:, None] * lbase
    bk = 1 / (1 + dt * r / (2 * l))
    rb = r * bk
    eff = rb.sum(axis=1)
    source_r = ETA * grid.surface_scale
    conductance = np.zeros(grid.ne)
    conductance[grid.left] = 1 / source_r
    conductance[grid.wall] = 1 / eff
    curl = grid.curl
    operator = (diags(2 * grid.m / dt + conductance) +
                dt / 2 * curl.T @ diags(grid.q) @ curl).tocsc()
    solve = splu(operator)
    profile = np.zeros(len(grid.wall))
    ny, nz = grid.shape[1:]
    for component in (1, 2):
        if polarization not in (("y" if component == 1 else "z"), "both"):
            continue
        for j in range(ny):
            for k in range(nz):
                index = (component - 1) * grid.plane + j * nz + k
                variation = 1 + (.3 * np.cos(2 * np.pi * j / ny) *
                                  np.cos(2 * np.pi * k / nz) if nonuniform else 0)
                profile[index] = grid.edge_length[index] * variation
    time = (np.arange(steps) + .5) * dt
    drive = np.exp(-.5 * ((time - 1.2e-9) / .2e-9)**2) * np.cos(
        2 * np.pi * 3e9 * (time - 1.2e-9))
    drive[time >= 2.4e-9] = 0
    e, b = np.zeros(grid.ne), np.zeros(grid.nf)
    states = np.zeros(r.shape)
    energy = np.zeros(steps + 1)
    left_e = np.zeros((steps, len(grid.wall)))
    wall_e, wall_j = np.zeros_like(left_e), np.zeros_like(left_e)
    copper, left_loss, work = np.zeros(steps), np.zeros(steps), np.zeros(steps)
    snapshots = []
    selected = set(np.linspace(0, steps - 1, 41).astype(int))
    component_peak = np.zeros(6)
    divergence_peak = 0.
    for n in range(steps):
        memory = np.sum(rb * states, axis=1)
        injection = 2 * profile * drive[n] / source_r
        rhs = 2 * grid.m / dt * e + curl.T @ (grid.q * b)
        rhs[grid.left] += injection
        rhs[grid.wall] -= memory / eff
        emid = solve.solve(rhs)
        enew = 2 * emid - e
        bnew = b - dt * (curl @ emid)
        jmid = (emid[grid.wall] + memory) / eff
        imid = bk * states + (1 - bk) * jmid[:, None]
        inew = 2 * imid - states
        left_e[n], wall_e[n], wall_j[n] = emid[grid.left], emid[grid.wall], jmid
        copper[n] = np.sum(r * (jmid[:, None] - imid)**2)
        left_loss[n] = np.sum(emid[grid.left]**2 / source_r)
        work[n] = injection @ emid[grid.left]
        energy[n + 1] = grid.energy(enew, bnew, inew, l)
        if n in selected:
            snapshots.append((n, e.copy(), enew.copy(), b.copy(), bnew.copy(),
                              states.copy(), inew.copy()))
        for c, (start, stop) in enumerate(((0, grid.ex_count), (grid.ex_count, grid.ex_count + grid.t_count),
                                          (grid.ex_count + grid.t_count, grid.ne),
                                          (0, grid.t_count), (grid.t_count, grid.t_count + grid.ex_count),
                                          (grid.t_count + grid.ex_count, grid.nf))):
            values = enew[start:stop] if c < 3 else bnew[start:stop]
            component_peak[c] = max(component_peak[c], np.max(abs(values)))
        divergence_peak = max(divergence_peak, np.max(abs(grid.divergence(bnew))))
        e, b, states = enew, bnew, inew
    scale = max(abs(dt * np.sum(work)), np.max(energy), 1e-30)
    defect = energy[1:] - energy[0] - dt * np.cumsum(work - left_loss - copper)
    selected_pol = 0 if polarization in ("y", "both") else 1
    sl = slice(selected_pol * grid.plane, (selected_pol + 1) * grid.plane)
    snapshots = {
        "snapshot_step": np.array([x[0] for x in snapshots]),
        **{name: np.stack([x[index] for x in snapshots]) for index, name in enumerate(
            ("e_old", "e_new", "b_old", "b_new", "i_old", "i_new"), start=1)},
    }
    return {
        "time_mid_s": time, "drive_V_per_m": drive, "source_profile_m": profile,
        "left_edge_V": left_e, "wall_edge_V": wall_e, "wall_edge_current_A": wall_j,
        "left_mean_E_V_per_m": np.mean(left_e[:, sl] / grid.edge_length[sl], axis=1),
        "wall_mean_E_V_per_m": np.mean(wall_e[:, sl] / grid.edge_length[sl], axis=1),
        "wall_mean_K_A_per_m": np.mean(wall_j[:, sl] / grid.dual_width[sl], axis=1),
        "energy_J": energy, "source_work_W": work, "source_resistor_loss_W": left_loss,
        "copper_resistor_loss_W": copper, "R_edge_ohm": r, "L_edge_H": l,
        "source_R_edge_ohm": source_r, "component_peak_integral_units": component_peak,
        "metrics": {
            "energy_closure_relative": float(np.max(abs(defect)) / scale),
            "source_off_energy_increase_relative": float(max(0., np.max(np.diff(energy)[time >= 2.4e-9])) / scale),
            "divergence_B_flux_max_Wb": float(divergence_peak),
            "copper_loss_J": float(dt * np.sum(copper)), "final_energy_J": float(energy[-1]),
        },
        "snapshots": snapshots,
    }
