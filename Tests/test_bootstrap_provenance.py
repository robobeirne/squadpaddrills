"""Native-package inventory logic only; synthetic bytes never establish UE execution."""

import hashlib
from pathlib import Path
import sys
import tempfile
import unittest


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "Content/Python"))
from squadpad_bootstrap import EditorBuildError, _imported_native_files


class BootstrapProvenanceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="squadpad_native_inventory_")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.packages = {
            "/Game/Probe/Athlete": b"synthetic mesh package; not a native asset",
            "/Game/Probe/Pass": b"synthetic animation package; not a native asset",
            "/Game/Probe/Receive": b"different synthetic animation package",
            "/Game/Probe/AthleteSkeleton": b"synthetic shared Skeleton package",
        }
        for package, data in self.packages.items():
            path = self.package_file(package)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        skeleton = "/Game/Probe/AthleteSkeleton.AthleteSkeleton"
        self.imports = [
            {"kind": "SkeletalMesh", "path": "/Game/Probe/Athlete.Athlete", "skeleton": skeleton},
            {"kind": "AnimSequence", "path": "/Game/Probe/Pass.Pass", "skeleton": skeleton},
            {"kind": "AnimSequence", "path": "/Game/Probe/Receive.Receive", "skeleton": skeleton},
        ]

    def package_file(self, package):
        return self.root / "Content" / (package[len("/Game/"):] + ".uasset")

    def test_inventory_covers_all_imports_and_deduplicates_the_shared_skeleton(self):
        records = _imported_native_files(self.root, self.imports)
        self.assertEqual([record["package"] for record in records], sorted(self.packages))
        for record in records:
            package = record["package"]
            self.assertEqual(set(record), {"package", "file", "sha256", "size_bytes"})
            self.assertEqual(record["file"], str(self.package_file(package)))
            self.assertEqual(record["sha256"], hashlib.sha256(self.packages[package]).hexdigest())
            self.assertEqual(record["size_bytes"], len(self.packages[package]))

    def test_missing_saved_skeleton_prevents_a_successful_inventory(self):
        self.package_file("/Game/Probe/AthleteSkeleton").unlink()
        with self.assertRaisesRegex(EditorBuildError, "Native package was not written to disk"):
            _imported_native_files(self.root, self.imports)

    def test_inventory_records_actual_saved_bytes(self):
        package = "/Game/Probe/Pass"
        altered = b"new saved animation bytes"
        self.package_file(package).write_bytes(altered)
        records = {record["package"]: record for record in _imported_native_files(self.root, self.imports)}
        self.assertEqual(records[package]["sha256"], hashlib.sha256(altered).hexdigest())
        self.assertEqual(records[package]["size_bytes"], len(altered))

    def test_missing_import_reference_cannot_silently_reduce_coverage(self):
        del self.imports[1]["skeleton"]
        with self.assertRaisesRegex(EditorBuildError, "lacks its skeleton package reference"):
            _imported_native_files(self.root, self.imports)


if __name__ == "__main__":
    unittest.main(verbosity=2)
