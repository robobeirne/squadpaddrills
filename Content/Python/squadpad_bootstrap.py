"""Build a first SquadPad review map in the Unreal Editor.

Public entry point: build(manifest_path, project_root=None, run_name=None,
                          receipt_path=None, open_editor=True).

This is an asset-only editor script, not a runtime app or a simulation. It imports
the existing athlete skeleton and same-skeleton FBX clips, then writes native
Animation Sequences, a Level Sequence and a saved map with an autoplay actor.
Actual editor execution is required. Import/save/readback is never reported as
visual acceptance, calibrated contact or a successfully played runtime sequence.
See Tools/Unreal/README.md for assumptions and the API reference baseline.
"""

from __future__ import annotations

from datetime import datetime, timezone
import importlib
import json
import math
from pathlib import Path
import re
import sys
import traceback
import uuid

from squadpad_plan import InputError, frame_time, plan_import, sha256_file


class EditorBuildError(RuntimeError):
    """Required native import, serialization or binding validation failed."""


def _now():
    return datetime.now(timezone.utc).isoformat()


def _require(condition, message):
    if not condition:
        raise EditorBuildError(message)


def _write_receipt(path, receipt):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(receipt, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _path(obj):
    return obj.get_path_name() if obj is not None else None


def _v(unreal, values):
    return unreal.Vector(*values)


def _r(unreal, values):
    return unreal.Rotator(pitch=values["pitch"], yaw=values["yaw"], roll=values["roll"])


def _xyz(value):
    return [float(value.x), float(value.y), float(value.z)]


def _rate(rate):
    return float(rate.numerator) / float(rate.denominator)


def _stage(receipt, name, details=None):
    receipt["stages"].append({"stage": name, "status": "passed", "time_utc": _now(),
                              "details": details or {}})


def _import_fbx(unreal, record, destination, settings, skeleton=None):
    """Use one explicitly configured legacy FBX factory, with no replacement."""
    animation_only = skeleton is not None
    options = unreal.FbxImportUI()
    options.set_editor_property("automated_import_should_detect_type", False)
    options.set_editor_property("import_as_skeletal", True)
    options.set_editor_property("import_mesh", not animation_only)
    options.set_editor_property("import_animations", animation_only)
    options.set_editor_property("import_materials", not animation_only)
    options.set_editor_property("import_textures", not animation_only)
    options.set_editor_property("create_physics_asset", False)
    options.set_editor_property("mesh_type_to_import", unreal.FBXImportType.FBXIT_ANIMATION
                                if animation_only else unreal.FBXImportType.FBXIT_SKELETAL_MESH)
    if animation_only:
        options.set_editor_property("skeleton", skeleton)
        options.set_editor_property("override_animation_name", record["name"])
    data = options.get_editor_property("anim_sequence_import_data" if animation_only
                                       else "skeletal_mesh_import_data")
    for key in ("convert_scene", "convert_scene_unit", "force_front_x_axis", "import_uniform_scale"):
        data.set_editor_property(key, settings[key])
    # Import-data objects can inherit saved editor preferences. Clear extra
    # offsets explicitly; the axis/unit conversion above must happen only once.
    data.set_editor_property("import_translation", unreal.Vector(0, 0, 0))
    data.set_editor_property("import_rotation", unreal.Rotator(0, 0, 0))
    if animation_only:
        # Source durations and contact clocks use the range written by the FBX
        # exporter. Do not inherit a saved manual range or frame-snapping option.
        exported_time = unreal.FBXAnimationLengthImportType.FBXALIT_EXPORTED_TIME
        data.set_editor_property("animation_length", exported_time)
        data.set_editor_property("snap_to_closest_frame_boundary", False)
        _require(data.get_editor_property("animation_length") == exported_time and
                 data.get_editor_property("snap_to_closest_frame_boundary") is False,
                 "FBX import options did not retain the exported time range without frame snapping")
        data.set_editor_property("import_bone_tracks", True)
        data.set_editor_property("use_default_sample_rate", False)
        data.set_editor_property("custom_sample_rate", 0)
    else:
        data.set_editor_property("update_skeleton_reference_pose", False)
        data.set_editor_property("use_t0_as_ref_pose", False)
    task = unreal.AssetImportTask()
    for key, value in {
        "filename": record["source_path"], "destination_path": destination,
        "destination_name": record["name"], "options": options,
        "factory": unreal.FbxFactory(), "automated": True, "async_": False,
        "replace_existing": False, "replace_existing_settings": False, "save": True,
    }.items():
        task.set_editor_property(key, value)
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    objects = {_path(obj): obj for obj in task.get_objects() if obj is not None}
    for asset_path in task.get_editor_property("imported_object_paths"):
        obj = unreal.load_asset(asset_path)
        if obj is not None:
            objects[_path(obj)] = obj
    wanted = unreal.AnimSequence if animation_only else unreal.SkeletalMesh
    matches = [obj for obj in objects.values() if isinstance(obj, wanted)]
    _require(len(matches) == 1, f"FBX import of {record['id']} produced {len(matches)} "
             f"{wanted.__name__} assets. Expected one. Inspect Unreal Output Log; check FBX "
             "skeleton, animation take, and that Interchange has not replaced FbxFactory.")
    asset = matches[0]
    _require(_path(asset).startswith(destination + "/"), "Importer escaped the new asset folder")
    return asset, sorted(objects)


def _channels(unreal, section):
    found = {}
    for channel in unreal.MovieSceneSectionExtensions.get_all_channels(section):
        name = str(channel.get_editor_property("channel_name"))
        if isinstance(channel, unreal.MovieSceneScriptingDoubleChannel):
            found[name] = channel
    expected = {f"{group}.{axis}" for group in ("Location", "Rotation", "Scale") for axis in "XYZ"}
    _require(expected.issubset(found), "Transform channel names differ from the documented "
             f"nine-channel layout. Got: {sorted(found)}. No positional channel guess was made.")
    return found


def _transform_values(position, rotation, scale):
    # Unreal transform channel rotation X/Y/Z maps to roll/pitch/yaw.
    values = {}
    for group, vector in (("Location", position),
                          ("Rotation", [rotation["roll"], rotation["pitch"], rotation["yaw"]]),
                          ("Scale", scale)):
        values.update({f"{group}.{axis}": value for axis, value in zip("XYZ", vector)})
    return values


def _key_seconds(unreal, key, tick_rate):
    time = key.get_time(time_unit=unreal.MovieSceneTimeUnit.TICK_RESOLUTION)
    return float(time.frame_number.value) / tick_rate


def _range(unreal, section, start, end, tick_rate=24000.0):
    unreal.MovieSceneSectionExtensions.set_range_seconds(section, start, end)
    actual_start = unreal.MovieSceneSectionExtensions.get_start_frame_seconds(section)
    actual_end = unreal.MovieSceneSectionExtensions.get_end_frame_seconds(section)
    _require(abs(actual_start - start) <= 1 / tick_rate + 1e-6 and
             abs(actual_end - end) <= 1 / tick_rate + 1e-6,
             "Section timing shifted by more than one sequence tick; display-frame rounding is not accepted")
    return {"requested_start_seconds": start, "actual_start_seconds": actual_start,
            "start_error_seconds": actual_start - start,
            "requested_end_seconds": end, "actual_end_seconds": actual_end,
            "end_error_seconds": actual_end - end}


def _add_transform(unreal, binding, keys, position, rotation, scale, duration, fps, tick_rate):
    track = binding.add_track(unreal.MovieScene3DTransformTrack)
    section = track.add_section()
    range_record = _range(unreal, section, 0.0, duration)
    channels = _channels(unreal, section)
    defaults = _transform_values(position, rotation, scale)
    for name, value in defaults.items():
        channels[name].set_default(value)
    # Explicit holds cover the entire sequence, including any source key gaps.
    keys = [dict(key) for key in keys]
    if keys[0]["time_seconds"] > 0:
        keys.insert(0, {"time_seconds": 0.0, "position_cm": position, "rotation_deg": rotation})
    if keys[-1]["time_seconds"] < duration:
        keys.append(dict(keys[-1], time_seconds=duration))
    written = []
    for value in keys:
        seconds = value["time_seconds"]
        whole, fraction = frame_time(seconds, fps)
        data = _transform_values(value["position_cm"], value.get("rotation_deg", rotation), scale)
        actual_times = []
        for name, number in data.items():
            key = channels[name].add_key(unreal.FrameNumber(whole), number, sub_frame=fraction,
                                        time_unit=unreal.MovieSceneTimeUnit.DISPLAY_RATE,
                                        interpolation=unreal.MovieSceneKeyInterpolation.LINEAR)
            actual_times.append(_key_seconds(unreal, key, tick_rate))
        _require(max(actual_times) - min(actual_times) < 1 / tick_rate,
                 "Transform channels disagree on key time")
        _require(abs(actual_times[0] - seconds) <= 1 / tick_rate + 1e-7,
                 "Transform key lost fractional timing; requested-to-native error exceeds one sequence tick")
        written.append({"requested_seconds": seconds, "actual_seconds": actual_times[0],
                        "error_seconds": actual_times[0] - seconds, "values": data})
    return {"range": range_record, "defaults": defaults, "keys": written}


def _add_animation(unreal, binding, asset, clip, tick_rate):
    tracks = [t for t in binding.get_tracks() if isinstance(t, unreal.MovieSceneSkeletalAnimationTrack)]
    track = tracks[0] if tracks else binding.add_track(unreal.MovieSceneSkeletalAnimationTrack)
    section = track.add_section()
    end = clip["start_seconds"] + clip["duration_seconds"]
    range_record = _range(unreal, section, clip["start_seconds"], end)
    actual_length = float(asset.get_editor_property("sequence_length"))
    source_end = clip["source_start_seconds"] + clip["duration_seconds"] * clip["play_rate"]
    _require(clip["loop"] or source_end <= actual_length + 1e-6,
             f"Non-looping section would wrap {asset.get_name()}: needs {source_end:.8f}s, "
             f"imported clip contains {actual_length:.8f}s. Pad the source end; do not retime contact.")
    params = section.get_editor_property("params")
    params.set_editor_property("animation", asset)
    params.set_editor_property("force_custom_mode", True)
    params.set_editor_property("reverse", False)
    params.set_editor_property("skip_anim_notifiers", True)
    play_rate = params.get_editor_property("play_rate")
    if hasattr(play_rate, "set_fixed_play_rate"):
        play_rate.set_fixed_play_rate(clip["play_rate"])
        params.set_editor_property("play_rate", play_rate)
    else:
        # Earlier API releases expose a float. The engine check still requires
        # the manifest's target version; this branch does not waive that check.
        params.set_editor_property("play_rate", clip["play_rate"])
    offset_ticks = round(clip["source_start_seconds"] * tick_rate)
    params.set_editor_property("start_frame_offset", unreal.FrameNumber(offset_ticks))
    params.set_editor_property("first_loop_start_frame_offset", unreal.FrameNumber(0))
    params.set_editor_property("end_frame_offset", unreal.FrameNumber(0))
    section.set_editor_property("params", params)
    return {"asset_path": _path(asset), "clip": clip, "range": range_record,
            "source_offset_actual_seconds": offset_ticks / tick_rate,
            "source_offset_error_seconds": offset_ticks / tick_rate - clip["source_start_seconds"]}


def _spawn_mesh(unreal, actors, mesh, label, position, rotation):
    actor = actors.spawn_actor_from_object(mesh, _v(unreal, position), _r(unreal, rotation), transient=False)
    _require(actor is not None, f"Could not spawn {label} from {_path(mesh)}")
    actor.set_actor_label(label)
    return actor


def _disable_physics(unreal, component):
    component.set_simulate_physics(False)
    component.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)


def _plain_material(unreal, destination, name, color):
    material = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        name, destination, unreal.Material, unreal.MaterialFactoryNew())
    _require(material is not None, f"Could not create {name}")
    expression = unreal.MaterialEditingLibrary.create_material_expression(
        material, unreal.MaterialExpressionConstant3Vector, -300, 0)
    expression.set_editor_property("constant", unreal.LinearColor(*color, 1.0))
    _require(unreal.MaterialEditingLibrary.connect_material_property(
        expression, "", unreal.MaterialProperty.MP_BASE_COLOR), "Could not connect material color")
    roughness = unreal.MaterialEditingLibrary.create_material_expression(
        material, unreal.MaterialExpressionConstant, -300, 150)
    roughness.set_editor_property("r", 0.85)
    _require(unreal.MaterialEditingLibrary.connect_material_property(
        roughness, "", unreal.MaterialProperty.MP_ROUGHNESS), "Could not connect roughness")
    unreal.MaterialEditingLibrary.recompile_material(material)
    return material


def _scene_environment(unreal, actors, namespace, plan):
    """A simple review floor and fixed lights; no football physics or baked lighting."""
    green = _plain_material(unreal, namespace + "/Review", "M_Pitch", [0.025, 0.13, 0.055])
    white = _plain_material(unreal, namespace + "/Review", "M_Ball", [0.8, 0.8, 0.8])
    cube = unreal.load_asset("/Engine/BasicShapes/Cube")
    sphere = unreal.load_asset("/Engine/BasicShapes/Sphere")
    _require(cube is not None and sphere is not None, "Engine BasicShapes Cube/Sphere assets are absent")
    floor = _spawn_mesh(unreal, actors, cube, "Review pitch", [0, 0, -5],
                        {"pitch": 0, "yaw": 0, "roll": 0})
    floor.set_actor_scale3d(unreal.Vector(50, 50, 0.1))
    floor_component = floor.get_component_by_class(unreal.StaticMeshComponent)
    floor_component.set_material(0, green)
    _disable_physics(unreal, floor_component)
    for label, rotation, intensity in (("Review key", (-45, -45, 0), 5.0),
                                       ("Review fill", (-35, 135, 0), 1.5)):
        actor = actors.spawn_actor_from_class(unreal.DirectionalLight, unreal.Vector(0, 0, 500),
                                              unreal.Rotator(*rotation), transient=False)
        _require(actor is not None, f"Could not create {label}")
        actor.set_actor_label(label)
        actor.get_component_by_class(unreal.DirectionalLightComponent).set_intensity(intensity)
    return sphere, white, [green, white]


def _make_camera(unreal, actors, sequence, plan, duration):
    positions = [actor["position_cm"] for actor in plan["actors"]]
    center = [sum(p[i] for p in positions) / len(positions) for i in range(3)]
    center[2] += 90.0
    spread = max(math.dist(p[:2], center[:2]) for p in positions)
    distance = max(1300.0, spread * 3.5)
    camera_spec = plan.get("camera") or {}
    target = camera_spec.get("target_cm", center)
    position = camera_spec.get("position_cm", [center[0] + distance * 0.67,
                                                 center[1] - distance * 0.74,
                                                 center[2] + distance * 0.48])
    rotation = unreal.MathLibrary.find_look_at_rotation(_v(unreal, position), _v(unreal, target))
    camera = actors.spawn_actor_from_class(unreal.CameraActor, _v(unreal, position), rotation, transient=False)
    _require(camera is not None, "Could not create review camera")
    camera.set_actor_label("Review camera")
    component = camera.get_component_by_class(unreal.CameraComponent)
    component.set_field_of_view(float(camera_spec.get("field_of_view_degrees", 50.0)))
    component.set_aspect_ratio(16.0 / 9.0)
    binding = sequence.add_possessable(camera)
    binding_id = unreal.MovieSceneObjectBindingID()
    binding_id.set_editor_property("guid", binding.get_id())
    track = sequence.add_track(unreal.MovieSceneCameraCutTrack)
    section = track.add_section()
    timing = _range(unreal, section, 0, duration)
    section.set_editor_property("camera_binding_id", binding_id)
    return camera, binding, {"position_cm": position, "target_cm": target, "range": timing}


def _disk_record(project_root, asset_path, extension):
    package = asset_path.split(".", 1)[0]
    _require(package.startswith("/Game/"), f"Refusing to hash an external package: {package}")
    path = project_root / "Content" / (package[len("/Game/"):] + extension)
    _require(path.is_file(), f"Native package was not written to disk: {path}")
    return {"package": package, "file": str(path), "sha256": sha256_file(path), "size_bytes": path.stat().st_size}


def _imported_native_files(project_root, imports):
    """Hash saved imported assets and their actual Skeleton packages exactly once."""
    _require(imports, "Cannot attest an empty native import inventory")
    packages = set()
    for record in imports:
        _require(record.get("kind") in ("SkeletalMesh", "AnimSequence"),
                 "Unsupported native import kind in package inventory")
        for key in ("path", "skeleton"):
            asset_path = record.get(key)
            _require(isinstance(asset_path, str) and asset_path,
                     f"Native import lacks its {key} package reference")
            packages.add(asset_path.split(".", 1)[0])
    return [_disk_record(project_root, package, ".uasset") for package in sorted(packages)]


def _verify_readback(unreal, sequence, actor_system, expected, autoplay_name, tick_rate):
    """Validate persisted native references and key data, without accepting geometry."""
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    resolved = unreal.SequencerTools.get_bound_objects(
        world, sequence, sequence.get_bindings(), sequence.get_playback_range())
    bound = {str(item.binding_proxy.get_id()): list(item.bound_objects) for item in resolved}
    bindings = {str(binding.get_id()): binding for binding in sequence.get_bindings()}
    verified = []
    for ident, entry in expected.items():
        objects = bound.get(entry["guid"], [])
        _require(len(objects) == 1 and objects[0].get_name() == entry["actor_name"],
                 f"Saved binding {ident} did not resolve to its own actor")
        binding = bindings[entry["guid"]]
        if entry.get("mesh_path"):
            component = objects[0].get_component_by_class(unreal.SkeletalMeshComponent)
            _require(component is not None and _path(component.get_skinned_asset()) == entry["mesh_path"],
                     f"Saved actor {ident} references a different skeletal mesh")
        transform = entry.get("transform")
        if transform:
            tracks = [t for t in binding.get_tracks() if isinstance(t, unreal.MovieScene3DTransformTrack)]
            _require(len(tracks) == 1 and len(tracks[0].get_sections()) == 1,
                     f"Saved transform track missing/duplicated for {ident}")
            channels = _channels(unreal, tracks[0].get_sections()[0])
            for name, channel in channels.items():
                if name not in transform["defaults"]:
                    continue
                keys = channel.get_keys()
                _require(len(keys) == len(transform["keys"]), f"Saved key count changed for {ident}/{name}")
                for key, target in zip(keys, transform["keys"]):
                    _require(abs(_key_seconds(unreal, key, tick_rate) - target["actual_seconds"]) <= 1e-8,
                             f"Saved key time changed for {ident}/{name}")
                    _require(abs(key.get_value() - target["values"][name]) < 1e-4,
                             f"Saved key value changed for {ident}/{name}")
        wanted_animations = entry.get("animations", [])
        tracks = [t for t in binding.get_tracks() if isinstance(t, unreal.MovieSceneSkeletalAnimationTrack)]
        sections = sorted([s for t in tracks for s in t.get_sections()],
                          key=lambda s: unreal.MovieSceneSectionExtensions.get_start_frame_seconds(s))
        _require(len(sections) == len(wanted_animations), f"Saved animation section count changed for {ident}")
        for section, target in zip(sections, wanted_animations):
            params = section.get_editor_property("params")
            _require(_path(params.get_editor_property("animation")) == target["asset_path"],
                     f"Saved animation reference changed for {ident}")
            _require(params.get_editor_property("force_custom_mode"), "Saved animation no longer forces playback")
            actual_start = unreal.MovieSceneSectionExtensions.get_start_frame_seconds(section)
            actual_end = unreal.MovieSceneSectionExtensions.get_end_frame_seconds(section)
            _require(abs(actual_start - target["range"]["actual_start_seconds"]) <= 1e-6,
                     f"Saved animation start changed for {ident}")
            _require(abs(actual_end - target["range"]["actual_end_seconds"]) <= 1e-6,
                     f"Saved animation end changed for {ident}")
            rate = params.get_editor_property("play_rate")
            rate = rate.to_fixed_play_rate() if hasattr(rate, "to_fixed_play_rate") else float(rate)
            _require(abs(rate - target["clip"]["play_rate"]) <= 1e-8, f"Saved animation rate changed for {ident}")
            offset = params.get_editor_property("start_frame_offset").value / tick_rate
            _require(abs(offset - target["source_offset_actual_seconds"]) <= 1e-8,
                     f"Saved animation source offset changed for {ident}")
        verified.append({"id": ident, "binding_guid": entry["guid"], "actor_path": _path(objects[0]),
                         "native_animation_sections": len(sections)})
    cuts = [t for t in sequence.get_tracks() if isinstance(t, unreal.MovieSceneCameraCutTrack)]
    _require(len(cuts) == 1 and len(cuts[0].get_sections()) == 1, "Saved camera cut is absent/duplicated")
    cut_id = cuts[0].get_sections()[0].get_editor_property("camera_binding_id").get_editor_property("guid")
    _require(str(cut_id) == expected["review_camera"]["guid"], "Saved camera cut targets a different binding")
    autoplay = [a for a in actor_system.get_all_level_actors() if a.get_name() == autoplay_name]
    _require(len(autoplay) == 1 and isinstance(autoplay[0], unreal.LevelSequenceActor),
             "Saved autoplay LevelSequenceActor did not reopen")
    _require(_path(autoplay[0].get_sequence()) == _path(sequence), "Autoplay actor references another sequence")
    settings = autoplay[0].get_editor_property("playback_settings")
    _require(settings.get_editor_property("auto_play"), "Saved sequence autoplay flag is false")
    return {"status": "passed", "bindings": verified, "autoplay_actor_path": _path(autoplay[0]),
            "scope": "Reopened map, reloaded sequence, bound native assets and serialized channel data. "
                     "This does not establish runtime playback or animation quality."}


def build(manifest_path, project_root=None, run_name=None, receipt_path=None, open_editor=True):
    """Create a new review namespace or fail with a durable, explicit receipt.

    A failed partial build is retained for diagnosis; nothing is reimported or
    deleted. Use a new run name after fixing inputs. Unsaved maps are protected.
    """
    manifest_path = Path(manifest_path).expanduser().resolve()
    root = Path(project_root).expanduser().resolve() if project_root else manifest_path.parent
    run_name = run_name or (datetime.now(timezone.utc).strftime("Run_%Y%m%dT%H%M%SZ_") + uuid.uuid4().hex[:8])
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", run_name):
        raise InputError("run_name must start with a letter and contain only letters, numbers or underscores")
    receipt_path = Path(receipt_path).expanduser().resolve() if receipt_path else root / "Saved/SquadPad/Receipts" / (run_name + ".json")
    if receipt_path.exists():
        raise InputError(f"Receipt already exists; choose a new run name/path: {receipt_path}")
    receipt = {"schema": "squadpad.unreal.bootstrap-receipt.v1", "status": "started", "run_name": run_name,
               "started_utc": _now(), "manifest_path": str(manifest_path), "receipt_path": str(receipt_path),
               "stages": [], "imports": [], "unreal_execution_verified": False,
               "runtime_playback": {"status": "not_run"}, "visual_acceptance": {"status": "not_reviewed"},
               "contact_geometry": {"status": "unmeasured", "accepted": False},
               "native_ios_integration": {"status": "not_run"}}
    unreal = None
    try:
        _write_receipt(receipt_path, receipt)
        plan = plan_import(manifest_path, root, require_files=True)
        # Packaged, required metadata validator. It deliberately does not measure geometry.
        tools_path = str(root / "Tools")
        if tools_path not in sys.path:
            sys.path.insert(0, tools_path)
        quality = importlib.import_module("squadpad_quality")
        contacts_validation = quality.validate_contacts(plan)
        receipt["input"] = {key: plan[key] for key in (
            "manifest_sha256", "fixture_path", "fixture_sha256", "fixture_id", "provenance",
            "selection", "clock", "readiness", "coordinate_transform", "sources", "contacts", "export_evidence")}
        receipt["input"]["contact_metadata_validation"] = contacts_validation
        receipt["input"]["ball_track_status"] = [
            {"id": b["id"], "trajectory_status": b.get("trajectory_status", "unspecified"),
             "post_receive_motion": b.get("post_receive_motion")} for b in plan["balls"]]
        receipt["contact_geometry"]["status"] = "not_evaluated" if not contacts_validation["unmeasured_count"] else "unmeasured"
        _stage(receipt, "input_validation", {"scope": "Source files and metadata only"})
        _write_receipt(receipt_path, receipt)
        try:
            unreal = importlib.import_module("unreal")
        except ImportError as exc:
            raise EditorBuildError("Run build() inside the Unreal Editor Python environment. "
                                   "System Python cannot create native Unreal assets.") from exc
        engine = unreal.SystemLibrary.get_engine_version()
        _require(re.match(r"^" + re.escape(plan["target_engine"]) + r"(?:\.|-)", engine),
                 f"Expected Unreal {plan['target_engine']}; running {engine}")
        running_root = Path(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir())).resolve()
        _require(running_root == root, f"Wrong editor project: {running_root}; expected {root}")
        dirty_maps = unreal.EditorLoadingAndSavingUtils.get_dirty_map_packages()
        _require(not dirty_maps, "The editor has an unsaved map. Save or discard it explicitly before "
                 "running bootstrap; creating the review map would close that level.")
        namespace = "/Game/SquadPad/Bootstrap/" + run_name
        _require(not unreal.EditorAssetLibrary.does_directory_exist(namespace),
                 f"Fresh namespace required; {namespace} already exists. No assets were replaced.")
        receipt["engine_version"] = engine
        receipt["asset_namespace"] = namespace
        receipt["unreal_execution_verified"] = True
        settings = {"convert_scene": True, "convert_scene_unit": True,
                    "force_front_x_axis": True, "import_uniform_scale": 1.0}
        settings.update(plan["import_settings"])
        _require(settings == {"convert_scene": True, "convert_scene_unit": True,
                              "force_front_x_axis": True, "import_uniform_scale": 1.0},
                 "Unexpected import axes/scale. The source exporter and fixture use this exact contract; "
                 "change and revalidate them together before changing import settings.")
        receipt["fbx_import_settings"] = settings
        _stage(receipt, "editor_environment", {"engine": engine, "asset_namespace": namespace})
        meshes, skeletons, animations, owned_assets = {}, {}, {}, []
        for ident, record in plan["meshes"].items():
            mesh, paths = _import_fbx(unreal, record, namespace + "/Meshes/" + record["name"], settings)
            skeleton = mesh.get_editor_property("skeleton")
            _require(isinstance(skeleton, unreal.Skeleton), f"Mesh {ident} has no native Skeleton")
            meshes[ident], skeletons[ident] = mesh, skeleton
            owned_assets.extend(paths)
            receipt["imports"].append({"id": ident, "kind": "SkeletalMesh", "path": _path(mesh),
                                       "skeleton": _path(skeleton), "source_sha256": record["sha256"],
                                       "created_assets": paths})
            _write_receipt(receipt_path, receipt)
        for ident, record in plan["animations"].items():
            animation, paths = _import_fbx(unreal, record, namespace + "/Animations/" + record["name"],
                                           settings, skeletons[record["mesh_id"]])
            _require(_path(animation.get_editor_property("skeleton")) == _path(skeletons[record["mesh_id"]]),
                     f"Animation {ident} did not import onto the existing athlete skeleton")
            length = float(animation.get_editor_property("sequence_length"))
            _require(length > 0 and abs(length - record["duration_seconds"]) <= 1 / plan["frame_rate"] + 1e-6,
                     f"Imported {ident} duration {length} differs from expected {record['duration_seconds']}. "
                     "Check sample rate/take/export range before using contact timings.")
            track_names = [str(n) for n in animation.get_editor_property("animation_track_names")]
            _require(len(track_names) >= 15, f"Animation {ident} has only {len(track_names)} bone tracks")
            animation.set_editor_property("enable_root_motion", False)
            animation.set_editor_property("force_root_lock", False)
            animation.set_editor_property("rate_scale", 1.0)
            animations[ident] = animation
            owned_assets.extend(paths)
            receipt["imports"].append({"id": ident, "kind": "AnimSequence", "path": _path(animation),
                                       "skeleton": _path(animation.get_editor_property("skeleton")),
                                       "duration_seconds": length, "duration_error_seconds": length - record["duration_seconds"],
                                       "bone_track_count": len(track_names), "source_sha256": record["sha256"],
                                       "created_assets": paths})
            _write_receipt(receipt_path, receipt)
        _stage(receipt, "fbx_import", {"meshes": len(meshes), "native_animation_sequences": len(animations),
                                       "scope": "Native types, skeleton references, bone-track presence and duration"})
        editor_level = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
        actor_system = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
        map_path = namespace + "/L_FirstPass"
        _require(editor_level.new_level(map_path), f"Could not create {map_path}")
        sequence = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
            "LS_FirstPass", namespace, unreal.LevelSequence, unreal.LevelSequenceFactoryNew())
        _require(sequence is not None, "Could not create Level Sequence")
        sequence.set_display_rate(unreal.FrameRate(plan["frame_rate"], 1))
        sequence.set_tick_resolution_directly(unreal.FrameRate(24000, 1))
        tick_rate = _rate(sequence.get_tick_resolution())
        duration = plan["duration_seconds"]
        sequence.set_playback_start(0)
        playback_end = math.ceil(duration * plan["frame_rate"])
        sequence.set_playback_end(playback_end)
        # Cover the rounded playback boundary so the camera does not release in
        # a trailing partial display frame. Source action/contact clocks stay intact.
        review_duration = playback_end / plan["frame_rate"]
        receipt["timing"] = {"display_frame_rate": plan["frame_rate"], "tick_resolution": tick_rate,
                             "requested_duration_seconds": duration, "playback_end_frame": playback_end,
                             "playback_end_seconds": review_duration,
                             "trailing_hold_rounding_seconds": review_duration - duration,
                             "key_rounding": "Display-frame subframes converted to integer sequence ticks; individual errors recorded"}
        expected = {}
        for actor_data in plan["actors"]:
            ident = actor_data["id"]
            actor = _spawn_mesh(unreal, actor_system, meshes[actor_data["mesh_id"]],
                                "Player " + actor_data.get("label", ident), actor_data["position_cm"], actor_data["rotation_deg"])
            component = actor.get_component_by_class(unreal.SkeletalMeshComponent)
            _require(component is not None, f"{ident} did not spawn a skeletal mesh component")
            _disable_physics(unreal, component)
            required_bones = plan["meshes"][actor_data["mesh_id"]].get("required_bones", ["Hips", "RightFoot", "RightToeBase"])
            _require(all(component.get_bone_index(bone) >= 0 for bone in required_bones),
                     f"{ident} lacks required athlete bones: {required_bones}")
            origin, extent = actor.get_actor_bounds(False)
            _require(80 <= extent.z * 2 <= 300,
                     f"{ident} imported height {extent.z * 2:.2f}cm is implausible; check unit conversion")
            binding = sequence.add_possessable(actor)
            transform = _add_transform(unreal, binding, actor_data["transform_keys"], actor_data["position_cm"],
                                       actor_data["rotation_deg"], [1, 1, 1], review_duration, plan["frame_rate"], tick_rate)
            action_sections = [dict(clip) for clip in actor_data["clips"]]
            trailing = action_sections[-1]
            if review_duration > duration and trailing.get("generated_idle"):
                trailing["duration_seconds"] += review_duration - duration
                trailing["review_boundary_extension_seconds"] = review_duration - duration
            elif review_duration > duration:
                raise EditorBuildError("Rounded playback boundary requires trailing idle coverage; "
                                       "the bootstrap will not stretch an authored action")
            sections = [_add_animation(unreal, binding, animations[clip["animation_id"]], clip, tick_rate)
                        for clip in action_sections]
            expected[ident] = {"guid": str(binding.get_id()), "actor_name": actor.get_name(),
                               "mesh_path": _path(meshes[actor_data["mesh_id"]]), "transform": transform,
                               "animations": sections, "reference_bounds_extent_cm": _xyz(extent),
                               "root_motion": actor_data.get("root_motion", {"status": "not_requested"})}
        sphere, ball_material, materials = _scene_environment(unreal, actor_system, namespace, plan)
        owned_assets.extend(_path(material) for material in materials)
        for data in plan["balls"]:
            actor = _spawn_mesh(unreal, actor_system, sphere, "Ball " + data["id"], data["position_cm"],
                                {"pitch": 0, "yaw": 0, "roll": 0})
            component = actor.get_component_by_class(unreal.StaticMeshComponent)
            _disable_physics(unreal, component)
            component.set_material(0, ball_material)
            origin, extent = actor.get_actor_bounds(False)
            _require(min(extent.x, extent.y, extent.z) > 0 and max(extent.x, extent.y, extent.z) / min(extent.x, extent.y, extent.z) < 1.01,
                     "Engine sphere bounds are not spherical")
            scale = data["radius_cm"] / extent.x
            actor.set_actor_scale3d(unreal.Vector(scale, scale, scale))
            binding = sequence.add_possessable(actor)
            transform = _add_transform(unreal, binding, data["keys"], data["position_cm"],
                                       {"pitch": 0, "yaw": 0, "roll": 0}, [scale] * 3, review_duration,
                                       plan["frame_rate"], tick_rate)
            expected[data["id"]] = {"guid": str(binding.get_id()), "actor_name": actor.get_name(),
                                    "transform": transform, "radius_cm": data["radius_cm"],
                                    "representation": "Engine sphere; no authored football panels or spin"}
        camera, binding, camera_record = _make_camera(unreal, actor_system, sequence, plan, review_duration)
        expected["review_camera"] = {"guid": str(binding.get_id()), "actor_name": camera.get_name()}
        autoplay = actor_system.spawn_actor_from_class(unreal.LevelSequenceActor, unreal.Vector(0, 0, 0), transient=False)
        _require(autoplay is not None, "Could not create autoplay LevelSequenceActor")
        autoplay.set_actor_label("SquadPad first pass autoplay")
        autoplay.set_sequence(sequence)
        playback = autoplay.get_editor_property("playback_settings")
        for key, value in {"auto_play": True, "play_rate": 1.0, "random_start_time": False,
                           "pause_at_end": True, "disable_camera_cuts": False}.items():
            playback.set_editor_property(key, value)
        autoplay.set_editor_property("playback_settings", playback)
        autoplay_name = autoplay.get_name()
        sequence_path = _path(sequence)
        receipt["scene"] = {"map_path": map_path, "sequence_path": sequence_path, "bindings": expected,
                            "camera": camera_record, "autoplay_actor_name": autoplay_name,
                            "generated_idle_sections": [{"actor_id": a["id"], "clip": c} for a in plan["actors"]
                                                        for c in a["clips"] if c.get("generated_idle")],
                            "transition_quality": "Idle coverage is inserted without authored blending; not accepted"}
        _stage(receipt, "scene_authored")
        owned_assets.append(sequence_path)
        for asset_path in sorted(set(owned_assets)):
            asset = unreal.load_asset(asset_path)
            _require(asset is not None and unreal.EditorAssetLibrary.save_loaded_asset(asset, only_if_is_dirty=False),
                     f"Could not save {asset_path}")
        _require(editor_level.save_current_level(), f"Could not save {map_path}")
        receipt["native_files"] = [_disk_record(root, map_path, ".umap"),
                                   _disk_record(root, sequence_path, ".uasset")]
        _stage(receipt, "saved_native_packages")
        _write_receipt(receipt_path, receipt)
        unreal.LevelSequenceEditorBlueprintLibrary.close_level_sequence()
        reloaded, error = unreal.EditorLoadingAndSavingUtils.reload_packages(
            [sequence.get_outer()], unreal.ReloadPackagesInteractionMode.ASSUME_NEGATIVE)
        _require(reloaded, f"Could not reload saved Level Sequence package: {error}")
        _require(editor_level.load_level(map_path), f"Could not reopen saved level {map_path}")
        sequence = unreal.load_asset(sequence_path)
        _require(isinstance(sequence, unreal.LevelSequence), "Saved Level Sequence could not be loaded")
        receipt["readback"] = _verify_readback(unreal, sequence, actor_system, expected, autoplay_name, tick_rate)
        _stage(receipt, "reopened_native_readback", {"scope": receipt["readback"]["scope"]})
        receipt["editor_preview"] = {"status": "not_requested"}
        if open_editor:
            _require(unreal.LevelSequenceEditorBlueprintLibrary.open_level_sequence(sequence), "Could not open Sequencer UI")
            unreal.LevelSequenceEditorBlueprintLibrary.refresh_current_level_sequence()
            unreal.LevelSequenceEditorBlueprintLibrary.set_lock_camera_cut_to_viewport(True)
            receipt["editor_preview"] = {"status": "opened_for_manual_review", "playback_observed": False}
        # Bind later runtime construction to these saved native bytes, including
        # the shared Skeleton. References and FBX hashes alone cannot detect a
        # native mesh or performance edited after this successful bootstrap.
        receipt["imported_native_files"] = _imported_native_files(root, receipt["imports"])
        receipt["status"] = "native_import_save_readback_passed"
        receipt["finished_utc"] = _now()
        _write_receipt(receipt_path, receipt)
        unreal.log("SquadPad import/save/readback passed. Playback and visual/contact acceptance are still pending. "
                   + str(receipt_path))
        return receipt
    except Exception as exc:
        receipt["status"] = "failed"
        receipt["finished_utc"] = _now()
        receipt["error"] = {"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()}
        receipt["partial_assets_policy"] = "Preserved for diagnosis; no deletion or replacement attempted"
        _write_receipt(receipt_path, receipt)
        if unreal is not None:
            unreal.log_error("SquadPad bootstrap failed: " + str(exc) + ". Receipt: " + str(receipt_path))
        raise
