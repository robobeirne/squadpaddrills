# Athlete A export

All commands run from the repository root. Restore the inputs from
[Asset setup](../../Docs/ASSET_SETUP.md) before asset-dependent work. The public
repository includes the exporter code, not the GLBs, capture files or FBXs.

## Original offline export status

This exporter has run on the four supplied assets in an isolated cloud-local
Blender 5.2.2 LTS Python environment. It produced the real athlete mesh and
idle/pass/receive FBXs retained in the separate bundle at `SourceData/athlete_a/`. All four files
were reopened and passed the shared skeleton, reference-pose, weighted-geometry,
animation-channel and duration checks. The source athlete has 12 skinned mesh
objects, 54,937 imported vertices and no unweighted vertices.

Revision 4 uses `DerivedInputs/athlete_a_shoulders.glb` as the target mesh. The
reproducible correction lowers the sleeve-cap bulk and smooths original upper-shirt
weights. All supplied body attributes, skeleton/rest data and original captures
remain unchanged. See the [model recipe](../Model/README.md). The historical
`Reports/shoulder-correction.md` is preserved in the original offline package.

The reference-head roundtrip maximum error was 0.000000822581 m, below the
unchanged 0.0001 m tolerance. Separate Blender previews check the visible export.
Unreal units, basis, material appearance, playback and actual football contact
remain native execution gates. No `.uasset` or `.umap` is supplied yet.

## External real inputs

- `SourceInputs/athlete_a.glb`, 1,971,868 bytes.
- `SourceInputs/xbot/footballer.glb`, 451,292 bytes. Its actual structure matches
  the expected 67-joint X Bot variant, not the separate youth model.
- `SourceInputs/mocap-drills.json` and `SourceInputs/mocap-drills.bin`, the paired
  schema-2 canonical data.

Both GLBs use mesh quantization and are smaller than the historically observed
full-size files. These supplied variants were revalidated before export.
`SourceInputs/input_manifest.json` binds all four exact byte streams. Independent
comparison of the source rest matrices with original `Xbot.glb` remains pending.

The originally cited source FBX directory was absent at its old Downloads path. This baseline uses the existing canonical baked clips. It does not claim a fresh original-FBX import.

## Reproduction

Run a dedicated headless Blender process with explicit paths to authorized local assets. Do not run this inside an unsaved interactive Blender session. The script resets its own headless scene between imports.

To reproduce the current corrected target from the unchanged upload, first run
the model recipe with Python, NumPy and SciPy available. Its output and receipt
must be fresh paths. Restored derived files can be used directly for export.
The examples below write a separate comparison rebuild under `Saved/`. They do
not replace the hash-pinned baseline bundle or update `import_manifest.json`.

```sh
python3 -B Tools/Model/repair_shoulders.py \
  --source SourceInputs/athlete_a.glb \
  --output Saved/ModelReview/athlete_a_shoulders.glb \
  --receipt Saved/ModelReview/athlete_a_shoulders.receipt.json
```

```sh
blender --background --factory-startup --python Tools/Export/export_athlete_fbx.py -- \
  --athlete-glb Saved/ModelReview/athlete_a_shoulders.glb \
  --source-rig-glb SourceInputs/xbot/footballer.glb \
  --mocap-index SourceInputs/mocap-drills.json \
  --mocap-bin SourceInputs/mocap-drills.bin \
  --output-dir Saved/Exports/athlete_a_rebuild \
  --manifest-prefix Saved/Exports/athlete_a_rebuild \
  --hold-pass-opening-sample
```

Default sampling is 60 fps. `--fps 30` is available. Sampling adds no new captured motion. The higher rate reduces interpolation error after retargeting. `--no-ground-lift` omits the viewer's body-lift correction. A repeat export needs `--overwrite-generated` and the output directory's exporter marker. Source assets must stay outside the generated directory.

If the original canonical `models/Xbot.glb` becomes available, supply `--canonical-bind-reference-glb /input/canonical/Xbot.glb`. The script then compares the source template's named parent hierarchy and model-space rest matrices against that reference and fails on a mismatch. Without the reference, the receipt explicitly records independent source-rest comparison as unperformed. Matching bone names alone is not proof of matching bind transforms. The optional check compares the provided model reference, not original recording bind metadata.

## Output contract

The restored baseline uses `SourceData/athlete_a/`. A comparison rebuild writes
the same output names beneath the chosen `--output-dir`. A deliberate baseline
replacement requires updated source provenance, import manifest and compiled
playback/binding review. Historical receipts are not edited to bless new bytes.

- `SourceData/athlete_a/athlete_a.fbx`: skeletal mesh, textures embedded and separately extracted.
- `SourceData/athlete_a/idle.fbx`, `pass.fbx`, `receive.fbx`: animations on the same named athlete skeleton, including skinned bind-reference geometry. Unreal imports animation only from these files.
- `idle.root.json`, `pass.root.json`, `receive.root.json`: separate horizontal actor movement tracks.
- `export_manifest.json`: evidence receipt using schema `squadpad.unreal.export-receipt.v1`, with provenance hashes, actual exported durations, corrections, structures and pending checks.
- `materials_source.json`: original glTF materials and unchanged embedded image bytes, for checking Unreal materials.
- `skeleton_reference.json`: Blender rest heads and bone parents.

The exporter writes `export_failure.json` and exits with an exception if an asset-dependent gate fails. A failed directory must not be accepted as a completed export. It does not overwrite the project's main import manifest. An authorized overwrite invalidates the previous success receipt before changing output assets. The exporter hashes every FBX and root JSON output, removes a stale failure receipt after success, and publishes the new success receipt atomically as the last operation. Import preflight must verify the receipt and matching asset hashes before importing.

## Timing and root movement

Idle source duration is 4.1 s. Pass source duration is 3.3925 s with candidate contact at 1.39262125 s. Receive source duration is 1.3 s with candidate contact at 0.57187 s.

FBX uses an integer frame range. The pass endpoint is padded to 3.4 s at either supported rate. The original action is not sped up or slowed down. Candidate contact times retain their original seconds. The added endpoint holds the final source pose. The manifest reports source duration, exported duration and padding separately.

The skeletal animations remove horizontal hip translation and retain vertical displacement. Companion actor tracks restore the original scaled planar hip movement. Pass and receive traces are relative to the exact contact sample. Their root JSON includes an explicit zero-valued key at the exact contact time, independent of the FBX frame grid. Idle uses its first sample as the anchor.

Apply these actor tracks once. Do not also enable skeletal root-motion extraction or add a second run-up formula. Contextual idle/action blending, planted-foot IK and transitions still need Unreal implementation.

Root files use schema `squadpad.unreal.root-motion.v1`, axes `unreal-z-up-x-forward`, centimetres and seconds, with `keys` entries containing `time_seconds` and `position_cm`. They declare `root_motion_baked_into_skeleton: false`.

## Coordinate and unit contract

The original character frame is +Y up, +Z forward, +X left. Generated JSON positions map to Unreal +Z up, +X forward, +Y right through `[z, -x, y] * 100`.

The mesh follows the glTF-to-Blender importer, then FBX with -Y forward and +Z up. Blender scene units are metres. The export uses `FBX_SCALE_UNITS`, global scale 1 and unit scaling enabled. Unreal import must use `force_front_x_axis=True`, `convert_scene=True`, `convert_scene_unit=True` and uniform import scale 1. No extra 100x scale or mesh yaw is part of this proposed contract.

The algebraic JSON conversion is covered by the pure checks. The Blender/FBX/Unreal conversion remains unverified until the real import. Before using JSON contact candidates, verify a known forward/right/up reference against imported skeleton positions. An apparent need for a corrective 90-degree mesh yaw is evidence the contract requires correction, not permission to hide another conversion in the actor fixture.

## Quality and provenance

The preparation code transfers each source bone's model-space turn from rest onto the target rest orientation. It scales hip travel using target/source rest hip height, applies the fixed relaxed finger pose from the canonical index and optionally bakes the viewer's ground-clearance lift. It normalizes decoded Int16 rotations before interpolation and world-matrix composition, and normalizes target rotation output. This prevents small quantization errors in quaternion length from introducing scale or skew before retargeting. Zero and nonfinite rotations fail before use. The fixed finger pose follows the same unit-rotation rule.

The clips preserve the existing canonical baseline. They do not include new Cruyff motion, animation improvements, physics, contextual blending, stateful foot locking or exact boot-surface contact. Contact metadata exposes foot and toe positions and an explicitly labelled heuristic ball centre. These values are not calibrated sockets or acceptance evidence.

The executed script checks a Blender FBX roundtrip for a common bone hierarchy, rest-head positions, weighted geometry, nonempty dynamic animation channels and duration. These checks do not replace Unreal playback, visual review or contact measurement.

## Cloud checks

```sh
python3 -B Tools/Export/retarget_core.py
python3 -B Tools/Export/test_retarget_inputs.py
```

These pure checks use synthetic inputs and require no asset bundle, Blender or
Unreal. They are suitable for the portable public check. The additional input
regressions exercise quantized source rotations at endpoints
and between frames. They verify unit quaternions and orthonormal rotation
matrices, not native animation quality. The original motion values are never
replaced with synthetic test data.

The [Khronos glTF 2.0 specification](https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html)
defines node rotation as a unit quaternion. The Int16 canonical input is a separate
application format, so the exporter makes its normalization explicit before
constructing transforms or writing glTF animation.

## Supplied-data compatibility and opening correction

Ten zero-valued fixed-finger placeholders are skipped only when their names are
terminal `Hand*4` leaves in the source rig, absent from the target and absent
from every animated track. Invalid effective rotations still fail. The receipt
lists every skipped name. Recorded quaternions are normalized before sampling.

The pass ends 5.0 ms before its metadata duration. Receive starts 9.2 ms after
zero and ends 3.4 ms before its metadata duration. Sampling holds those endpoint
poses, bounded to one measured source interval. It never shifts or stretches
source time to make endpoints fit.

The optional `--hold-pass-opening-sample` flag holds the second stored pass pose
back over the first 33.2 ms. The uncorrected first interval has a 64.48 cm source
hip jump and more than 80 degrees of upper-arm rotation. Baseline stills confirm
the abrupt change. The option samples both the skeletal pose and its separate
root trace at the same held time. Later samples, contact times and durations are
unchanged. The receipt identifies this derived edit, and the supplied JSON and
binary remain raw. Omit the flag to reproduce the original opening.

## Export defects found and resolved

Blender's glTF importer created an Icosphere used only as a custom bone-display
shape. The exporter now requests `disable_bone_shape=True` when supported by the
actual importer. It still rejects unexpected unskinned source geometry.

An armature-only animation FBX lacked explicit skin-cluster bind matrices. Its
roundtrip used a posed transform as rest, moving a hand by about 0.99 m. Each
animation FBX now includes the real skinned mesh as a bind reference. All strict
skeleton tolerances remain unchanged, and the Unreal path imports only animation
from these files onto the mesh's existing Skeleton. Historical diagnostics are
preserved in the original offline package's `Reports/`. They are not published
with this source checkout. The restored success receipt is the import authority
for its exact output bytes.
