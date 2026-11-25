"""Negative archive-integrity tests, separate from physical acceptance."""
import hashlib
import io
from pathlib import Path
import tarfile
import tempfile
import unittest

from capture_build_evidence import audit_archive
from verify_build_evidence import audit


class BuildEvidenceTests(unittest.TestCase):
    def package(self, files):
        directory = tempfile.TemporaryDirectory(prefix="ewc-source-integrity-test-")
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "source.tar.gz"
        with tarfile.open(path, "w:gz") as archive:
            for name, data in files:
                entry = tarfile.TarInfo("ngspice-fixed/" + name)
                entry.size = len(data)
                archive.addfile(entry, io.BytesIO(data))
        return path

    def tree(self, files):
        return {"tree": [
            {"type": "blob", "path": name,
             "sha": hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()}
            for name, data in files
        ]}

    def test_matching_source(self):
        files = [("COPYING", b"license"), ("src/test.c", b"code\n")]
        self.assertEqual(len(audit_archive(self.package(files), self.tree(files))), 2)

    def test_modified_blob_rejected(self):
        with self.assertRaisesRegex(AssertionError, "Git blob mismatch"):
            audit_archive(self.package([("a", b"changed")]), self.tree([("a", b"old")]))

    def test_missing_blob_rejected(self):
        with self.assertRaisesRegex(AssertionError, "Missing archive blobs"):
            audit_archive(self.package([("a", b"a")]), self.tree([("a", b"a"), ("b", b"b")]))

    def test_unexpected_blob_rejected(self):
        with self.assertRaisesRegex(AssertionError, "Unexpected archive file"):
            audit_archive(self.package([("a", b"a"), ("b", b"b")]), self.tree([("a", b"a")]))

    def test_duplicate_blob_rejected(self):
        with self.assertRaisesRegex(AssertionError, "Duplicate archive file"):
            audit_archive(self.package([("a", b"a"), ("a", b"a")]), self.tree([("a", b"a")]))

    def test_current_build_remains_uncompiled(self):
        result = audit()
        self.assertTrue(result["build_provenance_verified"])
        self.assertFalse(result["compiled_candidate"])
        self.assertFalse(result["candidate_solver_run"])
        self.assertFalse(result["full_power_passed"])


if __name__ == "__main__":
    unittest.main()
