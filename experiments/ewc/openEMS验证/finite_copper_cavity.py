"""Six-face surface-impedance coupling, frequency domain, not openEMS."""

import numpy as np
from scipy.sparse import diags
from scipy.sparse.linalg import spsolve

from finite_pec_cavity import FinitePEC, MU, EPS


class CopperCavity:
    def __init__(self, shape, lengths):
        self.pec = FinitePEC(shape, lengths)
        self.shape, self.lengths = self.pec.shape, self.pec.lengths
        self.spacing = self.pec.spacing
        self.curl = self.pec.full_curl
        self.gradient = self.pec.full_gradient
        self.ne, self.nf = self.pec.ne_full, self.pec.nf
        self.q = self.pec.q
        dual = [np.full(n + 1, h) for n, h in zip(self.shape, self.spacing)]
        for d in dual:
            d[[0, -1]] /= 2
        mass = []
        for c, es in enumerate(self.pec.es):
            for index in np.ndindex(es):
                area = np.prod([dual[a][index[a]] for a in range(3) if a != c])
                mass.append(EPS * area / self.spacing[c])
        self.m = np.array(mass)
        # One record per face/tangential edge. At a box edge, two face
        # currents act in parallel; each uses its own half surface width.
        edges, normals, sides, components, widths, scales = [], [], [], [], [], []
        for normal in range(3):
            for side in (0, 1):
                for c in range(3):
                    if c == normal:
                        continue
                    transverse = next(a for a in range(3) if a not in (normal, c))
                    for index in np.ndindex(self.pec.es[c]):
                        if index[normal] != side * self.shape[normal]:
                            continue
                        width = dual[transverse][index[transverse]]
                        edges.append(self.pec.edge(c, index))
                        normals.append(normal)
                        sides.append(side)
                        components.append(c)
                        widths.append(width)
                        scales.append(self.spacing[c] / width)
        self.wall_edges = np.array(edges)
        self.wall_normals = np.array(normals)
        self.wall_sides = np.array(sides)
        self.wall_components = np.array(components)
        self.wall_width_m = np.array(widths)
        self.wall_scale = np.array(scales)
        self.stiffness = (self.curl.T @ diags(self.q) @ self.curl).tocsc()

    def admittance(self, frequency, resistance, inductance):
        r, l = np.asarray(resistance, float), np.asarray(inductance, float)
        if (not np.isfinite(frequency) or frequency <= 0 or r.ndim != 1 or
                r.size == 0 or r.shape != l.shape or not np.all(np.isfinite(r)) or
                not np.all(np.isfinite(l)) or np.any(r <= 0) or np.any(l <= 0)):
            raise ValueError("Positive frequency and matching finite positive RL vectors required.")
        s = 2j * np.pi * frequency
        impedance = np.sum(1 / (1 / r + 1 / (s * l)))
        per_record = 1 / (impedance * self.wall_scale)
        total = np.zeros(self.ne, complex)
        np.add.at(total, self.wall_edges, per_record)
        return impedance, per_record, total

    def solve(self, frequency, resistance, inductance, drive):
        drive = np.asarray(drive, complex)
        if drive.shape != (self.ne,) or not np.all(np.isfinite(drive)):
            raise ValueError("Finite full edge-current drive required.")
        z, per_record, total = self.admittance(frequency, resistance, inductance)
        s = 2j * np.pi * frequency
        operator = self.stiffness + diags(s * s * self.m + s * total)
        e = spsolve(operator.tocsc(), s * drive)
        b = -(self.curl @ e) / s
        wall_j = per_record * e[self.wall_edges]
        wall_power = .5 * np.sum(per_record.real * abs(e[self.wall_edges])**2)
        source_power = .5 * np.real(np.vdot(e, drive))
        return {"e_V": e, "b_Wb": b, "wall_current_A": wall_j,
                "wall_impedance_ohm": z, "wall_power_W": float(wall_power),
                "source_power_W": float(source_power)}


def continuum_q(indices, lengths, sigma):
    """PEC perturbation reference for a mode with one zero electric index."""
    indices = np.asarray(indices)
    if indices.shape != (3,) or np.count_nonzero(indices) != 2 or np.any(indices < 0):
        raise ValueError("Exactly two positive indices required for this Q reference.")
    k = np.pi * indices / lengths
    omega = 299792458. * np.linalg.norm(k)
    zero = int(np.flatnonzero(indices == 0)[0])
    a, b = [c for c in range(3) if c != zero]
    la, lb, lc = np.asarray(lengths)[[a, b, zero]]
    surface_h2 = (lb * lc * k[a]**2 + la * lc * k[b]**2 +
                  la * lb / 2 * (k[a]**2 + k[b]**2)) / (omega * MU)**2
    rs = np.sqrt(omega * MU / (2 * sigma))
    q = omega * EPS * np.prod(lengths) / (4 * rs * surface_h2)
    return omega / (2 * np.pi), float(q)


def fit_pole(frequency, amplitude):
    frequency, amplitude = np.asarray(frequency), np.asarray(amplitude)
    center = float(np.mean(frequency))
    width = float(np.ptp(frequency))
    inverse = 1 / amplitude
    matrix = np.column_stack([(frequency - center) / width, np.ones(len(frequency))])
    slope, intercept = np.linalg.lstsq(matrix, inverse, rcond=None)[0]
    pole = center - width * intercept / slope
    residual = np.linalg.norm(matrix @ [slope, intercept] - inverse) / np.linalg.norm(inverse)
    return {"frequency_Hz": float(pole.real),
            "Q_from_local_pole": float(pole.real / (2 * abs(pole.imag))),
            "pole_Hz": pole, "inverse_response_fit_residual": float(residual)}
