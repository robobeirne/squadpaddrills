"""Narrow exporter regressions; no real assets, Blender or Unreal are used."""

import copy
import json
import math
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from retarget_core import (Canonical, Retargeter, Rig, clip_sample_window, compose,
                           prepare_clip, prepare_fixed_fingers, qnorm, qslerp, read_glb)
from export_athlete_fbx import publish_receipt


class QuaternionInputTests(unittest.TestCase):
    @staticmethod
    def quantized(q):
        return tuple(round(v * 32767) / 32767 for v in qnorm(q))

    def assert_rigid_rotation(self, quaternion):
        self.assertAlmostEqual(math.hypot(*quaternion), 1, places=12)
        matrix = compose(q=quaternion)
        columns = [[matrix[column * 4 + row] for row in range(3)] for column in range(3)]
        for i in range(3):
            for j in range(3):
                self.assertAlmostEqual(sum(a * b for a, b in zip(columns[i], columns[j])),
                                       float(i == j), places=12)

    def test_quantized_endpoints_and_in_between_samples_stay_rigid(self):
        a = self.quantized((.2, -.3, .1, .8))
        b = self.quantized((-.4, .1, .5, .7))
        self.assertGreater(abs(math.hypot(*a) - 1), 1e-6)
        for time in (0, .25, .5, 1):
            with self.subTest(time=time):
                self.assert_rigid_rotation(qslerp(a, b, time))

    def test_canonical_integer_track_decodes_to_rigid_node_rotation(self):
        # Exercise the actual sampler with the retained [bone,kind,offset]
        # layout. These are deliberately synthetic integer motion values.
        library = Canonical.__new__(Canonical)
        library.doc = {"clips": {"synthetic": {
            "frames": 2, "time": 0, "tracks": [["mixamorig:Hips", "q", 0]]}}}
        library.times = (0, 1)
        library.pos = ()
        a = tuple(round(v * 32767) for v in qnorm((.2, -.3, .1, .8)))
        b = tuple(round(v * 32767) for v in qnorm((-.4, .1, .5, .7)))
        library.rot = a + b
        for time in (0, .5, 1):
            with self.subTest(time=time):
                self.assert_rigid_rotation(library.sample("synthetic", time)["Hips"]["q"])

    def test_invalid_rotations_fail_before_matrix_composition(self):
        for invalid in ((0, 0, 0, 0), (0, float("nan"), 0, 1),
                        (0, 0, float("inf"), 1), (0, 0, 1), (False, 0, 0, 1)):
            with self.subTest(quaternion=invalid):
                with self.assertRaises(ValueError):
                    qslerp(invalid, (0, 0, 0, 1), 0)

    def test_unused_terminal_placeholder_has_no_target_dependency(self):
        source = SimpleNamespace(bones={"LeftHandIndex4": 0}, nodes=[{}])
        target = SimpleNamespace(bones={})
        library = SimpleNamespace(doc={"fingers": {"LeftHandIndex4": [0, 0, 0, 0]}, "clips": {}})
        prepared, ignored = prepare_fixed_fingers(source, target, library)
        self.assertEqual(prepared, {})
        self.assertEqual(ignored, ["LeftHandIndex4"])
        self.assertEqual(library.doc["fingers"]["LeftHandIndex4"], [0, 0, 0, 0])
        for mode in ("target", "child", "animated"):
            with self.subTest(mode=mode):
                target.bones = {"LeftHandIndex4": 0} if mode == "target" else {}
                source.nodes = [{"children": [1]}, {}] if mode == "child" else [{}]
                library.doc["clips"] = {"pass": {"tracks": [["LeftHandIndex4", "q", 0]]}} if mode == "animated" else {}
                with self.assertRaises(ValueError):
                    prepare_fixed_fingers(source, target, library)

    def test_recorded_endpoint_holds_preserve_source_clock(self):
        library = Canonical.__new__(Canonical)
        library.doc = {"clips": {"receive": {"time": 0, "frames": 4}},
                       "meta": {"receive": {"duration": .1}}}
        library.times = (.0092, .04, .07, .0966)
        report = clip_sample_window(library, "receive")
        self.assertAlmostEqual(report["leading_hold_seconds"], .0092)
        self.assertAlmostEqual(report["trailing_hold_seconds"], .0034)
        self.assertEqual(library.times, (.0092, .04, .07, .0966))
        library.times = (.0092, .04, .07, .071)
        library.doc["meta"]["receive"]["duration"] = .3
        with self.assertRaisesRegex(ValueError, "endpoint hold"):
            clip_sample_window(library, "receive")


class PassOpeningHoldTests(unittest.TestCase):
    def setUp(self):
        def rig(height):
            return Rig({"asset": {"version": "2.0"}, "nodes": [
                {"name": "Armature", "children": [1]},
                {"name": "Hips", "translation": [0, height, 0], "children": [2, 4]},
                {"name": "LeftFoot", "translation": [-.1, -height, 0], "children": [3]},
                {"name": "LeftToeBase", "translation": [0, 0, .15]},
                {"name": "RightFoot", "translation": [.1, -height, 0], "children": [5]},
                {"name": "RightToeBase", "translation": [0, 0, .15]}],
                "scenes": [{"nodes": [0]}], "skins": [{"joints": [1, 2, 3, 4, 5]}]})
        self.source, self.target = rig(1), rig(.8)
        library = Canonical.__new__(Canonical)
        library.times = (0., .0332, .0664, .0996)
        library.pos = (0., 1., 0., .1, .98, -.65, .15, 1.01, -.63, .2, 1.02, -.60)
        quaternions = ((0, 0, 0, 1), (.3, .1, 0, .95), (.31, .08, 0, .94), (.32, .06, 0, .93))
        library.rot = tuple(round(v * 32767) for q in quaternions for v in qnorm(q))
        library.doc = {"fingers": {}, "clips": {}, "meta": {}}
        for name in ("idle", "pass", "receive"):
            library.doc["clips"][name] = {"time": 0, "frames": 4, "duration": .0996,
                                          "tracks": [["Hips", "p", 0], ["Hips", "q", 0]]}
            library.doc["meta"][name] = {"duration": .0996, "loop": name == "idle"}
            if name != "idle":
                library.doc["meta"][name].update(contact=.75, foot="right")
        self.library = library

    def retargeter(self, enabled=False):
        return Retargeter(self.source, self.target, self.library, ground_lift=False,
                          hold_pass_opening_sample=enabled)

    def test_opt_in_holds_pose_and_root_together_without_mutating_source(self):
        before = copy.deepcopy((self.library.doc, self.library.times, self.library.pos, self.library.rot))
        raw, held = self.retargeter(), self.retargeter(True)
        second = self.library.times[1]
        expected_pose = raw.sample("pass", second)
        expected_root = raw.raw_hips_world("pass", second)
        self.assertEqual(raw.derived_corrections, [])
        self.assertNotEqual(raw.sample("pass", 0), expected_pose)
        self.assertNotEqual(raw.raw_hips_world("pass", 0), expected_root)
        for time in (0., .01, 1 / 60, second - 1e-9):
            with self.subTest(time=time):
                self.assertEqual(held.sample("pass", time), expected_pose)
                self.assertEqual(held.raw_hips_world("pass", time), expected_root)
        self.assertEqual((self.library.doc, self.library.times, self.library.pos, self.library.rot), before)

    def test_later_contact_and_other_clips_keep_original_values(self):
        raw, held = self.retargeter(), self.retargeter(True)
        contact = self.library.doc["meta"]["pass"]["duration"] * .75
        for time in (self.library.times[1], .05, contact, .0996):
            with self.subTest(clip="pass", time=time):
                self.assertEqual(held.sample("pass", time), raw.sample("pass", time))
                self.assertEqual(held.raw_hips_world("pass", time), raw.raw_hips_world("pass", time))
        for clip in ("idle", "receive"):
            for time in (0., .01, .05, contact, .0996):
                with self.subTest(clip=clip, time=time):
                    self.assertEqual(held.sample(clip, time), raw.sample(clip, time))
                    self.assertEqual(held.raw_hips_world(clip, time), raw.raw_hips_world(clip, time))

    def test_hold_rejects_unexpected_opening_or_contact_bounds(self):
        original_doc, original_times = copy.deepcopy(self.library.doc), self.library.times
        cases = ("two_samples", "nonzero_start", "zero_second", "late_second", "early_contact")
        for case in cases:
            with self.subTest(case=case):
                self.library.doc, self.library.times = copy.deepcopy(original_doc), original_times
                if case == "two_samples":
                    self.library.doc["clips"]["pass"]["frames"] = 2
                elif case == "nonzero_start":
                    self.library.times = (.001, .0332, .0664, .0996)
                elif case == "zero_second":
                    self.library.times = (0., 0., .0664, .0996)
                elif case == "late_second":
                    self.library.times = (0., .0501, .0664, .0996)
                else:
                    self.library.doc["meta"]["pass"]["contact"] = .1
                with self.assertRaises(ValueError):
                    self.retargeter(True)

    def test_prepared_clip_reports_correction_and_keeps_duration_and_contact(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            raw = prepare_clip(self.retargeter(), self.target.doc, b"\0" * 4,
                               "pass", 60, path / "raw.glb")
            held = prepare_clip(self.retargeter(True), self.target.doc, b"\0" * 4,
                                "pass", 60, path / "held.glb")
            for field in ("contact_seconds", "source_duration_seconds", "duration_seconds", "frames"):
                self.assertEqual(held[field], raw[field])
            self.assertEqual(raw["derived_corrections"], [])
            correction = held["derived_corrections"][0]
            self.assertEqual(correction["source_sample_time_seconds"], self.library.times[1])
            self.assertFalse(correction["source_arrays_modified"])
            self.assertFalse(correction["later_source_timestamps_shifted"])
            raw_roots = {key["time_seconds"]: key["position_cm"] for key in raw["root_keys"]}
            held_roots = {key["time_seconds"]: key["position_cm"] for key in held["root_keys"]}
            self.assertEqual(held_roots[0.], held_roots[self.library.times[1]])
            for time, root in raw_roots.items():
                if time >= self.library.times[1]:
                    self.assertEqual(held_roots[time], root)
            doc, binary = read_glb(path / "held.glb")
            accessor = doc["accessors"][doc["animations"][0]["samplers"][0]["input"]]
            view = doc["bufferViews"][accessor["bufferView"]]
            times = struct.unpack_from(f"<{accessor['count']}f", binary, view.get("byteOffset", 0))
            self.assertTrue(any(abs(time - self.library.times[1]) < 1e-8 for time in times))


class ReceiptPublicationTests(unittest.TestCase):
    def test_success_clears_only_owned_stale_failure_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".squadpad-export-v1").write_text("squadpad.unreal.generated-export.v1\n")
            failure = root / "export_failure.json"
            failure.write_text(json.dumps({"schema": "squadpad.unreal.export-receipt.v1", "status": "failed"}))
            historical = root / "historical-armature-only-export-failure.json"
            historical.write_text("retained historical failure evidence")
            receipt = root / "export_manifest.json"
            successful = {"schema": "squadpad.unreal.export-receipt.v1",
                          "status": "fbx_roundtrip_structural_passed_unreal_validation_pending"}
            publish_receipt(receipt, successful)
            self.assertFalse(failure.exists())
            self.assertEqual(json.loads(receipt.read_text()), successful)
            self.assertEqual(historical.read_text(), "retained historical failure evidence")
            # A same-named foreign file is not silently deleted by publication.
            failure.write_text(json.dumps({"schema": "unrelated.file", "status": "failed"}))
            with self.assertRaisesRegex(ValueError, "not this exporter's"):
                publish_receipt(receipt, successful)
            self.assertTrue(failure.exists())
            self.assertEqual(json.loads(receipt.read_text()), successful)


if __name__ == "__main__":
    unittest.main(verbosity=2)
