#!/usr/bin/env python3
"""Build a traceable Unreal integration fixture from the existing rondo's first pass.

Standard library only. Default input is an observed source snapshot recovered from
this conversation. --source-root reads a LOCAL copy of the app source, never a
remote device. It does not execute source code or change the source repository.
"""
import argparse
import copy
import hashlib
import json
import math
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[2]
SNAPSHOT = PROJECT / "Fixtures/rondo-4v1.level-1.observed.json"
OUTPUT = PROJECT / "Fixtures/first_pass.drill.json"
SCHEMA = "squadpad.drill.fixture.v1"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def read_json(path):
    return json.loads(path.read_text(), parse_constant=lambda v: (_ for _ in ()).throw(ValueError(f"Nonfinite JSON: {v}")))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_inputs(snapshot, source_root):
    if source_root is None:
        observed = read_json(snapshot)
        require(observed.get("schema") == "squadpad.drill.observation.v1", "Unsupported observation schema")
        provenance = copy.deepcopy(observed["provenance"])
        provenance["snapshot_file"] = snapshot.name
        provenance["snapshot_sha256"] = digest(snapshot)
        return observed["payload"], observed["mocap"], provenance
    root = source_root.resolve(strict=True)
    paths = {
        "card": root / "ios-native/Resources/DrillCards/rondo-4v1.json",
        "timeline": root / "tools/drillkit/golden/rondo-4v1.level-1.timeline.json",
        "mocap": root / "ios-native/Resources/DrillCards/3D/mocap-drills.json",
    }
    card, timeline, mocap = (read_json(paths[key]) for key in ("card", "timeline", "mocap"))
    level = next((level for level in card["levels"] if level["number"] == 1), None)
    require(level is not None, "Rondo level 1 is absent")
    # Same field selection as tools/footballer/export_drills3d.py, without importing it.
    payload = {
        "id": "rondo-4v1", "name": card["name"],
        "summary": (card.get("purpose") or {}).get("simple", ""),
        "level": {"number": 1, "name": level["name"], "steps": level.get("steps", [])},
        "diagram": card["diagram"], "timeline": timeline,
    }
    provenance = {
        "evidence": "Local source files read by this adapter", "source_repository": str(root),
        "files": [{"path": str(path.relative_to(root)), "sha256": digest(path)} for path in paths.values()],
        "golden_recompiled": False,
    }
    return payload, mocap, provenance


def build_fixture(payload, mocap, provenance):
    require(payload.get("id") == "rondo-4v1" and payload["level"]["number"] == 1, "Only rondo-4v1 level 1 is supported")
    diagram, timeline = payload["diagram"], payload["timeline"]
    require(diagram.get("units") == "m", "Source diagram must use metres")
    segment = timeline["segments"][0]
    require(segment.get("kind") == "pass" and segment.get("from") == "A" and segment.get("to") == "B", "Expected first A-to-B pass")
    require(segment.get("t0") == 0 and number(segment.get("t1")) and segment["t1"] > 0, "Expected positive first pass starting at source zero")
    require(segment.get("height", "ground") == "ground", "Lofted passes are outside this fixture's scope")
    path = segment["path"]
    require(len(path) == 2 and all(len(p) == 2 and all(number(v) for v in p) for p in path), "Expected two finite path endpoints")
    start = timeline["start"]
    require(path[0] == start["players"]["A"] and path[1] == start["players"]["B"], "Pass endpoints must match original actor guide positions")
    held = [ball_id for ball_id, owner in start["balls"].items() if owner == "A"]
    require(len(held) == 1 and held[0] == "ball", "Initial ball ownership is missing or ambiguous")
    require(not segment.get("ball") or segment["ball"] == held[0], "Explicit pass ball differs from possession")
    meta = mocap["meta"]
    for name in ("pass", "receive"):
        info = meta[name]
        require(number(info.get("duration")) and info["duration"] > 0, f"Invalid {name} duration")
        require(number(info.get("contact")) and 0 < info["contact"] < 1, f"Invalid {name} contact phase")
        require(info.get("foot") == "right" and info.get("loop") is False, f"Unexpected {name} clip semantics")
        require(len(info["ball"]) == 2 and all(number(v) for v in info["ball"]), f"Invalid {name} ball candidate")

    # Full captured pass begins at zero. This adds preroll without stretching the drill.
    lead = meta["pass"]["contact"] * meta["pass"]["duration"]
    arrival = lead + segment["t1"]
    receive_start = arrival - meta["receive"]["contact"] * meta["receive"]["duration"]
    require(receive_start >= 0, "Receive clip requires additional preroll")
    duration = max(meta["pass"]["duration"], receive_start + meta["receive"]["duration"]) + 0.4
    origin = [(path[0][i] + path[1][i]) / 2 for i in (0, 1)]

    def world(point):
        return [(origin[1] - point[1]) * 100, (point[0] - origin[0]) * 100, 0.0]

    positions = [world(p) for p in path]
    yaw = math.degrees(math.atan2(positions[1][1] - positions[0][1], positions[1][0] - positions[0][0]))
    yaws = [yaw, (yaw + 360) % 360 - 180]
    actors = []
    targets = []
    for index, (actor_id, animation_id, clip_start) in enumerate((("A", "pass", 0.0), ("B", "receive", receive_start))):
        rotation = {"pitch": 0.0, "yaw": yaws[index], "roll": 0.0}
        position = positions[index]
        actors.append({
            "id": actor_id, "mesh_id": "athlete_a", "label": actor_id,
            "source_position_metres": path[index], "position_cm": position, "rotation_deg": rotation,
            "clips": [{"animation_id": animation_id, "start_seconds": clip_start, "duration_seconds": meta[animation_id]["duration"], "play_rate": 1.0, "loop": False}],
            "transform_keys": [{"time_seconds": t, "position_cm": position, "rotation_deg": rotation} for t in (0.0, duration)],
            "root_motion": {
                "mode": "contact_relative_clip_trace", "animation_id": animation_id,
                "anchor_contact_id": f"rondo-4v1.level-1.segment-0.{'kick' if index == 0 else 'receive'}",
                "status": "requires_export_descriptor", "coordinate_space": "actor_local_cm",
                "transform_keys_semantics": "stationary_world_guide_anchors",
            },
        })
        # A visible candidate, not a validated athlete contact. Canonical +x is LEFT.
        left, ahead = meta[animation_id]["ball"]
        forward_cm, right_cm = ahead * 100, -left * 100
        angle = math.radians(yaws[index])
        targets.append([
            position[0] + forward_cm * math.cos(angle) - right_cm * math.sin(angle),
            position[1] + forward_cm * math.sin(angle) + right_cm * math.cos(angle), 11.0,
        ])
    contacts = []
    for index, (event, actor_id, time, animation_id) in enumerate((("kick", "A", lead, "pass"), ("receive", "B", arrival, "receive"))):
        contacts.append({
            "id": f"rondo-4v1.level-1.segment-0.{event}", "event": event,
            "time_seconds": time, "source_time_seconds": segment["t0"] if index == 0 else segment["t1"],
            "actor_id": actor_id, "ball_id": "ball", "limb": "right_foot", "surface": "boot_surface_unmeasured",
            "animation_id": animation_id, "animation_contact_seconds": meta[animation_id]["contact"] * meta[animation_id]["duration"],
            "candidate_ball_center_cm": targets[index],
            "calibration": {"status": "unmeasured", "bone_name": None, "local_offset_cm": None},
        })
    provenance = copy.deepcopy(provenance)
    provenance["adapter"] = {"path": "Tools/Drill/export_first_pass.py", "version": 1}
    provenance["contact_candidate_basis"] = "Observed canonical X Bot metadata.ball, a heuristic ankle/toe offset. No athlete-rig or FBX contact measurements exist in this cloud package."
    return {
        "schema": SCHEMA, "id": "rondo-4v1.level-1.segment-0.pass-receive",
        "title": "4v1 circle rondo: isolated first A-to-B pass and reception",
        "units": {"distance": "centimetres", "time": "seconds", "angle": "degrees"},
        "axes": "unreal-z-up-x-forward", "frame_rate": 60, "duration_seconds": duration,
        "selection": {
            "kind": "existing_drill_segment", "source_card_id": "rondo-4v1", "source_level": 1,
            "source_segment_indices": [0], "source_time_range_seconds": [segment["t0"], segment["t1"]],
            "omitted_source_segment_indices": list(range(1, len(timeline["segments"]))),
            "omitted_actor_ids": [p["id"] for p in diagram["players"] if p["id"] not in ("A", "B")],
            "omitted_ball_ids": [b["id"] for b in diagram["balls"] if b["id"] != "ball"],
            "complete_drill": False,
        },
        "clock": {"source_to_playback_offset_seconds": lead, "scale": 1.0, "post_animation_review_hold_seconds": 0.4, "release_source": "timeline.segments[0].t0", "arrival_source": "timeline.segments[0].t1"},
        "coordinate_transform": {
            "source": "diagram x right, y down, metres", "diagram_origin_metres": origin,
            "formula": "UE.X = (origin.y - diagram.y) * 100; UE.Y = (diagram.x - origin.x) * 100; UE.Z = height_metres * 100",
            "source_xy_metres_to_unreal_xy_cm_matrix": [[0, -100], [100, 0]],
            "translation_cm": [origin[1] * 100, -origin[0] * 100, 0], "browser_css_mirror_applied": False,
        },
        "actors": actors,
        "balls": [{
            "id": "ball", "radius_cm": 11.0, "position_cm": targets[0],
            "keys": [{"time_seconds": t, "position_cm": p} for t, p in ((0.0, targets[0]), (lead, targets[0]), (arrival, targets[1]), (duration, targets[1]))],
            "interpolation": "linear", "trajectory_status": "provisional_contact_placement",
            "post_receive_motion": "Holds candidate arrival centre. A measured target-athlete foot trace is required for the cushioning motion.",
        }],
        "contacts": contacts,
        "possession": [{"time_seconds": 0.0, "state": "owned", "ball_id": "ball", "owner_id": "A"}, {"time_seconds": lead, "state": "in_transit", "ball_id": "ball", "owner_id": None, "from_actor_id": "A", "to_actor_id": "B"}, {"time_seconds": arrival, "state": "owned", "ball_id": "ball", "owner_id": "B"}],
        "readiness": {"source_segment_observed": True, "source_currentness_reverified": False, "skeletal_assets_present": False, "root_motion_descriptor_present": False, "athlete_contacts_calibrated": False, "unreal_import_verified": False, "visual_animation_accepted": False, "purpose": "Source-data and asset-integration fixture; animation and contact review are unfinished."},
        "source_payload": copy.deepcopy(payload),
        "source_motion_metadata": {key: copy.deepcopy(meta[key]) for key in ("idle", "pass", "receive")},
        "provenance": provenance,
    }


def validate(fixture):
    require(fixture.get("schema") == SCHEMA, "Unsupported fixture schema")
    require(fixture.get("units") == {"distance": "centimetres", "time": "seconds", "angle": "degrees"}, "Unexpected units")
    require(fixture.get("axes") == "unreal-z-up-x-forward", "Unexpected axes")
    duration = fixture["duration_seconds"]
    require(number(duration) and duration > 0, "Invalid duration")
    ids = {actor["id"] for actor in fixture["actors"]}
    require(ids == {"A", "B"} and len(fixture["actors"]) == 2, "Actor IDs must remain A and B")
    require(len(fixture["balls"]) == 1 and fixture["balls"][0]["id"] == "ball", "Ball ID must remain ball")
    for actor in fixture["actors"]:
        for key in actor["transform_keys"]:
            require(0 <= key["time_seconds"] <= duration, "Transform time out of range")
            require(len(key["position_cm"]) == 3 and all(number(v) for v in key["position_cm"]), "Nonfinite actor position")
            require(set(key["rotation_deg"]) == {"pitch", "yaw", "roll"} and all(number(v) for v in key["rotation_deg"].values()), "Invalid rotation object")
        for clip in actor["clips"]:
            require(0 <= clip["start_seconds"] < clip["start_seconds"] + clip["duration_seconds"] <= duration + 1e-9, "Clip window out of range")
    keys = fixture["balls"][0]["keys"]
    require(all(number(k["time_seconds"]) and len(k["position_cm"]) == 3 and all(number(v) for v in k["position_cm"]) for k in keys), "Invalid ball keys")
    require(all(a["time_seconds"] < b["time_seconds"] for a, b in zip(keys, keys[1:])), "Ball key times must increase")
    require(keys[0]["time_seconds"] == 0 and abs(keys[-1]["time_seconds"] - duration) < 1e-9, "Ball track must span fixture")
    by_event = {c["event"]: c for c in fixture["contacts"]}
    require(set(by_event) == {"kick", "receive"} and len(fixture["contacts"]) == 2, "Expected exactly two contacts")
    for contact in fixture["contacts"]:
        actor = next(a for a in fixture["actors"] if a["id"] == contact["actor_id"])
        clip = next(c for c in actor["clips"] if c["animation_id"] == contact["animation_id"])
        require(abs(clip["start_seconds"] + contact["animation_contact_seconds"] - contact["time_seconds"]) < 1e-9, "Clip and ball contact clocks disagree")
        require(contact["calibration"] == {"status": "unmeasured", "bone_name": None, "local_offset_cm": None}, "This adapter must not claim measured target contacts")
    segment = fixture["source_payload"]["timeline"]["segments"][0]
    require(abs(by_event["receive"]["time_seconds"] - by_event["kick"]["time_seconds"] - (segment["t1"] - segment["t0"])) < 1e-9, "Source pass time changed")
    require([p["owner_id"] for p in fixture["possession"]] == ["A", None, "B"], "Possession continuity changed")
    require(not fixture["readiness"]["visual_animation_accepted"], "No visual acceptance is available")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, default=SNAPSHOT)
    parser.add_argument("--source-root", type=Path, help="Optional LOCAL source checkout. Does not connect to a device.")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--check", action="store_true", help="Validate existing output without rewriting")
    args = parser.parse_args()
    try:
        if args.check:
            fixture = read_json(args.output)
        else:
            fixture = build_fixture(*load_inputs(args.snapshot, args.source_root))
        validate(fixture)
        if not args.check:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(fixture, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
        print(f"{'Validated' if args.check else 'Wrote'} {args.output}: source segment verified, contacts unmeasured, Unreal playback unverified")
    except (KeyError, ValueError, TypeError, OSError, StopIteration) as error:
        parser.exit(2, f"Fixture failed: {error}\n")


if __name__ == "__main__":
    main()
