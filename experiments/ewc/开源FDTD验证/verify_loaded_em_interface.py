"""Independently audit archived 3D responses and decide bridge readiness."""

import argparse
import csv
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.signal import convolve

ROOT = Path(__file__).parent


def load(folder, record):
    path = folder/(record["name"]+".npz")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == record["npz_sha256"]
    assert json.loads((folder/(record["name"]+".json")).read_text(encoding="utf-8")) == record
    with np.load(path, allow_pickle=False) as archive:
        data = {key: archive[key] for key in archive.files}
    assert all(np.isfinite(value).all() for value in data.values())
    assert data["time"][0] == 0 and np.all(np.diff(data["time"]) > 0)
    return data


def main(folder):
    source = folder/"loaded_em_summary.json"
    raw_summary = source.read_bytes()
    summary = json.loads(raw_summary)
    assert summary["version"] == "0.3.5"
    assert hashlib.sha256((ROOT/"run_loaded_em_interface.py").read_bytes()).hexdigest() == summary["script_sha256"]
    cases = {record["name"]: record for record in summary["cases"]}
    expected = {f"{kind}_{dx}mm"+(f"_{i}" if kind == "impulse" else "")
                for dx in (5, 2.5) for kind in ("impulse", "holdout")
                for i in (range(4) if kind == "impulse" else [0])}
    expected |= {"epsilon_plus_1pct", "load_double", "load_removed", "duration_double", "timestep_half"}
    assert set(cases) == expected
    arrays = {name: load(folder, record) for name, record in cases.items()}
    refinement_bytes = (folder/"loaded_em_refinement_summary.json").read_bytes()
    refinement = json.loads(refinement_bytes)
    assert refinement["source_summary_sha256"] == hashlib.sha256(raw_summary).hexdigest()
    assert refinement["runner_sha256"] == summary["script_sha256"]
    assert refinement["refinement_script_sha256"] == hashlib.sha256(
        (ROOT/"refine_loaded_em_interface.py").read_bytes()).hexdigest()
    extra = {r["name"]: r for r in refinement["cases"]}
    assert set(extra) == {"impulse_2mm_0", "holdout_2mm", "holdout_5mm_dt_half"}
    for name, record in extra.items():
        cases[name] = record
        arrays[name] = load(folder, record)
    assert len(summary["benchmarks"]) == 3
    for record in summary["benchmarks"]:
        data = load(folder, record)
        dt = data["time"][1]
        dx = record["dx_mm"]*1e-3
        exact = 299792458/2*np.sqrt(1/.16**2+1/.10**2)
        courant = .5/record["time_refinement"]
        discrete = np.arcsin(courant*np.sqrt(np.sin(np.pi*dx/.16/2)**2+
                                             np.sin(np.pi*dx/.10/2)**2))/(np.pi*dt)
        assert np.isclose(record["analytic_Hz"], exact, rtol=1e-12)
        assert np.isclose(record["discrete_Hz"], discrete, rtol=1e-12)
        phase = 2*np.pi*record["fitted_Hz"]*data["time"]
        design = np.column_stack((np.cos(phase), np.sin(phase), np.ones(len(phase))))
        residual = design@np.linalg.lstsq(design, data["signal"], rcond=None)[0]-data["signal"]
        assert np.linalg.norm(residual)/np.linalg.norm(data["signal"]) < 1e-6
        assert record["relative_discrete_error"] < 1e-6
    decisions, frequency_rows = [], []
    frequencies = np.linspace(2.5e9, 3.5e9, 41)
    frequency_responses = {}
    reciprocity = {}
    for name, record in cases.items():
        data = arrays[name]
        times, inputs, output = data["time"], data["drive"], data["readout"]
        assert inputs.shape == (record["steps"], 4)
        assert output.shape == (record["steps"]+1, 4)
        assert np.all(output[0] == 0)
        assert np.allclose(np.diff(times), record["dt_s"], rtol=1e-12, atol=1e-20)
        assert np.allclose(record["source_volume_m3"], 1e-6, rtol=1e-12)
        assert data["final_E"].shape == tuple(record["grid_shape"])+(3,)
        for axis in range(3):
            for component in range(3):
                if axis != component:
                    assert np.max(abs(np.take(data["final_E"][..., component], [0, -1], axis=axis))) == 0
        if record["kind"] == "impulse":
            intended = np.zeros_like(inputs)
            intended[0, record["driven"]] = 1
            assert np.array_equal(inputs, intended)
        else:
            midpoint = times[:-1]+record["dt_s"]/2
            for index in range(3):
                envelope = np.exp(-.5*((midpoint-(1.2+index*.5)*1e-9)/.3e-9)**2)
                expected_drive = (index+1)/3*envelope*np.cos(
                    2*np.pi*(3e9+index*.25e9)*midpoint+index*.7)
                assert np.allclose(inputs[:, index], expected_drive, atol=1e-12, rtol=1e-12)
            assert np.all(inputs[:, 3] == 0)
    for dx in (5, 2.5):
        direct = arrays[f"holdout_{dx}mm"]
        prediction = np.zeros_like(direct["readout"])
        matrix = np.stack([arrays[f"impulse_{dx}mm_{i}"]["readout"][1:] for i in range(4)], axis=1)
        # Explicitly request linear, not circular, convolution and discard only
        # times after the independently simulated observation interval.
        for source_index in range(3):
            for receiver in range(4):
                prediction[1:, receiver] += convolve(
                    direct["drive"][:, source_index], matrix[:, source_index, receiver],
                    mode="full", method="direct")[:len(prediction)-1]
        error = float(np.linalg.norm(prediction-direct["readout"])/np.linalg.norm(direct["readout"]))
        assert error < 1e-10
        assert np.allclose(prediction, direct["prediction"], rtol=1e-10, atol=1e-12)
        assert abs(error-cases[f"holdout_{dx}mm"]["relative_reconstruction_error"]) < 1e-12
        decisions.append({"dx_mm": dx, "relative_holdout_error": error})
        reciprocity[dx] = float(np.linalg.norm(matrix-matrix.transpose(0, 2, 1))/
                                max(np.linalg.norm(matrix), 1e-300))
        t = direct["time"][1:]-direct["time"][1]/2
        transform = np.exp(-2j*np.pi*frequencies[:, None]*t[None, :])
        response = transform@matrix[:, :3, 3]
        frequency_responses[dx] = response
        for source_index in range(3):
            for f, z in zip(frequencies, response[:, source_index]):
                frequency_rows.append({"dx_mm": dx, "source": source_index, "frequency_Hz": f,
                                       "real_normalized": z.real, "imag_normalized": z.imag,
                                       "magnitude_normalized": abs(z)})
    coarse = frequency_responses[5]
    fine = frequency_responses[2.5]
    frequency_change = float(np.linalg.norm(coarse-fine)/max(np.linalg.norm(fine), 1e-300))
    center_change = [float(abs(coarse[20, i]-fine[20, i])/max(abs(fine[20, i]), 1e-300))
                     for i in range(3)]
    baseline = arrays["impulse_5mm_0"]
    sensitivities = {}
    for name in ("epsilon_plus_1pct", "load_double", "load_removed"):
        y = arrays[name]["readout"][:, 3]
        ref = baseline["readout"][:, 3]
        assert len(y) == len(ref)
        sensitivities[name] = float(np.linalg.norm(y-ref)/max(np.linalg.norm(ref), 1e-300))
    assert sensitivities["load_removed"] > 1e-6
    extended = arrays["duration_double"]
    length = len(baseline["time"])
    assert np.array_equal(extended["readout"][:length], baseline["readout"])
    tail = float(np.linalg.norm(extended["readout"][length:, 3])/
                 max(np.linalg.norm(extended["readout"][:, 3]), 1e-300))
    half = arrays["timestep_half"]
    aligned = np.interp(half["time"], baseline["time"],
                        baseline["readout"][:, 3]/baseline["time"][1])
    target = half["readout"][:, 3]/half["time"][1]
    time_change = float(np.linalg.norm(aligned-target)/max(np.linalg.norm(target), 1e-300))
    holdout_changes = []
    for left_name, right_name in (
        ("holdout_5mm", "holdout_2.5mm"),
        ("holdout_2.5mm", "holdout_2mm"),
        ("holdout_5mm", "holdout_5mm_dt_half"),
    ):
        left, right = arrays[left_name], arrays[right_name]
        mask = right["time"] <= left["time"][-1]
        aligned = np.interp(right["time"][mask], left["time"], left["readout"][:, 3])
        target = right["readout"][mask, 3]
        holdout_changes.append({"left": left_name, "right": right_name,
                               "relative_readout_waveform_change": float(
                                   np.linalg.norm(aligned-target)/max(np.linalg.norm(target), 1e-300))})
    finest = arrays["impulse_2mm_0"]
    finest_t = finest["time"][1:]-finest["time"][1]/2
    finest_response = np.exp(-2j*np.pi*frequencies[:, None]*finest_t[None, :])@finest["readout"][1:, 3]
    extra_band_change = float(np.linalg.norm(fine[:, 0]-finest_response)/
                             max(np.linalg.norm(finest_response), 1e-300))
    for f, z in zip(frequencies, finest_response):
        frequency_rows.append({"dx_mm": 2, "source": 0, "frequency_Hz": f,
                               "real_normalized": z.real, "imag_normalized": z.imag,
                               "magnitude_normalized": abs(z)})
    assert source.read_bytes() == raw_summary
    assert (folder/"loaded_em_refinement_summary.json").read_bytes() == refinement_bytes
    result = {
        "data_integrity": "PASS", "summary_sha256": hashlib.sha256(raw_summary).hexdigest(),
        "refinement_summary_sha256": hashlib.sha256(refinement_bytes).hexdigest(),
        "case_count": len(cases), "benchmark_count": 3, "holdout_checks": decisions,
        "reciprocity_relative_difference": reciprocity,
        "band_2p5_to_3p5GHz_relative_mesh_change": frequency_change,
        "at_3GHz_relative_mesh_changes": center_change,
        "time_refinement_relative_waveform_change": time_change,
        "finite_bandwidth_holdout_changes": holdout_changes,
        "source0_band_relative_mesh_change_2p5_to_2mm": extra_band_change,
        "load_material_sensitivities": sensitivities,
        "after_12ns_extended_response_norm_fraction": tail,
        "full_hardware_gate": False,
        "bridge_readiness": "NOT_READY: normalized distributed interfaces, no validated physical RF port or detector",
        "note": "Audit PASS establishes archive consistency, not physical hardware readiness or mesh convergence",
    }
    with (folder/"loaded_frequency_response.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(frequency_rows[0]))
        writer.writeheader()
        writer.writerows(frequency_rows)
    (folder/"loaded_em_verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT/"第十一轮结果")
    main(parser.parse_args().output)
