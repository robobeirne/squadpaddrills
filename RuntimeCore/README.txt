SQUADPAD PORTABLE PLAYBACK CORE

Scope

C++17 typed-data validation and absolute-time state evaluation. No Unreal types,
JSON parser, skeletal evaluation, physics, IK or rendering dependency. No native
Unreal execution or iPhone performance result is established by these tests.

Public API

include/squadpad/playback.hpp defines PlanInput, PlaybackPlan and
PlaybackController. PlaybackPlan::Create copies and validates typed input.
Its data remains private and exposes a const view only. Input mutation after
construction does not change the plan. Evaluate accepts a finite time, clamps
the time to the plan duration and returns a fresh frame without side effects.

Actor and ball tracks already contain their world positions in centimetres.
The core never adds root motion. Yaw follows the shortest path, uses a positive
180-degree tie and returns [0,360). Position interpolation uses a weighted sum
to avoid overflowing the difference of large finite endpoints.

Sections select by the latest start at or before the sample. The final endpoint
uses the last section. Source time follows the section offset and play rate.
Looping sections use modulo, including modulo zero at an exact loop endpoint.
Nonlooping sections hold their final source endpoint. Tolerated tiny timeline
joins do not permit sampling beyond the selected section's declared span.

Contacts belong to the active nonlooping action at their timeline instant and
must match exactly one supported possession transition. They remain explicitly
unmeasured. State evaluation does not emit contact events. A controller emits
contacts over (previous time, new time], ordered by time then stable ID.

Controller rules

Construction and Load start at time zero, paused, rate1. Restart returns to zero
and pauses while preserving the selected rate. Seek has no contact effects and
preserves play state unless reaching the end. Play at the end stays paused.
Positive finite rates are required. Advance accepts finite nonnegative deltas,
caps at the final endpoint and pauses there. No automatic looping exists.

Validation

Typed validation covers identifiers, units, axes, root ownership, finite data,
strict key order, track coverage, contiguous actions, source motion bounds,
known references, contact timing and ownership transitions. JSON object shape,
unknown fields and asset bindings belong to Tools/Playback/evaluate_playback.py.
Native typed validation does not replace JSON ingress validation.

Run

From the project directory:
python3 -B Tools/Playback/verify_native.py

The verifier uses the available g++ compiler and Python reference evaluator.
It builds tests and a generated typed-fixture harness in a temporary directory,
removes binaries on completion, and writes Reports/runtime-core-validation.json.
The generated harness is data conversion for testing, not a JSON runtime parser.
Compiler flags, source hashes, input hashes, tolerances and scope accompany the
result. Run again only after a relevant source or contract change.

The verification covers the actual compiled first-pass fixture, independently
specified arithmetic and malformed cases, controls and event boundaries. The
fixture still carries provisional ball placement and unmeasured contacts.
