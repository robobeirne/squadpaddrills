# Native runtime source, revision 6

Implemented in source on 20 October 2026. Unreal compilation, reflected-code
generation and native execution remain unrun. The public checkout contains source
and portable fixtures, with no imported character assets or generated probe map.
Read [Runtime probe](RUNTIME_PROBE.md) for the native procedure,
[Asset setup](ASSET_SETUP.md) for its prerequisites, and
[Playback contract](PLAYBACK_CONTRACT.md) for core semantics.

## Build and ownership

`SquadPadRuntime` is a Runtime module loaded by both `SquadPad3D` and
`SquadPad3DEditor`. The module uses Core, CoreUObject, Engine and Json. Python,
UnrealEd and Sequencer do not implement its playhead. Its portable-core wrapper
includes the existing core source so there is one evaluator to maintain.

The core reports invalid typed input with C++ exceptions. The module enables
exception support and catches validation failures at the reader/control
boundary. Unity builds are disabled for this small module to keep the included
core translation unit isolated. The engine chooses its C++ standard. A C++20
GCC wrapper check passed here but does not substitute for UBT/UHT/native build.

## JSON ingress

`SquadPadRuntime::ParsePlaybackJson` uses a bounded token pass, an Unreal JSON
DOM and strict typed extraction, followed by portable semantic validation.
The caller's previous input remains unchanged on failure. Successful parsing
clears the error and publishes one validated candidate.

Decoded duplicate keys, malformed or extra roots, nonfinite numbers, wrong
types, unsupported fields and incorrect contract-key case are rejected.
Contract keys are lowercase ASCII with underscores. The exact root provenance
and evidence objects retain open metadata. Explicit JSON null alone denotes
an absent optional possession ID. Identifiers remain case-sensitive.

| Ingress resource | Limit |
|---|---:|
| Input length | 4 Mi TCHAR code units |
| Container depth | 32 |
| Notation tokens | 500,000 |
| Actors / balls / source motions | 64 / 32 / 1,024 |
| Combined actor and ball keys | 65,536 |
| Action sections | 4,096 |
| Contacts / possession records | 2,048 / 4,096 |

These bounds prevent unbounded ingestion. They are not measured device budgets.
The native scene baseline further limits balls to 16, coordinates to 10 km
from the origin and ball radius to 100 m. The actual fixture is far smaller.

Native automation lives under `Private/Tests/SquadPadPlaybackReaderTests.cpp`.
Its test name is `SquadPad.Runtime.PlaybackReader.Contract`. It reads the actual
first-pass fixture and covers preserved contact clocks, ownership, malformed
input, exact spelling, duplicates, limits and failed-output preservation. This
suite has not been compiled or run against Unreal in the cloud environment.

## Native binding policy

`USquadPadPlaybackDataAsset` stores playback JSON, appearance-slot bindings,
mesh profiles and Animation Sequence references. Each used profile must supply
exact motion coverage, and every clip must share the mesh's Skeleton.

The baseline requires non-additive clips, root extraction off, force root lock
off and asset rate scale one. Native duration may differ from the canonical
source by at most `1/60 + 1e-6` seconds, matching the retained export/import
policy. Endpoint clamping does not rescale source clocks. The native readback
records the gap and the actual instance time.

The direct-clip route rejects meshes requiring a post-process animation
Blueprint. Production corrective rigs, including any selected candidate that
needs them, require a deliberate extension and fresh validation. Disabling a
required corrective rig is not an acceptable path to character acceptance.

The ball mesh must be centered, with each bounds extent matching its declared
radius within 0.05 cm. Material references are optional. The probe uses the
engine sphere as a placeholder, with no authored football texture or spin.

## Scene and control behavior

The playback actor must remain at the identity world transform. Portable actor
tracks already own world displacement. Each athlete component uses a paused
single-node animation instance. The actor sets the current source sample,
suppresses notify traversal and requests bone refresh directly. Autonomous
animation clocks, physics and cloth are disabled for this baseline.

`LoadPlayback` validates and stages the new scene before replacing the current
one. Pending and active UObject references remain visible to garbage collection.
`UnloadPlayback` destroys transient components and control state while retaining
the configured data-asset reference. EndPlay and actor destruction clean up.

| Operation | Behavior |
|---|---|
| Play / Pause | Changes portable play state and actor ticking |
| Seek | Samples an absolute time without contact events |
| Restart | Pauses at zero |
| SetPlaybackRate | Sets a finite positive rate |
| AdvancePlayback | Advances the playing controller, applies the frame, then emits crossed contacts |
| UnloadPlayback | Releases transient scene/control state |

All calls and queries are game-thread only. Reentrant commands fail while a
command/event batch is running. Native callback destruction stops evaluation.
The final endpoint pauses. There is no automatic loop or negative playback.

`GetActorSourceTime` reports the canonical requested clock.
`GetActorSampledTime` queries the actual native instance clock.
`GetActorSampledAnimation` queries its actual current animation asset.
Mesh/ball getters expose components for native transform and bone inspection.
No cached expected clip identity stands in for this readback.

## Verification boundary

The original revision 6 preparation compiled the actual core wrapper and
unchanged portable core, with 84 assertions passing. That is dated offline
evidence. Current public CI reports establish the results for the published
source revision. Source review checked the actor/probe interfaces and
documented API shapes. Neither establishes native compilation or animation.

The native editor probe must verify serialized references, active clip,
transforms, clocks, possession and actual changing local bone poses. It repeats
seeks in reverse to detect history-dependent evaluation. Real ticks, callback
delivery, load/unload/GC, cooking and physical device lifecycle remain separate
execution gates. The current source does not claim complete visual quality,
geometric contact, production Swift integration or an accepted memory budget.

## Primary API references

Reviewed against Epic documentation on 20 October 2026. The actual installed
engine remains the compile and execution authority.

- [Unreal modules](https://dev.epicgames.com/documentation/en-us/unreal-engine/unreal-engine-modules)
- [Module properties](https://dev.epicgames.com/documentation/unreal-engine/module-properties-in-unreal-engine)
- [Target reference](https://dev.epicgames.com/documentation/unreal-engine/unreal-engine-build-tool-target-reference)
- [JSON reader](https://dev.epicgames.com/documentation/en-us/unreal-engine/API/Runtime/Json/TJsonReader)
- [Single-node animation instance](https://dev.epicgames.com/documentation/unreal-engine/API/Runtime/Engine/UAnimSingleNodeInstance)
- [Skeletal mesh component](https://dev.epicgames.com/documentation/unreal-engine/API/Runtime/Engine/USkeletalMeshComponent)
