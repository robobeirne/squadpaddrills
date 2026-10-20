"""Contact metadata and 3D measurement checks, without an engine dependency.

The included fixture has NO measured contacts. A successful metadata check cannot
be interpreted as a successful geometry check. Geometry is checked only against
supplied, identified evaluations of a calibrated surface on the intended actor.
"""

from __future__ import annotations

import math
import re


class QualityInputError(ValueError):
    """The contact contract or submitted evidence is inconsistent."""


def _require(condition, message):
    if not condition:
        raise QualityInputError(message)


def _number(value, name, minimum=None):
    _require(isinstance(value, (float, int)) and not isinstance(value, bool),
             f"{name} must be a finite number")
    _require(math.isfinite(value), f"{name} must be finite")
    if minimum is not None:
        _require(value >= minimum, f"{name} must be >= {minimum}")
    return float(value)


def _text(value, name):
    _require(isinstance(value, str) and bool(value.strip()), f"{name} is required")
    return value


def _vector(value, name):
    _require(isinstance(value, list) and len(value) == 3,
             f"{name} must contain three coordinates")
    return [_number(item, f"{name}[{i}]") for i, item in enumerate(value)]


def _hash(value, name):
    _require(isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value),
             f"{name} must be a lower-case SHA-256")
    return value


def validate_contacts(plan):
    """Validate intent and timing. Return JSON-safe findings, never geometry proof.

    Raises QualityInputError (a ValueError) for invalid references, ambiguous
    clocks or calibration records. Unmeasured geometry is valid input, reported
    explicitly as unmeasured. This function is shared by the offline preflight
    and Unreal bootstrap.
    """
    actors = {actor["id"]: actor for actor in plan["actors"]}
    balls = {ball["id"]: ball for ball in plan["balls"]}
    _require(isinstance(plan.get("contacts"), list) and plan["contacts"],
             "At least one intended contact is required for this review fixture")
    seen, findings, unmeasured = set(), [], 0
    for contact in plan["contacts"]:
        _require(isinstance(contact, dict), "Contact records must be objects")
        ident = _text(contact.get("id"), "contact.id")
        _require(ident not in seen, f"Duplicate contact ID: {ident}")
        seen.add(ident)
        for key in ("event", "actor_id", "ball_id", "limb", "surface", "animation_id"):
            _text(contact.get(key), f"{ident}.{key}")
        _require(contact["actor_id"] in actors, f"{ident}: unknown actor")
        _require(contact["ball_id"] in balls, f"{ident}: unknown ball")
        actor = actors[contact["actor_id"]]
        aid = contact["animation_id"]
        _require(aid in plan["animations"], f"{ident}: unknown animation")
        animation = plan["animations"][aid]
        _require(animation["mesh_id"] == actor["mesh_id"],
                 f"{ident}: animation belongs to a different mesh")
        source_limb = animation.get("source_limb")
        if source_limb:
            normalized = re.sub(r"[^a-z]", "", re.sub(r"^mixamorig:?", "", source_limb,
                                                       flags=re.IGNORECASE).lower())
            limb_names = {"rightfoot": "right_foot", "righttoebase": "right_foot",
                          "leftfoot": "left_foot", "lefttoebase": "left_foot",
                          "righthand": "right_hand", "lefthand": "left_hand"}
            _require(normalized in limb_names and contact["limb"] == limb_names[normalized],
                     f"{ident}: intended limb differs from the source animation metadata")
        scene_time = _number(contact.get("time_seconds"), f"{ident}.time_seconds", 0)
        _require(scene_time <= plan["duration_seconds"], f"{ident}: contact is outside playback")
        if plan.get("clock") and "source_time_seconds" in contact:
            clock = plan["clock"]
            source_time = _number(contact["source_time_seconds"], f"{ident}.source_time_seconds", 0)
            offset = _number(clock.get("source_to_playback_offset_seconds"), "clock offset", 0)
            scale = _number(clock.get("scale"), "clock scale", 0.000001)
            _require(abs(scene_time - (offset + source_time * scale)) <= 1e-6,
                     f"{ident}: playback changed the source choreography clock")
        local_time = _number(contact.get("animation_contact_seconds"),
                             f"{ident}.animation_contact_seconds", 0)
        _require(local_time <= animation["duration_seconds"],
                 f"{ident}: source contact is outside animation")
        declared = animation.get("contact_seconds")
        if declared is not None:
            declared = _number(declared, f"animation.{aid}.contact_seconds", 0)
            _require(abs(declared - local_time) <= 1e-6,
                     f"{ident}: fixture and manifest contact clocks disagree")
        matching = []
        for clip in actor["clips"]:
            if clip["animation_id"] != aid or clip.get("generated_idle"):
                continue
            offset = clip.get("source_start_seconds", 0)
            expected = clip["start_seconds"] + (local_time - offset) / clip["play_rate"]
            end = clip["start_seconds"] + clip["duration_seconds"]
            if clip["start_seconds"] - 1e-8 <= expected <= end + 1e-8:
                if abs(expected - scene_time) <= 1e-6:
                    _require(not clip["loop"],
                             f"{ident}: contact needs a single, identified action occurrence")
                    matching.append(expected)
        _require(len(matching) == 1,
                 f"{ident}: exactly one action section must align with the contact clock")
        calibration = contact.get("calibration")
        _require(isinstance(calibration, dict), f"{ident}: calibration object is required")
        status = calibration.get("status")
        _require(status in ("unmeasured", "measured"),
                 f"{ident}: calibration status must be unmeasured or measured")
        if status == "unmeasured":
            _require(calibration.get("bone_name") is None and calibration.get("local_offset_cm") is None,
                     f"{ident}: unmeasured contacts must not claim a calibrated bone offset")
            unmeasured += 1
        else:
            _text(calibration.get("id"), f"{ident}.calibration.id")
            _text(calibration.get("bone_name"), f"{ident}.calibration.bone_name")
            _text(calibration.get("method"), f"{ident}.calibration.method")
            _vector(calibration.get("local_offset_cm"), f"{ident}.calibration.local_offset_cm")
            normal = _vector(calibration.get("local_normal"), f"{ident}.calibration.local_normal")
            _require(abs(math.sqrt(sum(v * v for v in normal)) - 1) <= 1e-4,
                     f"{ident}: calibrated surface normal must have unit length")
            _hash(calibration.get("mesh_source_sha256"), f"{ident}.calibration.mesh_source_sha256")
            _require("unmeasured" not in contact["surface"].lower(),
                     f"{ident}: measured contact needs an identified, calibrated surface")
        findings.append({"id": ident, "actor_id": contact["actor_id"],
                         "ball_id": contact["ball_id"], "limb": contact["limb"],
                         "time_seconds": scene_time, "calibration_status": status})
    return {"status": "valid", "contacts": findings, "unmeasured_count": unmeasured,
            "scope": "contact metadata and clocks only; no evaluated geometry"}


def check_contact_geometry(plan, measurements=None, distance_tolerance_cm=1.5,
                           time_tolerance_seconds=None):
    """Check named boot/hand surface points against the intended ball in 3D.

    Default distance/time tolerances are proposed review thresholds, not validated
    realism criteria. A pass covers these submitted contact instants only. It
    does not prove foot planting, cushioning, collision response or visual quality.
    The caller supplies genuine Unreal-evaluated measurements; declarations alone
    are not a substitute for producing and inspecting that evidence.
    """
    validate_contacts(plan)
    tolerance = _number(distance_tolerance_cm, "distance_tolerance_cm", 0)
    if time_tolerance_seconds is None:
        time_tolerance_seconds = 0.5 / plan["frame_rate"]
    time_tolerance = _number(time_tolerance_seconds, "time_tolerance_seconds", 0)
    report = {"schema": "squadpad.contact.geometry-report.v1", "status": "blocked",
              "scope": "identified contact instants from submitted 3D evaluations only",
              "fixture_sha256": plan["fixture_sha256"],
              "thresholds": {"distance_tolerance_cm": tolerance,
                             "time_tolerance_seconds": time_tolerance,
                             "surface_normal_must_face_ball": True,
                             "basis": "proposed review thresholds; not a visual-quality score"},
              "contacts": [], "visual_quality_accepted": False}
    if measurements is not None:
        _require(isinstance(measurements, dict), "Measurements must be an object")
        _require(measurements.get("schema") == "squadpad.contact.measurements.v1",
                 "Unexpected measurements schema")
        _require(measurements.get("units") == {"distance": "centimetres", "time": "seconds"},
                 "Measurement units must be centimetres and seconds")
        _require(measurements.get("axes") == "unreal-z-up-x-forward", "Measurement axes disagree")
        _require(measurements.get("fixture_sha256") == plan["fixture_sha256"],
                 "Measurement fixture hash differs from the current fixture")
        evaluation = measurements.get("evaluation")
        _require(isinstance(evaluation, dict), "Measurement evaluation provenance is required")
        _require(evaluation.get("kind") == "unreal_evaluated_sequence",
                 "Geometry needs an evaluated Unreal sequence, not predicted fixture positions")
        for key in ("engine_version", "sequence_asset_path", "run_id", "captured_at_utc"):
            _text(evaluation.get(key), f"evaluation.{key}")
        _require(evaluation["sequence_asset_path"].startswith("/Game/"),
                 "Evaluation must identify the saved Unreal sequence")
        _hash(evaluation.get("build_receipt_sha256"), "evaluation.build_receipt_sha256")
        samples = measurements.get("samples")
        _require(isinstance(samples, list), "Measurement samples must be a list")
        expected_ids = {contact["id"] for contact in plan["contacts"]}
        sample_ids = []
        for sample in samples:
            _require(isinstance(sample, dict), "Measurement samples must be objects")
            ident = _text(sample.get("contact_id"), "sample.contact_id")
            _require(ident in expected_ids, f"Unknown measured contact: {ident}")
            sample_ids.append(ident)
        _require(len(sample_ids) == len(set(sample_ids)),
                 "Submit one identified evaluation per contact, not a nearest-sample search")
        by_id = {sample["contact_id"]: sample for sample in samples}
        report["evaluation"] = evaluation
    else:
        by_id = {}
    actors = {actor["id"]: actor for actor in plan["actors"]}
    balls = {ball["id"]: ball for ball in plan["balls"]}
    for contact in plan["contacts"]:
        result = {"id": contact["id"], "actor_id": contact["actor_id"],
                  "ball_id": contact["ball_id"], "limb": contact["limb"],
                  "surface": contact["surface"], "status": "blocked", "reasons": []}
        report["contacts"].append(result)
        calibration = contact["calibration"]
        if calibration["status"] != "measured":
            result["reasons"].append("No calibrated target-athlete surface exists")
            continue
        mesh = plan["meshes"][actors[contact["actor_id"]]["mesh_id"]]
        if not mesh.get("sha256"):
            result["reasons"].append("Target mesh source bytes are absent")
            continue
        if calibration["mesh_source_sha256"] != mesh["sha256"]:
            result["status"] = "fail"
            result["reasons"].append("Surface calibration belongs to a different mesh source")
            continue
        sample = by_id.get(contact["id"])
        if sample is None:
            result["reasons"].append("No evaluated 3D measurement was submitted for this contact")
            continue
        for key in ("actor_id", "ball_id", "limb", "surface"):
            if sample.get(key) != contact[key]:
                result["reasons"].append(f"Measurement identifies a different {key}")
        if sample.get("calibration_id") != calibration["id"]:
            result["reasons"].append("Measurement uses a different surface calibration")
        if sample.get("mesh_source_sha256") != mesh["sha256"]:
            result["reasons"].append("Measurement belongs to a different mesh source")
        if result["reasons"]:
            result["status"] = "fail"
            continue
        sampled_at = _number(sample.get("time_seconds"), "sample.time_seconds", 0)
        time_error = abs(sampled_at - contact["time_seconds"])
        ball = _vector(sample.get("ball_center_cm"), "sample.ball_center_cm")
        point = _vector(sample.get("contact_point_cm"), "sample.contact_point_cm")
        normal = _vector(sample.get("surface_normal_world"), "sample.surface_normal_world")
        _require(abs(math.sqrt(sum(v * v for v in normal)) - 1) <= 1e-4,
                 "Measured world surface normal must have unit length")
        delta = [ball[i] - point[i] for i in range(3)]
        separation = math.sqrt(sum(v * v for v in delta))
        radius = balls[contact["ball_id"]]["radius_cm"]
        gap = separation - radius
        facing = sum(normal[i] * delta[i] for i in range(3))
        result.update({"sample_time_seconds": sampled_at, "time_error_seconds": time_error,
                       "surface_to_ball_center_cm": separation, "ball_radius_cm": radius,
                       "sphere_surface_gap_cm": gap, "normal_facing_dot_cm": facing})
        if time_error > time_tolerance + 1e-9:
            result["reasons"].append("Evaluated instant lies outside the event time tolerance")
        if abs(gap) > tolerance + 1e-9:
            result["reasons"].append("Calibrated surface and ball surface do not meet in 3D")
        if facing <= 0:
            result["reasons"].append("Ball lies behind the calibrated outward-facing surface")
        result["status"] = "fail" if result["reasons"] else "pass"
    states = {result["status"] for result in report["contacts"]}
    report["status"] = "fail" if "fail" in states else "blocked" if "blocked" in states else "pass"
    return report
