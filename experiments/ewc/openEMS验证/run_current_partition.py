"""Native openEMS evidence for total, displacement and resistor currents.

The discrete Ampere law and constitutive conductance are audited, not fitted.
The original TEM benchmark is retained unchanged.
"""

import argparse
import json
import math
from pathlib import Path
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET

import numpy as np

from native_field_io import read_complex_field
from run_native_port_benchmark import (
    C0, MU0, HEIGHT, WIDTH, LOAD_POSITION, box, build_model, sha256, transform,
)

EPS0 = 1 / (MU0 * C0**2)
FREQ = np.array([2.5e9, 3e9, 3.5e9])


def configure(path, dx, ny, load, guarded):
    build_model(path, dx, load, 8, ny)
    root = ET.parse(path).getroot()
    props = root.find("./ContinuousStructure/Properties")
    if guarded:
        resistor = props.find("./LumpedElement/Primitives/Box")
        resistor.find("P1").set("Y", str(WIDTH / ny))
        resistor.find("P2").set("Y", str(WIDTH - WIDTH / ny))
    for radius in (2, 4):
        for zindex in (1, 2):
            probe = ET.SubElement(
                props, "ProbeBox", Name=f"loop_r{radius}_z{zindex}",
                Type="1", Weight="-1", NormDir="2", OverSampling="20",
            )
            # Exact grid-line coordinates snap down on the dual mesh.
            z = (zindex + 1) * HEIGHT / 4
            box(probe, [LOAD_POSITION - radius * dx, 0, z],
                [LOAD_POSITION + radius * dx, WIDTH, z])
    for name, kind in (("ef", 10), ("df", 14), ("rot", 13), ("jf", 12)):
        dump = ET.SubElement(
            props, "DumpBox", Name=name, DumpType=str(kind),
            DumpMode="0", FileType="0",
        )
        ET.SubElement(dump, "FD_Samples").text = ",".join(map(str, FREQ))
        box(dump, [LOAD_POSITION - 7 * dx, 0, 0],
            [LOAD_POSITION + 7 * dx, WIDTH, HEIGHT])
    ET.indent(root)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def read_loop(path):
    text = path.read_text()
    indices = re.findall(r"-> \[(\d+),(\d+),(\d+)\]", text)
    if len(indices) < 2:
        raise ValueError("No native loop discretization header.")
    start, stop = [np.array(item, dtype=int) for item in indices[:2]]
    data = np.loadtxt(path, comments="%")
    return start, stop, data


def analyze(folder, dx, ny, load, guarded):
    axes, fields = None, {}
    phase_error = 0
    for name in ("ef", "df", "rot", "jf"):
        vectors = []
        for frequency in FREQ:
            a, field, error = read_complex_field(folder, name, frequency)
            if axes is not None and any(not np.array_equal(x, y) for x, y in zip(axes, a)):
                raise ValueError("Field dump meshes differ.")
            axes = a
            vectors.append(field)
            phase_error = max(phase_error, error)
        fields[name] = np.asarray(vectors)
    native_x = np.rint(axes[0] / (dx * 1e-3)).astype(int)
    native_y = np.rint(axes[1] / (WIDTH * 1e-3 / ny)).astype(int)
    native_z = np.rint(axes[2] / (HEIGHT * 1e-3 / 4)).astype(int)
    center = round(LOAD_POSITION / dx)
    xload = (native_x >= center - 1) & (native_x <= center + 1)
    yload = ((native_y >= 1) & (native_y <= ny - 1)) if guarded else np.ones(len(native_y), bool)
    dx_m, dy_m, dz_m = dx * 1e-3, WIDTH * 1e-3 / ny, HEIGHT * 1e-3 / 4
    area = dx_m * dy_m
    # Same geometric normalization as Operator::Calc_LumpedElements().
    unit_gc = np.count_nonzero(xload) * np.count_nonzero(yload) * area / (HEIGHT * 1e-3)
    kappa = 1 / (load * unit_gc)
    log = (folder / "solver.log").read_text(errors="replace")
    timestep = float(re.search(r"FDTD timestep is:\s*([0-9.e+-]+)", log).group(1))
    omega = 2 * np.pi * FREQ
    derivative = 2j * np.sin(omega * timestep / 2) / timestep
    average = np.cos(omega * timestep / 2)
    u = np.loadtxt(folder / "port_ut3", comments="%")
    u_spectrum = transform(u, FREQ)
    ef, df = [fields[key][..., 2] for key in ("ef", "df")]
    rot = fields["rot"][..., 2].copy()
    # Dump rot(H) uses electric timestamps although its values are magnetic.
    rot *= np.exp(-1j * omega * timestep / 2)[:, None, None, None]
    weighted_voltage = np.sum(
        ef[:, :4, yload][:, :, :, xload], axis=(1, 2, 3)) * (-dz_m)
    weighted_voltage /= np.count_nonzero(xload) * np.count_nonzero(yload)
    reference_branch = weighted_voltage / load
    rows = []
    recovered, loop_spectra, displacement_spectra = [], [], []
    for radius in (2, 4):
        for zindex in (1, 2):
            label = f"loop_r{radius}_z{zindex}"
            start, stop, data = read_loop(folder / label)
            xmask = (native_x > start[0]) & (native_x <= stop[0])
            ymask = (native_y > start[1]) & (native_y <= stop[1])
            zlocation = np.flatnonzero(native_z == start[2])
            if len(zlocation) != 1 or not np.any(xmask) or not np.any(ymask):
                raise ValueError("Loop surface is not covered by raw dumps.")
            zi = int(zlocation[0])
            total = transform(data, FREQ)
            dflux = -np.sum(df[:, zi, ymask][:, :, xmask], axis=(1, 2)) * area
            displacement = derivative * dflux
            remaining = total - displacement
            enclosed = -np.sum(
                ef[:, zi, ymask & yload][:, :, xmask & xload],
                axis=(1, 2)) * kappa * area * average
            curl = -np.sum(rot[:, zi, ymask][:, :, xmask], axis=(1, 2)) * area
            all_conductance = -np.sum(
                ef[:, zi, yload][:, :, xload], axis=(1, 2)) * kappa * area * average
            # Restore only the independently measured conductance edges outside
            # the selected loop. Do not rescale the current to a known answer.
            restored = (remaining + all_conductance - enclosed) / average
            current_scale = np.maximum(abs(reference_branch), 1e-15)
            row = {
                "loop": label,
                "native_start": start.tolist(), "native_stop": stop.tolist(),
                "raw_UI_vs_R_max_relative_error": float(np.max(abs(u_spectrum / total / load - 1))),
                "ampere_partition_max_relative_error": float(np.max(abs(remaining - enclosed) / current_scale)),
                "loop_vs_curl_dump_max_relative_error": float(np.max(abs(total - curl) / current_scale)),
                "restored_branch_vs_weighted_voltage_max_relative_error": float(np.max(
                    abs(restored - reference_branch) / current_scale)),
                "center_vs_weighted_voltage_max_relative_error": float(np.max(
                    abs(u_spectrum - weighted_voltage) / np.maximum(abs(weighted_voltage), 1e-15))),
                "restored_branch_vs_center_voltage_max_relative_error": float(np.max(
                    abs(restored - u_spectrum / load) /
                    np.maximum(abs(u_spectrum / load), 1e-15))),
            }
            recovered.append(restored)
            loop_spectra.append(total)
            displacement_spectra.append(displacement)
            rows.append(row)
    passed = all(
        row["ampere_partition_max_relative_error"] < .01 and
        row["loop_vs_curl_dump_max_relative_error"] < .01 and
        row["restored_branch_vs_weighted_voltage_max_relative_error"] < .01
        for row in rows
    )
    spread = float(np.max(abs(np.array(recovered) - recovered[0])) /
                   np.max(abs(reference_branch)))
    passed = passed and spread < .01
    result = {
        "dx_mm": dx, "ny": ny, "load_ohm": load, "guarded_load": guarded,
        "frequency_Hz": FREQ.tolist(), "timestep_s": timestep,
        "kappa_model_S_per_m": kappa, "phase_reconstruction_max_error": phase_error,
        "D_vs_air_epsilonE_max_relative_error": float(np.max(abs(df - EPS0 * ef)) /
                                                     np.max(abs(df))),
        "material_J_dump_max_A_s_per_m2": float(np.max(abs(fields["jf"]))),
        "loop_checks": rows, "current_partition_1percent_pass": passed,
        "loop_branch_max_spread_relative": spread,
        "center_voltage_interface_1percent_pass": passed and all(
            row["center_vs_weighted_voltage_max_relative_error"] < .01 and
            row["restored_branch_vs_center_voltage_max_relative_error"] < .01 for row in rows),
        "general_connector_pass": False, "full_joint_simulation_complete": False,
    }
    return result, {
        "frequency_Hz": FREQ, "axes_x_m": axes[0], "axes_y_m": axes[1], "axes_z_m": axes[2],
        "electric_field": fields["ef"], "D_field": fields["df"],
        "rotH_field": fields["rot"], "material_J_field": fields["jf"],
        "weighted_voltage": weighted_voltage, "reference_branch": reference_branch,
        "restored_branch": np.array(recovered),
        "loop_current": np.array(loop_spectra),
        "displacement_current": np.array(displacement_spectra),
    }


def main(args):
    args.output.mkdir(parents=True, exist_ok=True)
    args.raw_dir.mkdir(parents=True, exist_ok=True)
    summaries = []
    for dx in args.spacing:
        for load in args.loads:
            for guarded in args.guards:
                name = f"dx{dx:g}_ny{args.ny}_R{load:g}_guard{guarded}"
                folder = args.raw_dir / name
                folder.mkdir(parents=True, exist_ok=True)
                model = folder / "model.xml"
                if args.analyze_only:
                    with tempfile.TemporaryDirectory(dir=args.raw_dir) as scratch:
                        expected = Path(scratch) / "model.xml"
                        configure(expected, dx, args.ny, load, bool(guarded))
                        if expected.read_bytes() != model.read_bytes():
                            raise RuntimeError("Cached model differs from requested configuration.")
                    if "Time for " not in (folder / "solver.log").read_text(errors="replace"):
                        raise RuntimeError("Cached native run has no completion marker.")
                    print(f"Analyze completed native run: {name}", flush=True)
                else:
                    configure(model, dx, args.ny, load, bool(guarded))
                    print(f"Native current partition: {name}", flush=True)
                    run = subprocess.run(
                        [str(args.solver), str(model), "--engine=multithreaded", "--numThreads=2"],
                        cwd=folder, capture_output=True, timeout=180)
                    (folder / "solver.log").write_bytes(run.stdout + run.stderr)
                    if run.returncode:
                        raise RuntimeError(f"Native solver failed ({run.returncode}): {folder}")
                result, arrays = analyze(folder, dx, args.ny, load, bool(guarded))
                result["case"] = name
                result["raw_folder"] = str(folder)
                result["raw_manifest"] = [
                    {"name": p.name, "sha256": sha256(p)}
                    for p in sorted(folder.iterdir()) if p.is_file()
                ]
                np.savez_compressed(args.output / (name + ".npz"), **arrays)
                (args.output / (name + ".json")).write_text(
                    json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
                summaries.append(result)
                print(json.dumps({key: result[key] for key in (
                    "case", "current_partition_1percent_pass", "loop_checks")}), flush=True)
    (args.output / "summary.json").write_text(json.dumps({
        "case_count": len(summaries), "cases": summaries,
        "scope": "discrete air TEM current partition; not copper or a connector",
        "solver_sha256": sha256(args.solver), "script_sha256": sha256(Path(__file__)),
        "all_current_partition_pass": all(s["current_partition_1percent_pass"] for s in summaries),
        "full_joint_simulation_complete": False,
    }, indent=2, allow_nan=False), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, default=Path(tempfile.gettempdir()) / "ewc-openems/current-partition")
    parser.add_argument("--solver", type=Path, default=Path(tempfile.gettempdir()) / "ewc-openems/solver/openEMS/openEMS.exe")
    parser.add_argument("--spacing", type=float, nargs="+", default=[1, .5])
    parser.add_argument("--loads", type=float, nargs="+", default=[25, 50, 100])
    parser.add_argument("--guards", type=int, nargs="+", default=[0, 1])
    parser.add_argument("--ny", type=int, default=40)
    parser.add_argument("--analyze-only", action="store_true")
    main(parser.parse_args())
