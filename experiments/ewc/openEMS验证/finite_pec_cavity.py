"""Finite integral Maxwell PEC baseline, not a real copper cavity or openEMS."""

import numpy as np
from scipy.sparse import coo_matrix, diags
from scipy.sparse.linalg import eigsh, splu

MU = 4e-7 * np.pi
C0 = 299792458.
EPS = 1 / (MU * C0**2)


class FinitePEC:
    def __init__(self, shape, lengths):
        shape = np.asarray(shape, dtype=float)
        lengths = np.asarray(lengths, dtype=float)
        if (shape.shape != (3,) or lengths.shape != (3,) or
                not np.all(np.isfinite(shape)) or np.any(shape != np.floor(shape)) or np.any(shape < 2) or
                not np.all(np.isfinite(lengths)) or np.any(lengths <= 0)):
            raise ValueError("Three integer cell counts >=2 and finite positive lengths required.")
        self.shape = tuple(map(int, shape))
        self.lengths = tuple(lengths)
        self.spacing = lengths / shape
        nx, ny, nz = self.shape
        self.es = [(nx, ny + 1, nz + 1), (nx + 1, ny, nz + 1),
                   (nx + 1, ny + 1, nz)]
        self.fs = [(nx + 1, ny, nz), (nx, ny + 1, nz), (nx, ny, nz + 1)]
        self.eoff = np.r_[0, np.cumsum([np.prod(x) for x in self.es])]
        self.foff = np.r_[0, np.cumsum([np.prod(x) for x in self.fs])]
        self.ne_full, self.nf = int(self.eoff[-1]), int(self.foff[-1])
        active, mass = [], []
        dual = [np.full(n + 1, h) for n, h in zip(self.shape, self.spacing)]
        for a in dual:
            a[[0, -1]] /= 2
        for c, es in enumerate(self.es):
            for index in np.ndindex(es):
                other = [a for a in range(3) if a != c]
                active.append(all(0 < index[a] < self.shape[a] for a in other))
                mass.append(EPS * np.prod([dual[a][index[a]] for a in other]) /
                            self.spacing[c])
        self.active = np.flatnonzero(active)
        self.m = np.asarray(mass)[self.active]
        self.ne = len(self.active)
        q = []
        for c, fs in enumerate(self.fs):
            area = np.prod(np.delete(self.spacing, c))
            q.extend(dual[c][index[c]] / (MU * area) for index in np.ndindex(fs))
        self.q = np.asarray(q)
        rows, cols, vals = [], [], []

        def loop(face, edges):
            for sign, component, index in edges:
                rows.append(face)
                cols.append(self.edge(component, index))
                vals.append(sign)

        for c, fs in enumerate(self.fs):
            a, b = (c + 1) % 3, (c + 2) % 3
            for index in np.ndindex(fs):
                plus_a, plus_b = list(index), list(index)
                plus_a[a] += 1
                plus_b[b] += 1
                loop(self.face(c, index), [(1, a, index), (1, b, plus_a),
                                          (-1, a, plus_b), (-1, b, index)])
        self.full_curl = coo_matrix((vals, (rows, cols)),
                                   shape=(self.nf, self.ne_full)).tocsr()
        self.curl = self.full_curl[:, self.active]
        rows, cols, vals = [], [], []
        nodes = (nx + 1, ny + 1, nz + 1)
        for c, es in enumerate(self.es):
            for index in np.ndindex(es):
                end = list(index)
                end[c] += 1
                row = self.edge(c, index)
                rows.extend([row, row])
                cols.extend([np.ravel_multi_index(index, nodes),
                             np.ravel_multi_index(tuple(end), nodes)])
                vals.extend([-1, 1])
        self.full_gradient = coo_matrix((vals, (rows, cols)),
                                       shape=(self.ne_full, np.prod(nodes))).tocsr()
        interior = [np.ravel_multi_index(i, nodes) for i in np.ndindex(nodes)
                    if all(0 < i[a] < self.shape[a] for a in range(3))]
        self.gradient = self.full_gradient[self.active][:, interior]
        self.stiffness = (self.curl.T @ diags(self.q) @ self.curl).tocsc()

    def edge(self, component, index):
        return int(self.eoff[component] + np.ravel_multi_index(tuple(index), self.es[component]))

    def face(self, component, index):
        return int(self.foff[component] + np.ravel_multi_index(tuple(index), self.fs[component]))

    def lift(self, e):
        full = np.zeros(self.ne_full, dtype=np.asarray(e).dtype)
        full[self.active] = e
        return full

    def divergence(self, b):
        fields = [b[self.foff[c]:self.foff[c + 1]].reshape(self.fs[c]) for c in range(3)]
        return sum(np.diff(fields[c], axis=c) for c in range(3)).ravel()

    def charge(self, e):
        # Interior dual-node charge only; PEC surface charge is not included.
        return -self.gradient.T @ (self.m * e)

    def sample_mode(self, indices, polarization=None):
        indices = np.asarray(indices, dtype=float)
        if (indices.shape != (3,) or not np.all(np.isfinite(indices)) or
                np.any(indices != np.floor(indices)) or np.any(indices >= self.shape) or
                np.count_nonzero(indices) < 2 or np.any(indices < 0)):
            raise ValueError("PEC cavity modes require at least two positive indices.")
        k = np.pi * indices / np.asarray(self.lengths)
        kd = 2 * np.sin(k * self.spacing / 2) / self.spacing
        if polarization is None:
            if np.count_nonzero(indices) == 2:
                polarization = (indices == 0).astype(float)
            else:
                polarization = np.cross(kd, [1., 0., 0.])
        polarization = np.asarray(polarization, dtype=float)
        if polarization.shape != (3,) or not np.all(np.isfinite(polarization)):
            raise ValueError("Three finite polarization components required.")
        if abs(kd @ polarization) > 1e-10 * max(1., np.linalg.norm(kd) * np.linalg.norm(polarization)):
            raise ValueError("Polarization must be discrete-transverse.")
        fields = []
        for c, es in enumerate(self.es):
            xyz = np.indices(es).astype(float)
            xyz[c] += .5
            value = np.full(es, polarization[c] * self.spacing[c])
            for a in range(3):
                phase = k[a] * self.spacing[a] * xyz[a]
                value *= np.cos(phase) if a == c else np.sin(phase)
            fields.append(value.ravel())
        e = np.concatenate(fields)[self.active]
        norm = np.sqrt(self.m @ (e * e))
        if norm == 0:
            raise ValueError("Zero mode vector.")
        return e / norm

    def solve_mode(self, indices):
        seed = self.sample_mode(indices)
        seed_lambda = float(seed @ (self.stiffness @ seed))
        # Targeted shift avoids the gradient nullspace, without deleting it.
        eigenvalue, vector = eigsh(self.stiffness, k=1, M=diags(self.m),
                                  sigma=seed_lambda * (1 + 1e-5), v0=seed,
                                  tol=1e-10)
        e = vector[:, 0]
        e /= np.sqrt(self.m @ (e * e))
        return float(eigenvalue[0]), e


def transient(grid, dt, steps, seed=541):
    if not np.isfinite(dt) or dt <= 0 or int(steps) != steps or steps < 2:
        raise ValueError("Positive finite dt and integer steps >=2 required.")
    rng = np.random.default_rng(seed)
    pattern = rng.normal(size=grid.ne)
    pattern /= np.linalg.norm(pattern)
    curl = grid.curl
    factor = splu((diags(2 * grid.m / dt) + dt / 2 * grid.stiffness).tocsc())
    e, b = np.zeros(grid.ne), np.zeros(grid.nf)
    e_history, b_history = [e.copy()], [b.copy()]
    currents, work = [], []
    for n in range(steps):
        time = (n + .5) * dt
        pulse = np.sin(np.pi * time / .4e-9)**2 if time < .4e-9 else 0.
        current = 1e-6 * pulse * pattern
        emid = factor.solve(2 * grid.m / dt * e + curl.T @ (grid.q * b) + current)
        e = 2 * emid - e
        b = b - dt * (curl @ emid)
        e_history.append(e.copy())
        b_history.append(b.copy())
        currents.append(current)
        work.append(float(current @ emid))
    return {"e_V": np.asarray(e_history), "b_Wb": np.asarray(b_history),
            "current_A": np.asarray(currents), "source_work_W": np.asarray(work),
            "dt_s": np.array(dt), "shape": np.array(grid.shape),
            "lengths_m": np.array(grid.lengths)}
