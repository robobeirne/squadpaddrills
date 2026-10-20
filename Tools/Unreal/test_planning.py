"""Cloud-only contract and motion-math tests. These do not execute Unreal.

The temporary FBX files are deliberately fake signature stubs. They exercise
presence/hash checks only and are destroyed after each test. No test result is
evidence of actual FBX import, skeleton quality, animation or visual acceptance.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "Content/Python"))
from squadpad_plan import InputError, _sample_keys, frame_time, plan_import, sha256_file
from squadpad_bootstrap import EditorBuildError, _range, build


class PlanningOnlyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="_planning_test_", dir=Path(__file__).parent)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.manifest = json.loads((PROJECT / "import_manifest.json").read_text())
        self.fixture = json.loads((PROJECT / "Fixtures/first_pass.drill.json").read_text())
        self.manifest_path = self.root / "import_manifest.json"
        self.fixture_path = self.root / self.manifest["fixture_path"]
        self.write_json(self.manifest_path, self.manifest)
        self.write_json(self.fixture_path, self.fixture)
        files = []
        for record in [self.manifest["mesh"], *self.manifest["animations"]]:
            path = self.root / record["fbx"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"Kaydara FBX Binary TEST-ONLY SIGNATURE STUB " + record["id"].encode())
            files.append(path)
            if "root_motion" not in record:
                continue
            contact = record.get("contact_seconds")
            duration = record["duration_seconds"]
            if contact is None:
                keys = [{"time_seconds": 0, "position_cm": [0, 0, 0]},
                        {"time_seconds": duration, "position_cm": [0, 0, 0]}]
            else:
                keys = [{"time_seconds": 0, "position_cm": [-60, 0, 0]},
                        {"time_seconds": contact, "position_cm": [0, 0, 0]},
                        {"time_seconds": duration, "position_cm": [12, 0, 0]}]
            root_path = self.root / record["root_motion"]["path"]
            self.write_json(root_path, {"schema": "squadpad.unreal.root-motion.v1", "animation_id": record["id"],
                                       "axes": "unreal-z-up-x-forward", "units": "cm", "time_units": "seconds",
                                       "contact_seconds": contact, "root_motion_baked_into_skeleton": False,
                                       "keys": keys})
            files.append(root_path)
        self.files = files
        self.export_path = self.root / self.manifest["export_receipt_path"]
        self.write_json(self.export_path, {
            "schema": "squadpad.unreal.export-receipt.v1",
            "status": "fbx_roundtrip_structural_passed_unreal_validation_pending",
            "test_only": "Synthetic parser fixture; no export or roundtrip actually occurred",
            "outputs": {path.name: {"sha256": sha256_file(path), "bytes": path.stat().st_size} for path in files},
        })

    @staticmethod
    def write_json(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def plan(self, require_files=True):
        return plan_import(self.manifest_path, require_files=require_files)

    def test_contact_subframes_are_not_rounded_to_display_frames(self):
        seconds = 1.39262125
        whole, fraction = frame_time(seconds, 60)
        self.assertEqual(whole, 83)
        self.assertAlmostEqual(fraction, 0.557275, places=9)
        self.assertAlmostEqual((whole + fraction) / 60, seconds, places=12)

    def test_contact_relative_root_is_anchored_and_rotated_once(self):
        plan = self.plan()
        for actor in plan["actors"]:
            contact = next(c for c in plan["contacts"] if c["actor_id"] == actor["id"])
            at_contact = _sample_keys(actor["transform_keys"], contact["time_seconds"])["position_cm"]
            for actual, expected in zip(at_contact, actor["guide_position_cm"]):
                self.assertAlmostEqual(actual, expected, places=9)
            self.assertEqual(actor["root_motion"]["application_count"], 1)
        first = plan["actors"][0]
        angle = math.radians(first["rotation_deg"]["yaw"])
        self.assertAlmostEqual(first["position_cm"][0], 250 - 60 * math.cos(angle), places=9)
        self.assertAlmostEqual(first["position_cm"][1], 250 - 60 * math.sin(angle), places=9)

    def test_root_endpoints_hold_through_idle_without_teleport(self):
        plan = self.plan()
        for actor in plan["actors"]:
            action = next(c for c in actor["clips"] if not c["generated_idle"])
            keys = actor["transform_keys"]
            start = _sample_keys(keys, action["start_seconds"])["position_cm"]
            end = _sample_keys(keys, action["start_seconds"] + action["duration_seconds"])["position_cm"]
            self.assertEqual(_sample_keys(keys, 0)["position_cm"], start)
            self.assertEqual(_sample_keys(keys, plan["duration_seconds"])["position_cm"], end)
            self.assertNotEqual(end, actor["guide_position_cm"])

    def test_missing_sources_are_pending_for_metadata_plan(self):
        for path in self.files + [self.export_path]:
            path.unlink()
        plan = self.plan(require_files=False)
        self.assertFalse(plan["all_source_files_present"])
        self.assertEqual(len([s for s in plan["sources"] if s["sha256"] is None]), 8)
        self.assertTrue(all(a["root_motion"]["status"] == "missing_export_descriptor" for a in plan["actors"]))

    def test_missing_inputs_fail_before_unreal_import_and_leave_honest_receipt(self):
        (self.root / self.manifest["mesh"]["fbx"]).unlink()
        receipt_path = self.root / "test_failure_receipt.json"
        with self.assertRaisesRegex(InputError, "Required source file is absent"):
            build(self.manifest_path, run_name="TestOnly", receipt_path=receipt_path, open_editor=False)
        receipt = json.loads(receipt_path.read_text())
        self.assertEqual(receipt["status"], "failed")
        self.assertFalse(receipt["unreal_execution_verified"])
        self.assertEqual(receipt["runtime_playback"]["status"], "not_run")
        self.assertFalse(receipt["contact_geometry"]["accepted"])

    def test_partial_or_stale_export_hash_is_rejected(self):
        path = self.root / self.manifest["animations"][1]["fbx"]
        with path.open("ab") as stream:
            stream.write(b" changed after receipt")
        with self.assertRaisesRegex(InputError, "Export receipt hash/length mismatch"):
            self.plan()

    def test_root_displacement_cannot_be_applied_twice(self):
        self.manifest["root_motion_baked_into_skeleton"] = True
        for clip in self.manifest["animations"]:
            clip.pop("root_motion_baked_into_skeleton", None)
        self.write_json(self.manifest_path, self.manifest)
        with self.assertRaisesRegex(InputError, "double displacement"):
            self.plan()

    def test_root_descriptor_requires_zero_at_exact_contact(self):
        path = self.root / self.manifest["animations"][1]["root_motion"]["path"]
        data = json.loads(path.read_text())
        data["keys"][1]["position_cm"] = [1, 0, 0]
        self.write_json(path, data)
        with self.assertRaisesRegex(InputError, "explicit zero offset"):
            self.plan()

    def test_ball_contact_and_original_drill_scope_survive_planning(self):
        plan = self.plan()
        self.assertFalse(plan["selection"]["complete_drill"])
        self.assertEqual(plan["source_payload"], self.fixture["source_payload"])
        self.assertEqual(plan["balls"][0]["keys"], self.fixture["balls"][0]["keys"])
        self.assertTrue(all(c["calibration"]["status"] == "unmeasured" for c in plan["contacts"]))

    def test_fixture_cannot_silently_add_a_second_unit_conversion(self):
        self.fixture["units"]["distance"] = "metres"
        self.write_json(self.fixture_path, self.fixture)
        with self.assertRaisesRegex(InputError, "Fixture units"):
            self.plan()

    def test_section_wrapper_that_drops_subframes_is_rejected(self):
        class FakeSections:
            @staticmethod
            def set_range_seconds(section, start, end):
                section["start"], section["end"] = round(start * 60) / 60, round(end * 60) / 60

            @staticmethod
            def get_start_frame_seconds(section):
                return section["start"]

            @staticmethod
            def get_end_frame_seconds(section):
                return section["end"]

        class FakeUnreal:
            MovieSceneSectionExtensions = FakeSections

        with self.assertRaisesRegex(EditorBuildError, "one sequence tick"):
            _range(FakeUnreal, {}, 1.86575125, 3.16575125)


if __name__ == "__main__":
    print("Planning/math checks only. Fake FBX stubs and one API mock; Unreal is not executed.")
    unittest.main(verbosity=2)
