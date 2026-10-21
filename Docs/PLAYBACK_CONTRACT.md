# SP1 portable playback contract

Status: implemented and testable without Unreal. This contract samples object
transforms, source animation clocks and possession. It does not evaluate bones,
apply IK, generate improved athletes or certify visual quality.

The public repository includes the compiled playback and binding JSON below.
Recompiling them from the original assets requires the separate bundle described
in [Asset setup](ASSET_SETUP.md). Portable evaluation and C++ parity checks run
from a clean public checkout.

## Files and ownership

`Contracts/drill_playback_v1.schema.json` describes the portable data shape.
`Tools/Playback/evaluate_playback.py` enforces additional temporal and reference
rules. The semantic validator is authoritative for rules JSON Schema cannot
express. `RuntimeCore` implements the same state and clock rules in standard C++.

`Tools/Playback/compile_playback.py` reads the existing validated import plan.
It emits `Fixtures/Playback/first_pass.playback.json` and
`Fixtures/Playback/athlete_a_baseline.bindings.json`. Original source files,
the historical first-pass fixture and root tracks remain unchanged.

The current fixture contains two actors and one ball. It is the isolated first
pass from the observed rondo, not the complete drill. The original release at
1.39262125 seconds and receive at 2.43762125 seconds retain a 1.045-second transfer.

## Data boundary

The playback file declares centimetres, seconds and degrees with Unreal's Z-up,
X-forward axes. Actor tracks own all world travel. Skeletal root extraction must
remain disabled when this playback mode runs in Unreal.

Motion IDs describe source performances. Action occurrence IDs distinguish two
uses of the same source motion. Each actor has an appearance slot. A separate
binding maps the slot to a mesh, skeleton and corresponding retargeted animation
assets. A different visible athlete requires compatible retargeted clips and new
contact calibration. Changing an appearance label alone does not perform this work.

The actor positions combine authored drill anchors with contact-relative planar
displacement derived from the recorded motion. The fixture contains no skeletal
pose tracks, mesh geometry or textures. It is derived application playback data,
not a synthetic motion recording.

The portable file includes provenance hashes, not source machine paths. Its
evidence records that exported inputs were validated when it was compiled. This
does not mean those inputs are included in this checkout. It marks contact placement provisional,
transitions unblended, and native execution unverified. The source has no team
possession field. `evidence.team_possession` explicitly records
`not_provided_by_source`; shirt colour is never interpreted as team ownership.

## Evaluation rules

`PlaybackPlan(document).evaluate(seconds)` is side-effect-free. Finite queries
clamp into `[0, duration_seconds]`. Repeated calls and arbitrary seek order produce
the same state. Non-finite values and booleans used as numbers fail validation.

Actor and ball positions use linear interpolation, with explicit endpoint holds.
Tracks need at least two ordered keys and full-duration coverage. Each coordinate
uses weighted interpolation to avoid overflow from subtracting large opposite
finite values. Yaw normalizes each endpoint first, follows the shortest path,
uses the positive direction for an exact 180-degree tie, and returns `[0, 360)`.

Action sections cover the full duration. Their starts select the active action,
so a shared boundary belongs to the incoming section. The final endpoint belongs
to the final section. Join and coverage comparison allows `1e-8` seconds of
floating-point error. Evaluation clamps local elapsed time into the selected
section's declared range, including tiny tolerated gaps or overlaps.

Source time equals `source_start + elapsed * play_rate`. Looping clips wrap by
the canonical duration. Non-looping clips hold their final source time. Source
end arithmetic must stay finite even for looping clips. This version contains no
crossfade or skeletal pose blend.

Possession applies the latest record at or before the query time. Every ball
needs a record at zero. An owned record identifies its owner. An in-transit
record identifies the sender and intended receiver and has no owner.

Each noninitial possession change requires one matching kick or receive contact.
Each contact requires one corresponding possession change. Contacts reference an
existing actor, ball, action occurrence and source motion. Their source and scene
clocks must agree within `1e-7` seconds. The referenced action must be the active
action at the exact contact instant. A contact assigned to the outgoing action at
a nonfinal section boundary fails. Contacts on looped sections are unsupported.

Contact entries remain `unmeasured` in SP1. Matching event clocks does not establish
boot-surface contact, receiving cushion, foot locking or absence of body penetration.

## Preview clock

`PlaybackController(plan)` starts paused at zero and rate 1.

| Operation | Behaviour |
|---|---|
| `play()` | Starts forward playback unless already at the end. |
| `pause()` | Holds the current time. |
| `seek(t)` | Clamps and evaluates time without contact side effects. Preserves play state unless the result reaches the end. |
| `restart()` | Returns to zero and pauses, without side effects. |
| `set_rate(r)` | Requires a finite positive rate. Negative and zero rates fail. |
| `advance(dt)` | Requires finite nonnegative wall time. While playing, advances by rate and stops at the end. |

An advancing interval emits contacts in `(old_time, new_time]`, ordered by time
and ID. A zero delta emits nothing and preserves play state. Seeking and restarting
emit nothing. There is no automatic looping. Reverse inspection uses direct time
queries, rather than a negative playback rate. Replay after a deliberate restart
emits contacts again when forward playback crosses their times.

The returned frame contains ordered actors with position, yaw, appearance slot,
action ID, source motion ID and source time. Balls contain position, radius and
possession. Control updates wrap this frame with `crossed_contact_ids`.

## Athlete binding checks

`validate_bindings()` checks the playback digest, exact slot coverage, known
profiles, skeleton agreement, mesh-bound calibration freshness, animation target
mesh hashes, source hash format and canonical animation durations.

`validate_bindings_with_source_files()` additionally loads the strict current
import plan, which verifies source bytes against the export receipt. It compares
bound asset IDs, canonical motion identities, mesh/animation hashes, target rig
and timing against those independently read sources. This rejects a binding with
internally consistent but stale source hashes.

The playback digest is SHA-256 of UTF-8 JSON with sorted keys, no whitespace,
`ensure_ascii=False` and finite numbers. These checks establish metadata and file
compatibility. They do not prove native asset import or a successful visual swap.

## Builder commands

From the repository root, with Python 3 and a C++17 compiler available:

```sh
python3 -B Tools/Playback/evaluate_playback.py Fixtures/Playback/first_pass.playback.json --time 2.43762125 --bindings Fixtures/Playback/athlete_a_baseline.bindings.json
python3 -B Tools/Playback/verify_native.py --report Saved/Verification/portable-core.json
```

The evaluation CLI's binding check is metadata-only. The parity runner compiles
the standard C++ core and compares its results with the Python reference. Neither
command requires the separate asset bundle or executes Unreal.

After restoring the exact bundle in [Asset setup](ASSET_SETUP.md), a comparison
compilation can write to a separate output directory:

```sh
python3 -B Tools/Playback/compile_playback.py --output-dir Saved/PlaybackRebuild
```

Compilation checks current source files. Compare the resulting documents with
the checked-in fixtures before proposing a fixture update. Keep historical source
receipts unchanged. Asset-dependent regression tests are listed in Asset setup.
