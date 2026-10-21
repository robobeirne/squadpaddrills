# Athlete shoulder correction

This repository includes the recipe. Its input GLB, derived output and historical
review receipts remain in the separate bundle described in
[Asset setup](../../Docs/ASSET_SETUP.md). Restore the required inputs before
running asset work. The status below describes the original offline review.

`repair_shoulders.py` creates a derived athlete from the supplied runtime GLB.
It smooths the shirt's existing skin weights and reduces the raised sleeve-cap
shape while retaining the supplied body and skeleton. This is an authored
correction with recorded provenance. It does not recover missing source anatomy
or original Blender construction history.

The revision 4 shoulder correction passed seven candidate closeups,
additional final-export closeups and independent geometric checks. This is a
bounded review of the corrected
shoulders, not acceptance of every animation frame or native Unreal playback.
The recipe's generated receipt records construction and provenance;
the original offline package's `Reports/shoulder-correction.md` records the
visual decision and remaining issues. Its full-scene video and final render and
encoding receipts are retained under `Preview/` in that package, not this checkout.

## Input and derived file names

Run the commands below from the `SquadPad3D` project root.

| Role | Project-relative path |
| --- | --- |
| Unchanged supplied athlete | `SourceInputs/athlete_a.glb` |
| Correction recipe | `Tools/Model/repair_shoulders.py` |
| Derived athlete for export | `DerivedInputs/athlete_a_shoulders.glb` |
| Derived-asset receipt | `DerivedInputs/athlete_a_shoulders.receipt.json` |
| Motion source rig | `SourceInputs/xbot/footballer.glb` |
| Canonical motion pair | `SourceInputs/mocap-drills.json` and `SourceInputs/mocap-drills.bin` |

The recipe accepts only the supplied athlete A variant with SHA-256:

```text
9a8d3106b79d04638d98ef35f74ab5c23bcbadb6901c40e2a97b648ce116542c
```

This pins the measured mesh, quantization basis, skin and proportions. The masks
are specific to that asset. A full-size original or another athlete requires its
own inspection and correction recipe. The input must remain in `SourceInputs`;
the corrected GLB must remain distinguishable as a derived asset.

## What the recipe changes

The weight pass pools coincident shirt vertices across material seams, then
smooths their original influences using a spatial Gaussian average. This is a
logical weld for sampling. It does not merge mesh vertices or discard the UV and
material splits. The resulting field is blended into the upper shirt and its
crest/number meshes. The strongest four influences are retained and normalized.

The cap pass applies a smooth displacement in the decoded rest space. It lowers
the upper sleeve cap and reduces its depth around the original left or right
upper-arm joint. Positions are converted back through the existing skin decode
basis, and normals follow the displacement's inverse-transpose Jacobian.

The current recipe uses these parameters. Distances are metres in the decoded
model space, with Y up and `abs(X)` measuring lateral distance from the centre.

| Operation | Parameter |
| --- | --- |
| Weight smoothing | Gaussian sigma `0.022`; at most `128` neighbours |
| Weight blend | At most `0.9` of the smoothed field |
| Weight height fade | Rises smoothly from `Y = 1.375` to `1.465` |
| Weight outer fade | Falls smoothly from `abs(X) = 0.255` to `0.31` |
| Cap inner fade | Rises smoothly from `abs(X) = 0.125` to `0.205` |
| Cap outer fade | Falls smoothly from `abs(X) = 0.255` to `0.31` |
| Cap height gate | Rises from `−0.005` to `0.06` above that side's Arm rest-joint Y |
| Cap vertical reduction | At most `0.018` |
| Cap depth fit | At most `12%` reduction about that side's Arm rest-joint Z |

Both outer fades reach zero at `abs(X) = 0.31`. This narrower boundary protects
the surviving upper-arm surface, as well as the outer sleeves and cuffs. The
central collar region is outside the cap's inner mask. The 18 mm limit describes
the vertical component; the receipt records the actual total displacement,
including the depth adjustment.

The current derived receipt records approximately 18.6583 mm maximum total
displacement and a minimum sampled warp-Jacobian determinant of approximately
0.547765. The final recipe hash is:

```text
cd9c2efd232ff0c57e6475b6ade0e88b9c79ccb34b8d99114ed377d95b0dd55f
```

Independent candidate 5 checks retained the body and face exactly through five
sampled poses, preserved the cuff Trim, and found no new intersections with the
surviving upper arm surface in rest or those poses. Existing intersections remain
in the pass-opening and receive-contact samples. The test is regional and does
not establish globally collision-free clothing.

## What remains unchanged

The recipe retains every supplied body position, normal, joint index and skin
weight. It also retains the 52-bone hierarchy, local rest transforms and inverse
bind matrices, the UV and triangle-index data, material definitions, textures
and embedded image bytes. The original uploaded GLB is never edited.

The motion source rig and canonical motion files are separate inputs to the
exporter. This model correction changes no capture sample, contact time, actor
path or drill clock. The subsequent export uses the existing skeleton and
retarget logic.

The runtime body omits skin underneath much of the shirt, including the covered
shoulder region. There is no complete surface available for nearest-body weight
transfer or shrinkwrapping. This correction creates no replacement torso or
shoulder anatomy. Localized cloth folds and the original graphic edges remain
visible. Additional poses and later model changes still require their own
fitting and deformation review.

## Python requirements and model build

The model recipe uses Python 3, NumPy and SciPy. It does not require Blender for
this step. The cloud review environment used Python `3.12.14`, NumPy `2.3.5` and
SciPy `1.17.0`. An isolated environment with those package versions is:

```sh
python3 -m venv /tmp/squadpad-model-env
/tmp/squadpad-model-env/bin/python -m pip install numpy==2.3.5 scipy==1.17.0
```

For a comparison rebuild, create a fresh derived pair with:

```sh
/tmp/squadpad-model-env/bin/python Tools/Model/repair_shoulders.py \
  --source SourceInputs/athlete_a.glb \
  --output Saved/ModelReview/athlete_a_shoulders.glb \
  --receipt Saved/ModelReview/athlete_a_shoulders.receipt.json
```

The recipe creates the parent directory. It requires fresh, distinct output and
receipt paths and refuses to overwrite either file or the input. For a repeat
build, retain the previous derived pair separately or use a fresh pair of review
paths. Keep the GLB and its own receipt together.

The receipt records the original input hash, recipe hash, derived output hash,
parameters, modified primitives and measured displacement. A different recipe
or generated GLB needs a new receipt. The original GLB binary buffer remains an
unchanged prefix of the derived buffer, with new attribute data appended and
referenced by the corrected primitives.

## Export the derived athlete and existing motion

Use a dedicated headless Blender process. From the project root, this command
exports the comparison model with idle, pass and receive on its retained rig
into a fresh output directory:

```sh
blender --background --factory-startup --python Tools/Export/export_athlete_fbx.py -- \
  --athlete-glb Saved/ModelReview/athlete_a_shoulders.glb \
  --source-rig-glb SourceInputs/xbot/footballer.glb \
  --mocap-index SourceInputs/mocap-drills.json \
  --mocap-bin SourceInputs/mocap-drills.bin \
  --output-dir Saved/Exports/athlete_a_rebuild \
  --manifest-prefix Saved/Exports/athlete_a_rebuild \
  --fps 60 \
  --hold-pass-opening-sample
```

This leaves the restored baseline and its hashes intact. A later deliberate
replacement must update the import manifest and regenerate/review the compiled
playback and bindings. `--overwrite-generated` is available only for an
exporter-owned directory with its marker. It is not needed for the fresh
comparison directory above. Source and derived inputs stay outside that output.

The explicit `--hold-pass-opening-sample` option is a separate, documented motion
correction. It holds the second stored pass pose back over the first
`0.03319999948143959` seconds, using the same sample for skeletal pose and actor
root travel. It preserves later timestamps and contacts and does not rewrite the
canonical motion files. It is a derived opening hold, not recovered capture.

The exporter writes its own `export_manifest.json` in the selected output
directory after its
FBX roundtrip checks. Keep this export receipt as well as the model-correction
receipt: they identify different stages of the build. See
`Tools/Export/README.md` for the full FBX, root-track and import contract.

For a rebuild, review the rest silhouette, idle, pass opening, pass contact and
receive from the same matched camera views. Structural integrity, sampled
deformation, visible appearance and native Unreal playback remain separate
checks. The current bounded shoulder acceptance does not imply that a later
rebuild or an unreviewed motion automatically passes those checks.
