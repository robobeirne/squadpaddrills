"""Compile the validated first-pass plan into portable SP1 playback and bindings.

Reads local existing exports. Does not alter source motion, root tracks, model
data or the historical first-pass fixture. No Unreal execution occurs here.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "Content/Python"))
sys.path.insert(0, str(PROJECT / "Tools"))
from squadpad_plan import plan_import, sha256_file
from squadpad_quality import validate_contacts
from evaluate_playback import (PlaybackError, PlaybackPlan, document_sha256, require,
                               validate_bindings, SCHEMA, BINDING_SCHEMA)


def validate_source_bindings(plan, bindings):
    """Match binding hashes and clocks to an independently validated import plan.

    plan_import(require_files=True) checks current source bytes against the export
    receipt. This additional step rejects a self-consistent binding which names
    stale source bytes. It still does not validate native assets or retarget art.
    """
    require(plan.get("all_source_files_present") is True, "Binding source verification needs all source files")
    meshes = {mesh["name"]: mesh for mesh in plan["meshes"].values()}
    animations = {animation["name"]: animation for animation in plan["animations"].values()}
    require(len(meshes) == len(plan["meshes"]) and len(animations) == len(plan["animations"]),
            "Source asset names must be unique for binding ingestion")
    for profile in bindings["profiles"]:
        require(profile["mesh_asset_id"] in meshes, "Bound mesh asset is absent from validated sources")
        mesh = meshes[profile["mesh_asset_id"]]
        require(profile["mesh_source_sha256"] == mesh["sha256"], "Bound mesh hash differs from validated source")
        require(profile["skeleton_id"] == "baseline_rig." + mesh["id"], "Bound skeleton differs from validated baseline rig")
        for binding in profile["animations"]:
            require(binding["animation_asset_id"] in animations, "Bound animation is absent from validated sources")
            animation = animations[binding["animation_asset_id"]]
            require(binding["source_motion_id"] == "canonical." + animation["id"],
                    "Bound animation maps a different canonical motion")
            require(animation["mesh_id"] == mesh["id"], "Bound animation targets a different source rig")
            require(binding["animation_source_sha256"] == animation["sha256"],
                    "Bound animation hash differs from validated source")
            require(abs(binding["duration_seconds"] - animation["duration_seconds"]) <= 1e-8,
                    "Bound animation clock differs from validated source")
    return {"status": "source_binding_metadata_matches_validated_files", "native_assets_verified": False}


def validate_bindings_with_source_files(playback, bindings, manifest_path=PROJECT / "import_manifest.json"):
    result = validate_bindings(playback, bindings)
    plan = plan_import(manifest_path, require_files=True)
    result["source_files"] = validate_source_bindings(plan, bindings)
    return result


def compile_documents(manifest_path=PROJECT / "import_manifest.json"):
    manifest_path = Path(manifest_path).resolve()
    plan = plan_import(manifest_path, require_files=True)
    findings = validate_contacts(plan)
    require(findings["unmeasured_count"] == len(plan["contacts"]),
            "SP1 compiler only supports the unmeasured baseline contact contract")
    source_ids = {aid: "canonical." + aid for aid in plan["animations"]}
    actors = []
    action_map = {}
    slots = []
    for actor in plan["actors"]:
        require(actor.get("root_motion", {}).get("status") == "composed_from_export_descriptor" and
                actor["root_motion"].get("application_count") == 1,
                "Every actor requires one composed root track")
        keys = []
        for key in actor["transform_keys"]:
            require(abs(key["rotation_deg"]["pitch"]) <= 1e-8 and abs(key["rotation_deg"]["roll"]) <= 1e-8,
                    "SP1 supports planar yaw only")
            keys.append({"time_seconds": key["time_seconds"], "position_cm": list(key["position_cm"]),
                         "yaw_degrees": key["rotation_deg"]["yaw"]})
        sections = []
        for index, clip in enumerate(actor["clips"]):
            sid = f"{actor['id']}.section.{index}"
            section = {"id": sid, "source_motion_id": source_ids[clip["animation_id"]],
                       "start_seconds": clip["start_seconds"],
                       "end_seconds": clip["start_seconds"] + clip["duration_seconds"],
                       "source_start_seconds": clip["source_start_seconds"],
                       "play_rate": clip["play_rate"], "loop": clip["loop"]}
            sections.append(section)
            action_map[sid] = (actor["id"], clip, section)
        appearance_slot = f"actor.{actor['id']}.appearance"
        actors.append({"id": actor["id"], "appearance_slot": appearance_slot,
                       "transform_keys": keys, "sections": sections})
        slots.append({"appearance_slot": appearance_slot, "profile_id": "baseline." + actor["mesh_id"]})
    contacts = []
    for contact in plan["contacts"]:
        matches = [(sid, clip, section) for sid, (aid, clip, section) in action_map.items()
                   if aid == contact["actor_id"] and clip["animation_id"] == contact["animation_id"] and
                   not clip["generated_idle"] and section["start_seconds"] <= contact["time_seconds"] <= section["end_seconds"]]
        require(len(matches) == 1, "Contact needs one exact action occurrence")
        contacts.append({"id": contact["id"], "kind": contact["event"], "time_seconds": contact["time_seconds"],
                         "actor_id": contact["actor_id"], "ball_id": contact["ball_id"], "action_id": matches[0][0],
                         "source_motion_id": source_ids[contact["animation_id"]],
                         "source_contact_seconds": contact["animation_contact_seconds"],
                         "limb": contact["limb"], "calibration_status": "unmeasured"})
    root = Path(plan["project_root"])
    document = {
        "schema": SCHEMA, "id": plan["fixture_id"],
        "units": {"distance": "centimetres", "time": "seconds", "angle": "degrees"},
        "axes": "unreal-z-up-x-forward", "root_ownership": "actor_tracks_only",
        "duration_seconds": plan["duration_seconds"],
        "motions": [{"id": source_ids[aid], "duration_seconds": clip["duration_seconds"]}
                    for aid, clip in sorted(plan["animations"].items())],
        "actors": actors,
        "balls": [{"id": ball["id"], "radius_cm": ball["radius_cm"], "position_keys": ball["keys"]}
                  for ball in plan["balls"]],
        "possession": sorted([{"time_seconds": p["time_seconds"], "ball_id": p["ball_id"], "state": p["state"],
                               "owner_id": p.get("owner_id"), "from_actor_id": p.get("from_actor_id"),
                               "to_actor_id": p.get("to_actor_id")} for p in plan["possession"]],
                             key=lambda p: (p["time_seconds"], p["ball_id"])),
        "contacts": sorted(contacts, key=lambda c: (c["time_seconds"], c["id"])),
        "provenance": {"source_fixture_sha256": plan["fixture_sha256"],
                       "source_manifest_sha256": plan["manifest_sha256"],
                       "source_export_receipt_sha256": plan["export_evidence"]["sha256"],
                       "canonical_index_sha256": sha256_file(root / "SourceInputs/mocap-drills.json"),
                       "canonical_binary_sha256": sha256_file(root / "SourceInputs/mocap-drills.bin"),
                       "source_card_id": plan["selection"]["source_card_id"],
                       "complete_drill": plan["selection"]["complete_drill"],
                       "source_to_playback_offset_seconds": plan["clock"]["source_to_playback_offset_seconds"]},
        "evidence": {"scope": "Portable presentation state and source clocks only",
                     "ball_placement": "provisional_source_heuristic", "section_transitions": "unblended_baseline",
                     "team_possession": "not_provided_by_source", "exported_input_files_validated": True,
                     "native_unreal_execution": False, "visual_quality_accepted": False,
                     "contact_geometry_accepted": False},
    }
    PlaybackPlan(document)
    profiles = []
    for mesh_id, mesh in sorted(plan["meshes"].items()):
        skeleton_id = "baseline_rig." + mesh_id
        profiles.append({
            "id": "baseline." + mesh_id, "skeleton_id": skeleton_id,
            "mesh_asset_id": mesh["name"], "mesh_source_sha256": mesh["sha256"],
            "animations": [{"source_motion_id": source_ids[aid], "animation_asset_id": clip["name"],
                            "skeleton_id": skeleton_id, "target_mesh_sha256": mesh["sha256"],
                            "animation_source_sha256": clip["sha256"], "duration_seconds": clip["duration_seconds"]}
                           for aid, clip in sorted(plan["animations"].items()) if clip["mesh_id"] == mesh_id],
            "calibration": {"status": "unmeasured", "mesh_source_sha256": mesh["sha256"]},
        })
    bindings = {"schema": BINDING_SCHEMA, "playback_id": document["id"],
                "playback_sha256": document_sha256(document), "profiles": profiles, "slots": slots}
    validate_bindings(document, bindings)
    validate_source_bindings(plan, bindings)
    return document, bindings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=PROJECT / "import_manifest.json")
    parser.add_argument("--output-dir", type=Path, default=PROJECT / "Fixtures/Playback")
    args = parser.parse_args(argv)
    try:
        document, bindings = compile_documents(args.manifest)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        paths = {"playback": args.output_dir / "first_pass.playback.json",
                 "bindings": args.output_dir / "athlete_a_baseline.bindings.json"}
        for key, data in (("playback", document), ("bindings", bindings)):
            paths[key].write_text(json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        print(json.dumps({"status": "portable_baseline_compiled", "files": {k: str(v.resolve()) for k, v in paths.items()},
                          "playback_sha256": document_sha256(document), "native_unreal_execution": False,
                          "visual_quality_accepted": False}, indent=2))
        return 0
    except (ValueError, OSError, TypeError, KeyError) as error:
        parser.exit(2, f"Playback compilation failed: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
