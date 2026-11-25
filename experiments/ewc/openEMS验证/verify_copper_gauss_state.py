"""Independent array collocation equations and positive-quadrature source energy."""
import numpy as np

from verify_copper_driven_state import stored_energy, equations_pass
from verify_finite_copper_cavity import geometry, node_divergence
from verify_finite_pec_cavity import curl_array, transpose_array


def check_collocation(shape, lengths, dt, r, l, before, after, stages, sources):
    m, q, edges, scale, _, _ = geometry(shape, lengths)
    rr, ll = scale[:, None] * r, scale[:, None] * l
    for group in (before, after):
        assert len(group) == 3
        for value, expected in zip(group, (m.shape, q.shape, ll.shape)):
            assert value.shape == expected and not np.iscomplexobj(value) and np.isfinite(value).all()
    es, bs, is_, js = stages
    for value, expected in zip(stages, ((2, len(m)), (2, len(q)), (2, *ll.shape), (2, len(edges)))):
        assert value.shape == expected and not np.iscomplexobj(value) and np.isfinite(value).all()
    assert sources.shape == (2, len(m)) and np.isfinite(sources).all() and not np.iscomplexobj(sources)
    assert np.isfinite(dt) and dt > 0 and np.all(rr > 0) and np.all(ll > 0)
    root = np.sqrt(3) / 6
    tableau = np.array([[.25, .25 - root], [.25 + root, .25]])
    electric_flux, magnetic_flux, branch_flux, source_wall = [], [], [], []
    wall_errors = []

    def relative(residual, *terms):
        return float(np.linalg.norm(residual) /
                     max(sum(np.linalg.norm(term) for term in terms), 1e-25))

    for s in range(2):
        wall = np.bincount(edges, weights=js[s], minlength=len(m))
        sw = sources[s] - wall
        electric_flux.append(transpose_array(q * bs[s], shape) + sw)
        magnetic_flux.append(-curl_array(es[s], shape))
        resistive = rr * (js[s, :, None] - is_[s])
        branch_flux.append(resistive)
        source_wall.append(sw)
        wall_errors.append(relative(es[s, edges] - resistive.sum(axis=1), es[s, edges]))
    electric_flux, magnetic_flux, branch_flux, source_wall = map(
        np.array, (electric_flux, magnetic_flux, branch_flux, source_wall))
    errors = dict(electric=[], magnetic=[], RL=[], charge=[])
    weights = (*tableau, np.array([.5, .5]))
    ends = ((es[0], bs[0], is_[0]), (es[1], bs[1], is_[1]), after)
    for end, w in zip(ends, weights):
        differences = [x - y for x, y in zip(end, before)]
        increments = (m * differences[0], differences[1], ll * differences[2])
        predicted = (dt * (w @ electric_flux), dt * (w @ magnetic_flux),
                     dt * np.einsum("s,sjk->jk", w, branch_flux))
        for key, inc, pred in zip(("electric", "magnetic", "RL"), increments, predicted):
            errors[key].append(relative(inc - pred, inc, pred))
        charge = node_divergence(increments[0], shape)
        expected_charge = dt * node_divergence(w @ source_wall, shape)
        errors["charge"].append(relative(charge - expected_charge, charge, expected_charge))
    u0, u1 = [stored_energy(*state, m, q, ll) for state in (before, after)]
    heat = dt / 2 * np.sum(rr[None, :, :] * (js[:, :, None] - is_)**2)
    work = dt / 2 * np.sum(es * sources)
    result = {key: max(values) for key, values in errors.items()}
    result.update(wall=max(wall_errors),
                  energy=float(abs(u1 - u0 + heat - work) / max(u0, u1, abs(work), 1e-25)),
                  before_energy_J=float(u0), after_energy_J=float(u1),
                  heat_J=float(heat), source_work_J=float(work))
    assert result["heat_J"] >= 0
    return result


__all__ = ["check_collocation", "equations_pass"]
