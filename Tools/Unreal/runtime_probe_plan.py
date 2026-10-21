"""Validate inputs for a fresh native runtime probe. This module does not run Unreal.

Receipt and package checks only establish consistency with a prior bootstrap.
The editor builder must still load each native asset and verify its real type,
skeleton and source-clock policy. Unit-test packages are synthetic bytes.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import sys

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "Content/Python"))
sys.path.insert(0, str(PROJECT / "Tools/Playback"))
from squadpad_plan import plan_import, sha256_file
from compile_playback import compile_documents, validate_bindings_with_source_files
from evaluate_playback import PlaybackPlan, document_sha256


class RuntimeProbeError(ValueError):
    """A runtime probe prerequisite is absent or inconsistent."""


def require(condition, message):
    if not condition:
        raise RuntimeProbeError(message)


def _number(value, label):
    require(not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value),
            label + " must be finite")
    return float(value)


def _hash(value, label):
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value), label + " is not a SHA-256")
    return value


def _read_strict_json(path):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "Duplicate JSON member: " + key)
            result[key] = value
        return result

    def validate_finite(value):
        if isinstance(value, float):
            require(math.isfinite(value), "JSON contains a non-finite number")
        elif isinstance(value, dict):
            for item in value.values():
                validate_finite(item)
        elif isinstance(value, list):
            for item in value:
                validate_finite(item)

    result = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    validate_finite(result)
    return result


def _object_path(value, namespace):
    require(isinstance(value, str) and re.fullmatch(r"/Game/(?:[A-Za-z0-9_]+/)*[A-Za-z0-9_]+\.[A-Za-z0-9_]+", value),
            "Native object path is invalid")
    package, name = value.rsplit(".", 1)
    require(package.rsplit("/", 1)[-1] == name, "Object and package names differ")
    require(package.startswith(namespace + "/"), "Native object escaped the bootstrap namespace")
    return value


def _package_file(root, package, extension):
    require(isinstance(package, str) and re.fullmatch(r"/Game/(?:[A-Za-z0-9_]+/)*[A-Za-z0-9_]+", package),
            "Native package path is invalid")
    require(extension in (".uasset", ".umap"), "Unsupported native package extension")
    path = (root / "Content" / (package[len("/Game/"):] + extension)).resolve()
    require(path.is_relative_to(root / "Content"), "Native package escaped Content")
    require(path.is_file(), "Required native package is absent: " + str(path))
    return path


def _current_package(root, object_path):
    package = object_path.split(".", 1)[0]
    path = _package_file(root, package, ".uasset")
    return {"package": package, "relative_file": path.relative_to(root).as_posix(),
            "sha256": sha256_file(path), "size_bytes": path.stat().st_size}


def verify_pose_response(samples, required_motions, threshold_cm=0.1):
    """Require evaluated bone movement within each first-pass active clip.

    Remove the sampled actor's world translation/yaw before comparing bones so
    actor travel cannot disguise an unchanged reference pose. This tests native
    pose response only, not correct anatomy, deformation or football contact.
    """
    require(_number(threshold_cm, "Pose response threshold") > 0, "Pose threshold must be positive")
    checks = []
    for actor_id, motion_ids in required_motions.items():
        require(bool(motion_ids), "No active pose requirement for actor " + actor_id)
        for motion_id in motion_ids:
            frames = []
            for sample in samples:
                rows = [a for a in sample["actors"] if a["id"] == actor_id and a["source_motion_id"] == motion_id]
                require(len(rows) <= 1, "Duplicate actor pose sample")
                if not rows:
                    continue
                row = rows[0]
                yaw = math.radians(_number(row["yaw_degrees"], "Sample yaw"))
                c, s = math.cos(yaw), math.sin(yaw)
                origin = [_number(v, "Sample origin") for v in row["position_cm"]]
                require(len(origin) == 3, "Sample origin must have three coordinates")
                local = {}
                for bone, point in row["review_bone_positions_cm"].items():
                    require(len(point) == 3, "Sample bone must have three coordinates")
                    x, y, z = [_number(v, "Sample bone") - o for v, o in zip(point, origin)]
                    local[bone] = [c*x + s*y, -s*x + c*y, z]
                frames.append((sample["time_seconds"], row, local))
            best = None
            pair_count = 0
            for i, (time_a, row_a, pose_a) in enumerate(frames):
                for time_b, row_b, pose_b in frames[i+1:]:
                    if (abs(row_a["source_time_seconds"] - row_b["source_time_seconds"]) <= 1e-6 or
                            abs(row_a["native_sample_seconds"] - row_b["native_sample_seconds"]) <= 1e-6):
                        continue
                    require(pose_a and pose_a.keys() == pose_b.keys(), "Reviewed bone coverage differs")
                    pair_count += 1
                    for bone, point in pose_a.items():
                        displacement = math.dist(point, pose_b[bone])
                        if best is None or displacement > best["max_displacement_cm"]:
                            best = {"actor_id": actor_id, "source_motion_id": motion_id, "bone": bone,
                                    "max_displacement_cm": displacement,
                                    "time_pair_seconds": [time_a, time_b],
                                    "native_sample_pair_seconds": [row_a["native_sample_seconds"], row_b["native_sample_seconds"]],
                                    "actor_local_pair_cm": [point, pose_b[bone]]}
            require(best is not None, "No distinct native samples for active clip " + actor_id + ":" + motion_id)
            require(best["max_displacement_cm"] > threshold_cm,
                    "Native reviewed bones did not respond to active clip " + actor_id + ":" + motion_id)
            best["compared_sample_pairs"] = pair_count
            checks.append(best)
    require(bool(checks), "No active native pose checks were defined")
    return {"status": "passed", "actor_local_threshold_cm": threshold_cm, "checks": checks,
            "scope": "Actual reviewed bones changed within each required active clip after world travel/yaw removal. No visual/contact acceptance."}


def validate_bootstrap_receipt(receipt, source_plan, root):
    """Check metadata and disk hashes. Native class/pose checks belong to Unreal."""
    require(isinstance(receipt, dict), "Bootstrap receipt must be an object")
    require(receipt.get("schema") == "squadpad.unreal.bootstrap-receipt.v1", "Unsupported bootstrap receipt")
    require(receipt.get("status") == "native_import_save_readback_passed" and
            receipt.get("unreal_execution_verified") is True, "Bootstrap has no successful native import/readback")
    readback = receipt.get("readback")
    require(isinstance(readback, dict) and readback.get("status") == "passed", "Bootstrap readback did not pass")
    engine = receipt.get("engine_version")
    require(isinstance(engine, str) and re.match(r"^" + re.escape(source_plan["target_engine"]) + r"(?:\.|-)", engine),
            "Bootstrap engine does not match the source target")
    namespace = receipt.get("asset_namespace")
    require(isinstance(namespace, str) and re.fullmatch(r"/Game/SquadPad/Bootstrap/[A-Za-z][A-Za-z0-9_]*", namespace),
            "Bootstrap namespace is invalid")
    stages = receipt.get("stages")
    require(isinstance(stages, list) and all(isinstance(s, dict) for s in stages), "Bootstrap stages are absent")
    required_stages = {"input_validation", "editor_environment", "fbx_import", "saved_native_packages", "reopened_native_readback"}
    for name in required_stages:
        matches = [s for s in stages if s.get("stage") == name]
        require(len(matches) == 1 and matches[0].get("status") == "passed", "Missing or ambiguous bootstrap stage: " + name)
    incoming = receipt.get("input")
    require(isinstance(incoming, dict), "Bootstrap input record is absent")
    for field in ("manifest_sha256", "fixture_sha256", "fixture_id"):
        require(incoming.get(field) == source_plan[field], "Bootstrap has stale " + field)
    export = incoming.get("export_evidence")
    require(isinstance(export, dict) and export.get("sha256") == source_plan["export_evidence"]["sha256"],
            "Bootstrap export receipt differs from current sources")
    source_rows = incoming.get("sources")
    require(isinstance(source_rows, list) and all(isinstance(x, dict) for x in source_rows), "Bootstrap source inventory is absent")
    source_index = {(x.get("kind"), x.get("id")): x.get("sha256") for x in source_rows}
    require(len(source_index) == len(source_rows), "Bootstrap source inventory has duplicate IDs")
    current_sources = {(x["kind"], x["id"]): x["sha256"] for x in source_plan["sources"]}
    require(source_index == current_sources, "Bootstrap source hashes differ from current validated files")
    scene = receipt.get("scene")
    require(isinstance(scene, dict), "Bootstrap scene record is absent")
    map_path = scene.get("map_path")
    require(isinstance(map_path, str) and map_path.startswith(namespace + "/"), "Bootstrap map escaped its namespace")
    sequence_path = _object_path(scene.get("sequence_path"), namespace)
    expected_files = {map_path: ".umap", sequence_path.split(".", 1)[0]: ".uasset"}
    native_files = receipt.get("native_files")
    require(isinstance(native_files, list) and all(isinstance(x, dict) for x in native_files), "Bootstrap native package inventory is absent")
    native_index = {x.get("package"): x for x in native_files}
    require(len(native_index) == len(native_files) and set(native_index) == set(expected_files),
            "Bootstrap native package inventory differs from its map/sequence")
    checked_files = []
    for package, extension in expected_files.items():
        row = native_index[package]
        path = _package_file(root, package, extension)
        require(sha256_file(path) == _hash(row.get("sha256"), "Native package hash"), "Bootstrap native package changed: " + package)
        require(type(row.get("size_bytes")) is int and path.stat().st_size == row["size_bytes"], "Bootstrap package size differs: " + package)
        checked_files.append({"package": package, "relative_file": path.relative_to(root).as_posix(),
                              "sha256": row["sha256"], "size_bytes": row["size_bytes"]})
    imports = receipt.get("imports")
    require(isinstance(imports, list) and all(isinstance(x, dict) for x in imports), "Bootstrap imports are absent")
    native_index = {(x.get("kind"), x.get("id")): x for x in imports}
    require(len(native_index) == len(imports), "Bootstrap imports have duplicate IDs")
    wanted = {("SkeletalMesh", k) for k in source_plan["meshes"]} | {("AnimSequence", k) for k in source_plan["animations"]}
    require(set(native_index) == wanted, "Bootstrap imports do not match the current baseline")
    meshes, animations, current_native = {}, {}, {}
    tolerance = 1.0 / source_plan["frame_rate"] + 1e-6
    for kind, records, output in (("SkeletalMesh", source_plan["meshes"], meshes),
                                  ("AnimSequence", source_plan["animations"], animations)):
        for ident, source in records.items():
            native = native_index[kind, ident]
            require(native.get("source_sha256") == source["sha256"], "Native import source differs: " + ident)
            path = _object_path(native.get("path"), namespace)
            skeleton = _object_path(native.get("skeleton"), namespace)
            record = {"id": ident, "path": path, "skeleton": skeleton, "source_sha256": source["sha256"]}
            if kind == "AnimSequence":
                length = _number(native.get("duration_seconds"), "Native animation duration")
                expected = source["duration_seconds"]
                require(length > 0 and abs(length - expected) <= tolerance, "Native animation duration differs: " + ident)
                require(skeleton == meshes[source["mesh_id"]]["skeleton"], "Native animation uses a different skeleton: " + ident)
                require(type(native.get("bone_track_count")) is int and native["bone_track_count"] >= 15,
                        "Native animation has insufficient bone tracks: " + ident)
                record.update(duration_seconds=length, canonical_duration_seconds=expected,
                              duration_error_seconds=length - expected)
            output[ident] = record
            for object_path in (path, skeleton):
                current_native[object_path] = _current_package(root, object_path)
    imported_files = receipt.get("imported_native_files")
    require(isinstance(imported_files, list) and all(isinstance(x, dict) for x in imported_files),
            "Bootstrap imported-native-package provenance is absent. Rerun the current native bootstrap to record imported package hashes.")
    imported_index = {x.get("package"): x for x in imported_files}
    current_packages = {x["package"]: x for x in current_native.values()}
    require(len(imported_index) == len(imported_files) and set(imported_index) == set(current_packages),
            "Bootstrap imported native package inventory must cover exactly the bound meshes, animations and Skeletons")
    for package, current in current_packages.items():
        recorded = imported_index[package]
        require(current["sha256"] == _hash(recorded.get("sha256"), "Imported native package hash"),
                "Bootstrap imported native package changed: " + package)
        require(type(recorded.get("size_bytes")) is int and current["size_bytes"] == recorded["size_bytes"],
                "Bootstrap imported native package size differs: " + package)
    return {"scope": "Receipt/source consistency and imported-package byte identity against bootstrap hashes; native classes and poses still require editor verification",
            "engine_version": engine, "asset_namespace": namespace, "checked_bootstrap_packages": checked_files,
            "meshes": meshes, "animations": animations,
            "current_native_asset_packages": [current_native[k] for k in sorted(current_native)]}


def plan_runtime_probe(bootstrap_receipt_path, project_root=PROJECT, playback_path=None, binding_path=None):
    root = Path(project_root).resolve()
    receipt_path = Path(bootstrap_receipt_path).expanduser().resolve()
    require(receipt_path.is_file(), "Successful native bootstrap receipt is required: " + str(receipt_path))
    playback_path = Path(playback_path or root / "Fixtures/Playback/first_pass.playback.json").resolve()
    binding_path = Path(binding_path or root / "Fixtures/Playback/athlete_a_baseline.bindings.json").resolve()
    source_plan = plan_import(root / "import_manifest.json", root, require_files=True)
    playback, bindings = _read_strict_json(playback_path), _read_strict_json(binding_path)
    validate_bindings_with_source_files(playback, bindings, root / "import_manifest.json")
    expected_playback, expected_bindings = compile_documents(root / "import_manifest.json")
    require(playback == expected_playback and bindings == expected_bindings, "Portable baseline no longer reproduces current validated sources")
    bootstrap = validate_bootstrap_receipt(_read_strict_json(receipt_path), source_plan, root)
    mesh_ids = {v["name"]: k for k, v in source_plan["meshes"].items()}
    animation_ids = {v["name"]: k for k, v in source_plan["animations"].items()}
    profiles = []
    for profile in bindings["profiles"]:
        mesh = bootstrap["meshes"][mesh_ids[profile["mesh_asset_id"]]]
        motions = []
        for motion in profile["animations"]:
            animation = bootstrap["animations"][animation_ids[motion["animation_asset_id"]]]
            motions.append(dict(animation, source_motion_id=motion["source_motion_id"]))
        profiles.append({"profile_id": profile["id"], "mesh": mesh, "motions": motions})
    sample_times = {0.0, playback["duration_seconds"]}
    for contact in playback["contacts"]:
        sample_times.add(contact["time_seconds"])
        sample_times.add(max(0.0, contact["time_seconds"] - 1 / 60))
        sample_times.add(min(playback["duration_seconds"], contact["time_seconds"] + 1 / 60))
    for actor in playback["actors"]:
        sample_times.update(s["start_seconds"] for s in actor["sections"])
    reference = PlaybackPlan(playback)
    return {"schema": "squadpad.unreal.runtime-probe-plan.v1", "status": "inputs_ready_for_native_probe",
            "project_root": str(root), "bootstrap_receipt_path": str(receipt_path),
            "bootstrap_receipt_sha256": sha256_file(receipt_path), "bootstrap": bootstrap,
            "playback_path": str(playback_path), "binding_path": str(binding_path),
            "playback_file_sha256": sha256_file(playback_path), "binding_file_sha256": sha256_file(binding_path),
            "playback_sha256": document_sha256(playback), "playback": playback,
            "playback_json": playback_path.read_text(encoding="utf-8"), "profiles": profiles,
            "appearance_bindings": bindings["slots"], "camera": source_plan.get("camera"),
            "source_plan": source_plan, "sample_frames": [reference.evaluate(t) for t in sorted(sample_times)],
            "pose_response_requirements": {a["id"]: sorted({s["source_motion_id"] for s in a["sections"]
                                                         if s["source_motion_id"] != "canonical.idle"})
                                           for a in playback["actors"]},
            "native_execution_this_call": False, "cooked_playback_verified": False, "ios_verified": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstrap-receipt", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=PROJECT)
    args = parser.parse_args(argv)
    try:
        plan = plan_runtime_probe(args.bootstrap_receipt, args.project_root)
        print(json.dumps({"status": plan["status"], "playback_sha256": plan["playback_sha256"],
                          "profiles": len(plan["profiles"]), "sample_frames": len(plan["sample_frames"]),
                          "native_execution_this_call": False}, indent=2))
        return 0
    except (ValueError, OSError, TypeError, KeyError) as exc:
        parser.exit(2, "Runtime probe preparation failed: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
