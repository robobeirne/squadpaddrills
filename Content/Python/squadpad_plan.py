"""Pure input planning for the SquadPad Unreal bootstrap.

This module does not import Unreal and does not establish animation quality.
It rejects missing assets, ambiguous units and inconsistent clip timelines before
the editor creates assets. Runtime evidence belongs in the editor receipt.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from bisect import bisect_right


class InputError(ValueError):
    """The supplied source manifest or choreography needs correction."""


def _number(value, label, minimum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InputError(f"{label} must be a finite number")
    value = float(value)
    if not math.isfinite(value) or (minimum is not None and value < minimum):
        raise InputError(f"{label} must be finite and >= {minimum}")
    return value


def _vector(value, label):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise InputError(f"{label} must contain three coordinates")
    return [_number(v, f"{label}[{i}]") for i, v in enumerate(value)]


def _rotation(value, label):
    if not isinstance(value, dict) or set(value) != {"pitch", "yaw", "roll"}:
        raise InputError(f"{label} must contain pitch, yaw and roll in degrees")
    return {k: _number(value[k], f"{label}.{k}") for k in ("pitch", "yaw", "roll")}


def _identifier(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_\-]+", value):
        raise InputError(f"{label} must use letters, numbers, underscores or hyphens")
    return value


def _source_path(root, relative_path, label, required):
    if not isinstance(relative_path, str) or not relative_path:
        raise InputError(f"{label} is required")
    relative = Path(relative_path)
    if relative.is_absolute():
        raise InputError(f"{label} must be relative to the project")
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise InputError(f"{label} escapes the project directory") from exc
    if required and not path.is_file():
        raise InputError(f"Required source file is absent: {path}")
    return path


def _read_json(path, label):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InputError(f"Cannot read {label}: {path}: {exc}") from exc


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def frame_time(seconds, frame_rate):
    """Return display-frame number and subframe without rounding event timing."""
    frames = _number(seconds, "seconds", 0) * _number(frame_rate, "frame_rate", 1)
    whole = math.floor(frames)
    subframe = frames - whole
    if subframe >= 1 - 1e-10:
        return whole + 1, 0.0
    if subframe <= 1e-10:
        return whole, 0.0
    return whole, subframe


def _keys(values, duration, label, rotation=False):
    if not isinstance(values, list) or not values:
        raise InputError(f"{label} must be a nonempty key list")
    out, previous = [], -1.0
    for i, value in enumerate(values):
        t = _number(value.get("time_seconds"), f"{label}[{i}].time_seconds", 0)
        if t <= previous or t > duration + 1e-8:
            raise InputError(f"{label} times must increase and stay inside the sequence")
        key = {"time_seconds": t, "position_cm": _vector(value.get("position_cm"), label)}
        if rotation:
            key["rotation_deg"] = _rotation(value.get("rotation_deg"), label)
        out.append(key)
        previous = t
    return out


def fill_idle_gaps(clips, duration, idle_animation_id):
    """Supply explicit, non-overlapping idle sections around prescribed actions.

    Idle is a baseline pose, not an authored transition. The receipt records every
    inserted section so this convenience cannot be confused with approved motion.
    """
    out, cursor = [], 0.0
    for clip in clips:
        start = clip["start_seconds"]
        if start > cursor + 1e-8:
            if not idle_animation_id:
                raise InputError("Timeline gaps require an idle animation or explicit clips")
            out.append({"animation_id": idle_animation_id, "start_seconds": cursor,
                        "duration_seconds": start - cursor, "play_rate": 1.0,
                        "loop": True, "source_start_seconds": 0.0,
                        "generated_idle": True})
        out.append(dict(clip))
        cursor = start + clip["duration_seconds"]
    if cursor < duration - 1e-8:
        if not idle_animation_id:
            raise InputError("Timeline gaps require an idle animation or explicit clips")
        out.append({"animation_id": idle_animation_id, "start_seconds": cursor,
                    "duration_seconds": duration - cursor, "play_rate": 1.0,
                    "loop": True, "source_start_seconds": 0.0,
                    "generated_idle": True})
    return out


def _root_descriptor(root, record, manifest, require_files):
    spec = record.get("root_motion")
    if not spec:
        return None, None
    if spec.get("mode") != "actor_track":
        raise InputError(f"Animation {record['id']} has unsupported root-motion mode")
    baked = record.get("root_motion_baked_into_skeleton", manifest.get("root_motion_baked_into_skeleton"))
    if baked is not False:
        raise InputError("Actor root traces require root_motion_baked_into_skeleton=false explicitly; "
                         "applying them to a travelling skeleton would double displacement")
    path = _source_path(root, spec.get("path"), f"animation.{record['id']}.root_motion.path", require_files)
    source = {"id": record["id"], "kind": "root_motion", "path": str(path), "sha256": None}
    if not path.is_file():
        return {"status": "missing", "source_path": str(path)}, source
    data = _read_json(path, "root-motion descriptor")
    if data.get("schema") != "squadpad.unreal.root-motion.v1":
        raise InputError(f"Unrecognised root descriptor schema: {path}")
    if data.get("axes") != "unreal-z-up-x-forward" or data.get("units") != "cm" or data.get("time_units") != "seconds":
        raise InputError(f"Root descriptor must already use Unreal centimetres/seconds: {path}")
    if data.get("animation_id") != record["id"]:
        raise InputError(f"Root descriptor references a different animation: {path}")
    maximum_time = record["duration_seconds"] + 1 / float(manifest.get("export_frame_rate", 60)) + 1e-6
    keys = _keys(data.get("keys"), maximum_time, f"root_motion.{record['id']}.keys")
    if keys[0]["time_seconds"] != 0 or keys[-1]["time_seconds"] < record["duration_seconds"] - 1e-8:
        raise InputError(f"Root descriptor does not cover the canonical source duration: {path}")
    if any(abs(key["position_cm"][2]) > 1e-5 for key in keys):
        raise InputError("Root descriptor must contain planar travel only; skeletal animation retains vertical hips motion")
    contact = data.get("contact_seconds")
    expected_contact = record.get("contact_seconds")
    if expected_contact is not None:
        contact = _number(contact, "root_motion.contact_seconds", 0)
        if abs(contact - expected_contact) > 1e-8:
            raise InputError("Root descriptor contact time differs from its canonical animation")
        zero_keys = [key for key in keys if abs(key["time_seconds"] - contact) < 1e-8]
        if len(zero_keys) != 1 or max(abs(v) for v in zero_keys[0]["position_cm"]) > 1e-5:
            raise InputError("Root descriptor must include an explicit zero offset at the exact contact time")
    elif contact is not None:
        raise InputError("A root descriptor invented a contact absent from the animation manifest")
    source["sha256"] = sha256_file(path)
    expected_hash = spec.get("sha256")
    if expected_hash and source["sha256"] != expected_hash.lower():
        raise InputError(f"Root descriptor hash mismatch: {path}")
    return dict(data, keys=keys, status="loaded", source_path=str(path), sha256=source["sha256"]), source


def _sample_keys(keys, seconds, rotation=False):
    """Linear interpolation with endpoint holds, using the same guide semantics as Sequencer."""
    times = [key["time_seconds"] for key in keys]
    upper = bisect_right(times, seconds)
    if upper == 0:
        return dict(keys[0])
    if upper >= len(keys):
        return dict(keys[-1])
    left, right = keys[upper - 1], keys[upper]
    factor = (seconds - left["time_seconds"]) / (right["time_seconds"] - left["time_seconds"])
    out = {"position_cm": [a + (b - a) * factor for a, b in zip(left["position_cm"], right["position_cm"])]}
    if rotation:
        out["rotation_deg"] = {k: left["rotation_deg"][k] +
                               (right["rotation_deg"][k] - left["rotation_deg"][k]) * factor
                               for k in ("pitch", "yaw", "roll")}
    return out


def compose_root_motion(actor, animation, contact, duration):
    """Compose one explicit contact-relative planar trace onto guide anchors.

    The first/last trace offsets are held through idle. They do not snap back to
    the guide origin, which is a contact anchor, not the athlete's rest position.
    """
    spec = actor["root_motion"]
    if spec.get("mode") != "contact_relative_clip_trace" or spec.get("coordinate_space") != "actor_local_cm":
        raise InputError("Actor root_motion requires contact_relative_clip_trace in actor_local_cm")
    candidates = [clip for clip in actor["clips"] if clip["animation_id"] == animation["id"] and not clip.get("generated_idle")]
    if len(candidates) != 1:
        raise InputError("This first-pass adapter supports one contact-relative action per actor")
    clip = candidates[0]
    if clip["loop"]:
        raise InputError("A contact-relative action trace must not loop")
    trace = animation.get("root_motion_data")
    if not trace or trace.get("status") != "loaded":
        pending = dict(actor)
        pending["root_motion"] = dict(spec, status="missing_export_descriptor")
        return pending
    anchor_time = clip["start_seconds"] + (trace["contact_seconds"] - clip["source_start_seconds"]) / clip["play_rate"]
    if abs(anchor_time - contact["time_seconds"]) > 1e-7 or contact["actor_id"] != actor["id"]:
        raise InputError("Root trace and contact event use different action clocks/actors")
    guides = actor["transform_keys"]
    if any(abs(key["rotation_deg"][k]) > 1e-7 for key in guides for k in ("pitch", "roll")):
        raise InputError("Planar actor root composition currently supports yaw-only guide rotations")
    end = clip["start_seconds"] + clip["duration_seconds"]
    times = {0.0, duration, clip["start_seconds"], end, contact["time_seconds"]}
    times.update(key["time_seconds"] for key in guides)
    for key in trace["keys"]:
        playback_time = clip["start_seconds"] + (key["time_seconds"] - clip["source_start_seconds"]) / clip["play_rate"]
        if clip["start_seconds"] <= playback_time <= end:
            times.add(playback_time)
    composed = []
    for seconds in sorted(times):
        guide = _sample_keys(guides, seconds, rotation=True)
        source_time = clip["source_start_seconds"] + min(max(seconds - clip["start_seconds"], 0), clip["duration_seconds"]) * clip["play_rate"]
        local = _sample_keys(trace["keys"], source_time)["position_cm"]
        angle = math.radians(guide["rotation_deg"]["yaw"])
        rotated = [local[0] * math.cos(angle) - local[1] * math.sin(angle),
                   local[0] * math.sin(angle) + local[1] * math.cos(angle), local[2]]
        composed.append({"time_seconds": seconds,
                         "position_cm": [a + b for a, b in zip(guide["position_cm"], rotated)],
                         "rotation_deg": guide["rotation_deg"]})
    return dict(actor, guide_transform_keys=guides, guide_position_cm=actor["position_cm"],
                position_cm=composed[0]["position_cm"], transform_keys=composed,
                root_motion=dict(spec, status="composed_from_export_descriptor", source_path=trace["source_path"],
                                 source_sha256=trace["sha256"], endpoint_policy="hold_first_and_last_trace_offsets_through_idle",
                                 application_count=1))


def plan_import(manifest_path, project_root=None, require_files=True):
    """Return a serializable build plan. This is input validation only."""
    manifest_path = Path(manifest_path).expanduser().resolve()
    root = Path(project_root).expanduser().resolve() if project_root else manifest_path.parent
    manifest = _read_json(manifest_path, "asset manifest")
    if manifest.get("schema") != "squadpad.unreal.import.v1":
        raise InputError("Expected manifest schema squadpad.unreal.import.v1")
    fixture_path = _source_path(root, manifest.get("fixture_path"), "fixture_path", True)
    fixture = _read_json(fixture_path, "drill fixture")
    if fixture.get("schema") != "squadpad.drill.fixture.v1":
        raise InputError("Expected fixture schema squadpad.drill.fixture.v1")
    if fixture.get("units") != {"distance": "centimetres", "time": "seconds", "angle": "degrees"}:
        raise InputError("Fixture units must be centimetres, seconds and degrees")
    if fixture.get("axes") != "unreal-z-up-x-forward":
        raise InputError("Fixture must already use unreal-z-up-x-forward coordinates")
    duration = _number(fixture.get("duration_seconds"), "duration_seconds", 0.000001)
    fps = _number(fixture.get("frame_rate", 60), "frame_rate", 1)
    if fps != int(fps):
        raise InputError("This bootstrap requires an integer display frame rate")
    fps = int(fps)
    mesh_records = manifest.get("meshes") or [manifest.get("mesh")]
    if not all(isinstance(m, dict) for m in mesh_records):
        raise InputError("Manifest requires a mesh record")
    animations = manifest.get("animations")
    if not isinstance(animations, list) or not animations:
        raise InputError("Manifest requires animation records")
    meshes, clips, sources = {}, {}, []
    for kind, records, destination in (("mesh", mesh_records, meshes), ("animation", animations, clips)):
        for record in records:
            ident = _identifier(record.get("id"), f"{kind}.id")
            if ident in destination:
                raise InputError(f"Duplicate {kind} ID: {ident}")
            path = _source_path(root, record.get("fbx"), f"{kind}.{ident}.fbx", require_files)
            if path.suffix.lower() != ".fbx":
                raise InputError(f"{kind}.{ident} requires a same-skeleton FBX source")
            source_hash = None
            if path.is_file():
                with path.open("rb") as stream:
                    signature = stream.read(4096)
                if not (signature.startswith(b"Kaydara FBX Binary") or b"FBXHeaderExtension" in signature):
                    raise InputError(f"Source is not recognisable FBX data: {path}")
                source_hash = sha256_file(path)
                expected = record.get("sha256")
                if expected and expected.lower() != source_hash:
                    raise InputError(f"Source hash mismatch: {path}")
            prepared = dict(record, id=ident, source_path=str(path), sha256=source_hash)
            prepared["name"] = _identifier(record.get("name", ident), f"{kind}.{ident}.name").replace("-", "_")
            if kind == "animation":
                prepared["duration_seconds"] = _number(record.get("duration_seconds"), f"animation.{ident}.duration_seconds", 0.000001)
                prepared["mesh_id"] = record.get("mesh_id", next(iter(meshes)))
                if prepared["mesh_id"] not in meshes:
                    raise InputError(f"Animation {ident} references an unknown mesh")
                prepared["root_motion_data"], root_source = _root_descriptor(root, prepared, manifest, require_files)
                if root_source:
                    sources.append(root_source)
            destination[ident] = prepared
            sources.append({"id": ident, "kind": kind, "path": str(path), "sha256": source_hash})
    actor_records = fixture.get("actors")
    ball_records = fixture.get("balls")
    if not isinstance(actor_records, list) or not actor_records:
        raise InputError("Fixture requires actors")
    if not isinstance(ball_records, list) or not ball_records:
        raise InputError("Fixture requires balls")
    actor_ids, ball_ids, actors, balls = set(), set(), [], []
    for actor in actor_records:
        ident = _identifier(actor.get("id"), "actor.id")
        if ident in actor_ids:
            raise InputError(f"Duplicate actor ID: {ident}")
        actor_ids.add(ident)
        if actor.get("mesh_id") not in meshes:
            raise InputError(f"Actor {ident} references an unknown mesh")
        prepared = dict(actor)
        prepared["position_cm"] = _vector(actor.get("position_cm"), f"actor.{ident}.position_cm")
        prepared["rotation_deg"] = _rotation(actor.get("rotation_deg"), f"actor.{ident}.rotation_deg")
        prepared["transform_keys"] = _keys(actor.get("transform_keys") or [{"time_seconds": 0, "position_cm": prepared["position_cm"], "rotation_deg": prepared["rotation_deg"]}], duration, f"actor.{ident}.transform_keys", True)
        timeline, previous_end = [], 0.0
        for i, clip in enumerate(actor.get("clips", [])):
            aid = clip.get("animation_id")
            if aid not in clips or clips[aid]["mesh_id"] != actor["mesh_id"]:
                raise InputError(f"Actor {ident} clip {i} has an incompatible animation ID")
            start = _number(clip.get("start_seconds"), "clip.start_seconds", 0)
            length = _number(clip.get("duration_seconds"), "clip.duration_seconds", 0.000001)
            rate = _number(clip.get("play_rate", 1.0), "clip.play_rate", 0.000001)
            offset = _number(clip.get("source_start_seconds", 0), "clip.source_start_seconds", 0)
            loop = clip.get("loop", False)
            if not isinstance(loop, bool):
                raise InputError("clip.loop must be a boolean")
            if start < previous_end - 1e-8 or start + length > duration + 1e-8:
                raise InputError(f"Actor {ident} has overlapping or out-of-range action sections")
            if offset >= clips[aid]["duration_seconds"]:
                raise InputError(f"Actor {ident} clip source offset exceeds its animation")
            if not loop and offset + length * rate > clips[aid]["duration_seconds"] + 1 / fps:
                raise InputError(f"Actor {ident} clip {aid} would repeat despite loop=false")
            timeline.append(dict(clip, start_seconds=start, duration_seconds=length, play_rate=rate,
                                 source_start_seconds=offset, loop=loop, generated_idle=False))
            previous_end = start + length
        idle_id = manifest.get("idle_animation_id", "idle")
        if idle_id not in clips or clips[idle_id]["mesh_id"] != actor["mesh_id"]:
            idle_id = None
        prepared["clips"] = fill_idle_gaps(timeline, duration, idle_id)
        actors.append(prepared)
    for ball in ball_records:
        ident = _identifier(ball.get("id"), "ball.id")
        if ident in ball_ids or ident in actor_ids:
            raise InputError(f"Duplicate scene-object ID: {ident}")
        ball_ids.add(ident)
        balls.append(dict(ball, radius_cm=_number(ball.get("radius_cm"), "ball.radius_cm", 0.000001),
                          position_cm=_vector(ball.get("position_cm"), "ball.position_cm"),
                          keys=_keys(ball.get("keys"), duration, f"ball.{ident}.keys")))
    contacts = fixture.get("contacts", [])
    for contact in contacts:
        _number(contact.get("time_seconds"), "contact.time_seconds", 0)
        if contact["time_seconds"] > duration or contact.get("actor_id") not in actor_ids or contact.get("ball_id") not in ball_ids:
            raise InputError("Contact references an unknown object or a time outside the sequence")
    for index, actor in enumerate(actors):
        spec = actor.get("root_motion")
        if not spec:
            continue
        animation_id = spec.get("animation_id")
        if animation_id not in clips or not clips[animation_id].get("root_motion"):
            raise InputError(f"Actor {actor['id']} requires an explicit root_motion descriptor in the animation manifest")
        matches = [contact for contact in contacts if contact.get("id") == spec.get("anchor_contact_id")]
        if len(matches) != 1:
            raise InputError("Actor root-motion anchor must reference exactly one contact")
        actors[index] = compose_root_motion(actor, clips[animation_id], matches[0], duration)
    export_evidence = {"status": "not_declared"}
    if manifest.get("export_receipt_path"):
        path = _source_path(root, manifest["export_receipt_path"], "export_receipt_path", require_files)
        source = {"id": "export_receipt", "kind": "export_receipt", "path": str(path), "sha256": None}
        export_evidence = {"status": "missing", "path": str(path)}
        if path.is_file():
            export = _read_json(path, "export receipt")
            if export.get("schema") != "squadpad.unreal.export-receipt.v1" or export.get("status") != "fbx_roundtrip_structural_passed_unreal_validation_pending":
                raise InputError("Export receipt does not establish successful structural source export")
            outputs = export.get("outputs")
            if not isinstance(outputs, dict):
                raise InputError("Export receipt requires an output filename/hash mapping")
            for item in sources:
                if item["kind"] not in ("mesh", "animation", "root_motion"):
                    continue
                output = outputs.get(Path(item["path"]).name)
                if not isinstance(output, dict) or not output.get("sha256"):
                    raise InputError(f"Export receipt omits required output: {item['path']}")
                if item["sha256"] and (item["sha256"] != output["sha256"] or Path(item["path"]).stat().st_size != output.get("bytes")):
                    raise InputError(f"Export receipt hash/length mismatch: {item['path']}")
            source["sha256"] = sha256_file(path)
            export_evidence = {"status": "structural_export_receipt_checked", "path": str(path),
                               "sha256": source["sha256"], "export_status": export["status"],
                               "scope": "Blender structural export provenance, not native Unreal or animation quality"}
        sources.append(source)
    return {"schema": "squadpad.unreal.build-plan.v1", "project_root": str(root),
            "manifest_path": str(manifest_path), "manifest_sha256": sha256_file(manifest_path),
            "fixture_path": str(fixture_path), "fixture_sha256": sha256_file(fixture_path),
            "fixture_id": fixture.get("id"), "title": fixture.get("title", "SquadPad first pass"),
            "provenance": fixture.get("provenance", {}), "target_engine": manifest.get("target_engine", "5.8"),
            "clock": fixture.get("clock", {}), "selection": fixture.get("selection", {}),
            "possession": fixture.get("possession", []), "readiness": fixture.get("readiness", {}),
            "source_payload": fixture.get("source_payload", {}),
            "coordinate_transform": fixture.get("coordinate_transform", {}),
            "export_evidence": export_evidence,
            "import_settings": manifest.get("import_settings", {}), "duration_seconds": duration,
            "frame_rate": fps, "meshes": meshes, "animations": clips, "actors": actors,
            "balls": balls, "contacts": contacts, "camera": fixture.get("camera"),
            "sources": sources, "all_source_files_present": all(x["sha256"] for x in sources),
            "evidence_scope": "input planning only, no Unreal import or runtime validation"}
