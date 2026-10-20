"""Later headless Blender entry point. No remote access or downloads.

Example argument shape (supply your actual authorized local asset paths):
  blender --background --factory-startup --python export_athlete_fbx.py -- \
    --athlete-glb /input/athlete_a.glb --source-rig-glb /input/xbot/footballer.glb \
    --mocap-index /input/mocap-drills.json --mocap-bin /input/mocap-drills.bin \
    --output-dir /project/SquadPad3D/SourceData/athlete_a

The script requires real assets. It cannot run from the recovered metadata alone.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parent))
from retarget_core import Canonical, Retargeter, Rig, prepare_clip, read_glb, sha256, write_glb


def parse_arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("athlete-glb","source-rig-glb","mocap-index","mocap-bin","output-dir"):
        parser.add_argument("--"+name,required=True,type=Path)
    parser.add_argument("--manifest-prefix",default="SourceData/athlete_a")
    parser.add_argument("--canonical-bind-reference-glb",type=Path,
                        help="Optional actual canonical Xbot.glb for independent source-rest comparison.")
    parser.add_argument("--fps",type=int,default=60,choices=(30,60))
    parser.add_argument("--no-ground-lift",action="store_true")
    parser.add_argument("--hold-pass-opening-sample",action="store_true",
                        help="Derived correction: hold stored pass sample one back to zero for its opening interval, preserving later times/contact.")
    parser.add_argument("--overwrite-generated",action="store_true",
                        help="Only overwrite a directory carrying this exporter's marker.")
    argv = sys.argv[sys.argv.index("--")+1:] if "--" in sys.argv else []
    return parser.parse_args(argv)


def save_json(path,value):
    Path(path).write_text(json.dumps(value,indent=2,allow_nan=False)+"\n")


def publish_receipt(path,value):
    """Publish last, atomically, so an importer never accepts a partial receipt."""
    path = Path(path)
    temporary = path.with_name(".squadpad-export-receipt.tmp")
    save_json(temporary,value)
    if (path.name == "export_manifest.json"
            and value.get("schema") == "squadpad.unreal.export-receipt.v1"
            and value.get("status") == "fbx_roundtrip_structural_passed_unreal_validation_pending"):
        old_failure = path.with_name("export_failure.json")
        if old_failure.exists() or old_failure.is_symlink():
            marker = path.with_name(".squadpad-export-v1")
            if (old_failure.is_symlink() or not old_failure.is_file()
                    or not marker.is_file()
                    or marker.read_text() != "squadpad.unreal.generated-export.v1\n"):
                raise ValueError("Cannot clear a failure file without the exporter-owned directory marker")
            failed = json.loads(old_failure.read_text())
            if (failed.get("schema") != "squadpad.unreal.export-receipt.v1"
                    or failed.get("status") != "failed"):
                raise ValueError("Existing failure file is not this exporter's failure receipt")
            old_failure.unlink()
    temporary.replace(path)


def compare_source_rest(source,reference,tolerance=0.0001):
    """Compare named model-space rest matrices, not merely matching bone names."""
    if set(source.bones) != set(reference.bones):
        raise ValueError("Source template differs from canonical reference bone set")
    maximum = 0.
    for name,idx in source.bones.items():
        ridx = reference.bones[name]
        source_parent = source.names.get(source.parent.get(idx))
        reference_parent = reference.names.get(reference.parent.get(ridx))
        if source_parent != reference_parent:
            raise ValueError(f"Source template parent differs from reference: {name}")
        maximum = max(maximum,max(abs(a-b) for a,b in zip(source.rest_world[idx],reference.rest_world[ridx])))
    if maximum > tolerance:
        raise ValueError(f"Source template rest matrices differ from canonical reference: {maximum}")
    return {"status":"named_model_space_rest_matrices_compared",
            "maximum_matrix_element_difference":maximum,"tolerance":tolerance,
            "scope":"Node-rest hierarchy and model-space transforms. Does not inspect original recording bind metadata."}


def image_files(doc,binary,output):
    """Extract original embedded bytes. No image resampling or colour conversion."""
    directory = output/"Textures"
    directory.mkdir(exist_ok=True)
    result = []
    for idx,image in enumerate(doc.get("images",[])):
        if "bufferView" not in image:
            raise ValueError("Expected embedded source GLB textures")
        view = doc["bufferViews"][image["bufferView"]]
        begin = view.get("byteOffset",0)
        data = binary[begin:begin+view["byteLength"]]
        if len(data) != view["byteLength"]:
            raise ValueError("Truncated embedded texture")
        extension = {"image/png":".png","image/jpeg":".jpg"}.get(image.get("mimeType"))
        if extension is None:
            raise ValueError(f"Unsupported image type: {image.get('mimeType')}")
        name = re.sub(r"[^a-zA-Z0-9_-]","_",image.get("name",f"image_{idx}"))
        filename = f"{idx:02d}_{name}{extension}"
        path = directory/filename
        path.write_bytes(data)
        result.append({"image_index":idx,"path":str(path.resolve()),
                       "file":f"Textures/{filename}","sha256":hashlib.sha256(data).hexdigest()})
    return result


def new_scene(bpy,fps):
    # The entry point rejects interactive Blender before reaching this call.
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1.0
    scene.render.fps = fps
    scene.render.fps_base = 1.0
    return scene


def one_armature(bpy):
    found = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
    if len(found) != 1:
        raise ValueError(f"Expected one armature, found {len(found)}")
    return found[0]


def import_gltf(bpy,path,fps,textures):
    new_scene(bpy,fps)
    props = bpy.ops.import_scene.gltf.get_rna_type().properties.keys()
    options = {"filepath":str(path)}
    if "import_pack_images" in props:
        options["import_pack_images"] = True
    # Use Blender's glTF orientation heuristic consistently for mesh and clips.
    if "bone_heuristic" in props:
        options["bone_heuristic"] = "BLENDER"
    # Blender5.2 otherwise creates an Icosphere in glTF_not_exported for every
    # pose bone's custom display shape. It is editor geometry, not the athlete.
    # Disable its creation instead of weakening the skinned-mesh validation.
    if "disable_bone_shape" in props:
        options["disable_bone_shape"] = True
    bpy.ops.import_scene.gltf(**options)
    armature = one_armature(bpy)
    armature.name = "Armature"
    armature.data.name = "SquadPadAthleteA"
    by_hash = {entry["sha256"]:entry["path"] for entry in textures}
    for image in bpy.data.images:
        packed = image.packed_file
        if packed:
            digest = hashlib.sha256(bytes(packed.data)).hexdigest()
            if digest not in by_hash:
                raise ValueError(f"Imported texture bytes differ from source: {image.name}")
            image.filepath_raw = by_hash[digest]
    return armature


def skeleton_record(armature):
    return {b.name:{"parent":b.parent.name if b.parent else None,
                    "head_world_m":list(armature.matrix_world @ b.head_local)}
            for b in armature.data.bones}


def compare_skeleton(expected,actual,tolerance_m=0.0001):
    if set(expected) != set(actual):
        raise ValueError(f"Skeleton bone set changed: missing={set(expected)-set(actual)}, added={set(actual)-set(expected)}")
    worst = 0.
    for name,ref in expected.items():
        current = actual[name]
        if current["parent"] != ref["parent"]:
            raise ValueError(f"Bone parent changed: {name}")
        error = math.sqrt(sum((x-y)**2 for x,y in zip(current["head_world_m"],ref["head_world_m"])))
        worst = max(worst,error)
    if worst > tolerance_m:
        raise ValueError(f"Skeleton rest position drift {worst:.9f}m exceeds {tolerance_m}m")
    return worst


def mesh_record(bpy,armature):
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not meshes:
        raise ValueError("No character geometry imported")
    vertices,unweighted = 0,0
    for mesh in meshes:
        if not any(m.type == "ARMATURE" and m.object == armature for m in mesh.modifiers):
            raise ValueError(f"Mesh lacks the common armature: {mesh.name}")
        bone_groups = {g.index for g in mesh.vertex_groups if g.name in armature.data.bones}
        for vertex in mesh.data.vertices:
            vertices += 1
            if sum(g.weight for g in vertex.groups if g.group in bone_groups) < 1e-7:
                unweighted += 1
    if unweighted:
        raise ValueError(f"Character contains {unweighted} unweighted vertices")
    return {"mesh_objects":len(meshes),"vertices":vertices,"unweighted_vertices":unweighted}


def action_curves(action):
    result = []
    # Blender 4.4+ layered actions, including Blender 5.x.
    for layer in getattr(action,"layers",()):
        for strip in layer.strips:
            for bag in getattr(strip,"channelbags",()):
                result.extend(bag.fcurves)
    # Earlier Blender actions expose fcurves directly.
    if not result:
        try:
            result.extend(action.fcurves)
        except AttributeError:
            pass
    return result


def activate_single_action(armature):
    ad = armature.animation_data
    if ad is None:
        raise ValueError("Animation import produced no animation data")
    action = ad.action
    if action is None:
        strips = [s for t in ad.nla_tracks for s in t.strips if s.action]
        candidates = {s.action.name:s for s in strips}
        if len(candidates) != 1:
            raise ValueError("Expected one imported action")
        strip = next(iter(candidates.values()))
        action = strip.action
        ad.action = action
        if hasattr(strip,"action_slot") and hasattr(ad,"action_slot"):
            ad.action_slot = strip.action_slot
    for track in ad.nla_tracks:
        track.mute = True
    curves = action_curves(action)
    if not curves:
        raise ValueError("No readable bone animation curves. Check Blender action API.")
    dynamic = []
    for curve in curves:
        values = [float(k.co[1]) for k in curve.keyframe_points]
        if not all(math.isfinite(v) for v in values):
            raise ValueError("Non-finite animation keys")
        if values and max(values)-min(values) > 1e-6:
            dynamic.append(curve.data_path)
    if not dynamic:
        raise ValueError("Animation has no changing channels")
    return action,{"curves":len(curves),"dynamic_curves":len(dynamic),"range_frames":list(action.frame_range)}


def export_fbx(bpy,path,armature,animation=False):
    bpy.ops.object.select_all(action="DESELECT")
    armature.select_set(True)
    # FBX skin clusters carry the explicit rest bind matrices. An armature-only
    # animation file reimported in Blender5.2 uses its posed default transforms
    # as rest, causing almost a metre of apparent hand-rest drift. Keep the real
    # skinned geometry as a bind reference in each source FBX. Unreal imports
    # those files with import_mesh=False and the existing target Skeleton.
    for obj in bpy.context.scene.objects:
        if obj.type == "MESH":
            obj.select_set(True)
    bpy.context.view_layer.objects.active = armature
    bpy.ops.export_scene.fbx(
        filepath=str(path),use_selection=True,
        object_types={"ARMATURE","MESH"},
        global_scale=1.0,apply_unit_scale=True,apply_scale_options="FBX_SCALE_UNITS",
        axis_forward="-Y",axis_up="Z",bake_space_transform=False,
        use_mesh_modifiers=True,mesh_smooth_type="FACE",use_tspace=False,
        add_leaf_bones=False,use_armature_deform_only=False,
        armature_nodetype="NULL",path_mode="COPY",embed_textures=not animation,
        bake_anim=animation,bake_anim_use_all_bones=True,
        bake_anim_use_nla_strips=False,bake_anim_use_all_actions=False,
        bake_anim_force_startend_keying=True,bake_anim_step=1.0,bake_anim_simplify_factor=0.0)
    if not path.exists() or path.stat().st_size < 1024:
        raise ValueError(f"FBX export did not produce a usable file: {path}")


def validate_fbx(bpy,path,expected,fps,animation):
    new_scene(bpy,fps)
    bpy.ops.import_scene.fbx(filepath=str(path),automatic_bone_orientation=False,
                             use_anim=animation,ignore_leaf_bones=False)
    armature = one_armature(bpy)
    result = {"rest_bone_max_error_m":compare_skeleton(expected,skeleton_record(armature)),
              "bone_count":len(armature.data.bones)}
    if animation:
        _,result["animation"] = activate_single_action(armature)
    else:
        result["mesh"] = mesh_record(bpy,armature)
    return result


def main():
    args = parse_arguments()
    try:
        import bpy
    except ImportError as exc:
        raise RuntimeError("Run this entry point inside Blender --background, using real source files.") from exc
    if not bpy.app.background:
        raise RuntimeError("Headless Blender is required. The exporter resets its own scene between assets.")
    inputs = {name:getattr(args,name).resolve() for name in ("athlete_glb","source_rig_glb","mocap_index","mocap_bin")}
    if args.canonical_bind_reference_glb:
        inputs["canonical_bind_reference_glb"] = args.canonical_bind_reference_glb.resolve()
    for label,path in inputs.items():
        if not path.is_file():
            raise FileNotFoundError(f"Missing required real source asset {label}: {path}")
    output = args.output_dir.resolve()
    marker = output/".squadpad-export-v1"
    if output.exists() and any(output.iterdir()):
        if not args.overwrite_generated or not marker.is_file():
            raise FileExistsError("Nonempty output directory. Use a new directory, or explicitly overwrite this exporter's generated directory.")
    if any(path == output or output in path.parents for path in inputs.values()):
        raise ValueError("Source assets must be outside the generated output directory")
    prefix = args.manifest_prefix.strip("/")
    if not prefix or Path(prefix).is_absolute() or ".." in Path(prefix).parts:
        raise ValueError("Manifest prefix must be a project-relative path")
    target_doc,target_bin = read_glb(inputs["athlete_glb"])
    source_doc,_ = read_glb(inputs["source_rig_glb"])
    target,source = Rig(target_doc),Rig(source_doc)
    source_rest_verification = {
        "status":"not_independently_compared",
        "basis":"The inspected project README states tools/footballer/xbot/footballer.glb preserves X Bot's rig. No original Xbot.glb was available during cloud recovery.",
        "requirement":"Supply the exact X Bot viewer template, not the separate youth footballer.glb. Use --canonical-bind-reference-glb when the original canonical model is available."}
    if "canonical_bind_reference_glb" in inputs:
        reference_doc,_ = read_glb(inputs["canonical_bind_reference_glb"])
        source_rest_verification = compare_source_rest(source,Rig(reference_doc))
    library = Canonical(inputs["mocap_index"],inputs["mocap_bin"])
    retargeter = Retargeter(source,target,library,not args.no_ground_lift,
                            hold_pass_opening_sample=args.hold_pass_opening_sample)
    output.mkdir(parents=True,exist_ok=True)
    # Invalidate an earlier successful generation before changing any outputs.
    # The nonempty-directory guard above requires an explicit generated overwrite.
    success_receipt = output/"export_manifest.json"
    if success_receipt.is_file():
        success_receipt.unlink()
    marker.write_text("squadpad.unreal.generated-export.v1\n")
    provenance = {label:{"path":str(path),"sha256":sha256(path),"bytes":path.stat().st_size}
                  for label,path in inputs.items()}
    manifest = {
        "schema":"squadpad.unreal.export-receipt.v1","status":"preparing",
        "blender_version":bpy.app.version_string,"source_provenance":provenance,
        "source_rig_rest_verification":source_rest_verification,
        "ignored_source_only_terminal_finger_placeholders":retargeter.ignored_terminal_fingers,
        "derived_motion_corrections":retargeter.derived_corrections,
        "derived_model_correction":target_doc.get("extras",{}).get("squadpad_derived_correction"),
        "exporter_sources":{p.name:{"sha256":sha256(p),"bytes":p.stat().st_size}
                            for p in (Path(__file__),Path(__file__).with_name("retarget_core.py"))},
        "mesh":{"id":"athlete_a","fbx":f"{prefix}/athlete_a.fbx","name":"SK_AthleteA"},
        "animations":[],"fixture_path":"Fixtures/first_pass.drill.json",
        "same_target_skeleton":True,"bone_name_prefix_removed":"mixamorig:",
        "animation_fbx_bind_reference":"Skinned mesh included to preserve explicit FBX rest bind matrices; Unreal import remains animation-only",
        "root_motion_baked_into_skeleton":False,
        "coordinates":{
            "metadata_axes":"unreal-z-up-x-forward","metadata_units":"cm",
            "source_to_metadata":"[source.z,-source.x,source.y]*100",
            "blender_scene_units":"meters","fbx_forward":"-Y","fbx_up":"Z",
            "fbx_scale_policy":"FBX_SCALE_UNITS, global_scale1.0, apply_unit_scaleTrue",
            "unreal_import":{"force_front_x_axis":True,"convert_scene":True,
                             "convert_scene_unit":True,"import_uniform_scale":1.0},
            "unreal_import_basis_validation":"pending"
        },
        "baked_operations":["Canonical source-to-X Bot motion already present in supplied binary",
                            "Viewer model-space retarget onto athlete rest skeleton",
                            "Vertical hip displacement scaled by rest hip height ratio",
                            "Planar hip coordinates anchored to idle reference",
                            "Fixed relaxed finger shape from canonical index",
                            "Decoded quaternion normalization before interpolation and world-matrix composition",
                            "Quaternion normalization for valid glTF rotation keys"],
        "not_baked":["Stateful foot locking","Contextual clip blends","Actor run-up movement",
                     "Ball motion","Football technique corrections","Physical contact calibration"],
        "contact_validation":"unmeasured",
        "unreal_visual_validation":"not_run"
    }
    if not args.no_ground_lift:
        manifest["baked_operations"].append("Viewer foot-clearance body lift folded into hip translation")
    if args.hold_pass_opening_sample:
        manifest["baked_operations"].append("Derived pass opening hold shared by skeletal pose and actor root, with source/contact clocks unchanged")
    try:
        textures = image_files(target_doc,target_bin,output)
        save_json(output/"materials_source.json",{
            "schema":"squadpad.unreal.material-source.v1","images":textures,
            "materials":target_doc.get("materials",[]),"textures":target_doc.get("textures",[]),
            "note":"Original glTF PBR metadata. Unreal material conversion and alpha modes need visual review."})
        with tempfile.TemporaryDirectory(prefix="squadpad-export-") as temporary:
            temporary = Path(temporary)
            rest_doc = json.loads(json.dumps(target_doc))
            rest_doc.pop("animations",None)
            for idx,name in target.names.items():
                rest_doc["nodes"][idx]["name"] = name
            rest_glb = temporary/"athlete_a_rest.glb"
            write_glb(rest_glb,rest_doc,target_bin)
            armature = import_gltf(bpy,rest_glb,args.fps,textures)
            expected = skeleton_record(armature)
            manifest["mesh"]["source_blender_structure"] = mesh_record(bpy,armature)
            armature.data.pose_position = "REST"
            if armature.animation_data:
                armature.animation_data_clear()
            export_fbx(bpy,output/"athlete_a.fbx",armature)
            manifest["mesh"]["fbx_roundtrip"] = validate_fbx(bpy,output/"athlete_a.fbx",expected,args.fps,False)
            for clip_id in ("idle","pass","receive"):
                path = temporary/f"{clip_id}.glb"
                info = prepare_clip(retargeter,target_doc,target_bin,clip_id,args.fps,path)
                armature = import_gltf(bpy,path,args.fps,textures)
                compare_skeleton(expected,skeleton_record(armature))
                action,action_info = activate_single_action(armature)
                scene = bpy.context.scene
                start,end = action.frame_range
                if abs(start-round(start)) > 1e-5:
                    raise ValueError(f"Non-integer imported start frame {start}. Explicit retiming is required.")
                scene.frame_start = round(start)
                scene.frame_end = scene.frame_start+info["frames"]-1
                expected_end = start+info["source_duration_seconds"]*args.fps
                if abs(end-expected_end) > .02:
                    raise ValueError(f"glTF import changed {clip_id} duration: frames {start}..{end}, expected end {expected_end}")
                scene.frame_set(scene.frame_start)
                export_fbx(bpy,output/f"{clip_id}.fbx",armature,True)
                roundtrip = validate_fbx(bpy,output/f"{clip_id}.fbx",expected,args.fps,True)
                imported_range = roundtrip["animation"]["range_frames"]
                actual_duration = (imported_range[1]-imported_range[0])/args.fps
                if abs(actual_duration-info["duration_seconds"]) > 1e-4:
                    raise ValueError(f"FBX roundtrip changed {clip_id} timing: {actual_duration}s")
                keys = info.pop("root_keys")
                root_file = f"{clip_id}.root.json"
                save_json(output/root_file,{
                    "schema":"squadpad.unreal.root-motion.v1","axes":"unreal-z-up-x-forward",
                    "units":"cm","time_units":"seconds","animation_id":clip_id,
                    "contact_seconds":info["contact_seconds"],"anchor":info["root_keys_anchor"],
                    "root_motion_baked_into_skeleton":False,"keys":keys})
                info.update(fbx=f"{prefix}/{clip_id}.fbx",root_motion={"mode":"actor_track","path":f"{prefix}/{root_file}"},
                            source_blender_animation=action_info,fbx_roundtrip=roundtrip)
                manifest["animations"].append(info)
                print(f"Exported {clip_id}: {info['duration_seconds']}s, contact {info['contact_seconds']}, surface contact unmeasured",flush=True)
            save_json(output/"skeleton_reference.json",{
                "schema":"squadpad.unreal.blender-skeleton.v1","axes":"blender-z-up-negative-y-forward",
                "units":"meters","bones":expected})
        manifest["status"] = "fbx_roundtrip_structural_passed_unreal_validation_pending"
        generated_assets = sorted(list(output.glob("*.fbx"))+list(output.glob("*.root.json")))
        manifest["outputs"] = {p.name:{"sha256":sha256(p),"bytes":p.stat().st_size}
                               for p in generated_assets}
        publish_receipt(success_receipt,manifest)
        print(f"Saved {output/'export_manifest.json'}. Unreal basis, appearance and contact review remain pending.",flush=True)
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["failure"] = f"{type(exc).__name__}: {exc}"
        save_json(output/"export_failure.json",manifest)
        raise


if __name__ == "__main__":
    main()
