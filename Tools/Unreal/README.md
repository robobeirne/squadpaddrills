# Unreal first-pass bootstrap

## What exists

`Content/Python/squadpad_plan.py` validates the manifest and original integration fixture without importing Unreal. `Content/Python/squadpad_bootstrap.py` implements the native editor build. Both are source in this repository. The character assets, original fixture and Blender exports are retained in the separate bundle described in [Asset setup](../../Docs/ASSET_SETUP.md). No Unreal compilation, native sequence, map or accepted native animation is established by this public handoff. The original offline package includes separately scoped Blender previews.

The first scene is the existing rondo's isolated A-to-B pass and reception. Its external-bundle fixture retains the full observed source payload and records the actors and actions omitted from this scene. This is the first integration baseline, not the completed drill or a newly authored Cruyff turn.

## Public interfaces

Restore the bundle first, then run local input checks from the repository root:

```sh
python3 -B Tools/pipeline.py offline-check
python3 -B Tools/pipeline.py preflight
python3 -B Tools/Unreal/test_planning.py
```

`offline-check` requires both original fixture JSON files. It permits missing FBX assets and reports them, but cannot run from the public checkout alone. `preflight` requires all declared FBX files, root traces and a successful, matching structural export receipt. Neither command runs Unreal or evaluates animation quality.

After real asset export and an authorized Unreal Editor launch, the editor Python console can call:

```python
from pathlib import Path
import unreal
import squadpad_bootstrap

root = Path(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()))
receipt = squadpad_bootstrap.build(root / "import_manifest.json")
```

The full interface is:

```python
build(manifest_path, project_root=None, run_name=None,
      receipt_path=None, open_editor=True)
```

Alternatively, Unreal's **Execute Python Script** action can run `Tools/Unreal/bootstrap_entry.py`. That entry uses this project's manifest by default. Optional environment variables are `SQUADPAD_IMPORT_MANIFEST`, `SQUADPAD_RUN_NAME`, `SQUADPAD_RECEIPT_PATH`, and `SQUADPAD_OPEN_EDITOR` (`1` opens Sequencer; `0` omits the UI preview). Relative paths resolve inside this project. The entry does not install or launch an editor and is not executed when the project merely opens.

For a later automated full-editor execution, use the actual installed editor executable and resolved local paths:

```sh
SQUADPAD_OPEN_EDITOR=0 "/path/to/UnrealEditor" "/path/to/SquadPad3D/SquadPad3D.uproject" \
  "-ExecutePythonScript=/path/to/SquadPad3D/Tools/Unreal/bootstrap_entry.py" \
  -unattended -nosplash
```

Epic's [editor Python guide](https://dev.epicgames.com/documentation/en-us/unreal-engine/scripting-the-unreal-editor-using-python) documents `-ExecutePythonScript` as the full-editor route which runs after startup and exits after the script. It still launches an editor process and is not a lightweight cloud check. Confirm the installed executable first. Inspect the generated receipt as well as the process result. A later visual session must open the recorded map and inspect actual playback. These native commands were not executed during cloud preparation.

The PythonScriptPlugin exposes the project's `Content/Python` directory. PythonScriptPlugin, EditorScriptingUtilities and SequencerScripting must be enabled. The script verifies the editor's project directory and engine major/minor against the manifest. It protects any pre-existing unsaved map rather than closing it implicitly.

Each build creates a new namespace, `/Game/SquadPad/Bootstrap/Run_<UTC>_<suffix>`. Supplying a previously used `run_name` fails. Source imports set `replace_existing=False`; old maps and animation edits are not reimported or deleted. A partial failed build remains available for diagnosis. The map and sequence paths are written into the receipt, so a launcher can open the exact review map without rewriting project defaults.

The default receipt is `Saved/SquadPad/Receipts/<run_name>.json`. A receipt records failure before any engine operation when required source inputs are absent. It is never used to infer native success from a cloud-only test.

## Native build behavior

The script imports one native SkeletalMesh and its Skeleton, then imports idle, pass and receive as Animation Sequences using that same Skeleton. It verifies native asset types, source duration, bone-track presence and skeleton references. Skeleton assignment is not retargeting: the Blender exporter must already have put each clip on the athlete's own skeleton.

The import explicitly uses `FbxFactory` and `FbxImportUI`. Its agreed settings are `convert_scene=True`, `convert_scene_unit=True`, `force_front_x_axis=True` and `import_uniform_scale=1.0`. The descriptor and fixture positions already use Unreal centimetres. The bootstrap performs no additional axis conversion, corrective mesh yaw or 100x scale. Native facing, left/right anatomy, rest pose, garment materials and deformation still need review. If a newer engine's Interchange pipeline bypasses these legacy import options, inspect the failed import's Output Log and correct the import route before accepting its output.

Animation import explicitly selects `FBXALIT_EXPORTED_TIME` and disables `snap_to_closest_frame_boundary`, then checks that the import-options object retained both settings before submitting the task. This uses the FBX export's declared range and prevents inherited range or snapping preferences from selecting a different span. The existing post-import duration check remains necessary: retained options alone do not prove what the importer produced. The importer also disables forced 30 fps sampling and uses automatic source-rate selection.

The planned Level Sequence contains two possessed skeletal actors, native skeletal animation sections, actor transform tracks, a sphere representing the ball, its position track, and one camera cut. A simple green floor and two lights make the sequence reviewable. It uses no simulated football flight. The sphere does not yet have a football panel texture or authored spin.

A saved LevelSequenceActor references the sequence with autoplay enabled and pause-at-end. This configures later PIE or standalone playback; writing that setting does not establish that playback occurred. The editor preview opens Sequencer and locks the camera cut for manual inspection. The build does not automatically start PIE, package iOS, or declare successful playback.

## Root motion and timing

The source FBX clips remove horizontal hip translation. The companion `.root.json` files retain it. A manifest declaration `root_motion_baked_into_skeleton:false` prevents applying an actor trace to a travelling skeleton twice.

Each fixture actor explicitly requests `contact_relative_clip_trace`. Its guide position is the actor origin at the contact event. The planner rotates the local trace by that actor's yaw and adds the guide position once. The exported trace must include an exact zero at the original contact time. The first trace offset is held through preroll idle; the last is held through trailing idle. Returning immediately to the guide origin would cause a teleport, so the planner does not do that.

This scene restores the action trace. The small separate idle drift is held at those action endpoints to preserve continuity; it is not added on top of the pass or receive trace.

The packaged pass releases at 1.39262125 seconds, and reception occurs at 2.43762125 seconds. The receiver's clip starts at 1.86575125 seconds. These source clocks are not snapped to display frames. Transform keys use display-frame subframes; native storage uses a 24,000 Hz tick resolution. Section and key readback records requested time, actual stored time and rounding error, and rejects shifts larger than one sequence tick plus floating point tolerance.

The requested scene duration is 3.7925 seconds. A 60 fps playback boundary rounds up to 3.8 seconds. Only the last generated idle section is extended by 7.5 ms to cover that boundary; authored pass and receive sections retain their original clocks. The transform and camera tracks cover the same rounded boundary.

Idle fills gaps with separate nonoverlapping sections. There is no authored blending, planted-foot IK, receiver cushioning, or new contact correction in this bootstrap. Those are subsequent animation work. Native imported clip length must be sufficient to avoid a non-looping action wrapping; the padded pass FBX can be 3.4 seconds while its original action remains 3.3925 seconds.

## What a successful native receipt establishes

A real successful run saves the new map and content assets, hashes the map and sequence packages, reloads the Level Sequence package, and reopens the map. It checks that bindings resolve to the intended actors and meshes; skeletal section references, time ranges, rates and source offsets survive; transform key values and native times survive; and the camera cut and autoplay actor still reference the intended sequence.

Its top-level success status is `native_import_save_readback_passed`. The following fields remain separate:

| Evidence | Bootstrap status |
| --- | --- |
| FBX file/receipt hashes | Checked before import |
| Native asset types, skeleton references, track presence and durations | Checked only during real Unreal execution |
| Saved and reopened map/sequence data | Checked only during real Unreal execution |
| Runtime playback | `not_run` |
| Visual animation acceptance | `not_reviewed` |
| Boot/ball surface contact geometry | `unmeasured` or `not_evaluated`; never accepted here |
| Native iOS integration and device performance | `not_run` |

The ball endpoints currently use existing canonical X Bot heuristics. They are not measured athlete boot sockets. Successful clocks, nearby ankles, or a visually plausible sphere do not establish valid surface contact. `Tools/squadpad_quality.py` checks contact metadata separately and requires real, fixture-bound evaluated geometry for a later contact decision.

## Local checks and remaining API verification

`test_planning.py` exercises fractional timing, contact-relative root composition, idle endpoint continuity, missing-file failure, stale export hashes, prevention of double root motion, fixture units and evidence preservation. It also uses one explicitly labelled API mock to check rejection of a wrapper that drops subframes. Temporary FBX signature stubs are not real assets and are removed after each test.

The implementation targets the manifest's Unreal 5.8 editor. The follow-up audit checked Epic's current Python pages labelled **5.8** for skeletal section parameters, fixed time-warp rates, section ranges, key time readback, FBX animation/skeletal import data, package reload and bound-object inspection. It corrected the animation import range policy described above. The dated audit `Reports/unreal-api-review-v2.md` is preserved in the original offline package, outside this public checkout. The official references below record its source coverage.

Some class pages could only be corroborated against earlier published references: `AssetImportTask` and `FbxImportUI` against 5.7, and the earlier double-channel writer and camera/playback baseline against 5.6. A current documentation match is not an installed-editor test. Python syntax and pure tests cannot certify API compatibility with a 5.8 binary. The native run must still verify import routing, sample times, saved bindings and actual pose evaluation; any incompatibility must fail visibly before a native result is claimed. Nonzero source offsets also need a native case because the current fixture uses zero offsets.

Official references for the current audit (class pages retrieved as 5.8):

- [Python scripting in Sequencer](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-scripting-in-sequencer-in-unreal-engine)
- [FBX animation import data](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/FbxAnimSequenceImportData), [animation range selection](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/FBXAnimationLengthImportType) and [skeletal import data](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/FbxSkeletalMeshImportData)
- [Skeletal animation section parameters](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/MovieSceneSkeletalAnimationParams) and [time-warp play rate](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/MovieSceneTimeWarpVariant)
- [Section ranges](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/MovieSceneSectionExtensions) and [double-key time readback](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/MovieSceneScriptingDoubleKey)
- [Map/package save and reload](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/EditorLoadingAndSavingUtils) and [bound-object inspection](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/SequencerTools)

Earlier reference coverage retained for the remaining wrappers:

- [AssetImportTask, 5.7](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/AssetImportTask?application_version=5.7) and [FbxImportUI, 5.7](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/FbxImportUI?application_version=5.7)
- [Double-channel writer, 5.6](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/MovieSceneScriptingDoubleChannel?application_version=5.6)
- [LevelSequenceActor, 5.6](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/LevelSequenceActor?application_version=5.6), [camera-cut section, 5.6](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/MovieSceneCameraCutSection?application_version=5.6) and [playback settings, 5.6](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/MovieSceneSequencePlaybackSettings?application_version=5.6)
