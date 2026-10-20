#!/usr/bin/env python3
"""Render bounded, read-only shoulder/body comparisons from the real FBX export."""
from __future__ import annotations

import argparse
import copy
import datetime
import json
import math
import tempfile
import time
from pathlib import Path

from render_first_pass import (
    PROJECT, create_actor, digest, evaluate_actor, import_animation,
    import_fbx_objects, material, prepare_plan, require, save_json,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=PROJECT / "import_manifest.json")
    parser.add_argument("--mesh-fbx", type=Path, help="Review an alternate derived mesh with the existing manifest's animations.")
    parser.add_argument("--pose", choices=("rest", "idle", "pass_contact", "receive_contact"), default="rest")
    parser.add_argument("--views", default="threequarter,front")
    parser.add_argument("--variants", default="clothed,body")
    parser.add_argument("--framing", choices=("shoulders", "full"), default="shoulders")
    parser.add_argument("--neutral", action="store_true", help="Use the same neutral material for clothed and body-only views.")
    parser.add_argument("--size", type=int, default=640)
    parser.add_argument("--samples", type=int, default=12)
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args()
    import bpy
    from mathutils import Vector

    started = time.perf_counter()
    require(bpy.app.background, "Use a dedicated cloud-local background process")
    require(320 <= args.size <= 1200 and 1 <= args.samples <= 32 and 1 <= args.threads <= 4, "Unbounded review settings")
    views = args.views.split(",")
    variants = args.variants.split(",")
    directions = {"front": (1, 0, 0), "back": (-1, 0, 0), "side": (0, -1, 0), "threequarter": (-.72, -1, .12)}
    require(views and len(views) <= 4 and all(view in directions for view in views), "Unsupported diagnostic views")
    require(variants and len(variants) <= 2 and all(variant in ("clothed", "body") for variant in variants), "Unsupported diagnostic variants")
    plan = prepare_plan(args.manifest)
    mesh_source = args.mesh_fbx.expanduser().resolve() if args.mesh_fbx else Path(plan["meshes"]["athlete_a"]["source_path"])
    require(mesh_source.is_file(), "Diagnostic mesh FBX is missing")
    output = args.output_dir.resolve()
    require(not output.exists(), "Choose a fresh output directory")
    output.mkdir(parents=True)
    hashes = {row["path"]: row["sha256"] for row in plan["sources"]}
    hashes[plan["manifest_path"]] = plan["manifest_sha256"]
    hashes[plan["fixture_path"]] = plan["fixture_sha256"]
    hashes[str(PROJECT / "SourceInputs/athlete_a.glb")] = digest(PROJECT / "SourceInputs/athlete_a.glb")
    hashes[str(mesh_source)] = digest(mesh_source)
    receipt = {"schema": "squadpad.shoulder-diagnostic.v1", "status": "preparing",
               "scope": "Actual exported athlete geometry and poses. Blender diagnostic, not Unreal evidence or an anatomical correction.",
               "created_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
               "pose": args.pose, "framing": args.framing, "neutral_clothed_material": args.neutral,
               "diagnostic_script_sha256": digest(Path(__file__)),
               "pose_and_import_helper_sha256": digest(Path(__file__).with_name("render_first_pass.py")),
               "settings": {"size": args.size, "samples": args.samples, "threads": args.threads, "engine": "CYCLES", "device": "CPU"},
               "input_sha256": hashes, "export_receipt_sha256": plan["export_evidence"]["sha256"],
               "mesh_source": {"path": str(mesh_source), "sha256": digest(mesh_source), "alternate_derived_fbx": bool(args.mesh_fbx)},
               "actor_global_placement": "Removed for centered anatomical comparison. Bone poses unchanged.", "outputs": []}
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        scene = bpy.context.scene
        scene.render.fps = plan["frame_rate"]
        scene.render.fps_base = 1
        scene.unit_settings.system = "METRIC"
        scene.unit_settings.scale_length = 1
        with tempfile.TemporaryDirectory(prefix="squadpad-shoulders-") as temporary:
            temporary = Path(temporary)
            objects, reference = import_fbx_objects(bpy, mesh_source, False, temporary, "mesh")
            original_names = {obj: obj.name for obj in objects}
            if args.mesh_fbx:
                baseline_objects, baseline = import_fbx_objects(bpy, plan["meshes"]["athlete_a"]["source_path"], False, temporary, "baseline_reference")
                require(set(reference.data.bones.keys()) == set(baseline.data.bones.keys()), "Alternate mesh changes the target bone set")
                error = 0.0
                for bone in reference.data.bones:
                    other = baseline.data.bones[bone.name]
                    require((bone.parent.name if bone.parent else None) == (other.parent.name if other.parent else None), "Alternate mesh changes a bone parent")
                    error = max(error, max(abs(bone.matrix_local[row][column] - other.matrix_local[row][column]) for row in range(4) for column in range(4)))
                object_error = max(abs(reference.matrix_world[row][column] - baseline.matrix_world[row][column]) for row in range(4) for column in range(4))
                require(error <= .0001 and object_error <= .0001, "Alternate mesh changes the rest rig or armature object basis")
                receipt["alternate_skeleton_check"] = {"bone_count": len(reference.data.bones), "rest_matrix_max_element_error": error, "armature_world_matrix_max_element_error": object_error}
                for obj in baseline_objects:
                    bpy.data.objects.remove(obj, do_unlink=True)
            actor_plan = copy.deepcopy(plan["actors"][0])
            animation = None
            seconds = 0.0
            if args.pose != "rest":
                ident, seconds, fixture_seconds = {"idle": ("idle", 0.0, 0.0),
                                                   "pass_contact": ("pass", 1.39262125, 1.39262125),
                                                   "receive_contact": ("receive", .57187, 2.43762125)}[args.pose]
                record = plan["animations"][ident]
                animation = import_animation(bpy, record, temporary, plan["frame_rate"], reference)
                receipt["fixture_scene_time_seconds"] = fixture_seconds
                actor_plan["clips"] = [{"animation_id": ident, "start_seconds": 0, "duration_seconds": record["duration_seconds"],
                                         "source_start_seconds": 0, "play_rate": 1, "loop": ident == "idle"}]
            actor = create_actor(bpy, objects, actor_plan)
            if args.pose == "rest":
                actor["armature"].data.pose_position = "REST"
                receipt["pose_evaluation"] = "True target skeleton rest pose, no animation"
            else:
                frame = seconds * plan["frame_rate"]
                scene.frame_set(math.floor(frame), subframe=frame % 1)
                receipt["pose_evaluation"] = evaluate_actor(actor, {animation["id"]: animation}, seconds)
            actor["root"].location = (0, 0, 0)
            actor["root"].rotation_euler = (0, 0, 0)
            meshes = actor["meshes"]
            def is_kit(obj):
                name = original_names[obj]
                return name.startswith(("Kit_", "Crest", "Number"))
            require(sum(is_kit(obj) for obj in meshes) == 7, "Unexpected kit component set, inspect before hiding")
            receipt["mesh_components"] = [{"name": original_names[obj], "vertices": len(obj.data.vertices), "hidden_for_body_view": is_kit(obj)} for obj in meshes]
            scene.render.engine = "CYCLES"
            scene.cycles.device = "CPU"
            scene.cycles.samples = args.samples
            scene.cycles.use_denoising = True
            scene.render.threads_mode, scene.render.threads = "FIXED", args.threads
            scene.render.resolution_x = scene.render.resolution_y = args.size
            scene.render.resolution_percentage = 100
            scene.render.image_settings.file_format = "PNG"
            scene.render.image_settings.color_mode = "RGB"
            scene.render.use_motion_blur = False
            scene.view_settings.view_transform = "AgX"
            scene.world = bpy.data.worlds.new("DiagnosticWorld")
            scene.world.use_nodes = True
            scene.world.node_tree.nodes["Background"].inputs["Color"].default_value = (.35, .35, .35, 1)
            scene.world.node_tree.nodes["Background"].inputs["Strength"].default_value = .45
            neutral = material(bpy, "DiagnosticNeutral", (.42, .42, .42), .72)
            data = bpy.data.cameras.new("DiagnosticCamera")
            camera = bpy.data.objects.new(data.name, data)
            bpy.context.collection.objects.link(camera)
            scene.camera = camera
            data.type = "ORTHO"
            data.ortho_scale = 1.15 if args.framing == "shoulders" else 2.2
            target = Vector((0, 0, 1.36 if args.framing == "shoulders" else .9))
            receipt["camera_contract"] = {"model_forward": "+X", "target_m": list(target), "orthographic_scale_m": data.ortho_scale,
                                          "distance_m": 4, "view_directions": {view: directions[view] for view in views}}
            lights = []
            for name, energy, size in (("Key", 330, 3), ("Fill", 190, 3)):
                light = bpy.data.lights.new(name, "AREA")
                light.energy, light.shape, light.size = energy, "DISK", size
                obj = bpy.data.objects.new(name, light)
                bpy.context.collection.objects.link(obj)
                lights.append(obj)
            scene.render.use_stamp = scene.render.use_stamp_note = True
            scene.render.stamp_font_size = 11
            for key in ("use_stamp_date", "use_stamp_time", "use_stamp_render_time", "use_stamp_frame", "use_stamp_scene", "use_stamp_camera", "use_stamp_filename", "use_stamp_marker"):
                setattr(scene.render, key, False)
            for view in views:
                direction = Vector(directions[view]).normalized()
                camera.location = target + direction * 4
                camera.rotation_euler = (target - camera.location).to_track_quat("-Z", "Y").to_euler()
                right = direction.cross(Vector((0, 0, 1))).normalized()
                for light, side in zip(lights, (-1, 1)):
                    light.location = target + direction * 2.5 + right * side * 1.5 + Vector((0, 0, 1.6))
                    light.rotation_euler = (target - light.location).to_track_quat("-Z", "Y").to_euler()
                for variant in variants:
                    for obj in meshes:
                        obj.hide_render = variant == "body" and is_kit(obj)
                    neutral_active = args.neutral or variant == "body"
                    bpy.context.view_layer.material_override = neutral if neutral_active else None
                    bpy.context.view_layer.update()
                    filename = f"{args.pose}_{view}_{variant}.png"
                    scene.render.stamp_note_text = f"BLENDER DIAGNOSTIC | {args.pose} | {view} | {variant} | {'neutral' if neutral_active else 'original materials'}"
                    scene.render.filepath = str(output / filename)
                    rendered_at = time.perf_counter()
                    bpy.ops.render.render(write_still=True)
                    row = {"file": filename, "view": view, "variant": variant, "neutral_material": neutral_active,
                           "sha256": digest(output / filename), "wall_seconds": round(time.perf_counter() - rendered_at, 6),
                           "camera_world_matrix": [list(row) for row in camera.matrix_world]}
                    receipt["outputs"].append(row)
                    save_json(output / "diagnostic_receipt.json", receipt)
                    print(json.dumps({"rendered": str(output / filename), "index": len(receipt["outputs"]), "total": len(variants) * len(views)}), flush=True)
        receipt["inputs_unchanged"] = all(digest(path) == expected for path, expected in hashes.items())
        require(receipt["inputs_unchanged"], "A source input changed during diagnostics")
        receipt["status"] = "diagnostic_stills_rendered"
        receipt["wall_seconds"] = round(time.perf_counter() - started, 6)
        save_json(output / "diagnostic_receipt.json", receipt)
        print(json.dumps({"status": receipt["status"], "images": len(receipt["outputs"]), "wall_seconds": receipt["wall_seconds"]}), flush=True)
    except Exception as error:
        receipt["status"], receipt["error"] = "failed", f"{type(error).__name__}: {error}"
        save_json(output / "diagnostic_receipt.json", receipt)
        raise


if __name__ == "__main__":
    main()
