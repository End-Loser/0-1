"""Small offline tests for field decoding and analytic reference limits."""

import base64
import math
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zlib

import numpy as np

from native_field_io import decode_array, read_complex_field
from run_copper_sheet_benchmark import MU0, SIGMA, sheet_reference


def compressed_array(values):
    raw = np.asarray(values, dtype="<f4").tobytes()
    chunks = [raw[i:i + 32768] for i in range(0, len(raw), 32768)]
    blocks = [zlib.compress(item) for item in chunks]
    header = np.array([len(chunks), 32768, len(chunks[-1])] +
                      [len(item) for item in blocks], dtype="<u4").tobytes()
    return base64.b64encode(header).decode() + base64.b64encode(b"".join(blocks)).decode()


class NativeMaterialTests(unittest.TestCase):
    def test_multiblock_vtk_decode(self):
        data = np.linspace(-2, 5, 30000, dtype=np.float32)
        array = ET.Element("DataArray", type="Float32", format="binary")
        array.text = compressed_array(data)
        np.testing.assert_array_equal(decode_array(array), data)

    def test_unsupported_array_rejected(self):
        array = ET.Element("DataArray", type="Float32", format="ascii")
        with self.assertRaises(ValueError):
            decode_array(array)

    def test_phase_reconstruction(self):
        expected = np.arange(18).reshape(1, 2, 3, 3) * (1 + .3j)
        with tempfile.TemporaryDirectory() as folder:
            for phase, label in ((0, "000"), (5, "085"), (10, "171")):
                root = ET.Element(
                    "VTKFile", type="RectilinearGrid", header_type="UInt32",
                    byte_order="LittleEndian", compressor="vtkZLibDataCompressor")
                grid = ET.SubElement(root, "RectilinearGrid")
                piece = ET.SubElement(grid, "Piece")
                point = ET.SubElement(piece, "PointData")
                vector = ET.SubElement(
                    point, "DataArray", type="Float32", format="binary", NumberOfComponents="3")
                vector.text = compressed_array(np.real(expected * np.exp(2j * math.pi * phase / 21)))
                coords = ET.SubElement(piece, "Coordinates")
                for data in ([0, 1, 2], [0, 1], [0]):
                    item = ET.SubElement(coords, "DataArray", type="Float32", format="binary")
                    item.text = compressed_array(data)
                ET.ElementTree(root).write(
                    Path(folder) / f"ef_f=3000000000.000000_p={label}.vtr")
            _, observed, error = read_complex_field(folder, "ef", 3e9)
            np.testing.assert_allclose(observed, expected, rtol=2e-7, atol=1e-7)
            self.assertLess(error, 2e-7)

    def test_sheet_dc_limit(self):
        d = 2e-6
        np.testing.assert_allclose(sheet_reference(np.array([1e-6]), SIGMA, d),
                                   1 / (SIGMA * d), rtol=1e-9)

    def test_thick_sheet_is_not_thick_wall(self):
        f, d = np.array([3e9]), 35e-6
        wall = np.sqrt(2j * np.pi * f * MU0 / SIGMA)
        np.testing.assert_allclose(sheet_reference(f, SIGMA, d), wall / 2, rtol=1e-10)

    def test_sheet_passivity(self):
        for d in (2e-6, 35e-6):
            self.assertTrue(np.all(np.real(sheet_reference(
                np.linspace(2.5e9, 3.5e9, 101), SIGMA, d)) > 0))


if __name__ == "__main__":
    unittest.main()
