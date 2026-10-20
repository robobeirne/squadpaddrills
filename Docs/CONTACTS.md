# Contact evidence contract

This contract is included because the public athlete audition specification
references it. Contact checking against real source bytes requires the separate
bundle described in [Asset setup](ASSET_SETUP.md). All commands below run from
the repository root.

## Current result

Both baseline fixture contacts are unmeasured. Their bone names and local offsets are
null. Their ball centres come from a canonical ankle/toe heuristic, not a measured
athlete boot surface. With the baseline bundle restored, the checker returns `blocked` until measured
calibration and evaluated geometry are supplied.

`validate_contacts(plan)` checks intent and clocks. It ties each event to an
actor, ball, source animation and limb, including the source metadata's right
foot. It rejects duplicate events, ambiguous action occurrences, source contact
drift and inconsistent calibration declarations. A valid result establishes
metadata consistency only.

## Measured surface calibration

After importing the real mesh, identify a point on the intended boot or glove
surface. Express the point and outward unit normal in the named bone's local
frame. Bind that calibration to the exact source mesh FBX hash and record how the
surface was measured. Replace the event's unmeasured declaration with:

```text
calibration:
  status: measured
  id: stable identifier for this mesh and surface calibration
  bone_name: the actual imported bone name
  local_offset_cm: [x, y, z] measured in that bone's local frame
  local_normal: [x, y, z] outward unit vector in the same frame
  mesh_source_sha256: SHA-256 of the imported mesh FBX
  method: description of the actual surface measurement
```

Use an identified surface name instead of `boot_surface_unmeasured`. The real
mesh FBX is retained in the separate bundle. No calibrated surface or evaluated
Unreal pose has been recorded. The Blender preview is separate evidence and is not accepted as
an `unreal_evaluated_sequence` measurement.
Do not use a bone centre or the midpoint between two hands as a surface estimate.

## Evaluated measurements

The geometry CLI accepts a JSON document with schema
`squadpad.contact.measurements.v1`. Its top-level fields are:

| Field | Required value |
|---|---|
| `units` | `{"distance":"centimetres","time":"seconds"}` |
| `axes` | `unreal-z-up-x-forward` |
| `fixture_sha256` | Hash of the current calibrated fixture file |
| `evaluation.kind` | `unreal_evaluated_sequence` |
| `evaluation.engine_version` | Actual engine version used |
| `evaluation.sequence_asset_path` | Saved `/Game/...` sequence |
| `evaluation.run_id` | Identifier for this evaluation |
| `evaluation.captured_at_utc` | Evaluation capture time |
| `evaluation.build_receipt_sha256` | Hash of the corresponding native build receipt |
| `samples` | One identified measurement per intended contact |

Each sample requires:

- `contact_id`, `actor_id`, `ball_id`, `limb`, `surface`.
- `calibration_id`, `mesh_source_sha256`.
- `time_seconds`, the actual evaluated sequence instant.
- `ball_center_cm`, a three-number world position.
- `contact_point_cm`, the calibrated surface point transformed by the evaluated
  skeletal pose into world space.
- `surface_normal_world`, the corresponding outward world-space unit normal.

Capture these values from the real evaluated sequence. The current bootstrap
does not provide a skeletal pose capture tool. Recording the values is a later
editor task, along with visual verification of the selected surface. File hashes
bind the submitted data to declared inputs. They do not authenticate a fabricated
measurement or prove that its producer actually ran Unreal.

Run:

```sh
python3 Tools/pipeline.py contacts --measurements /path/to/evaluated-contacts.json
```

The checker compares the intended surface point with the surface of the intended
ball in three dimensions. It requires the outward normal to face the ball and
the sample time to match the intended event. Defaults allow a 1.5 cm surface gap
and half a 60 fps display frame of timing error. These are proposed review
thresholds, configurable and always recorded in the report. They are not a
scientifically established realism score.

Unknown or duplicate sample IDs are rejected. A different player, ball, limb,
surface calibration or mesh cannot satisfy an event. Missing measurements or
missing mesh bytes block complete acceptance. Synthetic values in the unit tests
exercise these failures and are not exported as real evidence.

## What still needs visual and temporal review

A passing result covers the submitted contact instants. It does not show whether
the support foot slides, the ball penetrates the body between samples, the boot
accelerates naturally, a reception cushions the ball, or an idle blend snaps.
Those require visible playback and additional sampled traces as the runtime work
expands. The report deliberately leaves `visual_quality_accepted` false.
