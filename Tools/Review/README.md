# Blender first-pass asset preview

The renderer source is public. FBXs, source recordings, the original integration
fixture and previous renders remain in the separate bundle described in
[Asset setup](../../Docs/ASSET_SETUP.md). Restore that bundle before rendering.
All commands below run from the repository root.

`render_first_pass.py` builds a fresh two-athlete scene from the exported FBXs,
root descriptors and first-pass fixture. It runs inside a dedicated headless
Blender Python process. It creates a neutral floor, two soft lights, one camera
and a plain ball at the fixture's declared radius. It uses Cycles CPU rendering,
four threads and 960 × 540 pixels by default.

These are Blender asset previews. They do not establish an Unreal import,
Unreal playback, completed animation improvement or accepted boot contact.
Every rendered image carries that scope label. The JSON receipt records inputs,
timing, coordinate conversion, rendering settings and output hashes.

## Inputs and boundaries

The renderer calls the same `plan_import(require_files=True)` used by the Unreal
bootstrap. It requires successful export-receipt hashes and already composed
actor motion with `application_count: 1`. It does not apply a second root trace,
retarget again, invent ball rotation or add physics. Source file hashes are
checked after rendering.

FBX imports use temporary copies. Any embedded-image extraction therefore stays
outside the source export folder. The scene starts empty. Each athlete mesh must
use its imported armature. Unexpected unskinned mesh helpers stop the preview.
Animation FBXs include skinned geometry to carry their explicit rest bind
matrices. The renderer checks bone names, parents and rest matrices against the
review mesh, retains the imported armature action, then removes every temporary
animation object, including those bind-reference meshes.
No GLB import occurs in this script, so the glTF bone-display helper path is not
used.

## Coordinate conversion

The fixture's centimetre positions map to Blender metres as
`[UE.X, -UE.Y, UE.Z] / 100`. Actor parent yaw is `-UE.yaw`. A child parent rotates
the imported FBX model by +90 degrees around Z, converting its Blender -Y forward
direction to the preview's +X forward direction. The model receives no negative
scale or reflected mesh transform.

This mapping is the preview's declared convention. The native Unreal FBX import
basis remains a separate check.

## Clip timing and layered actions

The scene imports animations at the plan's 60 fps timebase. Each rendered instant
uses its exact scene seconds. The selected actor section determines source time
through its start, source offset, rate and loop setting. At the kick still, the
pass is sampled at 1.39262125 seconds. At reception, the receive clip is sampled
at 0.57187 seconds in scene time 2.43762125 seconds.

The renderer uses the animation slot assigned to the imported FBX armature. It
evaluates that slot's original transform FCurves at the source frame and applies
the resulting transforms to the matching mesh rig, then lets Blender evaluate
skin deformation. This avoids one global frame clock forcing both athletes to
the same clip time. It does not shift or change source keys. Multiple layers
writing the same channel, unsupported channel paths or a missing assigned slot
fail explicitly.

Idle sections follow the shared planner. They are abrupt baseline sections, not
authored blends. The baseline images preserve the first-sample jump in the
supplied pass. The current corrected export opts into holding stored sample one
over the opening 0.0332 seconds for both the pose and actor root. That derivative
does not shift later timestamps, change the contact clock or modify the source
arrays. It is a continuity correction, not recovered original capture. The
provisional ball path and held reception remain as supplied.

## Execution

Use a dedicated Python environment with the Blender `bpy` module installed and
working in background mode. The original offline review used Blender 5.2.2 LTS.
The public CI environment does not install Blender or run these renders. Point
`SQUADPAD_BPY_PYTHON` at the interpreter in the prepared environment. This is a
local path placeholder, not an instruction to connect to another machine.

```sh
export SQUADPAD_BPY_PYTHON="/absolute/path/to/blender-python-environment/bin/python"
mkdir -p Saved/BlenderUser/config Saved/BlenderUser/extensions
export BLENDER_USER_CONFIG="$(pwd)/Saved/BlenderUser/config"
export BLENDER_USER_EXTENSIONS="$(pwd)/Saved/BlenderUser/extensions"
"$SQUADPAD_BPY_PYTHON" -B Tools/Review/render_first_pass.py \
  --output-dir Saved/Review/kick --event kick --samples 12 --threads 4
```

The script parses arguments directly, so these commands use the prepared Python
interpreter rather than Blender's `--python` command-line argument forwarding.
It requires a fresh output directory and rejects an existing one. Run only after
the source export passes its receipt checks. Begin with one contact still.
`--event receive` selects the second contact. `--time-seconds` samples another
exact scene time. `--width`, `--height`, `--samples` and `--threads` bound the work.

After inspecting the still, a short frame sequence can be rendered separately:

```sh
"$SQUADPAD_BPY_PYTHON" -B Tools/Review/render_first_pass.py \
  --output-dir Saved/Review/frames --frames --fps 24 --samples 12 --threads 4
```

The regular path samples `index / fps` for `ceil(duration * fps)` frames.
Frame exposure adds a small final display hold instead of retiming the source.
The receipt distinguishes fixture duration from frame-exposure duration.
No video encoder runs automatically. The scene is not saved as a playable
`.blend` animation because each actor's clip clock is sampled directly.

## Shoulder diagnostic comparisons

`render_shoulder_diagnostic.py` uses the same FBX import and independent pose
evaluation. It centers one athlete for fixed front, back, side and rear
three-quarter views. Supported poses are rig rest, idle, exact pass contact and
exact receive contact. `--variants clothed,body` hides kit objects for the body
view. The supplied body has cutouts beneath the garment, so this does not reveal
a complete torso or shoulder surface. `--neutral` replaces materials with
neutral shading without changing geometry.

```sh
"$SQUADPAD_BPY_PYTHON" -B Tools/Review/render_shoulder_diagnostic.py \
  --output-dir Saved/Review/shoulders --pose pass_contact \
  --views threequarter --variants clothed --size 640 --samples 12 --threads 4
```

`--mesh-fbx /absolute/path/to/alternate/athlete_a.fbx` can compare another mesh
with the current animations. Bone names, parents, rest matrices and armature
world transform must match the current mesh. Action clocks remain unchanged.

## Historical offline evidence

The original offline package retains the baseline and corrected exact-time
stills under `Reports/BlenderPreview/`, final shoulder comparisons under its
`shoulder_diagnostics/` subdirectory, and the short video and receipts under
`Preview/`. Those files are not included in this public checkout. Earlier
machine-specific commands and cache locations are preserved only in that
historical package.

The recorded final sequence covered the 3.7925-second fixture with 92 PNG frames
at 24 fps and 960 by 540 pixels. Source clocks and root descriptors were preserved.
Encoding and decoded frame checks recorded a 3.833333-second display duration,
including the final frame exposure. Sampled stills showed a reduced sleeve cap
while preserving the supplied face and body. The body under the shirt remained
incomplete. These observations are scoped Blender evidence, not current CI
results or accepted native animation.

Known baseline gaps remain: unblended section changes, provisional ball positions,
no receiving cushion and no measured boot contact. Native Unreal playback,
replacement-character review and device performance require their own evidence.
