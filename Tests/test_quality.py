"""Cloud-only regression tests for data and contact evidence failure modes.

Synthetic measurements below test the checker. They are not captured animation
evidence, are not exported as a fixture, and cannot establish real asset quality.
"""

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "Tools"))
sys.path.insert(0, str(PROJECT / "Tools/Drill"))
sys.path.insert(0, str(PROJECT / "Content/Python"))

from export_first_pass import build_fixture, load_inputs  # noqa: E402
from squadpad_plan import InputError, frame_time, plan_import  # noqa: E402
from squadpad_quality import QualityInputError, check_contact_geometry, validate_contacts  # noqa: E402


def current_plan():
    return plan_import(PROJECT / "import_manifest.json", require_files=False)


def synthetic_geometry():
    """Build two artificial contact instants solely to exercise failure cases."""
    plan = copy.deepcopy(current_plan())
    plan["meshes"]["athlete_a"]["sha256"] = "a" * 64
    samples = []
    for i, contact in enumerate(plan["contacts"]):
        contact["surface"] = "synthetic_boot_patch"
        contact["calibration"] = {
            "status": "measured", "id": f"synthetic_calibration_{i}",
            "bone_name": "RightFoot", "local_offset_cm": [0, 0, 0],
            "local_normal": [1, 0, 0], "mesh_source_sha256": "a" * 64,
            "method": "Synthetic unit-test values, not athlete evidence",
        }
        samples.append({
            "contact_id": contact["id"], "actor_id": contact["actor_id"],
            "ball_id": contact["ball_id"], "limb": contact["limb"],
            "surface": contact["surface"], "calibration_id": contact["calibration"]["id"],
            "mesh_source_sha256": "a" * 64, "time_seconds": contact["time_seconds"],
            "ball_center_cm": [11, 0, 11], "contact_point_cm": [0, 0, 11],
            "surface_normal_world": [1, 0, 0],
        })
    measurements = {
        "schema": "squadpad.contact.measurements.v1",
        "units": {"distance": "centimetres", "time": "seconds"},
        "axes": "unreal-z-up-x-forward", "fixture_sha256": plan["fixture_sha256"],
        "evaluation": {"kind": "unreal_evaluated_sequence", "engine_version": "synthetic-test",
                       "sequence_asset_path": "/Game/Synthetic/UnitTest", "run_id": "synthetic-test",
                       "captured_at_utc": "synthetic-test", "build_receipt_sha256": "b" * 64},
        "samples": samples,
    }
    return plan, measurements


class FixtureTests(unittest.TestCase):
    def test_fixture_is_deterministic_from_observed_source(self):
        rebuilt = build_fixture(*load_inputs(PROJECT / "Fixtures/rondo-4v1.level-1.observed.json", None))
        existing = json.loads((PROJECT / "Fixtures/first_pass.drill.json").read_text())
        self.assertEqual(existing, rebuilt)

    def test_original_pass_clock_and_ids_survive(self):
        plan = current_plan()
        self.assertEqual([actor["id"] for actor in plan["actors"]], ["A", "B"])
        events = {contact["event"]: contact for contact in plan["contacts"]}
        self.assertAlmostEqual(events["receive"]["time_seconds"] - events["kick"]["time_seconds"], 1.045)
        self.assertAlmostEqual(events["kick"]["time_seconds"], 1.39262125)
        self.assertEqual(validate_contacts(plan)["unmeasured_count"], 2)

    def test_fractional_contact_frame_is_not_rounded_to_display_frame(self):
        frame, subframe = frame_time(1.39262125, 60)
        self.assertEqual(frame, 83)
        self.assertGreater(subframe, 0)
        self.assertAlmostEqual((frame + subframe) / 60, 1.39262125, places=12)

    def test_missing_assets_fail_strict_import_preflight(self):
        # Exercise missing inputs in an isolated project even after real exports
        # have been added to the delivered project. Keep its real files intact.
        manifest = json.loads((PROJECT / "import_manifest.json").read_text())
        with tempfile.TemporaryDirectory(prefix="squadpad-missing-inputs-") as temporary:
            root = Path(temporary)
            manifest_path = root / "import_manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            fixture_path = root / manifest["fixture_path"]
            fixture_path.parent.mkdir(parents=True, exist_ok=True)
            fixture_path.write_bytes((PROJECT / manifest["fixture_path"]).read_bytes())
            with self.assertRaises(InputError) as caught:
                plan_import(manifest_path, require_files=True)
            self.assertIn("Required source file is absent", str(caught.exception))
            self.assertIn(str(root / manifest["mesh"]["fbx"]), str(caught.exception))

    def test_source_path_must_stay_inside_project(self):
        manifest = json.loads((PROJECT / "import_manifest.json").read_text())
        manifest["mesh"]["fbx"] = "../../unexpected.fbx"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manifest.json"
            path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(InputError, "escapes"):
                plan_import(path, project_root=PROJECT, require_files=False)

    def test_contact_clock_drift_is_rejected(self):
        plan = current_plan()
        plan["contacts"][0]["time_seconds"] += 0.05
        with self.assertRaisesRegex(QualityInputError, "clock|align"):
            validate_contacts(plan)

    def test_duplicate_event_is_rejected(self):
        plan = current_plan()
        plan["contacts"].append(copy.deepcopy(plan["contacts"][0]))
        with self.assertRaisesRegex(QualityInputError, "Duplicate"):
            validate_contacts(plan)

    def test_wrong_intended_foot_is_rejected(self):
        plan = current_plan()
        plan["contacts"][0]["limb"] = "left_foot"
        with self.assertRaisesRegex(QualityInputError, "limb"):
            validate_contacts(plan)

    def test_unmeasured_contact_cannot_claim_an_offset(self):
        plan = current_plan()
        plan["contacts"][0]["calibration"]["local_offset_cm"] = [0, 0, 0]
        with self.assertRaisesRegex(QualityInputError, "unmeasured"):
            validate_contacts(plan)


class GeometryTests(unittest.TestCase):
    def test_real_package_contacts_remain_blocked(self):
        report = check_contact_geometry(current_plan())
        self.assertEqual(report["status"], "blocked")
        self.assertEqual([row["status"] for row in report["contacts"]], ["blocked", "blocked"])
        self.assertFalse(report["visual_quality_accepted"])

    def test_exact_named_surface_contact_passes_geometry_only(self):
        plan, samples = synthetic_geometry()
        report = check_contact_geometry(plan, samples)
        self.assertEqual(report["status"], "pass")
        self.assertFalse(report["visual_quality_accepted"])

    def test_close_in_plan_view_but_above_boot_fails(self):
        plan, samples = synthetic_geometry()
        samples["samples"][0]["ball_center_cm"][2] += 50
        self.assertEqual(check_contact_geometry(plan, samples)["status"], "fail")

    def test_nearest_wrong_ball_is_not_accepted(self):
        plan, samples = synthetic_geometry()
        samples["samples"][0]["ball_id"] = "other_ball"
        self.assertEqual(check_contact_geometry(plan, samples)["status"], "fail")

    def test_wrong_actor_is_not_accepted(self):
        plan, samples = synthetic_geometry()
        samples["samples"][0]["actor_id"] = "B"
        self.assertEqual(check_contact_geometry(plan, samples)["status"], "fail")

    def test_wrong_foot_is_not_accepted(self):
        plan, samples = synthetic_geometry()
        samples["samples"][0]["limb"] = "left_foot"
        self.assertEqual(check_contact_geometry(plan, samples)["status"], "fail")

    def test_missing_one_contact_blocks_complete_acceptance(self):
        plan, samples = synthetic_geometry()
        samples["samples"].pop()
        self.assertEqual(check_contact_geometry(plan, samples)["status"], "blocked")

    def test_missing_mesh_bytes_block_measurement(self):
        plan, samples = synthetic_geometry()
        plan["meshes"]["athlete_a"]["sha256"] = None
        self.assertEqual(check_contact_geometry(plan, samples)["status"], "blocked")

    def test_stale_mesh_calibration_fails(self):
        plan, samples = synthetic_geometry()
        plan["contacts"][0]["calibration"]["mesh_source_sha256"] = "c" * 64
        self.assertEqual(check_contact_geometry(plan, samples)["status"], "fail")

    def test_stale_fixture_measurements_are_rejected(self):
        plan, samples = synthetic_geometry()
        samples["fixture_sha256"] = "c" * 64
        with self.assertRaisesRegex(QualityInputError, "fixture hash"):
            check_contact_geometry(plan, samples)

    def test_wrong_event_time_fails(self):
        plan, samples = synthetic_geometry()
        samples["samples"][0]["time_seconds"] += 0.1
        self.assertEqual(check_contact_geometry(plan, samples)["status"], "fail")

    def test_ball_behind_surface_fails(self):
        plan, samples = synthetic_geometry()
        samples["samples"][0]["surface_normal_world"] = [-1, 0, 0]
        self.assertEqual(check_contact_geometry(plan, samples)["status"], "fail")

    def test_duplicate_samples_do_not_enable_nearest_match(self):
        plan, samples = synthetic_geometry()
        samples["samples"].append(copy.deepcopy(samples["samples"][0]))
        with self.assertRaisesRegex(QualityInputError, "one identified"):
            check_contact_geometry(plan, samples)

    def test_predicted_fixture_positions_are_not_runtime_evidence(self):
        plan, samples = synthetic_geometry()
        samples["evaluation"]["kind"] = "predicted_fixture_positions"
        with self.assertRaisesRegex(QualityInputError, "evaluated Unreal"):
            check_contact_geometry(plan, samples)

    def test_nonfinite_measurements_are_rejected(self):
        plan, samples = synthetic_geometry()
        samples["samples"][0]["ball_center_cm"][0] = float("nan")
        with self.assertRaisesRegex(QualityInputError, "finite"):
            check_contact_geometry(plan, samples)


class CLITests(unittest.TestCase):
    def test_no_evidence_is_nonzero_and_explicitly_blocked(self):
        result = subprocess.run([sys.executable, str(PROJECT / "Tools/pipeline.py"), "contacts"],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
