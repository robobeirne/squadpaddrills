# Existing drill adapter

The fixture isolates the first A-to-B pass from `rondo-4v1`, level 1. It is an
existing drill segment, not a newly invented drill. The remaining rondo players,
ball and three later segments are omitted explicitly. The complete observed
source payload is retained inside the fixture.

The public checkout includes the adapter code and compiled application playback
under `Fixtures/Playback/`. The two original JSON files below remain in the
separate bundle. Restore them using [Asset setup](../../Docs/ASSET_SETUP.md)
before running this adapter.

## External inputs and public code

- `Fixtures/rondo-4v1.level-1.observed.json`: recovered source observation.
- `Fixtures/first_pass.drill.json`: generated Unreal integration fixture.
- `Tools/Drill/export_first_pass.py`: standard-library Python adapter.

The observation was reconstructed from a recorded read of the existing source
project. It does not establish current source repository bytes. The snapshot's
hash identifies that recovered observation only. The original source recordings
and a current source checkout are not part of this public repository.

## Generate and check

After restoring the original fixture and observation, from the repository root:

```sh
python3 -B Tools/Drill/export_first_pass.py --check
mkdir -p Saved/DrillRebuild
python3 -B Tools/Drill/export_first_pass.py --output Saved/DrillRebuild/first_pass.drill.json
```

The comparison output leaves the restored fixture and its recorded hash intact.
The default output would replace `Fixtures/first_pass.drill.json`, so use the
explicit comparison destination when reviewing regeneration.

The adapter writes only the requested output. It does not import the app's
Python exporter, execute Swift, install packages, compile native code or connect
to a remote machine.

When a local source checkout becomes available, regenerate from its card, golden
timeline and mocap metadata:

```sh
python3 -B Tools/Drill/export_first_pass.py \
  --source-root /absolute/path/to/local/checkout \
  --output Saved/DrillRebuild/current-source.first_pass.drill.json
```

This mode records file hashes. It reads an existing golden timeline and does not
recompile the Swift engine. A changed or unsupported first segment fails instead
of silently producing a different kind of action. Only the initial ground pass
from A to B in this drill is supported.

## Contract

The schema is `squadpad.drill.fixture.v1`, with centimetres, seconds and degrees.
Actors retain IDs `A` and `B`. The ball retains ID `ball`. Rotation objects use
`{pitch, yaw, roll}`. Timings are seconds and include fractional frames at the
declared 60 fps display rate.

The original diagram uses metres, x right and y down. The pair's centre is
`[4.5, 4.5]` metres. The conversion is:

```text
UE.X = (4.5 - diagram.y) * 100
UE.Y = (diagram.x - 4.5) * 100
UE.Z = height_metres * 100
```

A starts at `[250, 250, 0]` cm with yaw -135 degrees. B starts at
`[-250, -250, 0]` cm with yaw 45 degrees. The diagram's attack-up direction becomes
Unreal +X. No browser CSS mirroring is applied.

The source segment starts at 0 and ends at 1.045 seconds. The recorded pass lasts
3.3925 seconds and its observed contact phase is 0.4105. Therefore playback adds
1.39262125 seconds of preroll, preserving the 1.045-second transfer duration.
Release occurs at 1.39262125 and arrival at 2.43762125 seconds. The 1.3-second
receive clip starts at 1.86575125 seconds so its contact phase 0.4399 matches
arrival. The fixture lasts 3.7925 seconds, including a 0.4-second review hold after
the last action ends. These added periods do not play the omitted drill segments.

## Evidence boundaries

Contact candidates use the existing canonical X Bot metadata. In that metadata,
`ball` contains `[left, ahead]` in metres, derived from an ankle/toe heuristic.
The corresponding Unreal local direction is `[ahead, -left, up]`.

Neither candidate is a measured contact on the athlete mesh. Both contact
records explicitly contain:

```json
{"status": "unmeasured", "bone_name": null, "local_offset_cm": null}
```

The ball uses a provisional ground path between these candidates and then holds
the arrival position. The ball radius is an explicit 11 cm review assumption,
not a measured property of the source diagram. The receiver's cushioning ball
motion still requires a measured target-athlete foot trace.

Each actor's `root_motion` explicitly requires a contact-relative clip trace
from an export descriptor. `transform_keys` are stationary world guide anchors,
not permission to discard the captured run-up. The importer must rotate the
local trace by actor yaw and add it to these anchors, exactly once, alongside an
in-place FBX. Hold the first/last trace value through adjacent idle sections to
avoid a location jump. Missing root descriptors must block a faithful-motion
build rather than silently leaving the athlete running on the spot.

The included check verifies the bounded data contract, finite values, original
IDs, preserved pass duration, clip/contact timing and possession A → transit → B.
It does not validate boot surfaces, import success, runtime performance, visual
quality or iPhone integration. No calibrated contact or visual pass is claimed.
