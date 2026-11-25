"""Read the documented inline zlib-compressed VTK XML arrays from openEMS.

No VTK/HDF5 native Python extension is needed. This is file decoding, not a
field solver. Unsupported formats are rejected instead of guessed.
"""

import base64
import math
from pathlib import Path
import zlib
import xml.etree.ElementTree as ET

import numpy as np


def decode_array(element, endian="<"):
    if element.attrib.get("format") != "binary":
        raise ValueError("Expected inline binary VTK array.")
    text = "".join(element.text.split())
    prefix = base64.b64decode(text[:8])
    blocks = int(np.frombuffer(prefix[:4], dtype=endian + "u4")[0])
    header_chars = 4 * math.ceil((3 + blocks) * 4 / 3)
    header = np.frombuffer(base64.b64decode(text[:header_chars]), dtype=endian + "u4")
    if len(header) != 3 + blocks:
        raise ValueError("Invalid VTK compression header.")
    compressed = base64.b64decode(text[header_chars:], validate=True)
    parts, offset = [], 0
    for i, size in enumerate(header[3:]):
        part = zlib.decompress(compressed[offset:offset + int(size)])
        expected = int(header[2] if i == blocks - 1 else header[1])
        if len(part) != expected:
            raise ValueError("VTK decompression size mismatch.")
        parts.append(part)
        offset += int(size)
    if offset != len(compressed):
        raise ValueError("Unexpected VTK trailing bytes.")
    types = {"Float32": "f4", "Float64": "f8", "Int32": "i4"}
    return np.frombuffer(b"".join(parts), dtype=endian + types[element.attrib["type"]])


def read_vtr(path):
    root = ET.parse(path).getroot()
    if root.attrib.get("type") != "RectilinearGrid":
        raise ValueError("Only rectilinear VTK is supported.")
    if root.attrib.get("compressor") != "vtkZLibDataCompressor":
        raise ValueError("Unknown VTK compressor.")
    if root.attrib.get("header_type") != "UInt32":
        raise ValueError("Unknown VTK compression header type.")
    endian = "<" if root.attrib["byte_order"] == "LittleEndian" else ">"
    piece = root.find("./RectilinearGrid/Piece")
    axes = [decode_array(item, endian) for item in piece.findall("./Coordinates/DataArray")]
    if len(axes) != 3 or any(len(a) < 1 for a in axes):
        raise ValueError("Invalid rectilinear axes.")
    vectors = piece.findall("./PointData/DataArray")
    if len(vectors) != 1 or vectors[0].attrib.get("NumberOfComponents") != "3":
        raise ValueError("Expected one three-component field.")
    field = decode_array(vectors[0], endian).reshape(
        (len(axes[2]), len(axes[1]), len(axes[0]), 3))
    if not np.all(np.isfinite(field)):
        raise ValueError("Nonfinite VTK field.")
    return axes, field


def read_complex_field(folder, name, frequency):
    label = str(int(frequency)) if frequency == int(frequency) else str(frequency)
    prefix = f"{name}_f={label}"
    first = Path(folder) / (prefix + "_p=000.vtr")
    if not first.exists():
        # Windows C++ long is 32-bit; frequencies above LONG_MAX use decimals.
        prefix = f"{name}_f={frequency:.6f}"
        first = Path(folder) / (prefix + "_p=000.vtr")
    second = Path(folder) / (prefix + "_p=085.vtr")
    check = Path(folder) / (prefix + "_p=171.vtr")
    axes, real = read_vtr(first)
    other_axes, phase5 = read_vtr(second)
    if any(not np.array_equal(a, b) for a, b in zip(axes, other_axes)):
        raise ValueError("Phase snapshot meshes differ.")
    angle5 = 2 * math.pi * 5 / 21
    imag = (real * math.cos(angle5) - phase5) / math.sin(angle5)
    field = real.astype(np.complex128) + 1j * imag
    _, phase10 = read_vtr(check)
    predicted = np.real(field * np.exp(2j * math.pi * 10 / 21))
    error = float(np.max(abs(predicted - phase10)) /
                  max(np.max(abs(field)), np.finfo(float).tiny))
    if error > 2e-6:
        raise ValueError(f"VTK phase reconstruction failed: {error:g}")
    return axes, field, error
