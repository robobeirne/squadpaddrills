# SP2 runtime probe preparation

Status: source implemented in the cloud. Unreal compilation, editor execution,
packaging, Swift hosting and iPhone measurements remain unrun.

Begin with the [native build and reader gate](NATIVE_EXECUTION.md). That narrow
first gate needs only the public source and compiled fixture. The asset-backed
probe procedure below follows a successful build and restoration of the
separate input bundle.

This increment connects the portable playback design to an Unreal runtime
module and prepares an independent saved probe. Python creates the data asset
and map in the editor. The packaged scene uses C++ for playback. No Python
interpreter or Level Sequence controls the runtime playhead.

The commands below require restoration of the separate asset bundle from
[Asset setup](ASSET_SETUP.md), then a real Unreal build. They are outside the
portable public CI gate. All shell commands use the repository root as their
working directory.

## Inputs and ownership

First run the native bootstrap successfully. No successful native bootstrap
receipt or imported native assets are included in this repository. The required
receipt has schema `squadpad.unreal.bootstrap-receipt.v1` and status
`native_import_save_readback_passed`. The input manifest, original fixture,
export receipt and every source hash must still match. The saved baseline map
and sequence must exist with their recorded bytes. Required mesh, animation
and skeleton packages must also match the original hashes and sizes in
`imported_native_files`, with exact unique package coverage. A saved asset edit
cannot reuse the earlier successful receipt merely by keeping its path and
duration. An older receipt lacking this inventory requires rerunning the
current bootstrap. The revision 5 bootstrap source is preserved in the original
offline package under `Docs/History/`. That historical copy is not part of this
public checkout. The current version adds this imported-package provenance record.

`Tools/Unreal/runtime_probe_plan.py` checks this evidence without importing
Unreal. Its result is input readiness only. A receipt alone does not prove the
actual native asset types. The editor builder loads every object, verifies its
class, skeleton, duration, bone-track count, root extraction and rate policy.

The baseline and probe use the exact same recorded engine build. A changed
engine requires a new baseline probe. Native assets with unsaved edits block
preparation. An unsaved map blocks creating a replacement editor level.

The first probe only supports the currently reproducible two-actor first pass.
Its playback and binding JSON must match fresh compilation from the validated
sources. A different asset or drill needs a deliberate extension to this
preparation path.
Duplicate JSON members and non-finite numbers are rejected before native
preparation, including numbers inside metadata.

## Runtime objects

`USquadPadPlaybackDataAsset` contains the playback JSON and hard references to
the selected meshes and Animation Sequences. Appearance slots resolve through
profiles. The ball mesh and material also use hard references. Cooking the
probe map therefore has a normal object-reference chain to its content.

The data asset does not load source FBX paths or JSON files from the user's
filesystem at runtime. Source hashes and the exact native references are
recorded by the editor build receipt.

`ASquadPadPlaybackActor` loads the data asset and applies the portable core's
world transforms, source clocks and possession. The first adapter uses manual
single-node sampling. The baseline remains unblended and uses the provisional
ball path. Native evaluated bones do not establish accepted football contact.

The actor starts paused at zero by default. `auto_load_on_begin_play` is true.
`auto_play` is false unless explicitly requested when building the probe.
Transient mesh components and control state are discarded by `unload_playback`.
The saved data-asset reference remains available for later reload.

`ASquadPadProbeGameMode` supplies the developer probe controller. The map sets
this game mode explicitly. A single camera has the tag `SquadPadProbeCamera`
and auto-activates for player zero. The controller selects this tagged camera.
No pawn or competing LevelSequence actor owns the view or playhead.

## Build sequence for the builder

These commands describe later native execution in an authorized environment.
They were not executed during cloud preparation. Public CI does not connect to
a developer machine. Replace the example engine location with the installed
engine and run from the repository root.

Build the editor target with the installed engine's build tool. For a Mac
engine layout, the command form is:

```sh
SQUADPAD_ENGINE_ROOT="/absolute/path/to/UE_5.8"
"$SQUADPAD_ENGINE_ROOT/Engine/Build/BatchFiles/Mac/Build.sh" \
  SquadPad3DEditor Mac Development \
  "$(pwd)/SquadPad3D.uproject" -WaitMutex
```

Record the actual compiler, engine version and build result. A GCC build of
`RuntimeCore` does not replace this Unreal/UHT compilation gate.

Execute the bootstrap as described in the
[Unreal bootstrap guide](../Tools/Unreal/README.md). Use its actual successful
receipt. Then check the
runtime probe inputs from the project root:

```sh
python3 -B Tools/Unreal/runtime_probe_plan.py \
  --bootstrap-receipt Saved/SquadPad/Receipts/ACTUAL_BOOTSTRAP_RUN.json
```

Inside the same compiled Unreal Editor, use:

```python
from pathlib import Path
import unreal
import squadpad_runtime_probe

root = Path(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()))
receipt = squadpad_runtime_probe.build(
    root / "Saved/SquadPad/Receipts/ACTUAL_BOOTSTRAP_RUN.json",
    project_root=root,
    auto_play=False,
)
```

Alternatively execute `Tools/Unreal/runtime_probe_entry.py` through Unreal's
Execute Python Script command. `SQUADPAD_BOOTSTRAP_RECEIPT` is required.
Optional variables are `SQUADPAD_RUNTIME_RUN_NAME`, `SQUADPAD_RUNTIME_RECEIPT`
and `SQUADPAD_RUNTIME_AUTOPLAY` (`0` or `1`, default `0`). Relative paths resolve
under this project. Merely opening the project does not execute the script.

Every run creates a fresh `/Game/SquadPad/RuntimeProbe/<run-name>` namespace.
The script refuses a reused namespace or receipt. It preserves partial assets
after a failure for diagnosis and never replaces the original bootstrap.

The result includes `DA_FirstPass`, `L_RuntimeFirstPass`, review materials,
floor, lights, the runtime actor and the tagged camera. The script saves and
reloads the data package, reopens the map, checks serialized references and
configuration, then explicitly loads and seeks the runtime actor in the editor.

## Editor readback

Readback samples zero, the final endpoint, exact contacts, their immediate
neighbouring 60 Hz samples and every action-section start. It compares actual
component transforms, action IDs, canonical source times and ball ownership
against the portable reference. It queries the single-node instance's actual
Animation Sequence and requires the expected imported asset. The native sample
time is recorded separately and checked against that asset's loaded duration.

An imported animation slightly shorter than the canonical source, within the
existing import tolerance, produces an endpoint hold. The receipt records this
gap. It must not silently rescale the entire action or move its contact clock.

The script checks the required foot/hip bones and records finite native bone
positions. It removes each actor's world translation and yaw and requires more
than 0.1 cm of reviewed-bone movement between distinct samples of each active
pass/receive clip. The receipt records the responding bone, sample pair and
maximum local displacement. A frozen reference pose or world travel alone
fails this gate. It repeats the seeks in reverse order and compares the
evaluated bone positions. These observations establish editor pose response
and seek consistency only. They do not measure the boot surface, establish
receiving cushion, accept garment deformation or prove realtime advancement.

Transient evaluation components are unloaded before the final save. Bound
baseline native packages must keep their starting hashes. A successful receipt
uses `native_probe_saved_editor_readback_passed` and still records realtime,
cooked playback, Swift, iOS, contact and visual acceptance as false.

## Native playback controls

After a successful native build, use the saved map in PIE or a Development
standalone build. The developer console commands are:

```text
SPStatus
SPPlay
SPPause
SPSeek 1.39262125
SPSeek 2.43762125
SPRate 0.25
SPRestart
SPUnload
SPLoad
```

These commands exercise the runtime actor. They are probe controls, not the
shipping mobile UI or Swift bridge. Confirm pause holds, seeking does not emit
contact effects, restart pauses at zero, repeated load/unload succeeds and the
final endpoint pauses. Observe real ticks separately from the editor seek test.

The map and data asset paths come from the successful probe receipt. Supply
the real map to the installed engine's cooking/packaging workflow. Do not set
a global startup map to a path which has not been created. No signing identity,
application identifier or installed Apple SDK is altered by this preparation.

The next delivery evidence is a cooked native viewer and an actual iPhone
launch. The minimal Swift host remains a separate implementation and lifecycle
gate. Record opening, closing, memory pressure, background/foreground, repeated
entry and resource measurements in the combined host.

## Asset-dependent offline verification

Restore the bundle before running this suite. It requires the real source
exports even though it does not launch Unreal. It is not a clean-checkout
portable CI command. From the repository root:

```sh
python3 -B -m unittest Tests.test_runtime_probe -v
```

The twenty-one focused tests use real exported sources copied into a
temporary project. Their native receipts and `.uasset`/`.umap` files are
explicitly synthetic test fixtures. Tests cover absent or failed bootstrap,
stale source hashes, missing/duplicate stages, changed native package bytes,
wrong skeleton/duration, invalid paths, missing assets, historical imported
package hashes and source preservation. Older receipts without the imported
inventory fail explicitly.
Three synthetic pose cases verify that the pose gate accepts local bone motion
and rejects both frozen poses and unchanged poses moved through the world.
They do not execute Unreal or manufacture native acceptance.

The existing core evidence is reused because the portable core source is
unchanged. Native C++ automation tests have their own Unreal execution gate.
Do not repeat unchanged Blender renders or the full baseline suite merely
because documentation or probe instructions changed.

## Primary API references

Reviewed on 20 October 2026. Actual native compilation and execution remain the
decisive API compatibility check.

- [DataAssetFactory](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/DataAssetFactory)
- [Editor loading, saving and package reload](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/EditorLoadingAndSavingUtils)
- [Editor actor subsystem](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/EditorActorSubsystem?application_version=5.8)
- [CameraActor](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/CameraActor)
- [AutoReceiveInput](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/AutoReceiveInput?application_version=5.7)
- [SceneComponent transforms](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/SceneComponent?application_version=5.7)
- [StaticMesh bounds](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/StaticMesh?application_version=5.7)
- [World settings lookup](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/World.html)
- [Skinned mesh identity and bone queries](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/SkinnedMeshComponent?application_version=5.7)
- [WorldSettings game mode](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/WorldSettings?application_version=5.5)
