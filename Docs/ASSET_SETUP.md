# Asset setup and verification boundaries

The public repository contains authored tools, the portable C++ core, Unreal
source, contracts and compiled playback fixtures. It does not include the raw
models, motion recordings, Blender exports, imported Unreal assets or previous
review media. Portable checks run without those assets. Asset conversion and
native scene preparation require the separate original revision 6 bundle.

All commands below use the repository root, containing `SquadPad3D.uproject`,
as the working directory. No command connects to a developer machine.

## What works in a clean public checkout

With Python 3 and a C++17 compiler available:

```sh
python3 -B Tools/Playback/evaluate_playback.py Fixtures/Playback/first_pass.playback.json --time 2.43762125 --bindings Fixtures/Playback/athlete_a_baseline.bindings.json
python3 -B Tools/Playback/verify_native.py --report Saved/Verification/portable-core.json
python3 -B Tools/Export/retarget_core.py
python3 -B Tools/Export/test_retarget_inputs.py
```

The native parity command builds standard C++, samples the published fixture
and compares the result with the Python reference. It does not compile an Unreal
module or evaluate bones. The exporter regressions use labelled synthetic data.
The main README and workflow define the full current portable CI command set.

The compiled fixture combines authored drill anchors and contact-relative planar
displacement derived from recorded motion. It retains contact clocks, possession
and source hashes. It contains no mesh, textures or skeletal pose tracks. It is
not a substitute for the original assets or a wholly synthetic motion recording.

## Restore the separate bundle

Use the trusted original revision 6 `SquadPad3D` directory supplied separately
to the project owner. No downloadable asset archive is published by this
repository. Restore these paths with their original bytes and relative layout:

| Path | Required contents and purpose |
| --- | --- |
| `SourceInputs/` | `athlete_a.glb`, `xbot/footballer.glb`, `mocap-drills.json`, `mocap-drills.bin`, and `input_manifest.json` |
| `DerivedInputs/` | `athlete_a_shoulders.glb` and its correction receipt |
| `SourceData/` | Complete `athlete_a/` export directory, including four FBXs, three `.root.json` tracks, `export_manifest.json`, materials, skeleton reference, textures and the exporter ownership marker |
| `Fixtures/first_pass.drill.json` | Original hash-pinned integration fixture |
| `Fixtures/rondo-4v1.level-1.observed.json` | Original recovered source observation |

The four FBXs are `athlete_a.fbx`, `idle.fbx`, `pass.fbx` and `receive.fbx`.
The root tracks are `idle.root.json`, `pass.root.json` and `receive.root.json`.
Copy each directory intact. Do not substitute similarly named full-size models,
regenerated exports, Git LFS pointer files or files from a different revision.
The model correction is specific to the supplied quantized athlete.

For a fresh checkout whose five destination paths are absent, this explicit
copy preserves the public code and existing `Fixtures/Playback` directory:

```sh
export SQUADPAD_ASSET_BUNDLE="/absolute/path/to/original-revision6/SquadPad3D"
python3 - <<'PY'
import os
from pathlib import Path
import shutil

source = Path(os.environ["SQUADPAD_ASSET_BUNDLE"]).expanduser().resolve(strict=True)
project = Path.cwd().resolve()
paths = (
    "SourceInputs", "DerivedInputs", "SourceData",
    "Fixtures/first_pass.drill.json",
    "Fixtures/rondo-4v1.level-1.observed.json",
)
if not (project / "SquadPad3D.uproject").is_file() or source == project:
    raise SystemExit("Choose the repository root and a separate original bundle")
for relative in paths:
    original, target = source / relative, project / relative
    if not original.exists() or target.exists() or target.is_symlink():
        raise SystemExit(f"Missing source or existing destination: {relative}")
for relative in paths:
    original, target = source / relative, project / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if original.is_dir():
        shutil.copytree(original, target)
    else:
        shutil.copy2(original, target)
print("Restored the five asset-bundle paths")
PY
```

The script refuses existing destinations. If assets are already restored, verify
them instead of copying over them. Preserve the original fixture and receipt
bytes, including historical metadata, so provenance hashes remain meaningful.
These local paths stay outside the public source commit.

`Tools/source_bundle.py` supports a different, four-input snapshot format. Its
`inspect --bundle` command validates that snapshot without extracting it. That
ZIP alone does not restore the corrected model, FBXs or original drill fixtures.

## Verify the restored baseline

First verify the immutable source fixture, export receipt and canonical pair
against the provenance already published in the compiled playback:

```sh
python3 - <<'PY'
import hashlib
import json
from pathlib import Path

project = Path.cwd()
playback = json.loads((project / "Fixtures/Playback/first_pass.playback.json").read_text())
provenance = playback["provenance"]
expected = {
    "Fixtures/first_pass.drill.json": provenance["source_fixture_sha256"],
    "import_manifest.json": provenance["source_manifest_sha256"],
    "SourceData/athlete_a/export_manifest.json": provenance["source_export_receipt_sha256"],
    "SourceInputs/mocap-drills.json": provenance["canonical_index_sha256"],
    "SourceInputs/mocap-drills.bin": provenance["canonical_binary_sha256"],
}
for relative, digest in expected.items():
    if hashlib.sha256((project / relative).read_bytes()).hexdigest() != digest:
        raise SystemExit(f"Baseline hash mismatch: {relative}")
print("Published provenance matches the restored baseline")
PY
python3 -B Tools/pipeline.py offline-check
python3 -B Tools/pipeline.py preflight
```

`offline-check` needs both original fixture JSON files even though it permits
missing FBXs. `preflight` requires the declared FBXs and root tracks and checks
their hashes and byte lengths against the successful structural export receipt.
Neither command runs Unreal or proves visual quality.

To compare source compilation with the checked-in portable contract, write a
separate output rather than overwriting the accepted baseline:

```sh
python3 -B Tools/Playback/compile_playback.py --output-dir Saved/PlaybackRebuild
```

Any proposed asset or fixture update needs fresh provenance and reviewed binding
changes. Do not edit old receipts to make changed bytes appear to match.

## Asset-dependent checks

The following integration suites need the restored bundle. They use synthetic
native packages where documented and do not establish actual engine execution:

```sh
python3 -B Tools/Unreal/test_planning.py
python3 -B -m unittest Tests.test_quality Tests.test_runtime_probe Tests.test_playback_source -v
```

Tests for source preservation, compilation and binding-source compatibility
belong to this gate. Missing asset inputs must not be reported as a successful
asset verification merely because the portable subset passes.

For conversion or inspection work, follow the scoped guides:

- [Original drill adapter](../Tools/Drill/README.md)
- [Shoulder correction recipe](../Tools/Model/README.md)
- [Blender FBX export](../Tools/Export/README.md)
- [Blender review rendering](../Tools/Review/README.md)

Use fresh output directories for comparison builds. A receipt from the original
bundle applies only to its recorded bytes. The public repository does not run
Blender renders in standard portable CI.

## Native Unreal and device gates

After restoration, use the installed engine to build the project, run the
[editor bootstrap](../Tools/Unreal/README.md), then prepare the
[runtime probe](RUNTIME_PROBE.md). The import manifest currently targets Unreal
5.8. Record the actual installed engine build and compiler. Source preparation
does not prove that those APIs compile against an installed editor.

| Gate | Evidence required |
| --- | --- |
| Portable core | Compiled C++ tests and comparison with the Python reference |
| Asset preparation | Matching source/export hashes and structural FBX checks |
| Native module | Real Unreal Build Tool and reflected-code compilation |
| Native bootstrap/probe | Saved assets, reload/readback and evaluated native bones |
| Realtime and cooked playback | Actual ticks, controls, events, lifecycle and packaged viewer |
| Swift and iPhone delivery | Host integration, device launch, repeated entry/exit and measured resource use |
| Athlete and football quality | Matched body/deformation review, calibrated contacts, transitions and receiving response |

Unreal compilation, native execution, Swift hosting, iPhone performance and
production-athlete acceptance remain unverified in this handoff. None can be
inferred from portable CI or synthetic native test files.

## Historical records and asset availability

The original offline package retains `Reports`, `Evidence`, `Preview` and
`Docs/History`. Public documentation identifies those records as historical
instead of linking to nonexistent public files. They are not needed for portable
CI and should not be copied into the public tree as part of asset restoration.

The audition JSON preserves its original snapshot labels, including
`supplied_bytes_present` and `local_baseline_present`. Those labels describe the
original package, not a promise that assets are present in a fresh checkout.
The implementation plan is a dated proposal. Current code and verification
boundaries are described by the playback, runtime and setup documents.

The public source handoff does not grant a new licence to third-party asset
files. Asset-specific source and redistribution records remain separate from
the code and compiled application contract.
