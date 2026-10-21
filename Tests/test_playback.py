"""Portable playback tests using the published first-pass fixture.

These tests require no model, mocap, FBX or Unreal installation. The fixture
retains the historical source provenance, but this suite does not revalidate
those source files. Source ingestion has its own explicit test module.
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


class PlaybackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document = read_json(PROJECT / "Fixtures/Playback/first_pass.playback.json")
        cls.bindings = read_json(PROJECT / "Fixtures/Playback/athlete_a_baseline.bindings.json")
        cls.plan = PlaybackPlan(cls.document)
        validate_bindings(cls.document, cls.bindings)

    def mutated(self):
        return copy.deepcopy(self.document)

    def test_original_contacts_and_transfer_duration_survive(self):
        kick, receive = self.document["contacts"]
        self.assertEqual(kick["time_seconds"], 1.39262125)
        self.assertEqual(receive["time_seconds"], 2.43762125)
        self.assertAlmostEqual(receive["time_seconds"] - kick["time_seconds"], 1.045, places=14)
        self.assertEqual([a["id"] for a in self.document["actors"]], ["A", "B"])
        self.assertEqual([b["id"] for b in self.document["balls"]], ["ball"])
        self.assertFalse(self.document["provenance"]["complete_drill"])


    def test_seek_order_and_output_mutation_do_not_change_state(self):
        rng = random.Random(471)
        times = [rng.random() * self.plan.duration_seconds for _ in range(100)]
        expected = {t: self.plan.evaluate(t) for t in times}
        for t in reversed(times):
            self.assertEqual(self.plan.evaluate(t), expected[t])
        altered = self.plan.document
        altered["actors"][0]["transform_keys"][0]["position_cm"][0] += 1000
        frame = self.plan.evaluate(times[0])
        frame["actors"][0]["position_cm"][0] += 1000
        self.assertEqual(self.plan.evaluate(times[0]), expected[times[0]])


    def test_possession_changes_at_exact_contact(self):
        kick, receive = [c["time_seconds"] for c in self.document["contacts"]]
        def state(time):
            return self.plan.evaluate(time)["balls"][0]["possession"]
        self.assertEqual(state(kick - 1e-9)["owner_id"], "A")
        self.assertEqual(state(kick)["state"], "in_transit")
        self.assertIsNone(state(kick)["owner_id"])
        self.assertEqual(state(receive - 1e-9)["state"], "in_transit")
        self.assertEqual(state(receive)["owner_id"], "B")


    def test_section_boundaries_choose_new_action_and_end_holds(self):
        for actor in self.document["actors"]:
            for section in actor["sections"]:
                at_start = next(a for a in self.plan.evaluate(section["start_seconds"])["actors"] if a["id"] == actor["id"])
                self.assertEqual(at_start["action_id"], section["id"])
                self.assertAlmostEqual(at_start["source_time_seconds"], section["source_start_seconds"], places=12)
        self.assertEqual(self.plan.evaluate(-100)["time_seconds"], 0.0)
        self.assertEqual(self.plan.evaluate(100)["time_seconds"], self.plan.duration_seconds)


    def test_pause_seek_restart_and_crossed_events(self):
        controller = PlaybackController(self.plan)
        self.assertEqual(controller.advance(1)["frame"]["time_seconds"], 0)
        controller.play()
        first = controller.advance(2)
        self.assertEqual(first["crossed_contact_ids"], [self.document["contacts"][0]["id"]])
        controller.pause()
        self.assertEqual(controller.advance(10)["frame"], first["frame"])
        self.assertEqual(controller.seek(3)["crossed_contact_ids"], [])
        self.assertEqual(controller.restart()["frame"]["time_seconds"], 0)
        self.assertFalse(controller.is_playing)
        controller.play()
        controller.set_rate(2)
        update = controller.advance(1.5)
        self.assertEqual(update["frame"], self.plan.evaluate(3))
        self.assertEqual(update["crossed_contact_ids"], [c["id"] for c in self.document["contacts"]])
        self.assertEqual(controller.advance(0)["crossed_contact_ids"], [])
        controller.advance(100)
        self.assertFalse(controller.is_playing)
        controller.play()
        self.assertFalse(controller.is_playing)


    def test_exact_advance_boundary_emits_contact_once(self):
        controller = PlaybackController(self.plan)
        controller.play()
        kick = self.document["contacts"][0]
        self.assertEqual(controller.advance(kick["time_seconds"])["crossed_contact_ids"], [kick["id"]])
        self.assertEqual(controller.advance(0)["crossed_contact_ids"], [])
        self.assertEqual(controller.advance(0.00001)["crossed_contact_ids"], [])


    def test_invalid_control_numbers_are_rejected(self):
        controller = PlaybackController(self.plan)
        for value in (float("nan"), float("inf"), True):
            with self.subTest(value=value), self.assertRaises(PlaybackError):
                self.plan.evaluate(value)
        for value in (0, -1, float("inf"), True):
            with self.subTest(value=value), self.assertRaises(PlaybackError):
                controller.set_rate(value)
        with self.assertRaises(PlaybackError):
            controller.advance(-0.1)


    def test_extreme_finite_control_rate_stays_bounded(self):
        controller = PlaybackController(self.plan)
        controller.play()
        controller.set_rate(1e308)
        controller.advance(0)
        self.assertEqual(controller.time_seconds, 0)
        self.assertTrue(controller.is_playing)
        result = controller.advance(1e308)
        self.assertEqual(result["frame"]["time_seconds"], self.plan.duration_seconds)


    def test_yaw_shortest_path_and_positive_half_turn(self):
        document = self.mutated()
        for left, right, expected in ((359, 1, 0), (0, 180, 90), (180, 0, 270)):
            document["actors"][0]["transform_keys"] = [
                {"time_seconds": 0, "position_cm": [0, 0, 0], "yaw_degrees": left},
                {"time_seconds": document["duration_seconds"], "position_cm": [0, 0, 0], "yaw_degrees": right}]
            value = PlaybackPlan(document).evaluate(document["duration_seconds"] / 2)["actors"][0]["yaw_degrees"]
            self.assertEqual(value, expected)


    def test_large_finite_transform_interpolation_stays_finite(self):
        document = self.mutated()
        document["actors"][0]["transform_keys"] = [
            {"time_seconds": 0, "position_cm": [-1e308, 0, 0], "yaw_degrees": -1e308},
            {"time_seconds": document["duration_seconds"], "position_cm": [1e308, 0, 0], "yaw_degrees": 1e308}]
        value = PlaybackPlan(document).evaluate(document["duration_seconds"] / 2)["actors"][0]
        self.assertEqual(value["position_cm"][0], 0)
        self.assertTrue(math.isfinite(value["yaw_degrees"]))


    def test_invalid_track_section_and_reference_mutations_fail(self):
        mutations = [
            lambda d: d["actors"][0]["transform_keys"][0]["position_cm"].__setitem__(0, float("nan")),
            lambda d: d["actors"][0]["transform_keys"].reverse(),
            lambda d: d["actors"][0].__setitem__("transform_keys", d["actors"][0]["transform_keys"][:1]),
            lambda d: d["actors"][0]["sections"][1].__setitem__("start_seconds", 0),
            lambda d: d["actors"][0]["sections"][0].__setitem__("source_motion_id", "missing"),
            lambda d: d["actors"].append(copy.deepcopy(d["actors"][0])),
            lambda d: d["contacts"][0].__setitem__("actor_id", "missing"),
            lambda d: d["contacts"][0].__setitem__("source_contact_seconds", 1.0),
            lambda d: d["contacts"][0].__setitem__("calibration_status", "measured"),
            lambda d: d.__setitem__("root_ownership", "both_skeleton_and_actor"),
            lambda d: d["actors"][1]["sections"][0].__setitem__("play_rate", 1e308),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                document = self.mutated()
                mutate(document)
                with self.assertRaises(PlaybackError):
                    validate_playback(document)


    def test_invalid_possession_cannot_disagree_with_contacts(self):
        mutations = [
            lambda d: d["possession"].pop(0),
            lambda d: d["possession"][0].__setitem__("owner_id", "missing"),
            lambda d: d["possession"][1].__setitem__("time_seconds", d["possession"][1]["time_seconds"] + 0.1),
            lambda d: d["possession"][2].__setitem__("owner_id", "A"),
            lambda d: d["possession"].reverse(),
            lambda d: d["contacts"].pop(),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                document = self.mutated()
                mutate(document)
                with self.assertRaises(PlaybackError):
                    validate_playback(document)


    def test_contact_on_outgoing_section_boundary_is_rejected(self):
        document = self.mutated()
        time = document["contacts"][0]["time_seconds"]
        sections = document["actors"][0]["sections"]
        sections[0]["end_seconds"] = time
        sections[1]["start_seconds"] = time
        with self.assertRaisesRegex(PlaybackError, "not active"):
            validate_playback(document)


    def test_incompatible_or_stale_bindings_fail(self):
        mutations = [
            lambda b: b["profiles"][0]["animations"][0].__setitem__("skeleton_id", "other_rig"),
            lambda b: b["profiles"][0]["animations"][0].__setitem__("duration_seconds", 100),
            lambda b: b["profiles"][0]["calibration"].__setitem__("mesh_source_sha256", "a" * 64),
            lambda b: b.__setitem__("playback_sha256", "b" * 64),
            lambda b: b["slots"].pop(),
            lambda b: b["profiles"][0]["animations"].pop(),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                bindings = copy.deepcopy(self.bindings)
                mutate(bindings)
                with self.assertRaises(PlaybackError):
                    validate_bindings(self.document, bindings)


    def test_portable_output_keeps_evidence_boundaries_explicit(self):
        serialized = json.dumps(self.document)
        for marker in ("/workspace/", "/Users/", "/Volumes/", "source_path", "project_root"):
            self.assertNotIn(marker, serialized)
        evidence = self.document["evidence"]
        self.assertTrue(evidence["exported_input_files_validated"])
        self.assertFalse(evidence["native_unreal_execution"])
        self.assertFalse(evidence["contact_geometry_accepted"])
        self.assertFalse(evidence["visual_quality_accepted"])
        self.assertEqual(evidence["team_possession"], "not_provided_by_source")



if __name__ == "__main__":
    unittest.main(verbosity=2)
