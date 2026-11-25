"""Independent evidence audit and pinned conducting-sheet parameter analysis.

This verifies equations against native probe/field data; it does not implement
an independent FDTD solver or claim physical hardware validation.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

import numpy as np

SOURCE_COMMIT = "92cd92a8d924c78a93453c25fa541384c2ac1d0e"
MU0 = 4e-7 * np.pi
EPS0 = 1 / (MU0 * 299792458.0**2)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pulse_dft(path, frequency, column=1):
    data = np.loadtxt(path, comments="%")
    t, v = data[:, 0], data[:, column]
    dt = np.mean(np.diff(t))
    assert np.allclose(np.diff(t), dt, rtol=1e-8, atol=1e-20)
    weights = np.full(len(t), 2 * dt)
    weights[[0, -1]] = dt
    return (weights * v) @ np.exp(-2j * np.pi * t[:, None] * frequency)


def check_manifest(case):
    folder = Path(case["raw_folder"])
    for item in case["raw_manifest"]:
        assert digest(folder / item["name"]) == item["sha256"]


def audit_current(folder):
    summary = json.loads((folder / "summary.json").read_text())
    worst_dft, worst_partition, worst_branch = 0., 0., 0.
    center_checks = []
    for case in summary["cases"]:
        check_manifest(case)
        raw = Path(case["raw_folder"])
        archive = np.load(folder / (case["case"] + ".npz"))
        f, dx, ny = archive["frequency_Hz"], case["dx_mm"] * 1e-3, case["ny"]
        E, D = archive["electric_field"][..., 2], archive["D_field"][..., 2]
        ix = np.rint(archive["axes_x_m"] / dx).astype(int)
        iy = np.arange(ny + 1)
        center = round(.150 / dx)
        element_x = np.flatnonzero(abs(ix - center) <= 1)
        element_y = np.arange(1, ny) if case["guarded_load"] else np.arange(ny + 1)
        area = dx * (.07534606269235414 / ny)
        conductivity = .01 / (case["load_ohm"] * len(element_x) * len(element_y) * area)
        assert np.isclose(conductivity, case["kappa_model_S_per_m"], rtol=1e-12)
        dt = case["timestep_s"]
        q = 2j / dt * np.sin(np.pi * f * dt)
        c = np.cos(np.pi * f * dt)
        voltages = E[:, :4][:, :, element_y][:, :, :, element_x]
        branch_reference = -np.sum(voltages, axis=(1, 2, 3)) * .0025
        branch_reference /= len(element_x) * len(element_y) * case["load_ohm"]
        assert np.max(abs(branch_reference - archive["reference_branch"])) < 1e-14
        center_reference = pulse_dft(raw / "port_ut3", f) / case["load_ohm"]
        worst_center = 0.
        for j, row in enumerate(case["loop_checks"]):
            text = (raw / row["loop"]).read_text()
            indices = re.findall(r"-> \[(\d+),(\d+),(\d+)\]", text)
            lo, hi = [np.array(item, int) for item in indices[:2]]
            xs = np.flatnonzero((ix > lo[0]) & (ix <= hi[0]))
            ys = np.arange(lo[1] + 1, hi[1] + 1)
            current = pulse_dft(raw / row["loop"], f)
            delta = float(np.max(abs(current - archive["loop_current"][j])))
            worst_dft = max(worst_dft, delta)
            assert delta < 1e-13
            flux = -area * np.sum(D[:, lo[2]][:, ys][:, :, xs], axis=(1, 2))
            displacement = q * flux
            ex = np.intersect1d(xs, element_x)
            ey = np.intersect1d(ys, element_y)
            partial = -conductivity * area * c * np.sum(
                E[:, lo[2]][:, ey][:, :, ex], axis=(1, 2))
            full = -conductivity * area * c * np.sum(
                E[:, lo[2]][:, element_y][:, :, element_x], axis=(1, 2))
            restored = (current - displacement + full - partial) / c
            reference = np.maximum(abs(branch_reference), 1e-15)
            err = float(np.max(abs(current - displacement - partial) / reference))
            branch_err = float(np.max(abs(restored - branch_reference) / reference))
            worst_partition, worst_branch = max(worst_partition, err), max(worst_branch, branch_err)
            assert err < .01 and branch_err < .01
            assert np.max(abs(restored - archive["restored_branch"][j])) < 1e-13
            center_error = float(np.max(
                abs(restored - center_reference) / np.maximum(abs(center_reference), 1e-15)))
            assert abs(center_error - row["restored_branch_vs_center_voltage_max_relative_error"]) < 1e-8
            worst_center = max(worst_center, center_error)
        center_checks.append({"case": case["case"], "guarded_load": case["guarded_load"],
                              "dx_mm": case["dx_mm"], "center_branch_max_relative_error": worst_center,
                              "center_interface_pass": case["center_voltage_interface_1percent_pass"]})
    return {
        "completed_runs": len(summary["cases"]), "independent_equation_audit_pass": True,
        "DFT_max_absolute_A_s_difference": worst_dft,
        "partition_max_relative_error": worst_partition,
        "branch_max_relative_error": worst_branch,
        "center_voltage_checks": center_checks,
    }


def source_snapshots(output):
    names = ("cond_sheet_parameter.h", "OptimizeCondSheetParameter.m", "operator_ext_conductingsheet.cpp")
    sources = []
    for name in names:
        endpoint = f"repos/thliebig/openEMS/contents/FDTD/extensions/{name}?ref={SOURCE_COMMIT}"
        run = subprocess.run(
            ["gh", "api", endpoint, "-H", "Accept: application/vnd.github.raw+json"],
            capture_output=True, check=True, timeout=45)
        text = run.stdout.decode("utf-8")
        sources.append({
            "name": name, "commit": SOURCE_COMMIT,
            "url": f"https://github.com/thliebig/openEMS/blob/{SOURCE_COMMIT}/FDTD/extensions/{name}",
            "sha256": hashlib.sha256(run.stdout).hexdigest(), "text": text,
        })
    output.write_text(json.dumps({"sources": sources}, indent=2), encoding="utf-8")
    return {item["name"]: item["text"] for item in sources}


def audit_sheet(folder, sources):
    summary = json.loads((folder / "summary.json").read_text())
    header = sources["cond_sheet_parameter.h"]
    arrays = {}
    for key in ("omega_stop", "g", "r1", "r2", "l1", "l2"):
        match = re.search(rf"double {key}\[\d+\]=\{{([^}}]+)\}}", header)
        arrays[key] = np.array([float(v) for v in match[1].split(",")])
    assert "Ydiff = real(1./Ys)-real(1./Y);" in sources["OptimizeCondSheetParameter.m"]
    results, dft_error = [], 0.
    for case in summary["cases"]:
        check_manifest(case)
        a = np.load(folder / (case["case"] + ".npz"))
        f, raw = a["frequency_Hz"], Path(case["raw_folder"])
        e = pulse_dft(raw / "sheet_E", f, 3)
        h1 = pulse_dft(raw / "sheet_Hleft", f, 2)
        h2 = pulse_dft(raw / "sheet_Hright", f, 2)
        z = e / (h2 - h1 - 2j * np.pi * f * EPS0 * case["dx_mm"] * 1e-3 * e)
        error = float(np.max(abs(z - a["impedance"])))
        dft_error = max(dft_error, error)
        assert error < 1e-10
        d = case["thickness_um"] * 1e-6
        if not d:
            assert np.max(abs(z)) == 0
            continue
        sigma = case["sigma_S_per_m"]
        w0 = 8 / (sigma * d**2 * MU0)
        omega = 2 * np.pi * f / w0
        index = int(np.flatnonzero(arrays["omega_stop"] > 2 * np.pi * 4.5e9 / w0)[0])
        rational_y = arrays["g"][index] + 1 / (
            arrays["r1"][index] + 1j * omega * arrays["l1"][index]) + 1 / (
            arrays["r2"][index] + 1j * omega * arrays["l2"][index])
        rational_z = 1 / (sigma * d * rational_y)
        q = (1 + 1j) * np.sqrt(omega)
        exact_z = q / np.tanh(q) / (sigma * d)
        assert np.max(abs(exact_z - a["sheet_reference"])) < 1e-14
        results.append({
            "case": case["case"], "parameter_row": index,
            "measured_vs_pinned_rational_max_relative_error": float(np.max(abs(z / rational_z - 1))),
            "rational_vs_exact_sheet_max_relative_complex_error": float(np.max(abs(rational_z / exact_z - 1))),
            "measured_vs_exact_sheet_max_relative_complex_error": case["sheet_target_max_relative_complex_error"],
            "measured_vs_exact_sheet_max_relative_real_error": case["sheet_target_max_relative_real_error"],
        })
    return {
        "completed_runs": len(summary["cases"]), "DFT_max_impedance_difference_ohm": dft_error,
        "real_only_parameter_fit_confirmed": True, "source_commit": SOURCE_COMMIT,
        "source_not_proven_identical_to_binary": True, "comparisons": results,
        "complex_surface_impedance_1percent_pass": False, "thick_cavity_wall_pass": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    sources = source_snapshots(args.root / "官方薄片模型来源快照.json")
    result = {
        "current": audit_current(args.root / "电流分离"),
        "copper_sheet": audit_sheet(args.root / "铜薄片", sources),
        "full_joint_simulation_complete": False,
    }
    tests = subprocess.run(
        [sys.executable, "-B", str(Path(__file__).parent / "test_native_material_benchmarks.py")],
        capture_output=True, check=True, timeout=30)
    result["offline_unit_tests"] = {
        "returncode": tests.returncode, "log": (tests.stdout + tests.stderr).decode("utf-8"),
    }
    (args.root / "verification.json").write_text(
        json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps(result, indent=2))
