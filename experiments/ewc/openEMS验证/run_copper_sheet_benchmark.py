"""Audit the openEMS conducting-sheet model against its analytic sheet target.

Its two-sided thin-sheet law is NOT a one-sided thick-wall impedance boundary.
The material conductivity comes from the project's sourced C11000 candidate.
"""

import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import xml.etree.ElementTree as ET

import numpy as np

from run_native_port_benchmark import (
    C0, MU0, HEIGHT, WIDTH, LOAD_POSITION, box, build_model, sha256, transform,
)

EPS0 = 1 / (MU0 * C0**2)
SIGMA = 58.58e6
FREQ = np.linspace(2.5e9, 3.5e9, 101)


def sheet_reference(frequency, sigma, thickness):
    gamma = np.sqrt(1j * 2 * np.pi * frequency * MU0 * sigma)
    return gamma / (2 * sigma) / np.tanh(gamma * thickness / 2)


def model(path, dx, nz, thickness_um):
    build_model(path, dx, 50, 8, 20)
    root = ET.parse(path).getroot()
    grid = root.find("./ContinuousStructure/RectilinearGrid")
    grid.find("ZLines").text = ",".join(map(str, np.linspace(0, HEIGHT, nz + 1)))
    props = root.find("./ContinuousStructure/Properties")
    for item in list(props):
        if item.tag == "LumpedElement" or item.attrib.get("Name") in ("port_ut3", "port_it3"):
            props.remove(item)
    if thickness_um == 0:
        sheet = ET.SubElement(props, "Metal", Name="pec_sheet")
    else:
        sheet = ET.SubElement(
            props, "ConductingSheet", Name="C11000_sheet",
            Conductivity=str(SIGMA), Thickness=str(thickness_um * 1e-6),
        )
    box(sheet, [LOAD_POSITION, 0, 0], [LOAD_POSITION, WIDTH, HEIGHT])
    for name, kind, x in (
        ("sheet_E", 2, LOAD_POSITION),
        ("sheet_Hleft", 3, LOAD_POSITION),
        ("sheet_Hright", 3, LOAD_POSITION + dx),
    ):
        probe = ET.SubElement(props, "ProbeBox", Name=name, Type=str(kind))
        point = [x, WIDTH / 2, HEIGHT / 2]
        box(probe, point, point)
    ET.indent(root)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def spectrum(path, component):
    data = np.loadtxt(path, comments="%")
    return transform(data[:, [0, component]], FREQ)


def main(args):
    args.output.mkdir(parents=True, exist_ok=True)
    args.raw_dir.mkdir(parents=True, exist_ok=True)
    summaries = []
    for nz in args.nz:
        for dx in args.spacing:
            for thickness in args.thickness:
                name = f"dx{dx:g}_nz{nz}_t{thickness:g}um"
                folder = args.raw_dir / name
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / "model.xml"
                model(path, dx, nz, thickness)
                print(f"Native conducting sheet: {name}", flush=True)
                run = subprocess.run(
                    [str(args.solver), str(path), "--engine=multithreaded", "--numThreads=2"],
                    cwd=folder, capture_output=True, timeout=180)
                (folder / "solver.log").write_bytes(run.stdout + run.stderr)
                if run.returncode:
                    raise RuntimeError(f"Native sheet run failed ({run.returncode}).")
                log = (folder / "solver.log").read_text(errors="replace")
                if "too high" in log.lower() or "fallback" in log.lower():
                    raise RuntimeError(f"Sheet approximation warning: {folder}")
                electric = spectrum(folder / "sheet_E", 3)
                left = spectrum(folder / "sheet_Hleft", 2)
                right = spectrum(folder / "sheet_Hright", 2)
                total_surface = right - left
                displacement = 1j * 2 * np.pi * FREQ * EPS0 * (dx * 1e-3) * electric
                conduction = total_surface - displacement
                impedance = electric / conduction
                thick_wall = np.sqrt(1j * 2 * np.pi * FREQ * MU0 / SIGMA)
                if thickness:
                    expected = sheet_reference(FREQ, SIGMA, thickness * 1e-6)
                    sheet_error = float(np.max(abs(impedance / expected - 1)))
                    real_error = float(np.max(abs(np.real(impedance) / np.real(expected) - 1)))
                    thick_error = float(np.max(abs(impedance / thick_wall - 1)))
                else:
                    expected = np.zeros_like(impedance)
                    sheet_error, real_error, thick_error = None, None, None
                result = {
                    "case": name, "dx_mm": dx, "nz": nz, "thickness_um": thickness,
                    "sigma_S_per_m": SIGMA, "temperature_assumption_C": 20,
                    "sheet_target_max_relative_complex_error": sheet_error,
                    "sheet_target_max_relative_real_error": real_error,
                    "one_sided_thick_wall_max_relative_complex_error": thick_error,
                    "sheet_model_1percent_pass": bool(sheet_error is not None and sheet_error < .01),
                    "thick_cavity_wall_pass": False,
                    "pec_impedance_max_ohm": float(np.max(abs(impedance))) if not thickness else None,
                    "surface_power_sign_nonnegative": bool(np.all(np.real(impedance) >= -1e-10)),
                    "Z_at_3GHz_ohm": [float(impedance[50].real), float(impedance[50].imag)],
                    "skin_depth_3GHz_um": float(np.sqrt(2 / (2 * np.pi * 3e9 * MU0 * SIGMA)) * 1e6),
                    "raw_folder": str(folder),
                    "raw_manifest": [{"name": p.name, "sha256": sha256(p)}
                                     for p in sorted(folder.iterdir()) if p.is_file()],
                }
                summaries.append(result)
                np.savez_compressed(
                    args.output / (name + ".npz"), frequency_Hz=FREQ,
                    E_z=electric, H_y_left=left, H_y_right=right,
                    impedance=impedance, sheet_reference=expected, thick_wall_reference=thick_wall)
                (args.output / (name + ".json")).write_text(
                    json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
                print(json.dumps({k: v for k, v in result.items() if k != "raw_manifest"}), flush=True)
    (args.output / "summary.json").write_text(json.dumps({
        "case_count": len(summaries), "cases": summaries,
        "scope": "two-sided conducting-sheet model, not a thick copper cavity",
        "script_sha256": sha256(Path(__file__)), "solver_sha256": sha256(args.solver),
        "full_joint_simulation_complete": False,
    }, indent=2, allow_nan=False), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, default=Path(tempfile.gettempdir()) / "ewc-openems/copper-sheet")
    parser.add_argument("--solver", type=Path, default=Path(tempfile.gettempdir()) / "ewc-openems/solver/openEMS/openEMS.exe")
    parser.add_argument("--spacing", type=float, nargs="+", default=[1, .5])
    parser.add_argument("--nz", type=int, nargs="+", default=[8, 16])
    parser.add_argument("--thickness", type=float, nargs="+", default=[0, 2, 35])
    main(parser.parse_args())
