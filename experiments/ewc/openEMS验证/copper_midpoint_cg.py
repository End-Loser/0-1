"""Jacobi-scaled CG for the unchanged full, real SPD midpoint operator."""
import numpy as np
from scipy.sparse import diags
from scipy.sparse.linalg import cg


class MidpointCG:
    def __init__(self, grid, dt, r, l, rtol=1e-13, maxiter=80):
        r, l = np.asarray(r), np.asarray(l)
        if (not np.isfinite(dt) or dt <= 0 or r.ndim != 1 or r.shape != l.shape or
                len(r) == 0 or not np.all(np.isfinite(r)) or not np.all(np.isfinite(l)) or
                np.any(r <= 0) or np.any(l <= 0) or not 0 < rtol <= 1e-13 or maxiter < 1):
            raise ValueError("Positive full-state physics and strict solve tolerance required")
        self.grid, self.dt, self.rtol, self.maxiter = grid, dt, rtol, maxiter
        self.edges = grid.wall_edges
        self.rr = grid.wall_scale[:, None] * r
        self.ll = grid.wall_scale[:, None] * l
        self.bk = 1 / (1 + dt * r / l / 2)
        self.ak = 1 - self.bk
        self.rb = self.rr * self.bk
        self.effective = self.rb.sum(axis=1)
        conductance = np.bincount(self.edges, weights=1 / self.effective, minlength=grid.ne)
        self.operator = (diags(2 * grid.m / dt + conductance) +
                         dt / 2 * grid.stiffness).tocsr()
        self.invroot = 1 / np.sqrt(self.operator.diagonal())
        scale = diags(self.invroot)
        self.scaled = (scale @ self.operator @ scale).tocsr()
        difference = self.scaled - self.scaled.T
        assert difference.nnz == 0 or np.max(abs(difference.data)) < 1e-15
        self.max_scaled_row_radius = float(
            np.max(np.asarray(abs(self.scaled).sum(axis=1)).ravel() - self.scaled.diagonal()))

    def solve(self, rhs, initial=None):
        rhs = np.asarray(rhs)
        if rhs.shape != (self.grid.ne,) or not np.all(np.isfinite(rhs)):
            raise ValueError("Finite complete edge RHS required")
        scaled_rhs = self.invroot * rhs
        guess = None if initial is None else np.asarray(initial) / self.invroot
        iterations = 0

        def counted(_):
            nonlocal iterations
            iterations += 1

        y, info = cg(self.scaled, scaled_rhs, x0=guess, rtol=self.rtol,
                     atol=0., maxiter=self.maxiter, callback=counted)
        if info != 0:
            raise RuntimeError(f"Full midpoint CG did not converge: info={info}")
        norm = np.linalg.norm(scaled_rhs)
        residual = np.linalg.norm(self.scaled @ y - scaled_rhs) / max(norm, 1e-300)
        if residual > 5e-13:
            raise RuntimeError(f"Full midpoint scaled residual rejected: {residual}")
        return self.invroot * y, iterations, float(residual)

    def step(self, e, b, state):
        memory = np.sum(self.rb * state, axis=1)
        rhs = 2 * self.grid.m / self.dt * e + self.grid.curl.T @ (self.grid.q * b)
        correction = np.bincount(
            self.edges, weights=memory / self.effective, minlength=self.grid.ne)
        emid, iterations, residual = self.solve(rhs - correction, initial=e)
        jmid = (emid[self.edges] + memory) / self.effective
        imid = self.bk * state + self.ak * jmid[:, None]
        return (2 * emid - e, b - self.dt * (self.grid.curl @ emid),
                2 * imid - state, jmid, iterations, residual)
