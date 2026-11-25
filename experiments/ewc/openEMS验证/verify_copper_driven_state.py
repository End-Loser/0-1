"""Independent array equations for a driven full E/B/wall-memory step."""
import numpy as np

from verify_finite_copper_cavity import geometry, node_divergence
from verify_finite_pec_cavity import curl_array, transpose_array


def stored_energy(e, b, state, m, q, ll):
    return .5 * (m @ e**2 + q @ b**2 + np.sum(ll * state**2))


def check_step(shape, lengths, dt, r, l, before, after, j, source):
    m, q, edges, scale, _, _ = geometry(shape, lengths)
    rr, ll = scale[:, None] * r, scale[:, None] * l
    expected_shapes = ((len(m),), (len(q),), rr.shape)
    for states in (before, after):
        for value, expected in zip(states, expected_shapes):
            assert np.asarray(value).shape == expected
            assert not np.iscomplexobj(value) and np.isfinite(value).all()
    assert np.asarray(j).shape == (len(edges),) and np.isfinite(j).all()
    assert np.asarray(source).shape == (len(m),) and np.isfinite(source).all()
    assert not np.iscomplexobj(source) and not np.iscomplexobj(j)
    assert np.isfinite(dt) and dt > 0
    assert np.all(rr > 0) and np.all(ll > 0)
    e0, b0, i0 = before
    e1, b1, i1 = after
    em, bm, im = [(a + b) / 2 for a, b in zip(before, after)]
    wall = np.zeros(len(m))
    np.add.at(wall, edges, j)
    ctqb = transpose_array(q * bm, shape)
    rate = m * (e1 - e0) / dt
    ce = curl_array(em, shape)
    lr = ll * (i1 - i0) / dt
    resistive = rr * (j[:, None] - im)
    drho = node_divergence(rate, shape)
    expected_charge = node_divergence(source - wall, shape)

    def relative(residual, *terms):
        return float(np.linalg.norm(residual) /
                     max(sum(np.linalg.norm(term) for term in terms), 1e-25))

    u0 = stored_energy(e0, b0, i0, m, q, ll)
    u1 = stored_energy(e1, b1, i1, m, q, ll)
    heat = dt * np.sum(rr * (j[:, None] - im)**2)
    work = dt * (em @ source)
    return dict(
        electric=relative(rate - ctqb + wall - source, rate, ctqb, wall, source),
        magnetic=relative((b1 - b0) / dt + ce, (b1 - b0) / dt, ce),
        RL=relative(lr - resistive, lr, resistive),
        wall=relative(em[edges] - resistive.sum(axis=1), em[edges]),
        charge=relative(drho - expected_charge, drho, expected_charge),
        energy=float(abs(u1 - u0 + heat - work) /
                     max(u0, u1, abs(work), 1e-25)),
        heat_J=float(heat), source_work_J=float(work),
        before_energy_J=float(u0), after_energy_J=float(u1))


def equations_pass(metrics):
    return all(np.isfinite(metrics[key]) and metrics[key] < limit for key, limit in (
        ("electric", 1e-7), ("magnetic", 1e-7), ("RL", 1e-5),
        ("wall", 1e-5), ("charge", 1e-6), ("energy", 1e-9)))
