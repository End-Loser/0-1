"""Archive selection evidence and check analytical candidate constraints.

This script does not simulate the electromagnetic cavity or a full RF device.
Manufacturer snapshots and original archives remain separate from calculations.
"""

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import shutil
import tempfile
import zipfile


SOURCES = {
    "copper": "https://alloys.copper.org/alloy/C11000",
    "laminate": "https://www.rogerscorp.com/advanced-electronics-solutions/"
                "rt-duroid-laminates/rt-duroid-5880-laminates",
    "lo": "https://www.analog.com/en/products/adf4351.html",
    "tx": "https://www.analog.com/en/products/adl5375.html",
    "rx": "https://www.analog.com/en/products/adl5380.html",
    "comparator": "https://www.ti.com/product/TLV3501",
    "comparator_timing": "https://www.ti.com/document-viewer/TLV3501/"
                         "datasheet/specifications#SBOS3215427",
    "adc": "https://www.ti.com/product/ADS8866",
    "dac": "https://www.ti.com/product/DAC80501",
    "coarse_gain": "https://www.analog.com/en/products/adl5335.html",
    "ti_archive": "https://www.ti.com/lit/zip/SLAC576",
    "ports": "https://docs.openems.de/python/openEMS/ports.html",
    "sheet": "https://openems.readthedocs.io/en/latest/python/CSXCAD/"
             "CSProperties/CSPropConductingSheet.html",
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def calculations():
    # Smooth, nonmagnetic, homogeneous bulk-metal approximation at 20 C.
    mu0 = 4 * math.pi * 1e-7
    frequency = 3e9
    sigma = 58e6 * 1.01
    skin = math.sqrt(2 / (2 * math.pi * frequency * mu0 * sigma))
    resistance = math.sqrt(math.pi * frequency * mu0 / sigma)
    return {
        "method": "analytical screening; not FDTD or a device simulation",
        "copper": {
            "source": SOURCES["copper"],
            "IACS_reference_S_per_m": 58e6,
            "conductivity_typical_101pct_IACS_S_per_m": sigma,
            "temperature_C": 20,
            "frequency_Hz": frequency,
            "skin_depth_m": skin,
            "smooth_thick_surface_resistance_ohm": resistance,
            "five_mm_grid_to_skin_depth_ratio": .005 / skin,
            "exclusions": ["surface roughness", "seams", "plating",
                           "temperature drift", "manufacturing variation"],
        },
        "gain_resolution": {
            "source": SOURCES["coarse_gain"],
            "step_dB_typical": .5,
            "nearest_code_half_step_dB": .25,
            "positive_half_step_relative_amplitude_error": 10**(.25 / 20)-1,
            "proxy_effective_coefficient_budget": .01,
            "sole_fine_trim_adequate": False,
            "condition": "arbitrary desired gain with nearest-code rounding; "
                         "actual gain error and drift excluded",
        },
        "calibration": {
            "source": SOURCES["adc"],
            "ADC_max_samples_per_second": 100e3,
            "assumed_conversions": 8,
            "minimum_sampling_sequence_s": 8 / 100e3,
            "previous_ideal_training_s": 640e-9,
            "minimum_sequence_to_previous_training_ratio": (8 / 100e3) / 640e-9,
            "condition": "eight sequential conversions at maximum throughput; "
                         "does not include startup, settling, averaging or SPI",
        },
        "dac": {
            "source": SOURCES["dac"],
            "assumed_full_scale_V": 2.5,
            "bits": 16,
            "ideal_LSB_V": 2.5 / 65536,
            "settling_time_product_page_typical_s": 5e-6,
            "condition": "ideal quantization only; not actual gain precision",
        },
    }


def main(args):
    output = args.output.resolve()
    evidence = output / "source_snapshots"
    evidence.mkdir(parents=True, exist_ok=True)
    records = []
    assignments = args.snapshot or [
        f"{path.stem}={path}" for path in sorted(evidence.glob("*.md"))
        if path.stem in SOURCES
    ]
    if not assignments:
        raise ValueError("Supply source snapshots before producing an evidence audit.")
    for assignment in assignments:
        key, filename = assignment.split("=", 1)
        if key not in SOURCES:
            raise ValueError(f"Unknown source key: {key}")
        original = Path(filename)
        if not original.is_file():
            raise FileNotFoundError(original)
        destination = evidence / f"{key}.md"
        if original.resolve() != destination.resolve():
            shutil.copyfile(original, destination)
        records.append({
            "id": key, "url": SOURCES[key],
            "snapshot": str(destination.relative_to(output)),
            "sha256": digest(destination),
            "format": "web-fetch markdown; not original manufacturer PDF",
        })
    archive = args.ti_archive.resolve()
    with zipfile.ZipFile(archive) as bundle:
        entries = [{"name": i.filename, "bytes": i.file_size}
                   for i in bundle.infolist() if not i.is_dir()]
    readable_spice = [
        row["name"] for row in entries
        if Path(row["name"]).suffix.lower() in
        {".cir", ".lib", ".sub", ".sp", ".spice"}
    ]
    checked_archive = {
        "url": SOURCES["ti_archive"],
        "sha256": digest(archive),
        "files": entries,
        "readable_spice_extension_files": readable_spice,
        "ngspice_compatibility": "not established; TINA project files are present",
        "license": "original archive includes reference-design notice; "
                   "kept in external cache, not redistributed",
    }
    package = Path(tempfile.gettempdir()) / "ewc-openems" / "fdtd-package" / "fdtd"
    source_inventory = {}
    for name in ("objects.py", "sources.py", "detectors.py", "__init__.py"):
        path = package / name
        if path.is_file():
            source_inventory[name] = {"sha256": digest(path)}
    result = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "script_sha256": digest(Path(__file__)),
        "scope": "candidate evidence and analytical constraints only",
        "sources": SOURCES,
        "snapshots": records,
        "ti_reference_archive": checked_archive,
        "python_default_import_scope": {
            key: importlib.util.find_spec(key) is not None
            for key in ("openEMS", "CSXCAD", "numpy")
        },
        "fdtd_cached_source_inventory": source_inventory,
        "calculations": calculations(),
        "stage_A_complete": False,
        "full_joint_simulation_complete": False,
    }
    checks = result["calculations"]
    assert 1e-6 < checks["copper"]["skin_depth_m"] < 1.3e-6
    assert checks["gain_resolution"]["positive_half_step_relative_amplitude_error"] > .01
    assert math.isclose(
        checks["calibration"]["minimum_sequence_to_previous_training_ratio"], 125)
    assert math.isclose(checks["dac"]["ideal_LSB_V"], 38.14697265625e-6)
    assert entries and not readable_spice
    with (output / "candidate_audit.json").open("w", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    with (output / "source_manifest.csv").open(
            "w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("id", "url", "snapshot", "sha256"))
        writer.writeheader()
        writer.writerows({key: item[key] for key in writer.fieldnames}
                         for item in records)
    print("Candidate arithmetic and archive-format checks: PASS")
    print(f"Archived source snapshots: {len(records)}")
    print("Full RF/electromagnetic joint simulation: NOT RUN")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).parent / "真实器件选型结果")
    parser.add_argument("--ti-archive", type=Path,
                        default=Path(tempfile.gettempdir()) / "ewc-openems" /
                        "TIPD105_SLAC576.zip")
    parser.add_argument("--snapshot", action="append", default=[])
    main(parser.parse_args())
