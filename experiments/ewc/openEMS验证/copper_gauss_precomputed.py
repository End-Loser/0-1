"""Separate full-state candidate caching only constant branch products."""
import numpy as np

from copper_gauss_legendre import GaussLegendre


class PrecomputedGaussLegendre(GaussLegendre):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.memory_weights = (self.rr * self.invbranch) * self.alpha
        self.initial_branch_weights = self.invbranch * self.alpha
        self.current_branch_weights = 1 - self.invbranch

    def step(self, e, b, state, sources):
        values = []
        for value, shape in zip((e, b, state, sources), (
                (self.grid.ne,), (self.grid.nf,), self.rr.shape, (2, self.grid.ne))):
            value = np.asarray(value)
            if value.shape != shape or np.iscomplexobj(value) or not np.isfinite(value).all():
                raise ValueError("Finite real full states and two collocation-node sources required")
            values.append(value)
        e, b, state, sources = values
        # Same full branch sum, with constant multiplications outside the time loop.
        memory = np.sum(self.memory_weights * state, axis=1)
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
        iy = self.initial_branch_weights * state + self.current_branch_weights * jy[:, None]

        def stages(value):
            return np.stack((2 * value.real, 2 * (self.t * value).real))

        es, bs, is_, js = map(stages, (ey, by, iy, jy))
        en, bn, sn = (old + np.sqrt(3) * (stage[1] - stage[0])
                      for old, stage in zip((e, b, state), (es, bs, is_)))
        return en, bn, sn, es, bs, is_, js, iterations, residual
