"""Offline runtime-probe tests with explicitly synthetic native receipts/packages.

The real source exports are copied into an isolated temporary project. Files
ending .uasset or .umap in that temporary project contain labelled test text.
Passing these tests establishes planner rejection/consistency only, not Unreal.
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "Tools/Unreal"))
from runtime_probe_plan import (RuntimeProbeError, plan_runtime_probe, validate_bootstrap_receipt, verify_pose_response)
from squadpad_plan import plan_import, sha256_file


class RuntimeProbePlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="squadpad-runtime-probe-test-")
        cls.root = Path(cls.temporary.name)
        for folder in ("SourceInputs", "SourceData", "Fixtures"):
            shutil.copytree(PROJECT / folder, cls.root / folder)
        shutil.copy2(PROJECT / "import_manifest.json", cls.root / "import_manifest.json")
        cls.source = plan_import(cls.root / "import_manifest.json", cls.root, require_files=True)
        cls.namespace = "/Game/SquadPad/Bootstrap/SyntheticTest"
        cls.receipt_path = cls.root / "synthetic-bootstrap-receipt.json"
        cls.receipt = cls._make_receipt()
        cls.receipt_path.write_text(json.dumps(cls.receipt), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    @classmethod
    def _fake_package(cls, package, extension):
        path = cls.root / "Content" / (package[len("/Game/"):] + extension)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(("NOT AN UNREAL ASSET. Synthetic runtime-probe test fixture: " + package).encode())
        return {"package": package, "file": str(path), "sha256": sha256_file(path), "size_bytes": path.stat().st_size}

    @classmethod
    def _make_receipt(cls):
        plan = cls.source
        imports = []
        imported_files = []
        skeletons = {}
        for ident, mesh in plan["meshes"].items():
            package = cls.namespace + "/Meshes/" + mesh["name"] + "/" + mesh["name"]
            skeleton_package = package + "_Skeleton"
            path = package + "." + mesh["name"]
            skeleton = skeleton_package + "." + mesh["name"] + "_Skeleton"
            skeletons[ident] = skeleton
            imported_files.append(cls._fake_package(package, ".uasset"))
            imported_files.append(cls._fake_package(skeleton_package, ".uasset"))
            imports.append({"kind": "SkeletalMesh", "id": ident, "path": path, "skeleton": skeleton,
                            "source_sha256": mesh["sha256"]})
        for ident, motion in plan["animations"].items():
            package = cls.namespace + "/Animations/" + motion["name"] + "/" + motion["name"]
            imported_files.append(cls._fake_package(package, ".uasset"))
            imports.append({"kind": "AnimSequence", "id": ident, "path": package + "." + motion["name"],
                            "skeleton": skeletons[motion["mesh_id"]], "source_sha256": motion["sha256"],
                            "duration_seconds": motion["duration_seconds"], "bone_track_count": 52})
        map_path, sequence = cls.namespace + "/L_FirstPass", cls.namespace + "/LS_FirstPass"
        return {"schema": "squadpad.unreal.bootstrap-receipt.v1", "status": "native_import_save_readback_passed",
                "test_scope": "Synthetic receipt and non-Unreal package bytes. Never native execution evidence.",
                "unreal_execution_verified": True, "readback": {"status": "passed"},
                "engine_version": "5.8.0-SYNTHETIC-TEST", "asset_namespace": cls.namespace,
                "stages": [{"stage": x, "status": "passed"} for x in
                           ("input_validation", "editor_environment", "fbx_import", "saved_native_packages", "reopened_native_readback")],
                "input": {x: copy.deepcopy(plan[x]) for x in
                          ("manifest_sha256", "fixture_sha256", "fixture_id", "export_evidence", "sources")},
                "scene": {"map_path": map_path, "sequence_path": sequence + ".LS_FirstPass"},
                "native_files": [cls._fake_package(map_path, ".umap"), cls._fake_package(sequence, ".uasset")],
                "imports": imports, "imported_native_files": imported_files}

    def changed(self):
        return copy.deepcopy(self.receipt)

    def test_valid_metadata_plans_actual_first_pass_without_native_claim(self):
        result = plan_runtime_probe(self.receipt_path, self.root)
        self.assertEqual(result["status"], "inputs_ready_for_native_probe")
        self.assertFalse(result["native_execution_this_call"])
        self.assertFalse(result["cooked_playback_verified"])
        self.assertFalse(result["ios_verified"])
        self.assertEqual(len(result["profiles"]), 1)
        self.assertEqual(len(result["appearance_bindings"]), 2)
        self.assertEqual({x["source_motion_id"] for x in result["profiles"][0]["motions"]},
                         {"canonical.idle", "canonical.pass", "canonical.receive"})
        times = {x["time_seconds"] for x in result["sample_frames"]}
        self.assertIn(1.39262125, times)
        self.assertIn(2.43762125, times)
        self.assertEqual(result["pose_response_requirements"], {"A": ["canonical.pass"], "B": ["canonical.receive"]})

    def test_missing_bootstrap_receipt_is_a_blocker(self):
        with self.assertRaisesRegex(RuntimeProbeError, "Successful native bootstrap receipt is required"):
            plan_runtime_probe(self.root / "missing.json", self.root)

    def test_duplicate_json_members_and_nonfinite_metadata_fail_before_preparation(self):
        playback = (self.root / "Fixtures/Playback/first_pass.playback.json").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory(prefix="squadpad-malformed-json-test-") as directory:
            path = Path(directory) / "malformed.playback.json"
            for prefix, message in (("{\"schema\":\"duplicate\",", "Duplicate JSON member"),
                                    ("{\"unexpected_metadata\":{\"infinite\":1e999},", "non-finite number")):
                path.write_text(prefix + playback.lstrip()[1:], encoding="utf-8")
                with self.subTest(message=message), self.assertRaisesRegex(RuntimeProbeError, message):
                    plan_runtime_probe(self.receipt_path, self.root, playback_path=path)

    def test_failed_or_unexecuted_bootstrap_is_rejected(self):
        for key, value in (("status", "started"), ("unreal_execution_verified", False), ("schema", "other"),
                           ("readback", None), ("readback", [])):
            with self.subTest(key=key):
                receipt = self.changed()
                receipt[key] = value
                with self.assertRaises(RuntimeProbeError):
                    validate_bootstrap_receipt(receipt, self.source, self.root)

    def test_missing_or_duplicate_success_stage_is_rejected(self):
        for duplicate in (False, True):
            receipt = self.changed()
            if duplicate:
                receipt["stages"].append(copy.deepcopy(receipt["stages"][0]))
            else:
                receipt["stages"].pop()
            with self.assertRaises(RuntimeProbeError):
                validate_bootstrap_receipt(receipt, self.source, self.root)

    def test_stale_manifest_fixture_or_export_is_rejected(self):
        for key in ("manifest_sha256", "fixture_sha256", "export_evidence"):
            receipt = self.changed()
            receipt["input"][key] = {"sha256": "a" * 64} if key == "export_evidence" else "a" * 64
            with self.subTest(key=key), self.assertRaises(RuntimeProbeError):
                validate_bootstrap_receipt(receipt, self.source, self.root)

    def test_stale_or_duplicated_source_inventory_is_rejected(self):
        for duplicate in (False, True):
            receipt = self.changed()
            if duplicate:
                receipt["input"]["sources"].append(copy.deepcopy(receipt["input"]["sources"][0]))
            else:
                receipt["input"]["sources"][0]["sha256"] = "a" * 64
            with self.assertRaises(RuntimeProbeError):
                validate_bootstrap_receipt(receipt, self.source, self.root)

    def test_receipt_native_package_hash_and_length_are_checked(self):
        for key, value in (("sha256", "a" * 64), ("size_bytes", 1)):
            receipt = self.changed()
            receipt["native_files"][0][key] = value
            with self.subTest(key=key), self.assertRaises(RuntimeProbeError):
                validate_bootstrap_receipt(receipt, self.source, self.root)

    def test_unexpected_or_duplicate_native_package_is_rejected(self):
        receipt = self.changed()
        receipt["native_files"].append(copy.deepcopy(receipt["native_files"][0]))
        with self.assertRaises(RuntimeProbeError):
            validate_bootstrap_receipt(receipt, self.source, self.root)

    def test_older_bootstrap_without_imported_hashes_requires_rerun(self):
        receipt = self.changed()
        receipt.pop("imported_native_files")
        with self.assertRaisesRegex(RuntimeProbeError, "Rerun the current native bootstrap"):
            validate_bootstrap_receipt(receipt, self.source, self.root)

    def test_imported_package_inventory_requires_exact_unique_coverage(self):
        for change in ("duplicate", "missing", "extra"):
            receipt = self.changed()
            if change == "duplicate":
                receipt["imported_native_files"].append(copy.deepcopy(receipt["imported_native_files"][0]))
            elif change == "missing":
                receipt["imported_native_files"].pop()
            else:
                receipt["imported_native_files"].append({"package": self.namespace + "/UnboundAsset"})
            with self.subTest(change=change), self.assertRaisesRegex(RuntimeProbeError, "cover exactly"):
                validate_bootstrap_receipt(receipt, self.source, self.root)

    def test_changed_imported_animation_bytes_cannot_reuse_successful_receipt(self):
        record = next(x for x in self.receipt["imported_native_files"] if "/Animations/" in x["package"])
        path = Path(record["file"])
        saved = path.read_bytes()
        try:
            path.write_bytes(saved[:-1] + (b"X" if saved[-1:] != b"X" else b"Y"))
            with self.assertRaisesRegex(RuntimeProbeError, "imported native package changed"):
                validate_bootstrap_receipt(self.receipt, self.source, self.root)
        finally:
            path.write_bytes(saved)

    def test_source_import_mismatch_is_rejected(self):
        receipt = self.changed()
        receipt["imports"][0]["source_sha256"] = "a" * 64
        with self.assertRaisesRegex(RuntimeProbeError, "source differs"):
            validate_bootstrap_receipt(receipt, self.source, self.root)

    def test_import_paths_cannot_escape_recorded_namespace(self):
        for path in ("/Game/Other/SK.SK", self.namespace + "/../../SK.SK", "/Engine/SK.SK"):
            receipt = self.changed()
            receipt["imports"][0]["path"] = path
            with self.subTest(path=path), self.assertRaises(RuntimeProbeError):
                validate_bootstrap_receipt(receipt, self.source, self.root)

    def test_import_skeleton_and_duration_are_checked(self):
        for field, value in (("skeleton", self.namespace + "/Other.Other"), ("duration_seconds", float("nan")),
                             ("duration_seconds", 500), ("bone_track_count", 2)):
            receipt = self.changed()
            animation = next(x for x in receipt["imports"] if x["kind"] == "AnimSequence")
            animation[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(RuntimeProbeError):
                validate_bootstrap_receipt(receipt, self.source, self.root)

    def test_missing_native_asset_file_is_rejected(self):
        receipt = self.changed()
        path = self.root / "Content" / (receipt["imports"][0]["path"].split(".")[0][len("/Game/"):] + ".uasset")
        saved = path.read_bytes()
        try:
            path.unlink()
            with self.assertRaisesRegex(RuntimeProbeError, "Required native package is absent"):
                validate_bootstrap_receipt(receipt, self.source, self.root)
        finally:
            path.write_bytes(saved)

    def test_changed_native_package_bytes_are_rejected(self):
        path = Path(self.receipt["native_files"][0]["file"])
        saved = path.read_bytes()
        try:
            path.write_bytes(saved + b"changed")
            with self.assertRaisesRegex(RuntimeProbeError, "native package changed"):
                validate_bootstrap_receipt(self.receipt, self.source, self.root)
        finally:
            path.write_bytes(saved)

    def test_planning_preserves_existing_project_files(self):
        files = [x for x in self.root.rglob("*") if x.is_file()]
        before = {str(x): sha256_file(x) for x in files}
        plan_runtime_probe(self.receipt_path, self.root)
        self.assertEqual(before, {str(x): sha256_file(x) for x in files})


class NativePoseResponseTests(unittest.TestCase):
    """Synthetic geometry checks validate the gate, never an Unreal pose."""
    @staticmethod
    def sample(time, local_foot, origin=(0, 0, 0), yaw=0):
        c, s = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
        x, y, z = local_foot
        world = [c*x - s*y + origin[0], s*x + c*y + origin[1], z + origin[2]]
        return {"time_seconds": time, "actors": [
            {"id": "A", "source_motion_id": "canonical.pass", "source_time_seconds": time,
             "native_sample_seconds": time, "position_cm": list(origin), "yaw_degrees": yaw,
             "review_bone_positions_cm": {"LeftFoot": world}}]}

    def test_changed_local_pose_records_responding_bone_and_sample_pair(self):
        result = verify_pose_response([self.sample(0, [10, 0, 5]), self.sample(1, [13, 4, 5], (400, 300, 0), 90)],
                                      {"A": ["canonical.pass"]})
        self.assertEqual(result["status"], "passed")
        self.assertAlmostEqual(result["checks"][0]["max_displacement_cm"], 5)
        self.assertEqual(result["checks"][0]["time_pair_seconds"], [0, 1])
        self.assertEqual(result["checks"][0]["bone"], "LeftFoot")

    def test_static_local_pose_fails_even_when_source_clock_changed(self):
        with self.assertRaisesRegex(RuntimeProbeError, "bones did not respond"):
            verify_pose_response([self.sample(0, [10, 0, 5]), self.sample(1, [10, 0, 5])],
                                 {"A": ["canonical.pass"]})

    def test_same_pose_with_only_world_travel_and_yaw_fails(self):
        with self.assertRaisesRegex(RuntimeProbeError, "bones did not respond"):
            verify_pose_response([self.sample(0, [10, 0, 5]), self.sample(1, [10, 0, 5], (400, 300, 20), 137)],
                                 {"A": ["canonical.pass"]})


if __name__ == "__main__":
    unittest.main()
