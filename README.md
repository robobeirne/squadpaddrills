# SquadPad drills: Unreal migration

Unreal project source, migration tools and a portable playback core for
SquadPad's football drill viewer. The project preserves authored drill timing
and recorded animation clocks while moving presentation into Unreal.

This repository contains the first implementation from the offline migration
work. The engine-independent core runs without Unreal. The Unreal runtime
module and editor builders are implemented in source, but have not yet been
compiled or executed inside Unreal. There is no playable native build yet.

## Run the portable checks

From the repository root, with Python 3.12 and a C++17-capable `g++`:

```sh
python3 -B -m unittest Tests.test_playback -v
python3 -B Tools/Playback/verify_native.py --report /tmp/squadpad-portable-validation.json
```

These commands need no models, capture pack, Blender, Unreal installation or
third-party Python packages. The current checks cover 15 Python contract and
control tests, 84 C++ assertions and 354 C++/Python comparisons. The
[portable workflow](.github/workflows/portable-playback.yml) runs the same checks
on pull requests and pushes to `main`. See [CI scope](Docs/CI.md) for details.

The workflow also exercises the native build launcher and SSH coordinator on
Python 3.12 and 3.10 using offline test fixtures. These launcher tests run no
Unreal process and make no worker connection.

A passing check establishes playback logic and reference agreement. Native
compilation, rendered animation, foot-to-ball contact and device performance
have separate acceptance gates.

## What is included

| Path | Purpose |
| --- | --- |
| `Source/` | Unreal targets, runtime module, strict JSON reader, data asset, playback actor and probe controls |
| `RuntimeCore/` | Engine-independent C++17 validation, time sampling and playback controls |
| `Contracts/` | Playback and athlete-binding JSON schemas |
| `Fixtures/Playback/` | Compiled two-player, one-ball pass and baseline asset-binding metadata |
| `Content/Python/` | Editor import, scene planning and native probe builders |
| `Tools/` | Source transport, Blender export, shoulder comparison, drill conversion and verification tools |
| `Tests/` | Portable tests and explicitly separate source-dependent suites |
| `Fixtures/Audition/` | Shared comparison specification for a future production athlete |
| `Docs/` | Architecture details, native procedure, asset setup and dated implementation plan |

The compiled pass lasts 3.7925 seconds. Strike occurs at 1.39262125 seconds and
reception at 2.43762125 seconds. It represents one isolated pass, rather than
the complete drill library. Both appearance slots currently reference the
same baseline athlete profile.

The playback fixture contains application world tracks derived from authored
drill anchors and the existing capture's horizontal root displacement. It
contains no mesh, textures, skeleton or joint-animation tracks. Its historical
source hashes remain intact. The fixture is not described as wholly synthetic
or as a newly licensed motion-capture pack.

## Asset and native prerequisites

The original models, capture binaries, Blender exports, textures, native Unreal
content and historical evidence bundle are supplied separately. They are not
part of this public source commit. The exact garment, reference-model and
capture redistribution records still need to be attached before publishing
that bundle. MakeHuman's core assets are provided under
[CC0](https://static.makehumancommunity.org/about/license.html).

[Asset setup](Docs/ASSET_SETUP.md) explains which original inputs each stage
requires. Portable checks work directly from a clean checkout. Fixture
regeneration, Blender tools and native import require the original asset
bundle. The corresponding source-dependent tests fail when inputs are absent.

The project declares the Unreal 5.8 family. A native build must record the
actual installed engine build and validate its APIs. The engine is not stored
or installed by this repository's workflow. The Swift app and existing
three.js viewer remain separate source projects.

## Next implementation gate

Run the [native build and reader gate](Docs/NATIVE_EXECUTION.md) in a suitable
Unreal environment. Its first test uses only public source and the compiled
fixture. The source increment includes a bounded native launcher, SSH
coordinator and dormant manual workflow. The [remote compute proposal](Docs/REMOTE_BUILD_COSTS.md)
prices one attempt; no worker has been provisioned or paid execution authorized.

After that gate passes, restore the reviewed asset inputs, import and reopen them, then build the
saved runtime probe. Inspect real animation poses and playback controls before
cooking a build. Prove iPhone playback and the Swift host before converting
the wider drill library.

The current baseline has unblended transitions and provisional ball placement.
Support-foot locking, reception cushioning, ball rotation, improved flight and
the production-athlete selection remain open work. Moving source into Unreal
does not by itself improve the athlete's anatomy or animation quality.

Start with [the playback contract](Docs/PLAYBACK_CONTRACT.md),
[runtime module](Docs/RUNTIME_MODULE.md), [native probe](Docs/RUNTIME_PROBE.md)
and [the dated implementation plan](Docs/IMPLEMENTATION_PLAN.txt).
