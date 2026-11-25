"""Extract causal, loaded 3D field responses using unmodified flaport/fdtd.

Drive and readout are normalized volume-current / mean-field interfaces.
They are deliberately NOT declared calibrated RF voltage or power ports.
"""

import argparse
import hashlib
import json
from pathlib import Path
import time

import fdtd
import numpy as np
from scipy.optimize import minimize_scalar
from scipy.signal import fftconvolve

ROOT = Path(__file__).parent
C0 = 299792458.0
SIZE_MM = (160, 100, 60)
CENTERS_MM = ((40, 50, 30), (120, 50, 30), (80, 25, 30), (80, 75, 30))
F0 = 3e9
SOURCE_RATE = 1e9


def coverage(coordinates, center, width=10):
    """Cell-volume fractions of a fixed physical box, independent of mesh."""
    dx = coordinates[1] - coordinates[0]
    return np.clip((np.minimum(coordinates + dx/2, center + width/2) -
                    np.maximum(coordinates - dx/2, center - width/2))/dx, 0, 1)


def configure(dx_mm, refinement=1, epsilon=2.1, load_sigma=.02):
    shape = tuple(round(length/dx_mm)+1 for length in SIZE_MM)
    axes = [np.arange(n)*dx_mm for n in shape]
    eps = np.ones(shape+(3,))
    block = np.ones(shape)
    for axis, lo, hi, center in zip(range(3), (60, 35, 20), (100, 65, 40), (80, 50, 30)):
        weight = coverage(axes[axis], center, hi-lo)
        view = [1, 1, 1]
        view[axis] = len(weight)
        block *= weight.reshape(view)
    eps += (epsilon-1)*block[..., None]
    # PEC fixes tangential E only. Normal E is not frozen on a 3D face.
    for axis in range(3):
        for face in (0, -1):
            for component in range(3):
                if component != axis:
                    index = [slice(None)]*4
                    index[axis], index[3] = face, component
                    eps[tuple(index)] = np.inf
    volumes = []
    for center in CENTERS_MM:
        box = np.ones(shape)
        for axis in range(3):
            weight = coverage(axes[axis], center[axis])
            view = [1, 1, 1]
            view[axis] = len(weight)
            box *= weight.reshape(view)
        volumes.append(box)
    sigma = np.broadcast_to(.002*block[..., None], eps.shape).copy()
    # A finite lossy pickup volume, not a lumped 50-ohm connector.
    sigma[..., 2] += load_sigma*volumes[3]
    grid = fdtd.Grid(shape, grid_spacing=dx_mm*1e-3, permittivity=eps,
                     courant_number=.5/refinement)
    medium = fdtd.AbsorbingObject(eps, sigma, name="medium")
    grid[:, :, :] = medium
    sources, profiles = [], []
    for index, box in enumerate(volumes):
        points = np.argwhere(box > 0)
        source = fdtd.LineSource(period=10**15, phase_shift=np.pi/2, name=f"drive{index}")
        grid[points[:, 0].tolist(), points[:, 1].tolist(), points[:, 2].tolist()] = source
        loc = tuple(points.T)+(np.full(len(points), 2),)
        profile = (SOURCE_RATE*grid.time_step*box[tuple(points.T)]*
                   medium.inverse_permittivity[loc]/(1+medium.absorption_factor[loc]))
        sources.append(source)
        profiles.append(profile)
    metadata = {
        "grid_shape": shape, "dx_mm": dx_mm, "dt_s": grid.time_step,
        "time_refinement": refinement, "size_mm": SIZE_MM, "epsilon_r": epsilon,
        "dielectric_sigma_S_per_m": .002, "pickup_sigma_z_S_per_m": load_sigma,
        "pickup_box_mm": [10, 10, 10], "centers_mm": CENTERS_MM,
        "source_rate_normalized_per_s": SOURCE_RATE,
        "source_volume_m3": [float(b.sum()*(dx_mm*1e-3)**3) for b in volumes],
        "normalization": "Normalized source-density drive and weighted mean Ez; not V/V or watts",
        "boundary": "PEC tangential E fixed on all six faces; normal E retained",
    }
    return grid, sources, profiles, volumes, metadata


def solve(dx_mm, duration_ns, kind, driven=0, refinement=1, epsilon=2.1, load_sigma=.02):
    grid, sources, profiles, volumes, metadata = configure(dx_mm, refinement, epsilon, load_sigma)
    steps = round(duration_ns*1e-9/grid.time_step)
    midpoint = (np.arange(steps)+.5)*grid.time_step
    drive = np.zeros((steps, 4))
    if kind == "impulse":
        drive[0, driven] = 1
    elif kind == "holdout":
        # Independent nontrivial waveforms, with no arithmetic answers in the excitation.
        for index in range(3):
            envelope = np.exp(-.5*((midpoint-(1.2+index*.5)*1e-9)/(.3e-9))**2)
            drive[:, index] = (index+1)/3*envelope*np.cos(
                2*np.pi*(F0+index*.25e9)*midpoint+index*.7)
    else:
        raise ValueError(kind)
    outputs = np.zeros((steps+1, 4))
    started = time.perf_counter()
    for step in range(steps):
        for index, source in enumerate(sources):
            phase = np.sin(2*np.pi*step/source.period+source.phase_shift)
            source.profile = profiles[index]*(drive[step, index]/phase)
        grid.step()
        for index, box in enumerate(volumes):
            outputs[step+1, index] = np.sum(grid.E[..., 2]*box)/box.sum()
        if step % 512 == 0 and not np.isfinite(grid.E).all():
            raise RuntimeError("Nonfinite electromagnetic field.")
    for axis in range(3):
        for component in range(3):
            if component != axis:
                assert np.max(np.abs(np.take(grid.E[..., component], [0, -1], axis=axis))) == 0
    assert np.isfinite(outputs).all()
    return {"time": np.arange(steps+1)*grid.time_step, "drive": drive, "readout": outputs,
            "final_E": grid.E, "final_H": grid.H}, {
                **metadata, "kind": kind, "driven": driven, "steps": steps,
                "runtime_s": time.perf_counter()-started,
                "final_other_E_fraction": float(np.linalg.norm(grid.E[..., :2])/
                                                max(np.linalg.norm(grid.E), 1e-300))}


def benchmark(dx_mm, refinement=1, duration_ns=8):
    shape = tuple(round(length/dx_mm)+1 for length in SIZE_MM)
    eps = np.ones(shape+(3,))
    for axis in range(3):
        for face in (0, -1):
            for component in range(3):
                if axis != component:
                    loc = [slice(None)]*4
                    loc[axis], loc[3] = face, component
                    eps[tuple(loc)] = np.inf
    grid = fdtd.Grid(shape, grid_spacing=dx_mm*1e-3, permittivity=eps,
                     courant_number=.5/refinement)
    x, y = np.arange(shape[0]), np.arange(shape[1])
    initial = np.sin(np.pi*x/(shape[0]-1))[:, None]*np.sin(np.pi*y/(shape[1]-1))[None, :]
    initial[[0, -1], :] = 0
    initial[:, [0, -1]] = 0
    grid.E[..., 2] = initial[..., None]
    steps = round(duration_ns*1e-9/grid.time_step)
    signal = [float(grid.E[shape[0]//2, shape[1]//2, shape[2]//2, 2])]
    for _ in range(steps):
        grid.step()
        signal.append(float(grid.E[shape[0]//2, shape[1]//2, shape[2]//2, 2]))
    times = np.arange(steps+1)*grid.time_step
    exact = C0/2*np.sqrt(1/.16**2+1/.10**2)
    discrete = np.arcsin(grid.courant_number*np.sqrt(
        np.sin(np.pi*dx_mm/160/2)**2+np.sin(np.pi*dx_mm/100/2)**2))/(np.pi*grid.time_step)

    def objective(frequency):
        phase = 2*np.pi*frequency*times
        design = np.column_stack((np.cos(phase), np.sin(phase), np.ones(len(times))))
        residual = design@np.linalg.lstsq(design, signal, rcond=None)[0]-signal
        return float(np.mean(residual**2))

    fit = minimize_scalar(objective, bounds=(exact*.97, exact*1.03), method="bounded",
                          options={"xatol": .001})
    assert fit.success
    return {"time": times, "signal": np.asarray(signal)}, {
        "dx_mm": dx_mm, "time_refinement": refinement, "analytic_Hz": exact,
        "discrete_Hz": discrete, "fitted_Hz": float(fit.x),
        "relative_analytic_error": abs(float(fit.x)/exact-1),
        "relative_discrete_error": abs(float(fit.x)/discrete-1)}


def main(args):
    args.output.mkdir(parents=True, exist_ok=True)
    cases, benchmarks, comparisons = [], [], []

    def save(name, arrays, record):
        path = args.output/(name+".npz")
        np.savez_compressed(path, **arrays)
        record.update(name=name, npz_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        (args.output/(name+".json")).write_text(json.dumps(record, indent=2), encoding="utf-8")
        return record

    for dx in (5, 2.5):
        arrays, record = benchmark(dx)
        benchmarks.append(save(f"benchmark_{dx}mm", arrays, record))
    arrays, record = benchmark(2.5, refinement=2)
    benchmarks.append(save("benchmark_2.5mm_dt_half", arrays, record))
    grids = {}
    for dx in (5, 2.5):
        kernels = []
        for driven in range(4):
            name = f"impulse_{dx}mm_{driven}"
            print(name, flush=True)
            arrays, record = solve(dx, args.duration_ns, "impulse", driven)
            cases.append(save(name, arrays, record))
            kernels.append(arrays["readout"][1:])
        print(f"holdout_{dx}mm", flush=True)
        direct, record = solve(dx, args.duration_ns, "holdout")
        prediction = np.zeros_like(direct["readout"])
        for source in range(3):
            for receiver in range(4):
                prediction[1:, receiver] += fftconvolve(
                    direct["drive"][:, source], kernels[source][:, receiver])[:len(prediction)-1]
        error = float(np.linalg.norm(prediction-direct["readout"])/
                      max(np.linalg.norm(direct["readout"]), 1e-300))
        direct["prediction"] = prediction
        cases.append(save(f"holdout_{dx}mm", direct, {**record, "relative_reconstruction_error": error}))
        grids[dx] = (direct["time"], np.stack(kernels, axis=1))
    for name, kwargs in (
        ("epsilon_plus_1pct", {"epsilon": 2.121}),
        ("load_double", {"load_sigma": .04}),
        ("load_removed", {"load_sigma": 0}),
        ("duration_double", {"duration_ns": 2*args.duration_ns}),
        ("timestep_half", {"refinement": 2}),
    ):
        print(name, flush=True)
        options = {"duration_ns": args.duration_ns, **kwargs}
        arrays, record = solve(5, kind="impulse", **options)
        cases.append(save(name, arrays, record))
    coarse_t, coarse = grids[5]
    fine_t, fine = grids[2.5]
    for source in range(3):
        # Impulse area is dt-dependent: compare the response per unit drive-time.
        aligned = np.interp(fine_t[1:], coarse_t[1:], coarse[:, source, 3]/(coarse_t[1]-coarse_t[0]))
        target = fine[:, source, 3]/(fine_t[1]-fine_t[0])
        comparisons.append({"source": source, "relative_mesh_waveform_change": float(
            np.linalg.norm(aligned-target)/max(np.linalg.norm(target), 1e-300))})
    summary = {
        "solver": "flaport/fdtd", "version": fdtd.__version__, "solver_module": fdtd.__file__,
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "duration_ns": args.duration_ns, "cases": cases, "benchmarks": benchmarks,
        "mesh_comparisons": comparisons,
        "scope": "Loaded 3D normalized field-response extraction; not calibrated RF-port/circuit co-simulation",
        "full_hardware_gate": False,
        "limitations": [
            "PEC walls: no finite-metal wall loss, seams or connectors",
            "Generic nondispersive dielectric, not measured commercial material",
            "Pickup conductivity is a distributed load, not a validated 50-ohm RF port",
            "Source is normalized volume-current excitation, not a voltage-driven antenna",
            "Finite impulse archive is not a proven long-time or band-limited error bound",
            "No actual RF amplifier, mixer, detector, ADC or calibration controller model",
            "No new full-adder timing, complete power, physical cost or multi-bit performance claim",
        ],
    }
    (args.output/"loaded_em_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({"cases": len(cases), "benchmarks": benchmarks,
                      "mesh_comparisons": comparisons}, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT/"第十一轮结果")
    parser.add_argument("--duration-ns", type=float, default=12)
    main(parser.parse_args())
