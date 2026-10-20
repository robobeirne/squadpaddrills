#!/usr/bin/env python3
"""Render the exported first pass in a fresh, cloud-local Blender Python process.

Outputs are Blender asset previews. They are not Unreal runtime evidence or
accepted football contacts. Input FBXs, root descriptors and uploaded assets are
read-only. The scene uses no physics or new animation.

Example with the isolated bpy interpreter:
  /path/to/bpy-python render_first_pass.py --output-dir /new/preview --event kick
  /path/to/bpy-python render_first_pass.py --output-dir /new/frames --frames --fps 24
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import re
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "Content/Python"))
from squadpad_plan import _sample_keys, plan_import  # noqa: E402

TRANSFORM_PATH = re.compile(r'^(?:pose\.bones\["(?:[^"\\]|\\.)+"\]\.)?(?:location|rotation_euler|rotation_quaternion|rotation_axis_angle|scale)$')
LABEL = "BLENDER ASSET PREVIEW | Contact unmeasured | Unreal playback unverified"


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save_json(path, value):
    temporary = Path(path).with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def fixture_point_to_blender(position_cm):
    """Convert the left-handed UE world convention to Blender right-handed metres."""
    return (position_cm[0] / 100, -position_cm[1] / 100, position_cm[2] / 100)


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=PROJECT / "import_manifest.json")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--event", choices=("kick", "receive"), default="kick")
    parser.add_argument("--time-seconds", type=float)
    parser.add_argument("--frames", action="store_true", help="Render regularly sampled PNG frames; no video encoder is launched.")
    parser.add_argument("--fps", type=int, default=24, choices=(24, 30))
    parser.add_argument("--width", type=int, default=960)
    parser.add_argument("--height", type=int, default=540)
    parser.add_argument("--samples", type=int, default=24)
    parser.add_argument("--threads", type=int, default=4)
    return parser.parse_args(argv)


def prepare_plan(manifest):
    plan = plan_import(manifest, require_files=True)
    require(plan["fixture_id"] == "rondo-4v1.level-1.segment-0.pass-receive",
            "This renderer supports only the first-pass fixture")
    require({row["id"] for row in plan["actors"]} == {"A", "B"} and len(plan["balls"]) == 1,
            "Expected two named athletes and one ball")
    require(plan["export_evidence"]["status"] == "structural_export_receipt_checked",
            "A successful, hash-matched export receipt is required")
    for actor in plan["actors"]:
        require(actor["root_motion"].get("application_count") == 1
                and actor["root_motion"].get("status") == "composed_from_export_descriptor",
                "Actor root movement must be composed exactly once by the shared planner")
        require(all(abs(key["rotation_deg"][axis]) < 1e-8 for key in actor["transform_keys"]
                    for axis in ("pitch", "roll")), "Preview currently supports yaw-only actor guides")
    return plan


def import_fbx_objects(bpy, source_path, use_animation, temporary, label):
    # Blender's FBX importer may extract embedded textures beside its input.
    # Import a private copy so source-export directories cannot receive helpers.
    directory = temporary / label
    directory.mkdir()
    copied = directory / Path(source_path).name
    shutil.copyfile(source_path, copied)
    require(digest(copied) == digest(source_path), "FBX copy changed before import")
    before = set(bpy.data.objects)
    bpy.ops.import_scene.fbx(filepath=str(copied), automatic_bone_orientation=False,
                             use_anim=use_animation, ignore_leaf_bones=False)
    objects = list(set(bpy.data.objects) - before)
    armatures = [obj for obj in objects if obj.type == "ARMATURE"]
    require(len(armatures) == 1, f"Expected one armature in {label}")
    require(not any(obj.type in {"CAMERA", "LIGHT"} for obj in objects),
            f"Unexpected camera/light in source {label}")
    return objects, armatures[0]


def selected_action_curves(action, slot):
    """Use only the imported armature's slot in a layered Blender 5.2 action."""
    curves = []
    if getattr(action, "layers", None):
        require(slot is not None, "Layered animation has no assigned armature action slot")
        for layer in action.layers:
            for strip in layer.strips:
                for bag in getattr(strip, "channelbags", ()):
                    if bag.slot_handle == slot.handle:
                        curves.extend(bag.fcurves)
    else:
        curves.extend(getattr(action, "fcurves", ()))
    require(curves, "No curves found for the armature's action slot")
    seen = set()
    for curve in curves:
        require(TRANSFORM_PATH.fullmatch(curve.data_path), f"Unsupported animation channel: {curve.data_path}")
        signature = (curve.data_path, curve.array_index)
        require(signature not in seen, f"Multiple layers write the same preview channel: {signature}")
        seen.add(signature)
    return curves


def import_animation(bpy, record, temporary, fps, reference_armature):
    objects, armature = import_fbx_objects(bpy, record["source_path"], True, temporary, record["id"])
    # Real skinned geometry carries FBX skin-cluster rest bind matrices. Keep it
    # during import and validation, then remove this entire temporary import.
    meshes = [obj for obj in objects if obj.type == "MESH"]
    require(meshes, "Animation FBX is missing its skinned bind reference")
    for mesh in meshes:
        require(any(mod.type == "ARMATURE" and mod.object == armature for mod in mesh.modifiers),
                f"Unexpected unskinned animation helper geometry: {mesh.name}")
    require(set(armature.data.bones.keys()) == set(reference_armature.data.bones.keys()),
            f"Animation skeleton differs from target mesh: {record['id']}")
    rest_error = 0.0
    for bone in armature.data.bones:
        reference = reference_armature.data.bones[bone.name]
        require((bone.parent.name if bone.parent else None) == (reference.parent.name if reference.parent else None),
                f"Animation bone parent differs from target mesh: {record['id']} / {bone.name}")
        rest_error = max(rest_error, max(abs(bone.matrix_local[row][column] - reference.matrix_local[row][column])
                                        for row in range(4) for column in range(4)))
    require(rest_error <= 0.0001,
            f"Animation rest matrix differs from target mesh: {record['id']} / maximum element error {rest_error}")
    data = armature.animation_data
    require(data is not None, "Imported animation has no animation data")
    action = data.action
    slot = getattr(data, "action_slot", None)
    if action is None:
        strips = [strip for track in data.nla_tracks for strip in track.strips if strip.action]
        require(len(strips) == 1, "Expected one imported animation action")
        action = strips[0].action
        slot = getattr(strips[0], "action_slot", None)
    action.use_fake_user = True
    curves = selected_action_curves(action, slot)
    start, end = (float(v) for v in action.frame_range)
    actual_duration = (end - start) / fps
    canonical_duration = record["duration_seconds"]
    require(abs(actual_duration - canonical_duration) <= 1 / fps + 1e-5,
            f"Imported animation duration differs from source contract: {record['id']}")
    modes = {bone.name: bone.rotation_mode for bone in armature.pose.bones}
    result = {"id": record["id"], "action": action, "slot": slot, "curves": curves,
              "frame_start": start, "frame_end": end, "import_fps": fps,
              "duration_seconds": actual_duration, "canonical_duration_seconds": canonical_duration,
              "bind_reference_mesh_count": len(meshes), "rest_matrix_max_element_error": rest_error,
              "object_rotation_mode": armature.rotation_mode, "bone_rotation_modes": modes}
    for obj in objects:
        bpy.data.objects.remove(obj, do_unlink=True)
    return result


def duplicate_actor_objects(bpy, objects):
    mapping = {}
    for original in objects:
        copied = original.copy()
        if original.type == "ARMATURE":
            copied.data = original.data.copy()
        bpy.context.collection.objects.link(copied)
        mapping[original] = copied
    for original, copied in mapping.items():
        copied.parent = mapping.get(original.parent)
        copied.matrix_parent_inverse = original.matrix_parent_inverse.copy()
        copied.matrix_basis = original.matrix_basis.copy()
        for modifier in copied.modifiers:
            if modifier.type == "ARMATURE" and modifier.object in mapping:
                modifier.object = mapping[modifier.object]
    return list(mapping.values())


def create_actor(bpy, objects, actor):
    armature = next(obj for obj in objects if obj.type == "ARMATURE")
    meshes = [obj for obj in objects if obj.type == "MESH"]
    require(meshes, "Imported athlete has no meshes")
    for mesh in meshes:
        require(any(mod.type == "ARMATURE" and mod.object == armature for mod in mesh.modifiers),
                f"Unexpected unskinned helper geometry: {mesh.name}")
    root = bpy.data.objects.new(f"Review_Actor_{actor['id']}", None)
    basis = bpy.data.objects.new(f"Review_FBX_Basis_{actor['id']}", None)
    bpy.context.collection.objects.link(root)
    bpy.context.collection.objects.link(basis)
    basis.parent = root
    basis.rotation_euler.z = math.pi / 2
    for obj in objects:
        obj.name = f"{actor['id']}_{obj.name}"
        if obj.parent not in objects:
            original_world = obj.matrix_world.copy()
            obj.parent = basis
            obj.matrix_parent_inverse.identity()
            obj.matrix_basis = original_world
        obj.animation_data_clear()
    armature.data.pose_position = "POSE"
    return {"root": root, "basis": basis, "armature": armature, "meshes": meshes,
            "base_matrix": armature.matrix_basis.copy(), "plan": actor}


def material(bpy, name, colour, roughness=0.65):
    result = bpy.data.materials.new(name)
    result.use_nodes = True
    shader = result.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = (*colour, 1)
    shader.inputs["Roughness"].default_value = roughness
    return result


def setup_environment(bpy, args):
    from mathutils import Vector
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = args.samples
    scene.cycles.use_denoising = True
    scene.render.threads_mode = "FIXED"
    scene.render.threads = args.threads
    scene.render.resolution_x = args.width
    scene.render.resolution_y = args.height
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.film_transparent = False
    scene.render.use_motion_blur = False
    scene.view_settings.view_transform = "AgX"
    scene.world = bpy.data.worlds.new("Review_World")
    scene.world.use_nodes = True
    scene.world.node_tree.nodes["Background"].inputs["Color"].default_value = (.45, .49, .55, 1)
    scene.world.node_tree.nodes["Background"].inputs["Strength"].default_value = .35
    bpy.ops.mesh.primitive_plane_add(size=30, location=(0, 0, -.012))
    floor = bpy.context.object
    floor.name = "Review_Floor"
    floor.data.materials.append(material(bpy, "Review_Floor_Material", (.095, .115, .105)))
    for name, location, energy, size in (("Key", (2, -4, 7), 1400, 5), ("Fill", (-4, 3, 5), 900, 4)):
        light = bpy.data.lights.new(f"Review_{name}", "AREA")
        light.energy, light.shape, light.size = energy, "DISK", size
        obj = bpy.data.objects.new(light.name, light)
        bpy.context.collection.objects.link(obj)
        obj.location = location
        obj.rotation_euler = (Vector((0, 0, .8)) - obj.location).to_track_quat("-Z", "Y").to_euler()
    data = bpy.data.cameras.new("Review_Camera")
    camera = bpy.data.objects.new("Review_Camera", data)
    bpy.context.collection.objects.link(camera)
    camera.location = (8, 8, 5)
    camera.rotation_euler = (Vector((0, 0, .9)) - camera.location).to_track_quat("-Z", "Y").to_euler()
    data.type, data.ortho_scale = "ORTHO", 9.5
    data.lens, data.clip_end = 50, 100
    scene.camera = camera
    scene.render.use_stamp = True
    scene.render.use_stamp_note = True
    scene.render.stamp_note_text = LABEL
    scene.render.stamp_font_size = 12
    for key in ("use_stamp_date", "use_stamp_time", "use_stamp_render_time", "use_stamp_frame",
                "use_stamp_scene", "use_stamp_camera", "use_stamp_filename", "use_stamp_marker"):
        setattr(scene.render, key, False)
    return scene


def active_clip(actor, seconds):
    for clip in actor["clips"]:
        if clip["start_seconds"] <= seconds < clip["start_seconds"] + clip["duration_seconds"]:
            return clip
    return actor["clips"][-1]


def evaluate_actor(actor, animations, seconds):
    data, armature = actor["plan"], actor["armature"]
    transform = _sample_keys(data["transform_keys"], seconds, rotation=True)
    actor["root"].location = fixture_point_to_blender(transform["position_cm"])
    actor["root"].rotation_euler.z = -math.radians(transform["rotation_deg"]["yaw"])
    clip = active_clip(data, seconds)
    animation = animations[clip["animation_id"]]
    elapsed = min(max(seconds - clip["start_seconds"], 0), clip["duration_seconds"])
    source_seconds = clip["source_start_seconds"] + elapsed * clip["play_rate"]
    if clip["loop"]:
        source_seconds %= animation["canonical_duration_seconds"]
    else:
        source_seconds = min(source_seconds, animation["canonical_duration_seconds"])
    source_frame = animation["frame_start"] + source_seconds * animation["import_fps"]
    armature.matrix_basis = actor["base_matrix"].copy()
    armature.rotation_mode = animation["object_rotation_mode"]
    for bone in armature.pose.bones:
        bone.rotation_mode = animation["bone_rotation_modes"][bone.name]
        bone.matrix_basis.identity()
    # Each actor has its own clip clock. Evaluate the imported FBX FCurves in the
    # assigned action slot directly, then let Blender evaluate skin deformation.
    # No action keys are shifted, resampled, blended or written back to the FBX.
    for curve in animation["curves"]:
        value = float(curve.evaluate(source_frame))
        require(math.isfinite(value), "Nonfinite evaluated FBX curve")
        target = armature.path_resolve(curve.data_path)
        require(0 <= curve.array_index < len(target), "Invalid animation channel component")
        target[curve.array_index] = value
    return {"actor_id": data["id"], "animation_id": clip["animation_id"],
            "scene_seconds": seconds, "source_seconds": source_seconds, "source_frame": source_frame,
            "generated_idle": clip.get("generated_idle", False), "root_application_count": 1}


def render_preview(args):
    import bpy
    require(bpy.app.background, "Run in a dedicated headless Blender/bpy process")
    require(320 <= args.width <= 1920 and 180 <= args.height <= 1080, "Preview resolution outside limits")
    require(1 <= args.samples <= 128 and 1 <= args.threads <= 8, "Preview resource settings outside limits")
    plan = prepare_plan(args.manifest)
    output = args.output_dir.expanduser().resolve()
    require(not output.exists(), "Preview output directory already exists; choose a new directory")
    require(not any(output == Path(row["path"]) or output in Path(row["path"]).parents
                    for row in plan["sources"]), "Preview directory must not contain source inputs")
    duration = plan["duration_seconds"]
    if args.frames:
        require(args.time_seconds is None, "A time override is not supported for regular frame output")
        count = math.ceil(duration * args.fps)
        times = [index / args.fps for index in range(count)]
    else:
        selected = next(row for row in plan["contacts"] if row["event"] == args.event)
        selected_time = args.time_seconds if args.time_seconds is not None else selected["time_seconds"]
        require(math.isfinite(selected_time) and 0 <= selected_time <= duration, "Invalid preview time")
        times = [selected_time]
    source_hashes = {row["path"]: row["sha256"] for row in plan["sources"]}
    source_hashes[plan["manifest_path"]] = plan["manifest_sha256"]
    source_hashes[plan["fixture_path"]] = plan["fixture_sha256"]
    output.mkdir(parents=True)
    receipt = {"schema": "squadpad.blender-preview.v1", "status": "preparing", "label": LABEL,
               "blender_version": bpy.app.version_string, "created_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
               "fixture_id": plan["fixture_id"], "fixture_sha256": plan["fixture_sha256"],
               "export_receipt_sha256": plan["export_evidence"]["sha256"],
               "render_settings": {"engine": "CYCLES", "device": "CPU", "threads": args.threads,
                                   "samples": args.samples, "width": args.width, "height": args.height},
               "coordinate_conversion": {"fixture_point": "[UE.X, -UE.Y, UE.Z] / 100",
                                         "actor_parent_yaw_degrees": "-UE.yaw",
                                         "fbx_model_child_rotation_z_degrees": 90,
                                         "mesh_reflection_applied": False,
                                         "scope": "Blender preview convention, not tested Unreal FBX import basis"},
               "duration_seconds": duration, "output_frame_rate": args.fps if args.frames else None,
               "frame_exposure_duration_seconds": len(times) / args.fps if args.frames else None,
               "fbx_imported_from_temporary_copies": True,
               "physics_simulated": False, "contextual_blends_authored": False,
               "contact_geometry_accepted": False, "unreal_execution_verified": False,
               "visual_quality_accepted": False, "outputs": []}
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        require(len(bpy.context.scene.objects) == 0, "Fresh preview scene contains unexpected objects")
        bpy.context.scene.render.fps = plan["frame_rate"]
        bpy.context.scene.render.fps_base = 1
        bpy.context.scene.unit_settings.system = "METRIC"
        bpy.context.scene.unit_settings.scale_length = 1.0
        with tempfile.TemporaryDirectory(prefix="squadpad-preview-import-") as temporary:
            temporary = Path(temporary)
            mesh = plan["meshes"]["athlete_a"]
            objects, reference = import_fbx_objects(bpy, mesh["source_path"], False, temporary, "mesh")
            duplicates = duplicate_actor_objects(bpy, objects)
            animations = {ident: import_animation(bpy, record, temporary, plan["frame_rate"], reference)
                          for ident, record in plan["animations"].items()}
            actors = [create_actor(bpy, objects, plan["actors"][0]), create_actor(bpy, duplicates, plan["actors"][1])]
            scene = setup_environment(bpy, args)
            balls = []
            for record in plan["balls"]:
                bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=record["radius_cm"] / 100)
                ball = bpy.context.object
                ball.name = f"Review_Ball_{record['id']}"
                ball.data.materials.append(material(bpy, "Review_Ball_Material", (.82, .82, .76), .5))
                for polygon in ball.data.polygons:
                    polygon.use_smooth = True
                balls.append((record, ball))
            receipt["animation_imports"] = [{"id": key, "frame_start": row["frame_start"], "frame_end": row["frame_end"],
                                              "duration_seconds": row["duration_seconds"], "curves_in_slot": len(row["curves"]),
                                              "temporary_bind_reference_mesh_count": row["bind_reference_mesh_count"],
                                              "rest_matrix_max_element_error": row["rest_matrix_max_element_error"],
                                              "slot_identifier": row["slot"].identifier if row["slot"] else None}
                                             for key, row in animations.items()]
            for index, seconds in enumerate(times):
                frame_value = seconds * plan["frame_rate"]
                scene.frame_set(math.floor(frame_value), subframe=frame_value % 1)
                evaluated = [evaluate_actor(actor, animations, seconds) for actor in actors]
                for record, ball in balls:
                    ball.location = fixture_point_to_blender(_sample_keys(record["keys"], seconds)["position_cm"])
                bpy.context.view_layer.update()
                scene.render.stamp_note_text = f"{LABEL} | t={seconds:.6f}s"
                filename = f"frame_{index:04d}.png" if args.frames else f"first_pass_{args.event}.png"
                path = output / filename
                scene.render.filepath = str(path)
                bpy.ops.render.render(write_still=True)
                require(path.is_file() and path.stat().st_size > 0, "Render produced no image")
                receipt["outputs"].append({"file": filename, "time_seconds": seconds,
                                           "sha256": digest(path), "bytes": path.stat().st_size,
                                           "actors": evaluated})
                print(json.dumps({"rendered": filename, "time_seconds": seconds, "index": index + 1, "total": len(times)}), flush=True)
            receipt["input_files_unchanged"] = all(digest(path) == expected for path, expected in source_hashes.items())
            require(receipt["input_files_unchanged"], "An input changed during preview rendering")
        receipt["status"] = "blender_asset_preview_rendered"
        save_json(output / "preview_receipt.json", receipt)
        return receipt
    except Exception as error:
        receipt["status"] = "failed"
        receipt["error"] = f"{type(error).__name__}: {error}"
        save_json(output / "preview_failure.json", receipt)
        raise


if __name__ == "__main__":
    result = render_preview(parse_arguments())
    print(json.dumps({"status": result["status"], "images": len(result["outputs"]),
                      "unreal_execution_verified": False, "contact_geometry_accepted": False}), flush=True)
