#!/usr/bin/env python3
"""Derive a shoulder/collar weight and sleeve-cap correction for athlete A.

The uploaded GLB has no complete torso/shoulder skin under the kit. This recipe
fairs original shirt weights across seams, not across a missing body surface.
It preserves all supplied body geometry/weights, the rig, UVs and image bytes.
The upper sleeve caps receive a bounded smooth fit adjustment.
Run with Python, NumPy and SciPy. Blender is needed for subsequent FBX export.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
from scipy.spatial import cKDTree

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "Tools/Export"))
from retarget_core import Rig, read_glb, sha256, write_glb

SOURCE_SHA256 = "9a8d3106b79d04638d98ef35f74ab5c23bcbadb6901c40e2a97b648ce116542c"
COMPONENTS = {5120: "i1", 5121: "u1", 5122: "<i2", 5123: "<u2", 5125: "<u4", 5126: "<f4"}
WIDTHS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def decode(doc, binary, index):
    accessor = doc["accessors"][index]
    if "sparse" in accessor:
        raise ValueError("This bounded source recipe does not decode sparse accessors")
    view = doc["bufferViews"][accessor["bufferView"]]
    dtype = np.dtype(COMPONENTS[accessor["componentType"]])
    width = WIDTHS[accessor["type"]]
    offset = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    stride = view.get("byteStride", width * dtype.itemsize)
    result = np.ndarray((accessor["count"], width), dtype, binary,
                        offset=offset, strides=(stride, dtype.itemsize)).copy()
    if accessor.get("normalized"):
        result = result.astype(np.float64) / np.iinfo(dtype).max
        if dtype.kind == "i":
            result = np.maximum(result, -1.)
    return result


def smooth(low, high, values):
    t = np.clip((values - low) / (high - low), 0., 1.)
    return t * t * (3. - 2. * t)


def dense_weights(joints, weights, count):
    result = np.zeros((len(joints), count))
    for slot in range(4):
        np.add.at(result, (np.arange(len(joints)), joints[:, slot]), weights[:, slot])
    return result


def shirt_weight_sampler(doc, raw, rest, count):
    """Average coincident seam vertices, then fair original weights locally."""
    node = next(n for n in doc["nodes"] if n.get("name") == "Kit_Shirt")
    raw_positions, all_weights = [], []
    for primitive in doc["meshes"][node["mesh"]]["primitives"]:
        attr = primitive["attributes"]
        raw_positions.append(decode(doc, raw, attr["POSITION"]))
        joints = decode(doc, raw, attr["JOINTS_0"]).astype(int)
        weights = decode(doc, raw, attr["WEIGHTS_0"])
        all_weights.append(dense_weights(joints, weights, count))
    unique, inverse = np.unique(np.vstack(raw_positions), axis=0, return_inverse=True)
    pooled = np.zeros((len(unique), count))
    np.add.at(pooled, inverse, np.vstack(all_weights))
    pooled /= np.bincount(inverse)[:, None]
    points = (rest[0] @ np.column_stack((unique, np.ones(len(unique)))).T).T[:, :3]
    tree = cKDTree(points)

    def sample(positions):
        result = np.zeros((len(positions), count))
        for start in range(0, len(positions), 256):
            chunk = positions[start:start + 256]
            distances, indices = tree.query(chunk, k=min(128, len(points)))
            kernel = np.exp(-.5 * (distances / .022) ** 2)
            kernel /= kernel.sum(axis=1, keepdims=True)
            result[start:start + len(chunk)] = np.einsum("vk,vkj->vj", kernel, pooled[indices])
        return result
    return sample


def append_accessor(doc, binary, values, component_type, type_name):
    binary.extend(b"\0" * ((-len(binary)) % 4))
    raw = np.asarray(values, dtype=COMPONENTS[component_type]).tobytes(order="C")
    view = len(doc["bufferViews"])
    doc["bufferViews"].append({"buffer": 0, "byteOffset": len(binary),
                               "byteLength": len(raw), "target": 34962})
    binary.extend(raw)
    index = len(doc["accessors"])
    doc["accessors"].append({"bufferView": view, "componentType": component_type,
                             "count": len(values), "type": type_name})
    return index


def shape_caps(positions, rig):
    """Reduce padded upper sleeve volume with a smooth, bounded rest-space warp."""
    result = positions.copy()
    lateral = np.abs(positions[:, 0])
    lateral_mask = smooth(.125, .205, lateral) * (1. - smooth(.255, .31, lateral))
    side = positions[:, 0] >= 0
    left = rig.rest_world_trs[rig.bones["LeftArm"]][0]
    right = rig.rest_world_trs[rig.bones["RightArm"]][0]
    centre_y = np.where(side, left[1], right[1])
    centre_z = np.where(side, left[2], right[2])
    upper = smooth(-.005, .06, positions[:, 1] - centre_y)
    amount = lateral_mask * upper
    result[:, 1] -= .018 * amount
    result[:, 2] = centre_z + (positions[:, 2] - centre_z) * (1. - .12 * amount)
    return result


def cap_geometry(doc, raw, attributes, rest_positions, rig, rest_decode):
    """Push supplied normals through the warp Jacobian, retaining crease splits."""
    raw_positions = decode(doc, raw, attributes["POSITION"])
    normals = decode(doc, raw, attributes["NORMAL"]).astype(np.float64)
    shaped = shape_caps(rest_positions, rig)
    distances = np.linalg.norm(shaped - rest_positions, axis=1)
    affected = distances > 1e-9
    if not affected.any():
        return None
    inverse = np.linalg.inv(rest_decode)
    updated_positions = raw_positions.copy()
    updated_positions[affected] = (inverse @ np.column_stack((shaped[affected], np.ones(affected.sum()))).T).T[:, :3]
    active = rest_positions[affected]
    jacobian = np.empty((len(active), 3, 3))
    epsilon = 1e-5
    for axis in range(3):
        plus, minus = active.copy(), active.copy()
        plus[:, axis] += epsilon
        minus[:, axis] -= epsilon
        jacobian[:, :, axis] = (shape_caps(plus, rig) - shape_caps(minus, rig)) / (2. * epsilon)
    determinants = np.linalg.det(jacobian)
    if not np.isfinite(determinants).all() or determinants.min() < .4:
        raise ValueError("Cap fit warp folds or excessively collapses space")
    # glTF normals use the same quantization-decode basis as positions.
    world_normals = (np.linalg.inv(rest_decode[:3, :3]).T @ normals[affected].T).T
    warped_normals = np.linalg.solve(jacobian.transpose(0, 2, 1), world_normals[..., None])[..., 0]
    local_normals = (rest_decode[:3, :3].T @ warped_normals.T).T
    local_normals /= np.linalg.norm(local_normals, axis=1, keepdims=True)
    normals[affected] = local_normals
    # The old normalized integer normals permit quantization-length error.
    # Promoted FLOAT normals must be unit vectors, including untouched regions.
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    if not np.isfinite(lengths).all() or np.any(lengths < 1e-8):
        raise ValueError("Invalid source normal length")
    normals /= lengths
    return updated_positions, normals, {"vertices_shaped": int(affected.sum()),
            "maximum_displacement_m": float(distances.max()),
            "minimum_sampled_jacobian_determinant": float(determinants.min()),
            "maximum_vertical_reduction_m": float(np.max(rest_positions[:, 1] - shaped[:, 1]))}


def repair(source, destination, receipt_path):
    source = source.resolve(strict=True)
    destination = destination.resolve()
    receipt_path = receipt_path.resolve()
    if destination == source or destination == receipt_path or receipt_path == source or destination.exists() or receipt_path.exists():
        raise ValueError("Use fresh derived output and receipt paths; never overwrite an input")
    source_hash = sha256(source)
    if source_hash != SOURCE_SHA256:
        raise ValueError("This measured correction is specific to the supplied athlete A variant")
    doc, raw = read_glb(source)
    derived = copy.deepcopy(doc)
    binary = bytearray(raw)
    rig = Rig(doc)
    skin = doc["skins"][0]
    names = [rig.names[node] for node in skin["joints"]]
    ibm = decode(doc, raw, skin["inverseBindMatrices"]).reshape(-1, 4, 4).transpose(0, 2, 1)
    rest = np.asarray([np.asarray(rig.rest_world[node]).reshape(4, 4).T
                       for node in skin["joints"]]) @ ibm
    if np.max(np.abs(rest - rest[0])) > 2e-6:
        raise ValueError("Unexpected common rest/quantization decode")
    sample_weights = shirt_weight_sampler(doc, raw, rest, len(names))
    records = []
    for node in doc["nodes"]:
        if node.get("name") not in ("Kit_Shirt", "Crest", "Number"):
            continue
        mesh = node["mesh"]
        for primitive_index, primitive in enumerate(doc["meshes"][mesh]["primitives"]):
            attributes = primitive["attributes"]
            points = decode(doc, raw, attributes["POSITION"])
            joints = decode(doc, raw, attributes["JOINTS_0"]).astype(int)
            weights = decode(doc, raw, attributes["WEIGHTS_0"])
            homogeneous = np.column_stack((points, np.ones(len(points))))
            positions = np.einsum("vijk,vk,vi->vj", rest[joints], homogeneous, weights)[:, :3]
            lateral, height = np.abs(positions[:, 0]), positions[:, 1]
            influence = (.9 * (1. - smooth(.255, .31, lateral))
                         * smooth(1.375, 1.465, height))
            original = dense_weights(joints, weights, len(names))
            field = sample_weights(positions)
            combined = original * (1. - influence[:, None]) + field * influence[:, None]
            # Four influences remain the runtime contract. Sort deterministically.
            selected = np.argsort(-combined, axis=1, kind="stable")[:, :4]
            selected_weights = np.take_along_axis(combined, selected, axis=1)
            selected_weights /= selected_weights.sum(axis=1, keepdims=True)
            altered = np.zeros_like(combined)
            np.put_along_axis(altered, selected, selected_weights, axis=1)
            changed = np.max(np.abs(altered - original), axis=1) > 1e-8
            # Preserve exact original slot order and decoded values outside mask.
            selected[~changed] = joints[~changed]
            selected_weights[~changed] = weights[~changed]
            if not np.isfinite(selected_weights).all() or (selected_weights < 0).any():
                raise ValueError("Invalid derived weights")
            geometry = cap_geometry(doc, raw, attributes, positions, rig, rest[0])
            if not changed.any() and geometry is None:
                continue
            target_attributes = derived["meshes"][mesh]["primitives"][primitive_index]["attributes"]
            if changed.any():
                target_attributes["JOINTS_0"] = append_accessor(derived, binary, selected, 5121, "VEC4")
                target_attributes["WEIGHTS_0"] = append_accessor(derived, binary, selected_weights, 5126, "VEC4")
            shape_record = None
            if geometry is not None:
                updated_positions, updated_normals, shape_record = geometry
                updated_positions = np.asarray(updated_positions, dtype="<f4")
                position_accessor = append_accessor(derived, binary, updated_positions, 5126, "VEC3")
                derived["accessors"][position_accessor].update(min=updated_positions.min(axis=0).tolist(),
                                                            max=updated_positions.max(axis=0).tolist())
                target_attributes["POSITION"] = position_accessor
                target_attributes["NORMAL"] = append_accessor(derived, binary, updated_normals, 5126, "VEC3")
            current_positions = np.einsum("vijk,vk,vi->vj", rest[selected], homogeneous, selected_weights)[:, :3]
            records.append({"node": node["name"], "primitive": primitive_index,
                            "vertices_total": len(points), "weights_changed_vertices": int(changed.sum()),
                            "maximum_rest_surface_difference_from_weights_only_m": float(np.max(np.linalg.norm(current_positions - positions, axis=1))),
                            "maximum_weight_sum_error": float(np.max(np.abs(selected_weights.sum(axis=1) - 1.))),
                            "cap_shape": shape_record})
    if not records:
        raise ValueError("No demonstrated shoulder region was corrected")
    provenance = {"kind": "shoulder_weight_fairing_and_cap_fit_v1", "source_sha256": source_hash,
                  "recipe_sha256": sha256(Path(__file__)),
                  "description": "Fair original upper-shirt influences and reduce sleeve-cap bulk; preserve all supplied body geometry and weights",
                  "parameters": {"gaussian_sigma_m": .022, "maximum_neighbours": 128,
                                 "maximum_weight_blend": .9, "height_fade_m": [1.375, 1.465],
                                 "lateral_fade_m": [.255, .31], "cap_vertical_reduction_m": .018,
                                 "cap_depth_reduction_fraction": .12,
                                 "cap_lateral_fades_m": [[.125, .205], [.255, .31]],
                                 "cap_vertical_gate_above_arm_joint_m": [-.005, .06],
                                 "normal_jacobian_step_m": 1e-5}}
    derived.setdefault("extras", {})["squadpad_derived_correction"] = provenance
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_glb(destination, derived, binary)
    saved_doc, saved_binary = read_glb(destination)
    if any(saved_doc[key] != doc[key] for key in ("nodes", "skins", "materials", "textures", "images")):
        raise ValueError("Correction changed the rig or material contract")
    if saved_binary[:len(raw)] != raw or sha256(source) != source_hash:
        raise ValueError("Source geometry or bytes changed")
    for node in doc["nodes"]:
        if "mesh" in node and node.get("name") not in ("Kit_Shirt", "Crest", "Number"):
            if saved_doc["meshes"][node["mesh"]] != doc["meshes"][node["mesh"]]:
                raise ValueError("A mesh outside the bounded kit correction was modified")
    shirt_mesh = next(n["mesh"] for n in doc["nodes"] if n.get("name") == "Kit_Shirt")
    if saved_doc["meshes"][shirt_mesh]["primitives"][2] != doc["meshes"][shirt_mesh]["primitives"][2]:
        raise ValueError("The original cuff Trim must remain unchanged")
    report = {"schema": "squadpad.athlete.shoulder-correction.v1", "status": "derived_weights_and_cap_geometry_pending_visual_review",
              "source": {"path": str(source), "bytes": source.stat().st_size, "sha256": source_hash},
              "output": {"path": str(destination), "bytes": destination.stat().st_size, "sha256": sha256(destination)},
              "correction": provenance, "modified_primitives": records,
              "unchanged": ["Original uploaded GLB", "All supplied body POSITION/NORMAL/JOINTS/WEIGHTS and their bytes",
                            "UV and triangle index accessors and their bytes",
                            "52 bones, node hierarchy, local rest transforms and inverse-bind matrices",
                            "All material and texture/image bytes", "Original motion captures and contact clocks"],
              "limits": ["Runtime source omits covered shoulder and torso skin; no missing anatomy was reconstructed",
                         "Weight correction is an authored derived asset, not a recovered original",
                         "Requires matched close-up and motion review before accepting appearance",
                         "Unreal has not been executed"]}
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=PROJECT / "SourceInputs/athlete_a.glb")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    repair(args.source, args.output, args.receipt)
