"""Native openEMS CLI benchmark; no Python extension or custom field solver.

The PMC sidewalls enforce a uniform TEM mode, not a manufacturable connector.
All voltages/currents are solver probes. Analytic answers are used only in
post-processing. Raw solver files stay in the external cache.
"""

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import subprocess
import tempfile
import xml.etree.ElementTree as ET

import numpy as np

C0 = 299792458.0
MU0 = 4 * math.pi * 1e-7
ETA0 = MU0 * C0
HEIGHT = 10.0
WIDTH = ETA0 * HEIGHT / 50
LENGTH = 240.0
MEASURE1 = 60.0
MEASURE2 = 180.0
LOAD_POSITION = 150.0
FREQUENCIES = np.linspace(2.5e9, 3.5e9, 101)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def box(prop, start, stop, priority=10):
    primitives = ET.SubElement(prop, "Primitives")
    item = ET.SubElement(primitives, "Box", Priority=str(priority))
    for label, point in (("P1", start), ("P2", stop)):
        ET.SubElement(item, label, **dict(zip("XYZ", map(str, point))))


def build_model(path, dx, load, duration_ns, transverse_cells):
    root = ET.Element("openEMS")
    fdtd = ET.SubElement(
        root, "FDTD", NumberOfTimesteps="100000", endCriteria="0",
        MaxTime=str(duration_ns * 1e-9), OverSampling="20", f_max="4500000000",
        TimeStepMethod="1",
    )
    ET.SubElement(fdtd, "Excitation", Type="0", f0="3000000000", fc="1500000000")
    ET.SubElement(
        fdtd, "BoundaryCond", xmin="MUR", xmax="MUR",
        ymin="PMC", ymax="PMC", zmin="PEC", zmax="PEC",
    )
    csx = ET.SubElement(root, "ContinuousStructure", CoordSystem="0")
    grid = ET.SubElement(csx, "RectilinearGrid", DeltaUnit="0.001")
    for axis, values in (
        ("X", np.linspace(0, LENGTH, round(LENGTH / dx) + 1)),
        ("Y", np.linspace(0, WIDTH, transverse_cells + 1)),
        ("Z", np.linspace(0, HEIGHT, 5)),
    ):
        ET.SubElement(grid, axis + "Lines").text = ",".join(map(str, values))
    props = ET.SubElement(csx, "Properties")
    excitation = ET.SubElement(
        props, "Excitation", Name="source", Type="0", Excite="0,0,-1",
    )
    box(excitation, [20, 0, 0], [20, WIDTH, HEIGHT])
    for number, x in ((1, MEASURE1), (2, MEASURE2)):
        voltage = ET.SubElement(
            props, "ProbeBox", Name=f"port_ut{number}", Type="0", Weight="-1",
        )
        box(voltage, [x, WIDTH / 2, 0], [x, WIDTH / 2, HEIGHT])
        # Two adjacent native H samples bracket the E-voltage reference plane.
        # The native dual-grid probe snaps an exact grid line to its lower cell.
        for side, position in (("a", x), ("b", x + dx)):
            magnetic = ET.SubElement(
                props, "ProbeBox", Name=f"port_it{number}{side}",
                Type="3", Weight="1",
            )
            point = [position, WIDTH / 2, HEIGHT / 2]
            box(magnetic, point, point)
    for number, x, resistance in ((3, LOAD_POSITION, load),):
        start, stop = [x - dx, 0, 0], [x + dx, WIDTH, HEIGHT]
        resistor = ET.SubElement(
            props, "LumpedElement", Name=f"port_resist_{number}",
            Direction="2", Caps="1", R=str(resistance),
        )
        box(resistor, start, stop)
        voltage = ET.SubElement(
            props, "ProbeBox", Name=f"port_ut{number}", Type="0", Weight="-1",
        )
        box(voltage, [x, WIDTH / 2, 0], [x, WIDTH / 2, HEIGHT])
        current = ET.SubElement(
            props, "ProbeBox", Name=f"port_it{number}",
            Type="1", Weight="-1", NormDir="2",
        )
        box(current, [x - 2 * dx, 0, HEIGHT / 2], [x + 2 * dx, WIDTH, HEIGHT / 2])
    ET.indent(root)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def transform(data, frequencies):
    # Transform each probe on its own timestamps: E/H are staggered in time.
    t, value = data[:, 0], data[:, 1]
    return 2 * np.trapz(
        value[:, None] * np.exp(-2j * np.pi * t[:, None] * frequencies),
        t, axis=0,
    )


def analyze(folder):
    probes = {}
    for number in (1, 2, 3):
        for kind in ("u", "i"):
            names = [f"port_{kind}t{number}"]
            if kind == "i" and number < 3:
                names = [name + side for name in names for side in ("a", "b")]
            samples = [np.loadtxt(folder / name, comments="%") for name in names]
            if len(samples) == 2:
                # Native field probes give t,Hx,Hy,Hz. I=width*Hy for uniform TEM.
                if not np.array_equal(samples[0][:, 0], samples[1][:, 0]):
                    raise RuntimeError("Magnetic probe timestamps differ.")
                data = np.column_stack((
                    samples[0][:, 0],
                    .5 * (samples[0][:, 2] + samples[1][:, 2]) * WIDTH * 1e-3,
                ))
            else:
                data = samples[0]
            if len(data) < 100 or not np.all(np.isfinite(data)):
                raise RuntimeError("Invalid solver probe data.")
            probes[f"{kind}{number}"] = data
    u1, i1, u2, i2, ul, il = [
        transform(probes[name], FREQUENCIES)
        for name in ("u1", "i1", "u2", "i2", "u3", "i3")
    ]
    a1 = (u1 + 50 * i1) / 2
    b1 = (u1 - 50 * i1) / 2
    outgoing2 = (u2 + 50 * i2) / 2
    if np.min(np.abs(a1)) < 1e-15:
        raise RuntimeError("Incident spectrum is too weak.")
    return {
        "s11": b1 / a1, "s21": outgoing2 / a1,
        "load_z": ul / il,
        "load_power_fraction": .5 * np.real(ul * np.conj(il)) / (abs(a1)**2 / 100),
        "power_identity_error": float(np.max(np.abs(
            (abs(a1)**2 - abs(b1)**2) / 100 -
            .5 * np.real(u1 * np.conj(i1))
        )) / np.max(abs(a1)**2 / 100)),
        "probes": probes,
    }


def run(args):
    solver = args.solver.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    args.raw_dir.mkdir(parents=True, exist_ok=True)
    help_run = subprocess.run(
        [str(solver), "--help"], cwd=solver.parent,
        capture_output=True, timeout=30,
    )
    if help_run.returncode:
        raise RuntimeError(f"Native solver help failed: {help_run.returncode}")
    (args.raw_dir / "solver_help.log").write_bytes(help_run.stdout + help_run.stderr)
    rows, cases, raw_manifest = [], {}, []
    for dx in args.spacing:
        for load in args.loads:
            name = f"dx{dx:g}_ny{args.transverse_cells}_load{load:g}_t{args.duration_ns:g}"
            folder = args.raw_dir / name
            folder.mkdir(parents=True, exist_ok=True)
            model = folder / "model.xml"
            if args.analyze_only:
                with tempfile.TemporaryDirectory(dir=args.raw_dir) as scratch:
                    expected = Path(scratch) / "expected.xml"
                    build_model(expected, dx, load, args.duration_ns, args.transverse_cells)
                    if model.read_bytes() != expected.read_bytes():
                        raise RuntimeError("Cached model differs from requested configuration.")
                log = (folder / "solver.log").read_text(errors="replace")
                if "Time for " not in log:
                    raise RuntimeError("Cached solver run has no completion marker.")
                print(f"Analyze completed native run: {name}", flush=True)
            else:
                build_model(model, dx, load, args.duration_ns, args.transverse_cells)
                print(f"openEMS CLI: {name}", flush=True)
                process = subprocess.run(
                    [str(solver), str(model), "--engine=multithreaded",
                     "--numThreads=2", "--dump-statistics"],
                    cwd=folder, capture_output=True, timeout=180,
                )
                (folder / "solver.log").write_bytes(process.stdout + process.stderr)
                if process.returncode:
                    raise RuntimeError(
                        f"{name} failed ({process.returncode}); see {folder / 'solver.log'}")
            result = analyze(folder)
            beta = 2 * np.pi * FREQUENCIES / C0
            gamma = -50 / (2 * load + 50)
            expected11 = gamma * np.exp(
                -2j * beta * (LOAD_POSITION - MEASURE1) * 1e-3)
            expected21 = (1 + gamma) * np.exp(
                -1j * beta * (MEASURE2 - MEASURE1) * 1e-3)
            balance_error = float(np.max(abs(
                1 - abs(result["s11"])**2 - abs(result["s21"])**2 -
                result["load_power_fraction"])))
            summary = {
                "case": name, "spacing_x_mm": dx, "load_ohm": load,
                "s11_max_complex_error": float(np.max(abs(result["s11"] - expected11))),
                "s21_max_complex_error": float(np.max(abs(result["s21"] - expected21))),
                "load_z_max_relative_error": float(np.max(abs(result["load_z"] / load - 1))),
                "power_identity_error": result["power_identity_error"],
                "energy_balance_max_error": balance_error,
                "raw_folder": str(folder),
            }
            rows.append(summary)
            cases[(dx, load)] = result
            np.savez_compressed(
                args.output / (name + ".npz"), frequency_Hz=FREQUENCIES,
                s11=result["s11"], s21=result["s21"], load_z=result["load_z"],
                load_power_fraction=result["load_power_fraction"],
                expected_s11=expected11, expected_s21=expected21,
                **result["probes"],
            )
            for filename in ("model.xml", "solver.log", "port_ut1", "port_it1a",
                             "port_it1b", "port_ut2", "port_it2a", "port_it2b",
                             "port_ut3", "port_it3"):
                item = folder / filename
                raw_manifest.append({"path": str(item), "sha256": sha256(item)})
            print(json.dumps(summary), flush=True)
    with (args.output / "cases.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    changes = []
    for coarse, fine in zip(args.spacing, args.spacing[1:]):
        for load in args.loads:
            a, b = cases[(coarse, load)], cases[(fine, load)]
            changes.append({
                "coarse_mm": coarse, "fine_mm": fine, "load_ohm": load,
                "s11_max_change": float(np.max(abs(a["s11"] - b["s11"]))),
                "s21_max_change": float(np.max(abs(a["s21"] - b["s21"]))),
            })
    final_rows = [r for r in rows if r["spacing_x_mm"] == args.spacing[-1]]
    transmission_passed = all(
        r["s11_max_complex_error"] < .01 and r["s21_max_complex_error"] < .01
        and r["energy_balance_max_error"] < .01 for r in final_rows
    )
    passed = all(
        r["s11_max_complex_error"] < .01 and r["s21_max_complex_error"] < .01
        and r["load_z_max_relative_error"] < .01
        and r["energy_balance_max_error"] < .01 for r in final_rows
    )
    summary = {
        "solver": str(solver), "solver_sha256": sha256(solver),
        "native_help_returncode": help_run.returncode,
        "analysis_of_existing_runs": args.analyze_only,
        "benchmark": "uniform air TEM line, native voltage/H probes, lumped shunt load",
        "geometry_mm": {"length": LENGTH, "width": WIDTH, "height": HEIGHT},
        "reference_impedance_ohm": 50,
        "measurement_planes_mm": [MEASURE1, MEASURE2],
        "shunt_position_mm": LOAD_POSITION,
        "band_Hz": [float(FREQUENCIES[0]), float(FREQUENCIES[-1])],
        "duration_ns": args.duration_ns, "transverse_cells": [args.transverse_cells, 4],
        "cases": rows, "axial_refinement": changes, "raw_manifest": raw_manifest,
        "finest_analytic_1percent_pass": passed,
        "finest_transmission_and_power_1percent_pass": transmission_passed,
        "full_joint_simulation_complete": False,
        "limitations": [
            "PEC plates; copper surface loss is not implemented.",
            "PMC symmetry gives a uniform TEM mode; no SMA/probe connector.",
            "50 ohm is the TEM reference; the lumped resistor is a shunt, not an end termination.",
            "Transverse grid is fixed; refinement addresses this axial TEM benchmark only.",
            "TEM current uses the uniform-mode integral width*Hy; not a general connector current.",
            "Native load loop measures conduction plus displacement current; pure-R comparison is diagnostic.",
            "Power identity is an algebraic sanity check, not independent energy closure.",
            "No physical RF chipset, material tolerances or arithmetic feedback loop.",
        ],
    }
    (args.output / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Finest-grid analytic comparison: {'PASS' if passed else 'FAIL'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--solver", type=Path, default=Path(tempfile.gettempdir()) /
                        "ewc-openems/solver/openEMS/openEMS.exe")
    parser.add_argument("--raw-dir", type=Path, default=Path(tempfile.gettempdir()) /
                        "ewc-openems/native-port")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--spacing", type=float, nargs="+", default=[2, 1, .5])
    parser.add_argument("--loads", type=float, nargs="+", default=[50, 100, 25])
    parser.add_argument("--duration-ns", type=float, default=8)
    parser.add_argument("--transverse-cells", type=int, default=40)
    parser.add_argument("--analyze-only", action="store_true")
    run(parser.parse_args())
