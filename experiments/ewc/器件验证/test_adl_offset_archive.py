"""Exercise the actual dictionary archive contract and lossless recovery."""
from pathlib import Path
import tempfile
import unittest

from continue_adl_offset_holdout import recover_chunks
from run_c_saved_history64 import archive_rows, sha


class ArchiveContract(unittest.TestCase):
    def fixture(self, parent):
        wave, folder = parent / "wave.txt", parent / "archive"
        folder.mkdir()
        wave.write_text("time v(x)\n0 1\n1e-11 2\n2e-11 3\n", encoding="ascii")
        return wave, folder

    def test_actual_archive_returns_dictionary_not_tuple(self):
        with tempfile.TemporaryDirectory(prefix="ewc-offset-contract-") as temp:
            wave, folder = self.fixture(Path(temp))
            result = archive_rows(wave, folder, ["time", "x"])
            self.assertIsInstance(result, dict)
            self.assertEqual(result["rows"], 3)
            self.assertEqual(result["observed_last_time_s"], 2e-11)
            self.assertTrue(result["time_strictly_monotonic"])
            with self.assertRaises(ValueError):
                rows, chunks, error = result

    def test_recovery_does_not_change_raw_or_NPZ(self):
        with tempfile.TemporaryDirectory(prefix="ewc-offset-recovery-") as temp:
            wave, folder = self.fixture(Path(temp))
            expected = archive_rows(wave, folder, ["time", "x"])
            protected = {p: sha(p) for p in [wave] + list(folder.iterdir())}
            observed = recover_chunks(wave, folder, ["time", "x"])
            self.assertEqual(observed, expected)
            self.assertTrue(all(sha(p) == digest for p, digest in protected.items()))

    def test_raw_mismatch_rejected(self):
        with tempfile.TemporaryDirectory(prefix="ewc-offset-negative-") as temp:
            wave, folder = self.fixture(Path(temp))
            archive_rows(wave, folder, ["time", "x"])
            wave.write_text("time v(x)\n0 1\n1e-11 2\n2e-11 4\n", encoding="ascii")
            with self.assertRaises(AssertionError):
                recover_chunks(wave, folder, ["time", "x"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
