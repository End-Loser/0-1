"""Impressed full-edge midpoint current; unchanged full copper operator."""
import numpy as np

from copper_midpoint_cg import MidpointCG


class DrivenMidpoint(MidpointCG):
    def step(self, e, b, state, source_mid):
        values = []
        for value, shape in zip((e, b, state, source_mid),
                                ((self.grid.ne,), (self.grid.nf,),
                                 self.rr.shape, (self.grid.ne,))):
            value = np.asarray(value)
            if (value.shape != shape or np.iscomplexobj(value) or
                    not np.isfinite(value).all()):
                raise ValueError("Finite real complete states and midpoint source required")
            values.append(value)
        e, b, state, source_mid = values
        memory = np.sum(self.rb * state, axis=1)
        rhs = (2 * self.grid.m / self.dt * e +
               self.grid.curl.T @ (self.grid.q * b) + source_mid)
        correction = np.bincount(
            self.edges, weights=memory / self.effective, minlength=self.grid.ne)
        emid, iterations, residual = self.solve(rhs - correction, initial=e)
        jmid = (emid[self.edges] + memory) / self.effective
        imid = self.bk * state + self.ak * jmid[:, None]
        return (2 * emid - e, b - self.dt * (self.grid.curl @ emid),
                2 * imid - state, jmid, iterations, residual)
