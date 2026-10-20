"""Source-ingestion checks requiring the separately supplied original assets.

Run explicitly after restoring the approved source/export bundle. Missing
inputs are errors, not skipped successes. Public portable CI does not execute
this module and does not claim source or native-asset validation.
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import random
import sys
import unittest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "Tools/Playback"))
from evaluate_playback import (PlaybackError, PlaybackPlan, PlaybackController,
                               read_json, validate_bindings, validate_playback)
from compile_playback import compile_documents, validate_bindings_with_source_files
from squadpad_plan import plan_import, _sample_keys, sha256_file


class SourcePlaybackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document, cls.bindings = compile_documents()
        cls.plan = PlaybackPlan(cls.document)

    def mutated(self):
        return copy.deepcopy(self.document)

    def test_checked_in_documents_reproduce_exactly(self):
        self.assertEqual(self.document, read_json(PROJECT / "Fixtures/Playback/first_pass.playback.json"))
        self.assertEqual(self.bindings, read_json(PROJECT / "Fixtures/Playback/athlete_a_baseline.bindings.json"))


    def test_compilation_preserves_original_inputs_and_tracks(self):
        files = sorted((PROJECT / "SourceInputs").rglob("*"))
        files += [PROJECT / "Fixtures/first_pass.drill.json", PROJECT / "import_manifest.json"]
        files += sorted((PROJECT / "SourceData").rglob("*.root.json"))
        before = {str(p): sha256_file(p) for p in files if p.is_file()}
        compile_documents()
        self.assertEqual(before, {name: sha256_file(name) for name in before})


    def test_compiled_tracks_preserve_existing_root_composition(self):
        original = plan_import(PROJECT / "import_manifest.json", require_files=True)
        rng = random.Random(5721)
        for time in [0.0, self.plan.duration_seconds, *[rng.random() * self.plan.duration_seconds for _ in range(50)]]:
            frame = self.plan.evaluate(time)
            for old_actor, actor in zip(original["actors"], frame["actors"]):
                expected = _sample_keys(old_actor["transform_keys"], time)["position_cm"]
                for a, b in zip(actor["position_cm"], expected):
                    self.assertAlmostEqual(a, b, places=10)


    def test_current_bindings_match_real_export_sources(self):
        result = validate_bindings_with_source_files(self.document, self.bindings)
        self.assertFalse(result["native_assets_verified"])
        self.assertEqual(result["source_files"]["status"], "source_binding_metadata_matches_validated_files")


    def test_self_consistent_but_stale_animation_hash_fails_source_ingestion(self):
        bindings = copy.deepcopy(self.bindings)
        bindings["profiles"][0]["animations"][0]["animation_source_sha256"] = "a" * 64
        validate_bindings(self.document, bindings)
        with self.assertRaisesRegex(PlaybackError, "differs from validated source"):
            validate_bindings_with_source_files(self.document, bindings)



if __name__ == "__main__":
    unittest.main(verbosity=2)
