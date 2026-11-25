"""111 PEC copper perturbation and projected-response diagnostics, not a field solver."""
import numpy as np
from scipy.linalg import eigh

C0 = 299792458.
MU = 4e-7 * np.pi
EPS = 1 / (MU * C0**2)


def reference(lengths, sigma):
    lengths = np.asarray(lengths, dtype=float)
    if (lengths.shape != (3,) or not np.isfinite(lengths).all() or np.any(lengths <= 0)
            or not np.isfinite(sigma) or sigma <= 0):
        raise ValueError("Three positive lengths and positive conductivity required")
    k = np.pi / lengths
    omega = C0 * np.linalg.norm(k)
    p1 = np.cross(k, [1., 0., 0.])
    p1 /= np.linalg.norm(p1)
    p2 = np.cross(k, p1)
    p2 /= np.linalg.norm(p2)
    basis = np.column_stack((p1, p2))
    h = np.cross(k[:, None], basis, axis=0) / (omega * MU)
    volume = np.prod(lengths)
    mass = EPS * volume / 8 * (basis.T @ basis)
    weights = volume / 2 * (np.sum(1 / lengths) - 1 / lengths)
    surface = h.T @ (weights[:, None] * h)
    rs = np.sqrt(omega * MU / (2 * sigma))
    rates, rotation = eigh(rs * surface, mass)
    if not np.all(rates > 0):
        raise ValueError("Both copper-loss directions must dissipate")
    return dict(frequency_Hz=float(omega / (2 * np.pi)), Q=omega / rates,
                basis=basis, mass=mass, surface=surface, loss_rotation=rotation)


def discrete_sources(grid, basis):
    k = np.pi / np.asarray(grid.lengths)
    kd = 2 * np.sin(k * grid.spacing / 2) / grid.spacing
    polarizations, seeds = [], []
    for vector in np.asarray(basis).T:
        a = vector - kd * (kd @ vector) / (kd @ kd)
        seed = grid.pec.lift(grid.pec.sample_mode((1, 1, 1), a))
        for previous in seeds:
            seed -= previous * (previous @ (grid.m * seed))
        seed /= np.sqrt(grid.m @ seed**2)
        polarizations.append(a)
        seeds.append(seed)
    seeds = np.column_stack(seeds)
    return seeds, grid.m[:, None] * seeds, np.array(polarizations).T


def fit_matrix_poles(frequency, response):
    frequency, response = np.asarray(frequency, float), np.asarray(response, complex)
    if (frequency.ndim != 1 or len(frequency) < 3 or not np.isfinite(frequency).all()
            or np.any(np.diff(frequency) <= 0) or np.any(frequency <= 0)
            or response.shape != (len(frequency), 2, 2) or not np.isfinite(response).all()):
        raise ValueError("Ordered positive frequencies and complete finite 2x2 responses required")
    center, unit = np.mean(frequency), np.ptp(frequency)
    inverse = np.linalg.inv(response)
    matrix = np.column_stack(((frequency - center) / unit, np.ones(len(frequency))))
    coefficients = np.linalg.lstsq(matrix, inverse.reshape(len(frequency), 4), rcond=None)[0]
    slope, intercept = coefficients.reshape(2, 2, 2)
    values, vectors = np.linalg.eig(-np.linalg.solve(slope, intercept))
    poles = center + unit * values
    if not np.isfinite(poles).all() or np.any(poles.real <= 0) or np.any(poles.imag <= 0):
        raise ValueError("Growing, zero-loss or invalid poles are not admissible")
    q = poles.real / (2 * poles.imag)
    order = np.argsort(q)
    residual = np.linalg.norm(matrix @ coefficients - inverse.reshape(len(frequency), 4)) / np.linalg.norm(inverse)
    return dict(poles_Hz=poles[order], Q=q[order], eigenvectors=vectors[:, order],
                inverse_fit_residual=float(residual))
