"""Temporary synthetic data exercises transport and parser failure cases only.

These minimal GLB containers are not athletes or native animation evidence.
No synthetic source asset is retained in the delivered project.
"""
import copy
import json
import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "Tools"))
from source_bundle import (MANIFEST_NAME, REQUIRED, inspect_inputs, json_bytes, load_bundle,
                           pack_source, read_source_root)  # noqa: E402
from retarget_core import write_glb  # noqa: E402


def write_synthetic_source(root):
    doc = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}],
           "nodes": [
               {"name": "Armature", "children": [1, 6]},
               {"name": "mixamorig:Hips", "translation": [0, 1, 0], "children": [2, 4]},
               {"name": "mixamorig:LeftFoot", "translation": [-.1, -1, 0], "children": [3]},
               {"name": "mixamorig:LeftToeBase", "translation": [0, 0, .15]},
               {"name": "mixamorig:RightFoot", "translation": [.1, -1, 0], "children": [5]},
               {"name": "mixamorig:RightToeBase", "translation": [0, 0, .15]},
               {"name": "SyntheticContainer", "skin": 0, "mesh": 0}],
           "skins": [{"joints": [1, 2, 3, 4, 5]}], "meshes": [{"primitives": []}]}
    paths = {item["id"]: root / item["source_relative_path"] for item in REQUIRED}
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    for asset in ("athlete_a", "source_rig"):
        write_glb(paths[asset], doc, b"\0\0\0\0")
    baseline = json.loads((PROJECT / "import_manifest.json").read_text())
    times, positions, rotations = [], [], []
    index = {"schema": 2, "layout": {}, "clips": {}, "meta": {}, "fingers": {}}
    for clip in baseline["animations"]:
        clip_id = clip["id"]
        info = {"frames": 2, "time": len(times), "tracks": [["Hips", "p", len(positions)]]}
        times.extend([0, clip["duration_seconds"]])
        positions.extend([0, 1, 0, .02, 1, .04])
        for bone in ("Hips", "LeftFoot", "LeftToeBase", "RightFoot", "RightToeBase"):
            info["tracks"].append([bone, "q", len(rotations)])
            rotations.extend([0, 0, 0, 32767] * 2)
        index["clips"][clip_id] = info
        meta = {"duration": clip["duration_seconds"], "loop": clip_id == "idle"}
        if clip_id != "idle":
            meta.update(contact=clip["contact_phase"], foot="right")
        index["meta"][clip_id] = meta
    index["layout"] = {"times": len(times), "pos": len(positions), "rot": len(rotations)}
    paths["mocap_index"].write_bytes(json_bytes(index))
    paths["mocap_binary"].write_bytes(struct.pack(f"<{len(times) + len(positions)}f", *(times + positions))
                                    + struct.pack(f"<{len(rotations)}h", *rotations))
    return paths


class SourceBundleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "source"
        self.paths = write_synthetic_source(self.root)

    def test_bundle_copies_only_allowlisted_bytes_and_never_overwrites(self):
        (self.root / "unrelated-private.txt").write_text("must not be copied")
        output = Path(self.temporary.name) / "inputs.zip"
        report = pack_source(self.root, output)
        originals = read_source_root(self.root)
        self.assertEqual(load_bundle(output), originals)
        self.assertEqual(report["baseline_drift"], [])
        self.assertFalse(report["native_execution_verified"])
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(set(archive.namelist()), {item["archive_path"] for item in REQUIRED} | {MANIFEST_NAME})
        saved = output.read_bytes()
        with self.assertRaisesRegex(ValueError, "already exists"):
            pack_source(self.root, output)
        self.assertEqual(output.read_bytes(), saved)
        self.assertEqual(read_source_root(self.root), originals)

    def test_bundle_rejects_changed_bytes_and_unexpected_paths(self):
        original = Path(self.temporary.name) / "original.zip"
        pack_source(self.root, original)
        with zipfile.ZipFile(original) as archive:
            members = {name: archive.read(name) for name in archive.namelist()}
        cases = [dict(members, **{"inputs/mocap-drills.bin": members["inputs/mocap-drills.bin"] + b"x"}),
                 dict(members, **{"../unexpected.txt": b"no"})]
        for i, case in enumerate(cases):
            with self.subTest(case=i):
                path = Path(self.temporary.name) / f"invalid-{i}.zip"
                with zipfile.ZipFile(path, "w") as archive:
                    for name, raw in case.items():
                        archive.writestr(name, raw)
                with self.assertRaisesRegex(ValueError, "hashes|exactly"):
                    load_bundle(path)
                self.assertFalse((Path(self.temporary.name) / "unexpected.txt").exists())

    def test_source_link_and_lfs_pointer_are_rejected(self):
        path = self.paths["athlete_a"]
        actual = path.read_bytes()
        outside = Path(self.temporary.name) / "outside.glb"
        outside.write_bytes(actual)
        path.unlink()
        path.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "symlink"):
            read_source_root(self.root)
        path.unlink()
        path.write_text("version https://git-lfs.github.com/spec/v1\noid sha256:fake\nsize 100\n")
        with self.assertRaisesRegex(ValueError, "LFS pointer"):
            read_source_root(self.root)

    def test_truncated_binary_and_nonfinite_time_are_rejected(self):
        original = read_source_root(self.root)
        truncated = dict(original, mocap_binary=original["mocap_binary"][:-1])
        with self.assertRaisesRegex(ValueError, "index layout"):
            inspect_inputs(truncated)
        invalid = bytearray(original["mocap_binary"])
        struct.pack_into("<f", invalid, 0, float("nan"))
        with self.assertRaisesRegex(ValueError, "time samples"):
            inspect_inputs(dict(original, mocap_binary=bytes(invalid)))

    def test_duplicate_track_cannot_silently_replace_source_motion(self):
        blobs = read_source_root(self.root)
        index = json.loads(blobs["mocap_index"])
        index["clips"]["pass"]["tracks"].append(copy.deepcopy(index["clips"]["pass"]["tracks"][0]))
        blobs["mocap_index"] = json_bytes(index)
        with self.assertRaisesRegex(ValueError, "Duplicate source track"):
            inspect_inputs(blobs)

    def test_new_contact_metadata_is_reported_without_retiming(self):
        blobs = read_source_root(self.root)
        index = json.loads(blobs["mocap_index"])
        index["meta"]["pass"]["contact"] = .5
        blobs["mocap_index"] = json_bytes(index)
        before = copy.deepcopy(blobs)
        report = inspect_inputs(blobs)
        self.assertFalse(report["baseline_timing_and_limb_match"])
        self.assertEqual({row["field"] for row in report["baseline_drift"]}, {"contact_seconds", "contact_phase"})
        self.assertEqual(blobs, before)
        self.assertFalse(report["visual_quality_accepted"])


if __name__ == "__main__":
    unittest.main()
