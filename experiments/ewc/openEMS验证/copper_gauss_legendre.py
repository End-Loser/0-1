"""Two-stage fourth-order collocation of unchanged complete Maxwell/RL states."""
import numpy as np
from scipy.sparse import diags
from scipy.sparse.linalg import gmres


class GaussLegendre:
    def __init__(self, grid, dt, r, l, rtol=1e-13, maxiter=80):
        if np.iscomplexobj(r) or np.iscomplexobj(l):
            raise ValueError("Real positive RL physics required")
        r, l = np.asarray(r, float), np.asarray(l, float)
        if (not np.isfinite(dt) or dt <= 0 or r.ndim != 1 or r.shape != l.shape or
                not len(r) or not np.isfinite(r).all() or not np.isfinite(l).all() or
                np.any(r <= 0) or np.any(l <= 0) or not 0 < rtol <= 1e-13 or maxiter < 1):
            raise ValueError("Positive full-state physics and strict solve tolerance required")
        self.grid, self.dt, self.rtol, self.maxiter = grid, dt, rtol, maxiter
        self.edges = grid.wall_edges
        self.rr = grid.wall_scale[:, None] * r
        self.ll = grid.wall_scale[:, None] * l
        self.z = dt * (.25 + 1j * np.sqrt(3) / 12)
        self.t = (1j * np.sqrt(3) / 12) / (.25 - np.sqrt(3) / 6)
        self.beta = np.array([.5, 1 / (2 * self.t)])
        self.alpha = self.beta.sum()
        self.invbranch = 1 / (1 + self.z * r / l)
        self.effective = np.sum(self.rr * self.invbranch, axis=1)
        conductance = np.bincount(self.edges, weights=(1 / self.effective).real, minlength=grid.ne)
        conductance = conductance.astype(complex) + 1j * np.bincount(
            self.edges, weights=(1 / self.effective).imag, minlength=grid.ne)
        self.operator = (diags(grid.m + self.z * conductance) +
                         self.z**2 * grid.stiffness).tocsr()
        self.invroot = 1 / np.sqrt(self.operator.diagonal())
        scale = diags(self.invroot)
        self.scaled = (scale @ self.operator @ scale).tocsr()
        self.nodes = .5 + np.array([-1., 1.]) * np.sqrt(3) / 6

    def solve(self, rhs, initial=None):
        rhs = np.asarray(rhs, complex)
        if rhs.shape != (self.grid.ne,) or not np.isfinite(rhs).all():
            raise ValueError("Finite complete complex edge RHS required")
        scaled_rhs = self.invroot * rhs
        guess = None if initial is None else np.asarray(initial) / self.invroot
        iterations = 0

        def counted(_):
            nonlocal iterations
            iterations += 1

        y, info = gmres(self.scaled, scaled_rhs, x0=guess, rtol=self.rtol, atol=0.,
                        maxiter=self.maxiter, restart=min(20, self.grid.ne),
                        callback=counted, callback_type="legacy")
        if info != 0:
            raise RuntimeError(f"Full complex Gauss solve did not converge: info={info}")
        residual = np.linalg.norm(self.scaled @ y - scaled_rhs) / max(
            np.linalg.norm(scaled_rhs), 1e-300)
        if not np.isfinite(residual) or residual > 5e-13:
            raise RuntimeError(f"Full complex Gauss residual rejected: {residual}")
        return self.invroot * y, iterations, float(residual)

    def step(self, e, b, state, sources):
        values = []
        for value, shape in zip((e, b, state, sources), (
                (self.grid.ne,), (self.grid.nf,), self.rr.shape, (2, self.grid.ne))):
            value = np.asarray(value)
            if value.shape != shape or np.iscomplexobj(value) or not np.isfinite(value).all():
                raise ValueError("Finite real full states and two collocation-node sources required")
            values.append(value)
        e, b, state, sources = values
        memory = np.sum(self.rr * self.invbranch * (self.alpha * state), axis=1)
        correction = np.bincount(self.edges, weights=(memory / self.effective).real,
                                 minlength=self.grid.ne).astype(complex)
        correction += 1j * np.bincount(self.edges, weights=(memory / self.effective).imag,
                                      minlength=self.grid.ne)
        rhs = (self.alpha * self.grid.m * e +
               self.z * (self.beta @ sources +
                         self.alpha * (self.grid.curl.T @ (self.grid.q * b)) - correction))
        ey, iterations, residual = self.solve(rhs, initial=self.alpha * e)
        by = self.alpha * b - self.z * (self.grid.curl @ ey)
        jy = (ey[self.edges] + memory) / self.effective
        iy = self.invbranch * (self.alpha * state) + (1 - self.invbranch) * jy[:, None]

        def stages(value):
            return np.stack((2 * value.real, 2 * (self.t * value).real))

        es, bs, is_, js = map(stages, (ey, by, iy, jy))
        # b^T A^-1 = (-sqrt(3), sqrt(3)); avoids multiplying stiff RL derivatives.
        en, bn, sn = (old + np.sqrt(3) * (stage[1] - stage[0])
                      for old, stage in zip((e, b, state), (es, bs, is_)))
        return en, bn, sn, es, bs, is_, js, iterations, residual
