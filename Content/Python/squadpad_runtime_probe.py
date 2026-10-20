"""Build a saved runtime probe from a real completed native bootstrap.

Run inside the compiled SquadPad3D Unreal Editor. Python prepares assets only.
The saved map uses the C++ runtime actor, with no LevelSequence or Python player.
Editor seek/readback is distinct from cooked playback, Swift and iPhone proof.
"""
from __future__ import annotations

from datetime import datetime, timezone
import importlib
import math
from pathlib import Path
import re
import sys
import traceback
import uuid

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "Tools/Unreal"))
from runtime_probe_plan import RuntimeProbeError, plan_runtime_probe, verify_pose_response
from squadpad_bootstrap import (_disk_record, _now, _path, _require, _scene_environment,
                                _stage, _v, _write_receipt, _xyz)
from squadpad_plan import sha256_file


def _native_assets(unreal, plan):
    assets, verified = {}, []
    for profile in plan["profiles"]:
        mesh_record = profile["mesh"]
        mesh = unreal.load_asset(mesh_record["path"])
        _require(isinstance(mesh, unreal.SkeletalMesh), "Expected native SkeletalMesh: " + mesh_record["path"])
        skeleton = mesh.get_editor_property("skeleton")
        _require(isinstance(skeleton, unreal.Skeleton) and _path(skeleton) == mesh_record["skeleton"],
                 "Native mesh skeleton differs from bootstrap")
        assets[mesh_record["path"]] = mesh
        for motion in profile["motions"]:
            animation = unreal.load_asset(motion["path"])
            _require(isinstance(animation, unreal.AnimSequence), "Expected native AnimSequence: " + motion["path"])
            _require(_path(animation.get_editor_property("skeleton")) == _path(skeleton), "Native animation skeleton differs")
            length = float(animation.get_editor_property("sequence_length"))
            tolerance = 1.0 / plan["source_plan"]["frame_rate"] + 1e-6
            _require(math.isfinite(length) and length > 0 and
                     abs(length - motion["canonical_duration_seconds"]) <= tolerance,
                     "Native animation duration exceeds import tolerance")
            _require(abs(length - motion["duration_seconds"]) <= 1e-6, "Native animation duration changed after bootstrap")
            _require(animation.get_editor_property("enable_root_motion") is False and
                     animation.get_editor_property("force_root_lock") is False and
                     abs(float(animation.get_editor_property("rate_scale")) - 1.0) <= 1e-8,
                     "Native animation root/rate policy changed after bootstrap")
            _require(len(animation.get_editor_property("animation_track_names")) >= 15, "Native animation has insufficient tracks")
            assets[motion["path"]] = animation
            verified.append({"profile_id": profile["profile_id"], "source_motion_id": motion["source_motion_id"],
                             "path": motion["path"], "duration_seconds": length,
                             "canonical_duration_seconds": motion["canonical_duration_seconds"],
                             "endpoint_gap_seconds": max(0.0, motion["canonical_duration_seconds"] - length)})
    return assets, verified


def _create_data(unreal, namespace, plan, assets, sphere, material, radius):
    factory = unreal.DataAssetFactory()
    factory.set_editor_property("data_asset_class", unreal.SquadPadPlaybackDataAsset)
    data = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        "DA_FirstPass", namespace, unreal.SquadPadPlaybackDataAsset, factory)
    _require(isinstance(data, unreal.SquadPadPlaybackDataAsset), "Could not create runtime playback data asset")
    data.set_editor_property("playback_json", plan["playback_json"])
    profiles = []
    for spec in plan["profiles"]:
        profile = unreal.SquadPadAthleteAssetProfile()
        profile.set_editor_property("profile_id", spec["profile_id"])
        profile.set_editor_property("mesh", assets[spec["mesh"]["path"]])
        motions = []
        for item in spec["motions"]:
            motion = unreal.SquadPadMotionAssetBinding()
            motion.set_editor_property("source_motion_id", item["source_motion_id"])
            motion.set_editor_property("animation", assets[item["path"]])
            motions.append(motion)
        profile.set_editor_property("motions", motions)
        profiles.append(profile)
    data.set_editor_property("profiles", profiles)
    slots = []
    for spec in plan["appearance_bindings"]:
        slot = unreal.SquadPadAppearanceAssetBinding()
        slot.set_editor_property("appearance_slot", spec["appearance_slot"])
        slot.set_editor_property("profile_id", spec["profile_id"])
        slots.append(slot)
    data.set_editor_property("appearance_bindings", slots)
    data.set_editor_property("ball_mesh", sphere)
    data.set_editor_property("ball_material", material)
    data.set_editor_property("ball_mesh_radius_cm", radius)
    return data


def _verify_data(unreal, data, plan, sphere_path, material_path, radius):
    _require(isinstance(data, unreal.SquadPadPlaybackDataAsset), "Saved runtime data asset has wrong class")
    _require(data.get_editor_property("playback_json") == plan["playback_json"], "Saved playback JSON changed")
    profiles = list(data.get_editor_property("profiles"))
    _require(len(profiles) == len(plan["profiles"]), "Saved profile count changed")
    for actual, expected in zip(profiles, plan["profiles"]):
        _require(actual.get_editor_property("profile_id") == expected["profile_id"] and
                 _path(actual.get_editor_property("mesh")) == expected["mesh"]["path"], "Saved athlete profile changed")
        motions = list(actual.get_editor_property("motions"))
        _require(len(motions) == len(expected["motions"]), "Saved motion count changed")
        for motion, spec in zip(motions, expected["motions"]):
            _require(motion.get_editor_property("source_motion_id") == spec["source_motion_id"] and
                     _path(motion.get_editor_property("animation")) == spec["path"], "Saved motion reference changed")
    slots = [{"appearance_slot": x.get_editor_property("appearance_slot"),
              "profile_id": x.get_editor_property("profile_id")} for x in data.get_editor_property("appearance_bindings")]
    _require(slots == plan["appearance_bindings"], "Saved appearance slots changed")
    _require(_path(data.get_editor_property("ball_mesh")) == sphere_path and
             _path(data.get_editor_property("ball_material")) == material_path and
             abs(float(data.get_editor_property("ball_mesh_radius_cm")) - radius) <= 1e-5,
             "Saved ball representation changed")


def _camera(unreal, actors, plan):
    positions = [a["position_cm"] for a in plan["source_plan"]["actors"]]
    center = [sum(p[i] for p in positions) / len(positions) for i in range(3)]
    center[2] += 90.0
    spread = max(math.dist(p[:2], center[:2]) for p in positions)
    distance = max(1300.0, spread * 3.5)
    spec = plan["camera"] or {}
    target = spec.get("target_cm", center)
    position = spec.get("position_cm", [center[0] + distance * 0.67, center[1] - distance * 0.74,
                                         center[2] + distance * 0.48])
    rotation = unreal.MathLibrary.find_look_at_rotation(_v(unreal, position), _v(unreal, target))
    actor = actors.spawn_actor_from_class(unreal.CameraActor, _v(unreal, position), rotation, transient=False)
    _require(actor is not None, "Could not create runtime review camera")
    actor.set_actor_label("SquadPad runtime probe camera")
    actor.set_editor_property("tags", [unreal.Name("SquadPadProbeCamera")])
    actor.set_editor_property("auto_activate_for_player", unreal.AutoReceiveInput.PLAYER0)
    camera = actor.get_component_by_class(unreal.CameraComponent)
    camera.set_field_of_view(float(spec.get("field_of_view_degrees", 50.0)))
    camera.set_aspect_ratio(16.0 / 9.0)
    return actor, {"position_cm": position, "target_cm": target, "auto_activate_player_index": 0,
                   "tag": "SquadPadProbeCamera"}


def _finite_vector(value, label):
    result = _xyz(value)
    _require(all(math.isfinite(x) for x in result), label + " contains non-finite values")
    return result


def _close_vector(actual, expected, label, tolerance=1e-3):
    _require(len(actual) == len(expected) and all(abs(a-b) <= tolerance for a, b in zip(actual, expected)), label + " differs")


def _native_sample(unreal, actor, expected, plan):
    time = expected["time_seconds"]
    _require(actor.seek(time), "Runtime seek failed: " + actor.get_last_error())
    _require(abs(actor.get_playback_time() - time) <= 1e-9 and not actor.is_playing(), "Seek changed play state or time")
    result = {"time_seconds": time, "actors": [], "balls": []}
    profiles = {p["profile_id"]: p for p in plan["profiles"]}
    slots = {p["appearance_slot"]: profiles[p["profile_id"]] for p in plan["appearance_bindings"]}
    for target in expected["actors"]:
        ident = target["id"]
        component = actor.get_actor_mesh(ident)
        _require(isinstance(component, unreal.SkeletalMeshComponent), "Runtime actor component is absent: " + ident)
        profile = slots[target["appearance_slot"]]
        _require(_path(component.get_skinned_asset()) == profile["mesh"]["path"], "Runtime mesh binding differs")
        position = _finite_vector(component.get_world_location(), "Actor position")
        _close_vector(position, target["position_cm"], "Actor position")
        rotation = component.get_world_rotation()
        yaw = float(rotation.yaw)
        yaw_error = (yaw - target["yaw_degrees"] + 180.0) % 360.0 - 180.0
        _require(math.isfinite(yaw) and abs(yaw_error) <= 1e-4, "Runtime actor yaw differs")
        source_time = float(actor.get_actor_source_time(ident))
        sampled_time = float(actor.get_actor_sampled_time(ident))
        source = next(m for m in profile["motions"] if m["source_motion_id"] == target["source_motion_id"])
        sampled_animation = actor.get_actor_sampled_animation(ident)
        _require(isinstance(sampled_animation, unreal.AnimSequence) and _path(sampled_animation) == source["path"],
                 "Native sampled animation asset differs")
        _require(actor.get_actor_action(ident) == target["action_id"], "Runtime action boundary differs")
        _require(math.isfinite(source_time) and abs(source_time-target["source_time_seconds"]) <= 1e-9,
                 "Runtime source animation clock differs")
        expected_sample = min(source_time, float(sampled_animation.get_editor_property("sequence_length")))
        _require(math.isfinite(sampled_time) and abs(sampled_time-expected_sample) <= 1e-5, "Native animation sample clock differs")
        _require(not component.is_any_simulating_physics(), "Runtime actor unexpectedly simulates physics")
        bones = {}
        for name in ("Hips", "LeftFoot", "RightFoot", "LeftToeBase", "RightToeBase"):
            _require(component.get_bone_index(name) >= 0, "Native athlete lacks review bone: " + name)
            bones[name] = _finite_vector(component.get_socket_location(name), "Native bone " + name)
        result["actors"].append({"id": ident, "position_cm": position, "yaw_degrees": yaw,
                                 "action_id": target["action_id"], "source_motion_id": target["source_motion_id"],
                                 "native_animation_path": _path(sampled_animation),
                                 "source_time_seconds": source_time, "native_sample_seconds": sampled_time,
                                 "review_bone_positions_cm": bones})
    for target in expected["balls"]:
        component = actor.get_ball_mesh(target["id"])
        _require(isinstance(component, unreal.StaticMeshComponent), "Runtime ball component is absent")
        position = _finite_vector(component.get_world_location(), "Ball position")
        _close_vector(position, target["position_cm"], "Ball position")
        owner = actor.get_ball_owner(target["id"])
        _require(owner == (target["possession"]["owner_id"] or ""), "Runtime ball owner differs")
        _require(not component.is_any_simulating_physics(), "Runtime ball unexpectedly simulates physics")
        result["balls"].append({"id": target["id"], "position_cm": position, "owner_id": owner or None})
    return result


def _editor_readback(unreal, actor, data, plan):
    _require(actor.load_playback(data), "Runtime actor load failed: " + actor.get_last_error())
    try:
        _require(actor.is_loaded() and not actor.is_playing() and abs(actor.get_duration() - plan["playback"]["duration_seconds"]) <= 1e-9,
                 "Runtime initial control state differs")
        samples = [_native_sample(unreal, actor, frame, plan) for frame in plan["sample_frames"]]
        pose_response = verify_pose_response(samples, plan["pose_response_requirements"])
        first = {row["time_seconds"]: row for row in samples}
        for frame in reversed(plan["sample_frames"]):
            repeated = _native_sample(unreal, actor, frame, plan)
            original = first[frame["time_seconds"]]
            for current, previous in zip(repeated["actors"], original["actors"]):
                for bone, point in current["review_bone_positions_cm"].items():
                    _close_vector(point, previous["review_bone_positions_cm"][bone], "Reverse-seek evaluated bone")
        _require(actor.restart() and actor.get_playback_time() == 0.0 and not actor.is_playing(), "Runtime restart did not pause at zero")
        return {"status": "passed", "sample_count": len(samples), "reverse_seek_checks": len(samples), "samples": samples,
                "pose_response": pose_response,
                "scope": "Native editor loading, actual clip/state/clock queries, active local-pose response and reverse-seek consistency. No realtime/cooked, geometric contact or visual acceptance."}
    finally:
        actor.unload_playback()


def build(bootstrap_receipt_path, project_root=None, run_name=None, receipt_path=None, auto_play=False):
    """Create a fresh saved probe. Never replaces bootstrap assets or starts PIE."""
    root = Path(project_root).expanduser().resolve() if project_root else PROJECT
    _require(type(auto_play) is bool, "auto_play must be a boolean")
    run_name = run_name or (datetime.now(timezone.utc).strftime("Run_%Y%m%dT%H%M%SZ_") + uuid.uuid4().hex[:8])
    _require(isinstance(run_name, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", run_name), "Invalid runtime probe run name")
    receipt_path = Path(receipt_path).expanduser().resolve() if receipt_path else root / "Saved/SquadPad/RuntimeReceipts" / (run_name + ".json")
    _require(not receipt_path.exists(), "Runtime probe receipt exists. Choose a fresh run name/path")
    receipt = {"schema": "squadpad.unreal.runtime-probe-receipt.v1", "status": "started", "started_utc": _now(),
               "run_name": run_name, "stages": [], "native_editor_executed": False,
               "realtime_playback_verified": False, "cooked_playback_verified": False, "swift_host_verified": False,
               "ios_verified": False, "contact_geometry_accepted": False, "visual_quality_accepted": False}
    unreal = None
    try:
        _write_receipt(receipt_path, receipt)
        plan = plan_runtime_probe(bootstrap_receipt_path, root)
        receipt["input"] = {k: plan[k] for k in ("bootstrap_receipt_path", "bootstrap_receipt_sha256", "playback_file_sha256",
                                                "binding_file_sha256", "playback_sha256")}
        receipt["input"]["native_asset_packages"] = plan["bootstrap"]["current_native_asset_packages"]
        _stage(receipt, "input_validation")
        try:
            unreal = importlib.import_module("unreal")
        except ImportError as exc:
            raise RuntimeProbeError("Run inside the compiled SquadPad3D Unreal Editor. System Python cannot create native assets.") from exc
        running_root = Path(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir())).resolve()
        _require(running_root == root, "Wrong editor project")
        engine = unreal.SystemLibrary.get_engine_version()
        _require(engine == plan["bootstrap"]["engine_version"], "Use the exact engine build which created the baseline bootstrap")
        for name in ("SquadPadPlaybackDataAsset", "SquadPadPlaybackActor", "SquadPadAthleteAssetProfile",
                     "SquadPadMotionAssetBinding", "SquadPadAppearanceAssetBinding", "SquadPadProbeGameMode"):
            _require(hasattr(unreal, name), "Compiled runtime type is unavailable: " + name)
        _require(not unreal.EditorLoadingAndSavingUtils.get_dirty_map_packages(), "An unsaved editor map must be resolved before creating the probe")
        native_packages = {x["package"] for x in plan["bootstrap"]["current_native_asset_packages"]}
        dirty = {_path(p) for p in unreal.EditorLoadingAndSavingUtils.get_dirty_content_packages()}
        _require(not dirty.intersection(native_packages), "Bound native assets contain unsaved edits")
        namespace = "/Game/SquadPad/RuntimeProbe/" + run_name
        _require(not (root / "Content/SquadPad/RuntimeProbe" / run_name).exists(), "Fresh runtime probe disk namespace required")
        _require(not unreal.EditorAssetLibrary.does_directory_exist(namespace), "Fresh runtime probe namespace required")
        receipt.update(native_editor_executed=True, engine_version=engine, asset_namespace=namespace)
        assets, native_clocks = _native_assets(unreal, plan)
        receipt["native_animation_clocks"] = native_clocks
        _stage(receipt, "native_assets_verified")
        levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
        actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
        map_path = namespace + "/L_RuntimeFirstPass"
        _require(levels.new_level(map_path), "Could not create runtime probe map")
        world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
        world.get_world_settings().set_editor_property("default_game_mode", unreal.SquadPadProbeGameMode)
        sphere, ball_material, materials = _scene_environment(unreal, actors, namespace, plan["source_plan"])
        bounds = sphere.get_bounds()
        extent = _finite_vector(bounds.box_extent, "Engine sphere bounds")
        _require(min(extent) > 0 and max(extent) / min(extent) < 1.01, "Engine ball bounds are not spherical")
        _close_vector(_finite_vector(bounds.origin, "Engine sphere origin"), [0.0, 0.0, 0.0], "Engine ball origin", 1e-4)
        data = _create_data(unreal, namespace, plan, assets, sphere, ball_material, extent[0])
        data_path = _path(data)
        actor = actors.spawn_actor_from_class(unreal.SquadPadPlaybackActor, unreal.Vector(0, 0, 0), transient=False)
        _require(actor is not None, "Could not create runtime playback actor")
        actor.set_actor_label("SquadPad runtime first pass")
        actor.set_editor_property("playback_data", data)
        actor.set_editor_property("auto_load_on_begin_play", True)
        actor.set_editor_property("auto_play", auto_play)
        actor_name = actor.get_name()
        camera, camera_record = _camera(unreal, actors, plan)
        camera_name = camera.get_name()
        _require(not any(isinstance(a, unreal.LevelSequenceActor) for a in actors.get_all_level_actors()),
                 "Runtime map contains a competing LevelSequence actor")
        for obj in [data, *materials]:
            _require(unreal.EditorAssetLibrary.save_loaded_asset(obj, only_if_is_dirty=False), "Could not save probe asset")
        _require(levels.save_current_level(), "Could not save runtime probe map")
        receipt["scene"] = {"map_path": map_path, "data_asset_path": data_path, "actor_name": actor_name,
                            "camera_name": camera_name, "camera": camera_record, "auto_play": auto_play,
                            "game_mode": "/Script/SquadPadRuntime.SquadPadProbeGameMode",
                            "ball_representation": "Engine sphere without authored football panels or spin"}
        _stage(receipt, "saved_native_probe")
        _write_receipt(receipt_path, receipt)
        reloaded, error = unreal.EditorLoadingAndSavingUtils.reload_packages(
            [data.get_outer()], unreal.ReloadPackagesInteractionMode.ASSUME_NEGATIVE)
        _require(reloaded, "Could not reload playback data package: " + str(error))
        _require(levels.load_level(map_path), "Could not reopen runtime probe map")
        data = unreal.load_asset(data_path)
        _verify_data(unreal, data, plan, _path(sphere), _path(ball_material), extent[0])
        reopened = list(actors.get_all_level_actors())
        matches = [a for a in reopened if a.get_name() == actor_name and isinstance(a, unreal.SquadPadPlaybackActor)]
        _require(len(matches) == 1 and sum(isinstance(a, unreal.SquadPadPlaybackActor) for a in reopened) == 1,
                 "Saved map must contain exactly one runtime playback actor")
        _require(not any(isinstance(a, unreal.LevelSequenceActor) for a in reopened), "Reopened map contains competing Sequencer playback")
        actor = matches[0]
        _require(_path(actor.get_editor_property("playback_data")) == data_path and
                 actor.get_editor_property("auto_load_on_begin_play") is True and
                 actor.get_editor_property("auto_play") is auto_play, "Saved actor configuration differs")
        cameras = [a for a in reopened if isinstance(a, unreal.CameraActor) and "SquadPadProbeCamera" in [str(t) for t in a.tags]]
        _require(len(cameras) == 1 and cameras[0].get_name() == camera_name and cameras[0].get_auto_activate_player_index() == 0,
                 "Saved runtime camera activation differs")
        world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
        mode = world.get_world_settings().get_editor_property("default_game_mode")
        _require(_path(mode) == "/Script/SquadPadRuntime.SquadPadProbeGameMode", "Saved map game mode differs")
        receipt["readback"] = _editor_readback(unreal, actor, data, plan)
        _stage(receipt, "reopened_editor_state_and_pose_queries", {"sample_count": receipt["readback"]["sample_count"]})
        _require(levels.save_current_level(), "Could not save clean runtime probe after transient readback")
        receipt["native_files"] = [_disk_record(root, map_path, ".umap"), _disk_record(root, data_path, ".uasset")]
        for record in plan["bootstrap"]["current_native_asset_packages"]:
            _require(sha256_file(root / record["relative_file"]) == record["sha256"], "Runtime probe modified a baseline native asset")
        receipt["status"] = "native_probe_saved_editor_readback_passed"
        receipt["finished_utc"] = _now()
        _write_receipt(receipt_path, receipt)
        unreal.log("SquadPad runtime probe saved and editor queries passed. Realtime, cooked, Swift and iOS gates remain pending. " + str(receipt_path))
        return receipt
    except Exception as exc:
        receipt.update(status="failed", finished_utc=_now(),
                       error={"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()},
                       partial_assets_policy="Preserved for diagnosis. Use a fresh run name after resolving the failure.")
        _write_receipt(receipt_path, receipt)
        if unreal is not None:
            unreal.log_error("SquadPad runtime probe failed: " + str(exc))
        raise
