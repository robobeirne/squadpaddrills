#!/usr/bin/env python3
"""Inspect or package four explicit local source files for the first Unreal scene.

Standard library only. No device connection, networking, Blender or Unreal.
The ZIP is an input snapshot, not an export receipt or native-quality result.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import os
import stat
import struct
import sys
import tempfile
import zipfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "Tools/Export"))
from retarget_core import Canonical, Retargeter, Rig, clean_name, clip_sample_window, qnorm, read_glb  # noqa: E402

SCHEMA = "squadpad.source-bundle.v1"
MANIFEST_NAME = "squadpad-source-manifest.json"
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
REQUIRED = (
    {"id": "athlete_a", "source_relative_path": "tools/footballer/mpfb/athlete_a.glb",
     "archive_path": "inputs/athlete_a.glb"},
    {"id": "source_rig", "source_relative_path": "tools/footballer/xbot/footballer.glb",
     "archive_path": "inputs/xbot/footballer.glb"},
    {"id": "mocap_index", "source_relative_path": "ios-native/docs/drill-cards/mocap-drills.json",
     "archive_path": "inputs/mocap-drills.json"},
    {"id": "mocap_binary", "source_relative_path": "ios-native/docs/drill-cards/mocap-drills.bin",
     "archive_path": "inputs/mocap-drills.bin"},
)
CORE_BONES = {"Hips", "LeftFoot", "RightFoot", "LeftToeBase", "RightToeBase"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def integer(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def read_json(raw):
    def invalid(value):
        raise ValueError(f"Nonfinite JSON constant: {value}")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, f"Duplicate JSON key: {key}")
            result[key] = value
        return result
    return json.loads(raw, parse_constant=invalid, object_pairs_hook=unique)


def json_bytes(value):
    return (json.dumps(value, indent=2, allow_nan=False) + "\n").encode("utf-8")


def file_records(blobs):
    require(set(blobs) == {item["id"] for item in REQUIRED}, "Expected exactly four source inputs")
    require(sum(len(raw) for raw in blobs.values()) <= MAX_TOTAL_BYTES, "Input snapshot exceeds size limit")
    records = []
    for item in REQUIRED:
        raw = blobs[item["id"]]
        require(0 < len(raw) <= MAX_FILE_BYTES, f"Missing, empty or oversized input: {item['id']}")
        require(not raw.startswith(b"version https://git-lfs.github.com/spec/v1"),
                f"Git LFS pointer supplied instead of actual bytes: {item['id']}")
        records.append(dict(item, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()))
    return records


def read_source_root(source_root):
    """Read only the allowlist. Reject symlinks below the explicitly chosen root."""
    root = Path(source_root).expanduser().resolve(strict=True)
    require(root.is_dir(), "Source root must be a local directory")
    blobs, snapshots = {}, {}
    for item in REQUIRED:
        path = root
        for part in Path(item["source_relative_path"]).parts:
            path = path / part
            require(not path.is_symlink(), f"Source input uses a symlink: {item['source_relative_path']}")
        require(path.resolve(strict=True).is_relative_to(root), "Source input escapes selected root")
        before = path.stat()
        require(stat.S_ISREG(before.st_mode), f"Source input is not a regular file: {item['id']}")
        require(0 < before.st_size <= MAX_FILE_BYTES, f"Empty or oversized source: {item['id']}")
        with path.open("rb") as handle:
            blobs[item["id"]] = handle.read(MAX_FILE_BYTES + 1)
        snapshots[path] = before
    for path, before in snapshots.items():
        after = path.stat()
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        require(all(getattr(before, key) == getattr(after, key) for key in fields),
                f"Source changed while reading; retry from a stable snapshot: {path.name}")
    file_records(blobs)
    return blobs


def load_bundle(bundle_path):
    """Verify bytes without extracting any archive-supplied path."""
    allowed = {item["archive_path"] for item in REQUIRED} | {MANIFEST_NAME}
    with zipfile.ZipFile(bundle_path, "r") as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        require(len(names) == len(set(names)) and set(names) == allowed,
                "Bundle must contain exactly the four named inputs and its manifest")
        require(sum(entry.file_size for entry in entries) <= MAX_TOTAL_BYTES + 1024 * 1024,
                "Bundle exceeds uncompressed size limit")
        for entry in entries:
            limit = 1024 * 1024 if entry.filename == MANIFEST_NAME else MAX_FILE_BYTES
            require(0 < entry.file_size <= limit, "Empty or oversized bundle member")
            mode = entry.external_attr >> 16
            require(not stat.S_ISLNK(mode) and not entry.is_dir(), "Bundle contains a link or directory")
            require(not entry.flag_bits & 1, "Encrypted bundles are not supported")
        manifest = read_json(archive.read(MANIFEST_NAME))
        require(manifest.get("schema") == SCHEMA, "Unsupported source bundle schema")
        blobs = {item["id"]: archive.read(item["archive_path"]) for item in REQUIRED}
    require(manifest.get("files") == file_records(blobs), "Bundle file hashes or inventory do not match")
    return blobs


def inspect_glb(path):
    doc, binary = read_glb(path)
    require(doc.get("asset", {}).get("version") == "2.0", "Expected a glTF 2.0 asset")
    length = doc["buffers"][0]["byteLength"]
    require(integer(length) and 0 <= len(binary) - length <= 3, "GLB embedded buffer length mismatch")
    for view in doc.get("bufferViews", []):
        start, size = view.get("byteOffset", 0), view["byteLength"]
        require(view.get("buffer") == 0 and integer(start) and integer(size)
                and start + size <= length, "GLB buffer view exceeds embedded bytes")
    for image in doc.get("images", []):
        require("uri" not in image and integer(image.get("bufferView"))
                and image["bufferView"] < len(doc.get("bufferViews", []))
                and image.get("mimeType") in {"image/png", "image/jpeg"},
                "Exporter requires embedded PNG/JPEG textures")
    rig = Rig(doc)
    require(CORE_BONES.issubset(rig.bones), "Rig lacks required hips, feet or toes")
    require(all(math.isfinite(v) for matrix in rig.rest_world.values() for v in matrix),
            "Rig contains a nonfinite rest transform")
    require(any("skin" in node and "mesh" in node for node in rig.nodes),
            "Expected a skinned mesh node, not a skeleton-only replacement")
    return rig, {"joint_count": len(rig.bones), "embedded_image_count": len(doc.get("images", [])),
                 "scope": "named rig, finite rest transforms and embedded-buffer bounds only"}


def inspect_inputs(blobs, import_manifest=PROJECT / "import_manifest.json"):
    records = file_records(blobs)
    plan_raw = Path(import_manifest).read_bytes()
    plan = read_json(plan_raw)
    expected = {row["id"]: row for row in plan["animations"]}
    require(set(expected) == {"idle", "pass", "receive"}, "Handoff supports the first three clips only")
    index = read_json(blobs["mocap_index"])
    require(index.get("schema") == 2, "Expected canonical mocap schema 2")
    layout = index["layout"]
    require(all(integer(layout.get(key)) for key in ("times", "pos", "rot")), "Invalid canonical layout")
    require(len(blobs["mocap_binary"]) == 4 * (layout["times"] + layout["pos"]) + 2 * layout["rot"],
            "Canonical binary does not match index layout")
    drift, clips = [], []

    def compare(clip_id, field, actual, baseline, tolerance=1e-5):
        equal = (abs(actual - baseline) <= tolerance if number(actual) and number(baseline)
                 else actual == baseline)
        if not equal:
            drift.append({"animation_id": clip_id, "field": field, "actual": actual, "planned": baseline})

    with tempfile.TemporaryDirectory(prefix="squadpad-source-check-") as temporary:
        paths = {}
        for item in REQUIRED:
            path = Path(temporary) / item["archive_path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(blobs[item["id"]])
            paths[item["id"]] = path
        target, target_report = inspect_glb(paths["athlete_a"])
        source, source_report = inspect_glb(paths["source_rig"])
        library = Canonical(paths["mocap_index"], paths["mocap_binary"])
        for clip_id in ("idle", "pass", "receive"):
            clip, meta, baseline = index["clips"][clip_id], index["meta"][clip_id], expected[clip_id]
            count, start = clip["frames"], clip["time"]
            require(integer(count) and count >= 2 and integer(start), f"Invalid time range: {clip_id}")
            sample_window = clip_sample_window(library, clip_id)
            duration = meta["duration"]
            seen = set()
            for bone, kind, offset in clip["tracks"]:
                name = clean_name(bone)
                require(name in source.bones and kind in {"p", "q"} and integer(offset),
                        f"Invalid source track: {clip_id}/{bone}")
                require((name, kind) not in seen, f"Duplicate source track: {clip_id}/{bone}")
                require(kind != "p" or name == "Hips", "Baseline exporter only supports Hips translation")
                seen.add((name, kind))
                width, values = (3, library.pos) if kind == "p" else (4, library.rot)
                end = offset + count * width
                require(end <= len(values), f"Source track exceeds binary: {clip_id}/{bone}")
                if kind == "p":
                    require(all(math.isfinite(v) for v in values[offset:end]), "Nonfinite source position")
                else:
                    for position in range(offset, end, width):
                        qnorm(tuple(v / 32767.0 for v in values[position:position + width]))
            require(("Hips", "p") in seen and all((bone, "q") in seen for bone in CORE_BONES),
                    f"Required hip/foot tracks are missing: {clip_id}")
            require(isinstance(meta.get("loop"), bool), f"Missing loop semantics: {clip_id}")
            compare(clip_id, "duration_seconds", duration, baseline["duration_seconds"])
            compare(clip_id, "loop", meta["loop"], clip_id == "idle")
            contact = None
            if clip_id != "idle":
                phase = meta["contact"]
                require(number(phase) and 0 < phase < 1, f"Invalid contact phase: {clip_id}")
                require(meta.get("foot") in {"left", "right"}, f"Missing contact foot: {clip_id}")
                contact = duration * phase
                compare(clip_id, "contact_seconds", contact, baseline["contact_seconds"])
                compare(clip_id, "contact_phase", phase, baseline["contact_phase"], 1e-7)
                compare(clip_id, "source_limb", meta["foot"].title() + "Foot", baseline["source_limb"])
            clips.append({"id": clip_id, "frames": count, "tracks": len(seen),
                          "duration_seconds": duration, "contact_seconds": contact,
                          "source_sample_window": sample_window})
        retargeter = Retargeter(source, target, library)
        for clip in clips:
            for t in (0, clip["contact_seconds"] or clip["duration_seconds"] / 2, clip["duration_seconds"]):
                _, world, _ = retargeter.sample(clip["id"], t)
                require(all(math.isfinite(v) for matrix in world.values() for v in matrix),
                        "Nonfinite retarget sample")
        leg_ratio = retargeter.leg_ratio
        ignored_fingers = retargeter.ignored_terminal_fingers
    return {"schema": "squadpad.source-inspection.v1", "status": "source_structure_checked",
            "files": records, "import_manifest_sha256": hashlib.sha256(plan_raw).hexdigest(),
            "rigs": {"athlete_a": target_report, "source_rig": source_report}, "clips": clips,
            "sampled_retarget": {"samples": 9, "source_leg_ratio": leg_ratio,
                                 "scope": "finite mathematical transforms at three times per clip"},
            "baseline_timing_and_limb_match": not drift, "baseline_drift": drift,
            "ignored_source_only_terminal_finger_placeholders": ignored_fingers,
            "limits": ["Not a complete glTF validator or skin/deformation check",
                       "Layout and track checks cannot prove an unrelated same-layout binary belongs to this index",
                       "Hashes identify this snapshot, not the original recording or canonical bind provenance",
                       "Current drill choreography and heuristic ball positions require separate source reconciliation"],
            "native_execution_verified": False, "visual_quality_accepted": False}


def pack_source(source_root, output, import_manifest=PROJECT / "import_manifest.json"):
    output = Path(output).expanduser().absolute()
    require(not output.exists() and not output.is_symlink(), "Output already exists; choose a new filename")
    require(output.parent.is_dir(), "Output parent directory must already exist")
    blobs = read_source_root(source_root)
    report = inspect_inputs(blobs, import_manifest)
    manifest = {"schema": SCHEMA, "created_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "purpose": "Four-file source snapshot for later asset export", "files": report["files"],
                "inspection_at_pack_time": report}
    descriptor, temporary = tempfile.mkstemp(prefix=".squadpad-source-", suffix=".zip", dir=output.parent)
    os.close(descriptor)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for item in REQUIRED:
                archive.writestr(item["archive_path"], blobs[item["id"]])
            archive.writestr(MANIFEST_NAME, json_bytes(manifest))
        load_bundle(temporary)  # CRC and manifest hashes verify exactly the copied bytes.
        os.link(temporary, output)  # Atomic publication that cannot replace an existing output.
    finally:
        Path(temporary).unlink(missing_ok=True)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=PROJECT / "import_manifest.json")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("requirements")
    inspect = commands.add_parser("inspect")
    selection = inspect.add_mutually_exclusive_group(required=True)
    selection.add_argument("--source-root", type=Path)
    selection.add_argument("--bundle", type=Path)
    pack = commands.add_parser("pack")
    pack.add_argument("--source-root", type=Path, required=True)
    pack.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "requirements":
            report = {"required_files": REQUIRED, "real_source_files_included": False}
        elif args.command == "pack":
            report = pack_source(args.source_root, args.output, args.manifest)
            report["created_bundle"] = str(args.output.absolute())
        else:
            blobs = read_source_root(args.source_root) if args.source_root else load_bundle(args.bundle)
            report = inspect_inputs(blobs, args.manifest)
        print(json_bytes(report).decode(), end="")
        return 3 if report.get("baseline_drift") else 0
    except (ValueError, OSError, KeyError, TypeError, IndexError, OverflowError,
            struct.error, zipfile.BadZipFile, RuntimeError, RecursionError) as error:
        print(f"Source handoff failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
