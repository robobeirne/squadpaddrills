"""Portable SP1 playback reference. No Unreal, renderer or skeletal pose evaluation.

The contract owns world tracks and source animation clocks. It preserves the
existing provisional contact geometry; successful evaluation is not art approval.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
import copy
import hashlib
import json
import math
from pathlib import Path
import re


SCHEMA = "squadpad.playback.v1"
BINDING_SCHEMA = "squadpad.athlete-bindings.v1"
EPS = 1e-8
CONTACT_EPS = 1e-7


class PlaybackError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise PlaybackError(message)


def object_fields(value, required, optional, label):
    require(isinstance(value, dict), f"{label} must be an object")
    require(set(required) <= set(value), f"{label} omits required fields")
    require(set(value) <= set(required) | set(optional), f"{label} has unsupported fields")


def number(value, label, minimum=None, positive=False):
    require(not isinstance(value, bool) and isinstance(value, (int, float)), f"{label} must be a number")
    try:
        value = float(value)
    except OverflowError as error:
        raise PlaybackError(f"{label} must fit a finite double") from error
    require(math.isfinite(value), f"{label} must be finite")
    require(minimum is None or value >= minimum, f"{label} is below its minimum")
    require(not positive or value > 0, f"{label} must be positive")
    return float(value)


def identifier(value, label):
    require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]+", value) is not None,
            f"{label} must be a portable identifier")
    return value


def digest(value, label):
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
            f"{label} must be a lowercase SHA-256")
    return value


def vector(value, label):
    require(isinstance(value, list) and len(value) == 3, f"{label} must have three coordinates")
    return [number(v, f"{label}[{i}]") for i, v in enumerate(value)]


def sequence(value, label, nonempty=True):
    require(isinstance(value, list) and (bool(value) or not nonempty), f"{label} must be a list")
    return value


def finite_json(value):
    try:
        json.dumps(value, allow_nan=False)
    except (ValueError, TypeError) as error:
        raise PlaybackError("Document must contain finite JSON values") from error


def document_sha256(value):
    finite_json(value)
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


def read_json(path):
    def invalid_constant(value):
        raise PlaybackError(f"Nonfinite JSON number: {value}")
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=invalid_constant)
    except json.JSONDecodeError as error:
        raise PlaybackError(f"Invalid JSON: {path}: {error}") from error


def _check_keys(keys, duration, label, yaw=False):
    sequence(keys, label)
    require(len(keys) >= 2, f"{label} needs at least two keys")
    previous = -1.0
    for key in keys:
        object_fields(key, {"time_seconds", "position_cm"} | ({"yaw_degrees"} if yaw else set()), set(), label)
        time = number(key["time_seconds"], f"{label}.time_seconds", 0)
        require(time > previous and time <= duration, f"{label} times must strictly increase inside duration")
        vector(key["position_cm"], f"{label}.position_cm")
        if yaw:
            number(key["yaw_degrees"], f"{label}.yaw_degrees")
        previous = time
    require(abs(keys[0]["time_seconds"]) <= EPS and abs(keys[-1]["time_seconds"] - duration) <= EPS,
            f"{label} must cover the full duration")


def validate_playback(document):
    """Validate SP1 structure and cross-record semantics before evaluating state."""
    finite_json(document)
    object_fields(document, {"schema", "id", "units", "axes", "root_ownership", "duration_seconds",
                            "motions", "actors", "balls", "possession", "contacts"},
                  {"provenance", "evidence"}, "playback")
    require(document["schema"] == SCHEMA, "Unsupported playback schema")
    identifier(document["id"], "playback.id")
    require(document["units"] == {"distance": "centimetres", "time": "seconds", "angle": "degrees"},
            "Playback units must be centimetres, seconds and degrees")
    require(document["axes"] == "unreal-z-up-x-forward", "Unsupported playback axes")
    require(document["root_ownership"] == "actor_tracks_only", "Root movement must belong to actor tracks only")
    for metadata in ("provenance", "evidence"):
        if metadata in document:
            require(isinstance(document[metadata], dict), f"{metadata} must be an object")
    duration = number(document["duration_seconds"], "duration_seconds", positive=True)
    motions = {}
    for motion in sequence(document["motions"], "motions"):
        object_fields(motion, {"id", "duration_seconds"}, set(), "motion")
        mid = identifier(motion["id"], "motion.id")
        require(mid not in motions, "Duplicate motion ID")
        motions[mid] = number(motion["duration_seconds"], "motion.duration_seconds", positive=True)
    actors, actions, slots = {}, {}, set()
    for actor in sequence(document["actors"], "actors"):
        object_fields(actor, {"id", "appearance_slot", "transform_keys", "sections"}, set(), "actor")
        aid = identifier(actor["id"], "actor.id")
        require(aid not in actors, "Duplicate actor ID")
        actors[aid] = actor
        slots.add(identifier(actor["appearance_slot"], "actor.appearance_slot"))
        _check_keys(actor["transform_keys"], duration, f"actor.{aid}.transform_keys", yaw=True)
        previous_end = 0.0
        for section in sequence(actor["sections"], f"actor.{aid}.sections"):
            object_fields(section, {"id", "source_motion_id", "start_seconds", "end_seconds",
                                    "source_start_seconds", "play_rate", "loop"}, set(), "section")
            sid = identifier(section["id"], "section.id")
            require(sid not in actions, "Duplicate action occurrence ID")
            mid = identifier(section["source_motion_id"], "section.source_motion_id")
            require(mid in motions, "Section references an unknown motion")
            start = number(section["start_seconds"], "section.start_seconds", 0)
            end = number(section["end_seconds"], "section.end_seconds", 0)
            require(end > start and end <= duration, "Section has an invalid range")
            require(abs(start - previous_end) <= EPS, "Sections must be contiguous without overlap")
            offset = number(section["source_start_seconds"], "section.source_start_seconds", 0)
            require(offset < motions[mid], "Section source start exceeds motion duration")
            rate = number(section["play_rate"], "section.play_rate", positive=True)
            require(isinstance(section["loop"], bool), "Section loop must be boolean")
            source_end = offset + (end - start) * rate
            require(math.isfinite(source_end), "Section source end must be finite")
            require(section["loop"] or source_end <= motions[mid] + EPS,
                    "Non-looping section exceeds motion duration")
            previous_end = end
            actions[sid] = (aid, section)
        require(abs(previous_end - duration) <= EPS, "Actor sections must cover the full duration")
    balls = {}
    for ball in sequence(document["balls"], "balls"):
        object_fields(ball, {"id", "radius_cm", "position_keys"}, set(), "ball")
        bid = identifier(ball["id"], "ball.id")
        require(bid not in balls and bid not in actors, "Duplicate scene object ID")
        number(ball["radius_cm"], "ball.radius_cm", positive=True)
        _check_keys(ball["position_keys"], duration, f"ball.{bid}.position_keys")
        balls[bid] = ball
    possession = {bid: [] for bid in balls}
    last_sort_key = None
    for record in sequence(document["possession"], "possession"):
        object_fields(record, {"time_seconds", "ball_id", "state", "owner_id", "from_actor_id", "to_actor_id"},
                      set(), "possession")
        time = number(record["time_seconds"], "possession.time_seconds", 0)
        require(time <= duration, "Possession lies outside duration")
        bid = record["ball_id"]
        require(isinstance(bid, str) and bid in balls, "Possession references an unknown ball")
        key = (time, bid)
        require(last_sort_key is None or key > last_sort_key, "Possession must sort strictly by time and ball ID")
        last_sort_key = key
        previous = possession[bid]
        require(not previous or time > previous[-1]["time_seconds"], "Possession times must increase per ball")
        if record["state"] == "owned":
            require(isinstance(record["owner_id"], str) and record["owner_id"] in actors,
                    "Owned possession needs an existing owner")
            require(record["from_actor_id"] is None and record["to_actor_id"] is None,
                    "Owned possession must omit transit endpoints")
        elif record["state"] == "in_transit":
            require(record["owner_id"] is None, "In-transit possession has no owner")
            require(isinstance(record["from_actor_id"], str) and record["from_actor_id"] in actors and
                    isinstance(record["to_actor_id"], str) and record["to_actor_id"] in actors and
                    record["from_actor_id"] != record["to_actor_id"], "Transit needs distinct existing actors")
        else:
            raise PlaybackError("Unsupported possession state")
        previous.append(record)
    for bid, records in possession.items():
        require(records and records[0]["time_seconds"] == 0, f"Ball {bid} needs initial possession at zero")
    contacts, last_sort_key = {}, None
    for contact in sequence(document["contacts"], "contacts", nonempty=False):
        object_fields(contact, {"id", "kind", "time_seconds", "actor_id", "ball_id", "action_id",
                                "source_motion_id", "source_contact_seconds", "limb", "calibration_status"},
                      set(), "contact")
        cid = identifier(contact["id"], "contact.id")
        require(cid not in contacts, "Duplicate contact ID")
        time = number(contact["time_seconds"], "contact.time_seconds", 0)
        require(time <= duration, "Contact lies outside duration")
        key = (time, cid)
        require(last_sort_key is None or key > last_sort_key, "Contacts must sort by time and ID")
        last_sort_key = key
        require(contact["kind"] in ("kick", "receive"), "Unsupported contact kind")
        require(contact["calibration_status"] == "unmeasured", "SP1 contacts must remain unmeasured")
        identifier(contact["limb"], "contact.limb")
        aid, bid, sid = contact["actor_id"], contact["ball_id"], contact["action_id"]
        require(isinstance(aid, str) and aid in actors and isinstance(bid, str) and bid in balls,
                "Contact references an unknown actor or ball")
        require(isinstance(sid, str) and sid in actions and actions[sid][0] == aid,
                "Contact references an unknown action occurrence on its actor")
        section = actions[sid][1]
        require(not section["loop"], "SP1 contact sections must not loop")
        require(contact["source_motion_id"] == section["source_motion_id"], "Contact motion differs from action")
        require(section["start_seconds"] <= time <= section["end_seconds"],
                "Contact lies outside action occurrence")
        actor_sections = actors[aid]["sections"]
        active_index = min(max(bisect_right([s["start_seconds"] for s in actor_sections], time) - 1, 0),
                           len(actor_sections) - 1)
        require(actor_sections[active_index]["id"] == sid, "Contact action is not active at its exact scene time")
        source = number(contact["source_contact_seconds"], "contact.source_contact_seconds", 0)
        require(source <= motions[contact["source_motion_id"]], "Contact lies outside source motion")
        expected = section["source_start_seconds"] + (time - section["start_seconds"]) * section["play_rate"]
        require(abs(source - expected) <= CONTACT_EPS, "Contact source and scene clocks disagree")
        contacts[cid] = contact
    matched = set()
    for bid, records in possession.items():
        for previous, current in zip(records, records[1:]):
            if previous["state"] == "owned" and current["state"] == "in_transit":
                require(previous["owner_id"] == current["from_actor_id"], "Pass sender differs from prior owner")
                kind, aid = "kick", current["from_actor_id"]
            elif previous["state"] == "in_transit" and current["state"] == "owned":
                require(previous["to_actor_id"] == current["owner_id"], "Receiver differs from intended arrival")
                kind, aid = "receive", current["owner_id"]
            else:
                raise PlaybackError("Unsupported consecutive possession states")
            candidates = [c for c in contacts.values() if c["ball_id"] == bid and c["actor_id"] == aid and
                          c["kind"] == kind and abs(c["time_seconds"] - current["time_seconds"]) <= CONTACT_EPS]
            require(len(candidates) == 1, "Possession transition needs one matching contact")
            require(candidates[0]["id"] not in matched, "Contact cannot satisfy two possession transitions")
            matched.add(candidates[0]["id"])
    require(matched == set(contacts), "Each contact must correspond to a possession transition")


def _position(keys, time):
    times = [key["time_seconds"] for key in keys]
    upper = bisect_right(times, time)
    if upper == 0:
        return list(keys[0]["position_cm"]), keys[0], keys[0], 0.0
    if upper == len(keys):
        return list(keys[-1]["position_cm"]), keys[-1], keys[-1], 0.0
    left, right = keys[upper - 1], keys[upper]
    fraction = (time - left["time_seconds"]) / (right["time_seconds"] - left["time_seconds"])
    position = [a * (1.0 - fraction) + b * fraction for a, b in zip(left["position_cm"], right["position_cm"])]
    return position, left, right, fraction


class PlaybackPlan:
    """Validated immutable-by-interface playback data with pure state sampling."""
    def __init__(self, document):
        validate_playback(document)
        self._document = copy.deepcopy(document)
        self._motions = {m["id"]: float(m["duration_seconds"]) for m in document["motions"]}
        self._duration = float(document["duration_seconds"])
        self._possession = {b["id"]: [p for p in self._document["possession"] if p["ball_id"] == b["id"]]
                            for b in self._document["balls"]}

    @property
    def document(self):
        return copy.deepcopy(self._document)

    @property
    def duration_seconds(self):
        return self._duration

    def evaluate(self, seconds):
        time = min(max(number(seconds, "query time"), 0.0), self._duration)
        actors = []
        for actor in self._document["actors"]:
            position, left, right, fraction = _position(actor["transform_keys"], time)
            left_yaw, right_yaw = left["yaw_degrees"] % 360.0, right["yaw_degrees"] % 360.0
            delta = (right_yaw - left_yaw) % 360.0
            if delta > 180.0:
                delta -= 360.0
            yaw = (left_yaw + delta * fraction) % 360.0
            starts = [s["start_seconds"] for s in actor["sections"]]
            index = min(max(bisect_right(starts, time) - 1, 0), len(starts) - 1)
            section = actor["sections"][index]
            section_elapsed = min(max(time - section["start_seconds"], 0.0),
                                  section["end_seconds"] - section["start_seconds"])
            local = section["source_start_seconds"] + section_elapsed * section["play_rate"]
            motion_duration = self._motions[section["source_motion_id"]]
            local = local % motion_duration if section["loop"] else min(max(local, 0.0), motion_duration)
            actors.append({"id": actor["id"], "appearance_slot": actor["appearance_slot"], "position_cm": position,
                           "yaw_degrees": yaw, "action_id": section["id"],
                           "source_motion_id": section["source_motion_id"], "source_time_seconds": local})
        balls = []
        for ball in self._document["balls"]:
            position = _position(ball["position_keys"], time)[0]
            records = self._possession[ball["id"]]
            index = bisect_right([p["time_seconds"] for p in records], time) - 1
            possession = {k: records[index][k] for k in ("state", "owner_id", "from_actor_id", "to_actor_id")}
            balls.append({"id": ball["id"], "radius_cm": ball["radius_cm"], "position_cm": position,
                          "possession": possession})
        return {"time_seconds": time, "actors": actors, "balls": balls}


class PlaybackController:
    """A bounded preview clock. Seeking has no contact side effects."""
    def __init__(self, plan):
        require(isinstance(plan, PlaybackPlan), "Controller needs a validated PlaybackPlan")
        self.plan = plan
        self.time_seconds = 0.0
        self.playback_rate = 1.0
        self.is_playing = False

    def _update(self, contacts=()):
        return {"frame": self.plan.evaluate(self.time_seconds), "crossed_contact_ids": list(contacts)}

    def play(self):
        self.is_playing = self.time_seconds < self.plan.duration_seconds

    def pause(self):
        self.is_playing = False

    def seek(self, seconds):
        self.time_seconds = min(max(number(seconds, "seek time"), 0.0), self.plan.duration_seconds)
        if self.time_seconds == self.plan.duration_seconds:
            self.is_playing = False
        return self._update()

    def restart(self):
        self.time_seconds = 0.0
        self.is_playing = False
        return self._update()

    def set_rate(self, rate):
        self.playback_rate = number(rate, "playback rate", positive=True)

    def advance(self, delta_seconds):
        delta = number(delta_seconds, "advance delta", 0)
        if delta == 0 or not self.is_playing:
            return self._update()
        previous = self.time_seconds
        if self.is_playing:
            remaining = self.plan.duration_seconds - previous
            if delta >= remaining / self.playback_rate:
                self.time_seconds = self.plan.duration_seconds
                self.is_playing = False
            else:
                self.time_seconds += delta * self.playback_rate
        crossed = [c["id"] for c in self.plan._document["contacts"]
                   if previous < c["time_seconds"] <= self.time_seconds]
        return self._update(crossed)


def load_playback(path):
    return PlaybackPlan(read_json(path))


def validate_bindings(playback, bindings):
    """Check explicit rig compatibility and calibration freshness, not mesh quality."""
    document = playback.document if isinstance(playback, PlaybackPlan) else playback
    validate_playback(document)
    finite_json(bindings)
    object_fields(bindings, {"schema", "playback_id", "playback_sha256", "profiles", "slots"}, set(), "bindings")
    require(bindings["schema"] == BINDING_SCHEMA, "Unsupported athlete bindings schema")
    require(bindings["playback_id"] == document["id"], "Bindings reference another playback ID")
    require(digest(bindings["playback_sha256"], "bindings.playback_sha256") == document_sha256(document),
            "Bindings use a stale playback hash")
    motions = {m["id"]: m for m in document["motions"]}
    profiles = {}
    for profile in sequence(bindings["profiles"], "binding profiles"):
        object_fields(profile, {"id", "skeleton_id", "mesh_asset_id", "mesh_source_sha256", "animations", "calibration"},
                      set(), "athlete profile")
        pid = identifier(profile["id"], "profile.id")
        require(pid not in profiles, "Duplicate athlete profile ID")
        skeleton = identifier(profile["skeleton_id"], "profile.skeleton_id")
        identifier(profile["mesh_asset_id"], "profile.mesh_asset_id")
        mesh_hash = digest(profile["mesh_source_sha256"], "profile.mesh_source_sha256")
        calibration = profile["calibration"]
        object_fields(calibration, {"status", "mesh_source_sha256"}, set(), "calibration")
        require(calibration["status"] == "unmeasured", "SP1 baseline calibration must remain unmeasured")
        require(calibration["mesh_source_sha256"] == mesh_hash, "Calibration belongs to a stale mesh")
        animations = {}
        for animation in sequence(profile["animations"], "profile.animations"):
            object_fields(animation, {"source_motion_id", "animation_asset_id", "skeleton_id", "target_mesh_sha256",
                                      "animation_source_sha256", "duration_seconds"}, set(), "bound animation")
            mid = identifier(animation["source_motion_id"], "binding.source_motion_id")
            require(mid in motions and mid not in animations, "Unknown or duplicate bound motion")
            identifier(animation["animation_asset_id"], "binding.animation_asset_id")
            require(animation["skeleton_id"] == skeleton, "Animation uses an incompatible skeleton")
            require(animation["target_mesh_sha256"] == mesh_hash, "Animation binding targets a stale mesh")
            digest(animation["animation_source_sha256"], "binding.animation_source_sha256")
            duration = number(animation["duration_seconds"], "binding.duration_seconds", positive=True)
            require(abs(duration - motions[mid]["duration_seconds"]) <= EPS, "Bound motion duration differs from canonical clock")
            animations[mid] = animation
        profiles[pid] = (profile, animations)
    slots = {}
    for binding in sequence(bindings["slots"], "appearance slots"):
        object_fields(binding, {"appearance_slot", "profile_id"}, set(), "slot binding")
        slot = identifier(binding["appearance_slot"], "binding.appearance_slot")
        require(slot not in slots, "Duplicate appearance slot binding")
        require(isinstance(binding["profile_id"], str) and binding["profile_id"] in profiles,
                "Appearance slot references an unknown athlete profile")
        slots[slot] = binding["profile_id"]
    required_slots = {actor["appearance_slot"] for actor in document["actors"]}
    require(set(slots) == required_slots, "Bindings must cover exactly the playback appearance slots")
    for actor in document["actors"]:
        animations = profiles[slots[actor["appearance_slot"]]][1]
        require(all(s["source_motion_id"] in animations for s in actor["sections"]),
                "Athlete profile lacks an animation required by its actor")
    return {"status": "binding_metadata_validated", "playback_sha256": document_sha256(document),
            "appearance_slots": len(slots), "profiles": len(profiles),
            "native_assets_verified": False, "contact_calibration_verified": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("playback", type=Path)
    parser.add_argument("--time", type=float, default=0.0)
    parser.add_argument("--bindings", type=Path)
    args = parser.parse_args(argv)
    try:
        plan = load_playback(args.playback)
        result = {"frame": plan.evaluate(args.time), "scope": "state sampling only; no skeletal or Unreal evaluation"}
        if args.bindings:
            result["binding_validation"] = validate_bindings(plan, read_json(args.bindings))
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    except (PlaybackError, OSError) as error:
        parser.exit(2, f"Playback failed: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
