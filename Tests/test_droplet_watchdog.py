"""Synthetic clock/provider tests only. No account, token or network is used."""
from __future__ import annotations

import copy
import io
import json
from pathlib import Path
import signal
import sys
import tempfile
import textwrap
import unittest
from unittest import mock
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Tools/Cloud"))
import droplet_watchdog as guard


BASE = 1_792_584_000.0
CONFIG = guard.Config("squadpad-native-synthetic-0001", "synthetic-catalogue-slug", 12345)


class FakeClock:
    def __init__(self):
        self.elapsed, self.offset, self.hook = 0.0, 0.0, None
        self.sleeps = []

    def time(self):
        return BASE + self.elapsed + self.offset

    def monotonic(self):
        return 1000 + self.elapsed

    def sleep(self, seconds):
        assert 0 <= seconds <= 300
        self.sleeps.append(seconds)
        self.elapsed += seconds
        if self.hook:
            self.hook(self)


class SyntheticProvider:
    def __init__(self, clock):
        self.clock, self.calls, self.handler = clock, [], None
        self.create_at, self.early_delete_at = 30, None
        self.preexisting, self.deleted = False, False
        self.droplet = {"id": 987654321, "created_at": guard.utc(BASE + 30),
                        "name": CONFIG.name, "size_slug": CONFIG.size, "vcpus": 4,
                        "memory": 8192, "disk": 160, "region": {"slug": "lon1"},
                        "image": {"id": CONFIG.image_id}, "size": {"slug": CONFIG.size,
                        "price_hourly": 0.07143000000000001}, "features": ["private_networking"],
                        "backup_ids": [], "snapshot_ids": [], "volume_ids": []}

    def request(self, method, path):
        self.calls.append((method, path, self.clock.elapsed))
        if self.handler:
            reply = self.handler(method, path)
            if reply is not None:
                return reply
        if "?" in path:
            assert method == "GET"
            present = self.preexisting or (self.create_at is not None and self.clock.elapsed >= self.create_at)
            droplets = [copy.deepcopy(self.droplet)] if present else []
            return guard.Reply(200, {"droplets": droplets, "meta": {"total": len(droplets)}, "links": {}})
        assert path == "/v2/droplets/987654321"
        if method == "DELETE":
            self.deleted = True
            return guard.Reply(204)
        absent = self.deleted or (self.early_delete_at is not None and self.clock.elapsed >= self.early_delete_at)
        return guard.Reply(404) if absent else guard.Reply(200, {"droplet": copy.deepcopy(self.droplet)})


class DropletWatchdogTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.provider = SyntheticProvider(self.clock)
        self.events = []

    def run_guard(self):
        return guard.Watchdog(CONFIG, self.provider, clock=self.clock,
                              emit=lambda receipt: self.events.append(copy.deepcopy(receipt))).run()

    def assert_failed_without_absence(self, receipt, reason=None):
        self.assertEqual(receipt["status"], "failed")
        self.assertFalse(receipt["deletion_verified"])
        self.assertFalse(receipt["billing_cap_guaranteed"])
        if reason:
            self.assertEqual(receipt["failure_reason"], reason)

    def test_basic_profile_arms_binds_and_deletes_exact_id_at_provider_age(self):
        receipt = self.run_guard()
        self.assertEqual([e["status"] for e in self.events], ["armed", "bound", "deleting", "deleted_verified"])
        self.assertIsNone(self.events[0]["bound"])
        self.assertEqual(receipt["bound"]["id"], self.provider.droplet["id"])
        self.assertEqual(receipt["expected"]["memory"], 8192)
        self.assertEqual(receipt["expected"]["disk"], 160)
        self.assertEqual(receipt["expected"]["hourly_usd_ceiling"], "0.072")
        self.assertEqual(receipt["bound"]["observed_hourly_usd"], "0.07143000000000001")
        self.assertTrue(receipt["deletion_verified"])
        self.assertTrue(receipt["deletion_before_deadline"])
        self.assertFalse(receipt["billing_cap_guaranteed"])
        delete = next(call for call in self.provider.calls if call[0] == "DELETE")
        self.assertEqual(delete[1], "/v2/droplets/987654321")
        self.assertEqual(delete[2], 30 + guard.DELETE_AGE)
        self.assertEqual(self.provider.calls[-1][0:2], ("GET", delete[1]))
        self.assertLess(max(self.clock.sleeps), 901)

    def test_existing_name_including_case_variation_never_arms_or_deletes(self):
        self.provider.preexisting = True
        self.provider.droplet["name"] = CONFIG.name.upper()
        receipt = self.run_guard()
        self.assert_failed_without_absence(receipt, "planned_name_already_exists")
        self.assertFalse(receipt["armed"])
        self.assertEqual(len(self.provider.calls), 1)

    def test_ambiguous_incomplete_or_wrong_name_list_never_arms(self):
        documents = [
            {"droplets": [], "meta": {"total": 2}},
            {"droplets": [], "meta": {"total": 1}},
            {"droplets": [], "meta": {"total": 0}, "links": {"pages": {"next": "https://evil.invalid/"}}},
            {"droplets": [], "meta": {"total": False}},
            {"droplets": [{"name": "unrelated"}], "meta": {"total": 1}},
        ]
        for document in documents:
            with self.subTest(document=document):
                self.provider.handler = lambda *_: guard.Reply(200, document)
                receipt = self.run_guard()
                self.assert_failed_without_absence(receipt)
                self.assertFalse(receipt["armed"])
                self.assertEqual(receipt["delete_attempts"], 0)

    def test_wrong_configuration_or_extra_resources_never_bind(self):
        mutations = [
            lambda d: d.update(vcpus=8), lambda d: d.update(memory=65536), lambda d: d.update(disk=True),
            lambda d: d.update(size_slug="wrong-size"), lambda d: d["size"].update(slug="wrong-size"),
            lambda d: d["region"].update(slug="nyc3"), lambda d: d["image"].update(id=999),
            lambda d: d["size"].update(price_hourly="NaN"),
            lambda d: d.update(features=["backups"]), lambda d: d.update(backup_ids=[1]),
            lambda d: d.update(snapshot_ids=[2]), lambda d: d.update(volume_ids=["extra"]),
            lambda d: d.update(id=True), lambda d: d.update(name=CONFIG.name.upper()),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.setUp()
                mutate(self.provider.droplet)
                receipt = self.run_guard()
                self.assert_failed_without_absence(receipt)
                self.assertIsNone(receipt["bound"])
                self.assertEqual(receipt["delete_attempts"], 0)

    def test_previous_storage_profile_is_not_adopted(self):
        self.provider.droplet.update(memory=32768, disk=600)
        self.provider.droplet["size"].update(price_hourly="0.38988")
        receipt = self.run_guard()
        self.assert_failed_without_absence(receipt, "unexpected_droplet_configuration")
        self.assertTrue(receipt["armed"])
        self.assertIsNone(receipt["bound"])
        self.assertEqual(receipt["delete_attempts"], 0)
        self.assertFalse(any(method == "DELETE" for method, _, _ in self.provider.calls))

    def test_basic_price_ceiling_accepts_boundary_and_rejects_overage(self):
        for price in ("0.072", "0.072000001", "0.38988"):
            with self.subTest(price=price):
                self.setUp()
                self.provider.droplet["size"]["price_hourly"] = price
                self.provider.early_delete_at = 40
                receipt = self.run_guard()
                if price == "0.072":
                    self.assertEqual(receipt["status"], "deleted_verified")
                    self.assertTrue(receipt["deletion_before_deadline"])
                    self.assertEqual(receipt["bound"]["observed_hourly_usd"], price)
                else:
                    self.assert_failed_without_absence(receipt, "hourly_price_above_ceiling_or_invalid")
                    self.assertIsNone(receipt["bound"])
                    self.assertEqual(receipt["delete_attempts"], 0)
                    self.assertFalse(any(method == "DELETE" for method, _, _ in self.provider.calls))

    def test_old_future_or_invalid_provider_time_is_not_adopted(self):
        for timestamp in (guard.utc(BASE - 1), guard.utc(BASE + 36), "invalid", "2026-99-99T00:00:00Z"):
            with self.subTest(timestamp=timestamp):
                self.setUp()
                self.provider.droplet["created_at"] = timestamp
                receipt = self.run_guard()
                self.assert_failed_without_absence(receipt)
                self.assertIsNone(receipt["bound"])

    def test_unavailable_privileged_fields_are_explicit_and_do_not_claim_no_extras(self):
        for field in ("backup_ids", "snapshot_ids", "volume_ids"):
            del self.provider.droplet[field]
        self.provider.early_delete_at = 40
        receipt = self.run_guard()
        self.assertEqual(set(receipt["bound"]["extra_resource_visibility"].values()), {"unavailable"})
        self.assertEqual(receipt["status"], "deleted_verified")
        self.assertEqual(receipt["delete_attempts"], 0)

    def test_authentication_and_permission_failures_are_not_absence(self):
        for status in (401, 403):
            for stage in ("arm", "bound", "delete"):
                with self.subTest(status=status, stage=stage):
                    self.setUp()
                    def respond(method, path):
                        if stage == "arm" or (stage == "bound" and "?" not in path) or (stage == "delete" and method == "DELETE"):
                            return guard.Reply(status, {"message": "SENSITIVE ERROR BODY"})
                    self.provider.handler = respond
                    receipt = self.run_guard()
                    self.assert_failed_without_absence(receipt, "provider_authentication_or_scope_failure")
                    self.assertNotIn("SENSITIVE", json.dumps(receipt))

    def test_creation_timeout_and_initial_network_error_never_claim_protection(self):
        self.provider.create_at = None
        receipt = self.run_guard()
        self.assert_failed_without_absence(receipt, "creation_window_expired")
        self.assertEqual(self.clock.elapsed, guard.CREATE_WINDOW)
        self.assertEqual(receipt["delete_attempts"], 0)
        self.setUp()
        def unavailable(*_):
            raise guard.TransientError()
        self.provider.handler = unavailable
        receipt = self.run_guard()
        self.assert_failed_without_absence(receipt, "initial_request_failed")
        self.assertFalse(receipt["armed"])

    def test_transient_provider_and_network_errors_retry_within_deadline(self):
        seen = set()
        def respond(method, path):
            if self.clock.elapsed == 15:
                return guard.Reply(500)
            if method == "DELETE" and "delete" not in seen:
                seen.add("delete")
                return guard.Reply(429)
            if self.provider.deleted and "get" not in seen:
                seen.add("get")
                raise guard.TransientError()
        self.provider.handler = respond
        receipt = self.run_guard()
        self.assertEqual(receipt["status"], "deleted_verified")
        self.assertEqual(receipt["transient_errors"], 3)
        self.assertLess(receipt["verified_absent_age_seconds"], guard.VERIFY_AGE)

    def test_delete_204_alone_is_not_absence_and_retries_are_bounded(self):
        self.provider.handler = lambda method, _: guard.Reply(204) if method == "DELETE" else None
        receipt = self.run_guard()
        self.assert_failed_without_absence(receipt, "deletion_verification_deadline_expired")
        self.assertEqual(receipt["delete_attempts"], 30)
        self.assertEqual(self.clock.elapsed, 30 + guard.VERIFY_AGE)

    def test_delete_404_alone_still_requires_fresh_get_404(self):
        self.provider.handler = lambda method, _: guard.Reply(404) if method == "DELETE" else None
        self.provider.early_delete_at = 30 + guard.DELETE_AGE + 60
        receipt = self.run_guard()
        self.assertEqual(receipt["status"], "deleted_verified")
        self.assertEqual(receipt["delete_attempts"], 3)
        self.assertEqual(self.provider.calls[-1][0], "GET")

    def test_confirmation_at_or_after_deadline_is_failed_even_if_absent(self):
        def respond(method, path):
            if method == "GET" and self.provider.deleted:
                self.clock.elapsed += 900
                return guard.Reply(404)
        self.provider.handler = respond
        receipt = self.run_guard()
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["failure_reason"], "absence_verified_after_deadline")
        self.assertTrue(receipt["deletion_verified"])
        self.assertFalse(receipt["deletion_before_deadline"])
        self.assertFalse(receipt["billing_cap_guaranteed"])

    def test_wrong_bound_identity_is_rejected_and_renaming_cannot_escape_tracking(self):
        for field, value in (("id", 999), ("created_at", guard.utc(BASE + 31))):
            with self.subTest(field=field):
                self.setUp()
                def respond(method, path):
                    if "?" not in path:
                        droplet = dict(self.provider.droplet, **{field: value})
                        return guard.Reply(200, {"droplet": droplet})
                self.provider.handler = respond
                self.assert_failed_without_absence(self.run_guard(), "bound_identity_changed")
        self.setUp()
        def rename(clock):
            if clock.elapsed > 30:
                self.provider.droplet["name"] = "renamed-after-binding"
        self.clock.hook = rename
        self.assertEqual(self.run_guard()["status"], "deleted_verified")

    def test_clock_rollback_does_not_extend_lifetime_and_forward_jump_is_conservative(self):
        for jump in (-7200, 7200):
            with self.subTest(jump=jump):
                self.setUp()
                self.clock.hook = lambda c: setattr(c, "offset", jump) if c.elapsed > 30 else None
                receipt = self.run_guard()
                deletion = next(c[2] for c in self.provider.calls if c[0] == "DELETE")
                self.assertLessEqual(deletion, 30 + guard.DELETE_AGE)
                self.assertEqual(receipt["status"], "deleted_verified")

    def test_cancellation_is_terminal_failure_and_handlers_are_restored(self):
        def cancel(clock):
            if clock.elapsed > 30:
                guard.interrupt()
        self.clock.hook = cancel
        argv = ["--name", CONFIG.name, "--size", CONFIG.size, "--image-id", str(CONFIG.image_id), "--watch"]
        previous = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(guard, "HttpTransport", return_value=self.provider), \
                mock.patch.object(guard, "Clock", return_value=self.clock), \
                mock.patch("sys.stdout", new_callable=io.StringIO):
            path = Path(directory) / "receipt.json"
            self.assertEqual(guard.main(argv + ["--receipt", str(path)]), 1)
            receipt = json.loads(path.read_text())
            self.assert_failed_without_absence(receipt, "interrupted")
        self.assertEqual(previous, {s: signal.getsignal(s) for s in previous})

    def test_preflight_never_accesses_token_or_transport_and_rejects_stale_receipt(self):
        argv = ["--name", CONFIG.name, "--size", CONFIG.size, "--image-id", str(CONFIG.image_id)]
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(guard.os, "environ") as environment, \
                mock.patch.object(guard, "HttpTransport") as transport, \
                mock.patch("sys.stdout", new_callable=io.StringIO):
            path = Path(directory) / "receipt.json"
            self.assertEqual(guard.main(argv + ["--receipt", str(path)]), 0)
            content = path.read_bytes()
            self.assertEqual(json.loads(content)["status"], "preflight_only")
            self.assertEqual(guard.main(argv + ["--receipt", str(path), "--watch"]), 1)
            self.assertEqual(path.read_bytes(), content)
            self.assertNotIn(mock.call(guard.TOKEN_ENV), environment.get.call_args_list)
            transport.assert_not_called()

    def test_missing_token_has_failed_receipt_without_any_request(self):
        argv = ["--name", CONFIG.name, "--size", CONFIG.size, "--image-id", str(CONFIG.image_id), "--watch"]
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(guard.os.environ, {}, clear=True), \
                mock.patch.object(guard, "build_opener") as opener, \
                mock.patch("sys.stdout", new_callable=io.StringIO):
            path = Path(directory) / "receipt.json"
            self.assertEqual(guard.main(argv + ["--receipt", str(path)]), 1)
            self.assert_failed_without_absence(json.loads(path.read_text()), "missing_or_invalid_token")
            opener.assert_not_called()

    def test_transport_restricts_origin_methods_paths_and_discards_error_bodies(self):
        with mock.patch.object(guard, "build_opener") as build:
            transport = guard.HttpTransport("synthetic-token-only")
            for method, path in (("POST", "/v2/droplets"), ("DELETE", "/v2/droplets?tag_name=all"),
                                 ("GET", "https://evil.invalid/"), ("GET", "/v2/droplets/1/destroy_with_associated_resources")):
                with self.subTest(method=method, path=path), self.assertRaises(guard.GuardError):
                    transport.request(method, path)
            build.return_value.open.assert_not_called()
            body = mock.Mock()
            build.return_value.open.side_effect = HTTPError(guard.API + "/v2/droplets/1", 401,
                                                          "SENSITIVE ERROR", {}, body)
            self.assertEqual(transport.request("GET", "/v2/droplets/1"), guard.Reply(401))
            body.read.assert_not_called()
            request = build.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, "https://api.digitalocean.com/v2/droplets/1")
            self.assertEqual(build.return_value.open.call_args.kwargs["timeout"], 15)
            self.assertIsNone(guard.NoRedirect().redirect_request(None, None, 302, None, None, "https://evil.invalid/"))
            build.return_value.open.side_effect = URLError("SENSITIVE NETWORK ERROR")
            with self.assertRaises(guard.TransientError) as failure:
                transport.request("GET", "/v2/droplets/1")
            self.assertEqual(str(failure.exception), "")

    def test_malformed_duplicate_and_nonfinite_json_fail_without_echoing_payload(self):
        for payload in (b'{"droplets": [], "droplets": [{"SECRET": 1}]}', b'{"SECRET": NaN}', b'not-json-SECRET'):
            with self.subTest(payload=payload), mock.patch.object(guard, "build_opener") as build:
                response = build.return_value.open.return_value.__enter__.return_value
                response.status, response.read.return_value = 200, payload
                transport = guard.HttpTransport("synthetic-token-only")
                with self.assertRaisesRegex(guard.GuardError, "^invalid_provider_json$"):
                    transport.request("GET", "/v2/droplets/1")


class ExistingDropletRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.provider = SyntheticProvider(self.clock)
        self.events = []
        self.configure_age(3600)

    def configure_age(self, age):
        self.provider.preexisting = True
        self.provider.droplet.update(name="ubuntu-s-4vcpu-8gb-lon1", created_at=guard.utc(BASE - age))
        self.config = guard.Config(self.provider.droplet["name"], CONFIG.size, CONFIG.image_id,
                                   self.provider.droplet["id"], self.provider.droplet["created_at"])

    def run_guard(self):
        return guard.Watchdog(self.config, self.provider, clock=self.clock,
                              emit=lambda receipt: self.events.append(copy.deepcopy(receipt))).run()

    def assert_rejected(self, receipt, reason):
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["failure_reason"], reason)
        self.assertFalse(receipt["armed"])
        self.assertFalse(receipt["pre_creation_armed"])
        self.assertFalse(receipt["deletion_verified"])
        self.assertFalse(receipt["billing_cap_guaranteed"])
        self.assertIsNone(receipt["bound"])
        self.assertFalse(any(method == "DELETE" for method, _, _ in self.provider.calls))
        self.assertTrue(all(path == "/v2/droplets/987654321" for _, path, _ in self.provider.calls))

    def argv(self):
        return ["--name", self.config.name, "--size", self.config.size, "--image-id", str(self.config.image_id),
                "--existing-id", str(self.config.existing_id), "--existing-created-at", self.config.existing_created_at]

    def test_exact_id_recovery_keeps_original_deadlines_without_pre_creation_arming(self):
        receipt = self.run_guard()
        self.assertEqual([e["status"] for e in self.events], ["recovering", "bound", "deleting", "deleted_verified"])
        self.assertEqual(receipt["binding_mode"], "existing_exact_id")
        self.assertEqual(receipt["creation_window_seconds"], 0)
        self.assertFalse(receipt["armed"])
        self.assertFalse(receipt["pre_creation_armed"])
        self.assertNotIn("armed_at_utc", receipt)
        self.assertEqual(receipt["recovery_started_at_utc"], guard.utc(BASE))
        self.assertEqual(receipt["bound"]["id"], self.config.existing_id)
        self.assertEqual(receipt["bound"]["created_at"], self.config.existing_created_at)
        self.assertEqual(receipt["delete_at_utc"], guard.utc(BASE - 3600 + guard.DELETE_AGE))
        self.assertEqual(receipt["verify_before_utc"], guard.utc(BASE - 3600 + guard.VERIFY_AGE))
        self.assertEqual(receipt["existing_age_at_binding_seconds"], 3600)
        self.assertFalse(receipt["deletion_due_on_recovery"])
        self.assertFalse(receipt["manual_deletion_required"])
        self.assertEqual(self.provider.calls[0][:2], ("GET", "/v2/droplets/987654321"))
        self.assertTrue(all(path == "/v2/droplets/987654321" for _, path, _ in self.provider.calls))
        deletion = next(call for call in self.provider.calls if call[0] == "DELETE")
        self.assertEqual(deletion[2], guard.DELETE_AGE - 3600)
        self.assertTrue(receipt["deletion_before_deadline"])
        self.assertFalse(receipt["billing_cap_guaranteed"])

    def test_recovery_rejects_wrong_exact_id_or_created_at(self):
        for field, value in (("id", 999), ("created_at", guard.utc(BASE - 3601)),
                             ("created_at", self.config.existing_created_at.replace("Z", ".000Z"))):
            with self.subTest(field=field, value=value):
                self.setUp()
                self.provider.droplet[field] = value
                self.assert_rejected(self.run_guard(), "existing_droplet_identity_mismatch")
                self.assertEqual(len(self.provider.calls), 1)

    def test_recovery_requires_full_configuration_and_exact_name(self):
        mutations = [
            lambda d: d.update(name=d["name"].upper()), lambda d: d.update(vcpus=8),
            lambda d: d.update(memory=32768), lambda d: d.update(disk=600),
            lambda d: d.update(size_slug="wrong-size"), lambda d: d["size"].update(slug="wrong-size"),
            lambda d: d["region"].update(slug="nyc3"), lambda d: d["image"].update(id=999),
            lambda d: d.update(features=["backups"]), lambda d: d.update(volume_ids=["extra"]),
            lambda d: d.update(backup_ids=[1]), lambda d: d.update(snapshot_ids=[2]),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.setUp()
                mutate(self.provider.droplet)
                receipt = self.run_guard()
                self.assert_rejected(receipt, "unexpected_extra_resources" if
                                     any(self.provider.droplet[key] for key in ("volume_ids", "backup_ids", "snapshot_ids"))
                                     else "unexpected_droplet_configuration")
        self.setUp()
        self.provider.droplet["size"]["price_hourly"] = "0.072000001"
        self.assert_rejected(self.run_guard(), "hourly_price_above_ceiling_or_invalid")

    def test_recovery_rejects_future_creation_even_with_matching_identity(self):
        for age in (-1, -0.001):
            with self.subTest(age=age):
                self.setUp()
                self.configure_age(age)
                self.assert_rejected(self.run_guard(), "existing_droplet_created_in_future")

    def test_recovery_at_or_after_delete_age_deletes_immediately_before_original_deadline(self):
        for age in (guard.DELETE_AGE, guard.DELETE_AGE + 60, guard.VERIFY_AGE - 1):
            with self.subTest(age=age):
                self.setUp()
                self.configure_age(age)
                receipt = self.run_guard()
                self.assertEqual(receipt["status"], "deleted_verified")
                self.assertTrue(receipt["deletion_due_on_recovery"])
                self.assertTrue(receipt["deletion_before_deadline"])
                self.assertFalse(receipt["manual_deletion_required"])
                self.assertEqual([(m, p) for m, p, _ in self.provider.calls], [
                    ("GET", "/v2/droplets/987654321"), ("DELETE", "/v2/droplets/987654321"),
                    ("GET", "/v2/droplets/987654321")])
                self.assertEqual(self.clock.elapsed, 0)
                self.assertEqual(receipt["verify_before_utc"], guard.utc(BASE - age + guard.VERIFY_AGE))

    def test_expired_recovery_refuses_adoption_and_requires_manual_deletion(self):
        for age in (guard.VERIFY_AGE, guard.VERIFY_AGE + 1, guard.VERIFY_AGE + 3600):
            with self.subTest(age=age):
                self.setUp()
                self.configure_age(age)
                receipt = self.run_guard()
                self.assert_rejected(receipt, "existing_droplet_lifetime_expired_manual_deletion_required")
                self.assertTrue(receipt["manual_deletion_required"])
                self.assertEqual(receipt["operator_action"], "Delete this verified Droplet through the provider console immediately.")
                self.assertEqual(len(self.provider.calls), 1)
                self.assertEqual(self.clock.elapsed, 0)

    def test_recovery_lookup_latency_counts_against_original_lifetime(self):
        self.configure_age(guard.VERIFY_AGE - 5)
        def delay(method, path):
            self.clock.elapsed += 10
        self.provider.handler = delay
        receipt = self.run_guard()
        self.assert_rejected(receipt, "existing_droplet_lifetime_expired_manual_deletion_required")
        self.assertTrue(receipt["manual_deletion_required"])

    def test_recovery_deadline_expiring_after_binding_requires_manual_deletion(self):
        def emit(receipt):
            if receipt["status"] == "bound":
                self.clock.elapsed = guard.VERIFY_AGE - 3600
        receipt = guard.Watchdog(self.config, self.provider, clock=self.clock, emit=emit).run()
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["failure_reason"], "deletion_verification_deadline_expired")
        self.assertIsNotNone(receipt["bound"])
        self.assertTrue(receipt["manual_deletion_required"])
        self.assertIn("provider console immediately", receipt["operator_action"])
        self.assertFalse(receipt["deletion_verified"])
        self.assertFalse(any(method == "DELETE" for method, _, _ in self.provider.calls))

    def test_recovery_ineffective_deletion_reaches_original_deadline_and_requires_manual_cleanup(self):
        self.configure_age(guard.DELETE_AGE)
        self.provider.handler = lambda method, _: guard.Reply(204) if method == "DELETE" else None
        receipt = self.run_guard()
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["failure_reason"], "deletion_verification_deadline_expired")
        self.assertEqual(self.clock.elapsed, guard.VERIFY_AGE - guard.DELETE_AGE)
        self.assertEqual(receipt["delete_attempts"], 30)
        self.assertTrue(receipt["manual_deletion_required"])
        self.assertFalse(receipt["deletion_verified"])
        self.assertFalse(receipt["deletion_before_deadline"])

    def test_recovery_late_verified_absence_does_not_request_deleting_absent_resource(self):
        self.configure_age(guard.VERIFY_AGE - 1)
        def respond(method, path):
            if method == "GET" and self.provider.deleted:
                self.clock.elapsed += 2
                return guard.Reply(404)
        self.provider.handler = respond
        receipt = self.run_guard()
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["failure_reason"], "absence_verified_after_deadline")
        self.assertTrue(receipt["deletion_verified"])
        self.assertFalse(receipt["deletion_before_deadline"])
        self.assertFalse(receipt["manual_deletion_required"])

    def test_initial_absence_errors_or_unrelated_response_do_not_claim_recovery(self):
        cases = [
            (guard.Reply(404), "existing_droplet_not_found"),
            (guard.Reply(401), "provider_authentication_or_scope_failure"),
            (guard.Reply(503), "initial_request_failed"),
            (guard.Reply(200, {"droplets": [self.provider.droplet]}), "invalid_existing_droplet_response"),
            (guard.Reply(204), "invalid_existing_droplet_response"),
        ]
        for reply, reason in cases:
            with self.subTest(status=reply.status, reason=reason):
                self.setUp()
                self.provider.handler = lambda *_: reply
                self.assert_rejected(self.run_guard(), reason)
                self.assertEqual(len(self.provider.calls), 1)

    def test_clock_rollback_never_restarts_existing_resource_allowance(self):
        self.clock.hook = lambda c: setattr(c, "offset", -7200)
        receipt = self.run_guard()
        deletion = next(call for call in self.provider.calls if call[0] == "DELETE")
        self.assertEqual(deletion[2], guard.DELETE_AGE - 3600)
        self.assertEqual(receipt["status"], "deleted_verified")

    def test_recovery_preflight_has_no_token_access_or_transport(self):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(guard.os, "environ") as environment, \
                mock.patch.object(guard, "HttpTransport") as transport, \
                mock.patch("sys.stdout", new_callable=io.StringIO):
            path = Path(directory) / "receipt.json"
            self.assertEqual(guard.main(self.argv() + ["--receipt", str(path)]), 0)
            receipt = json.loads(path.read_text())
            self.assertEqual(receipt["status"], "preflight_only")
            self.assertEqual(receipt["binding_mode"], "existing_exact_id")
            self.assertFalse(receipt["pre_creation_armed"])
            self.assertFalse(receipt["provider_api_contacted"])
            self.assertEqual(receipt["expected"]["id"], self.config.existing_id)
            self.assertEqual(receipt["expected"]["created_at"], self.config.existing_created_at)
            self.assertNotIn(mock.call(guard.TOKEN_ENV), environment.get.call_args_list)
            transport.assert_not_called()

    def test_cli_requires_paired_recovery_arguments_before_token_access(self):
        base = ["--name", CONFIG.name, "--size", CONFIG.size, "--image-id", str(CONFIG.image_id), "--watch"]
        for extra in (["--existing-id", str(self.config.existing_id)],
                      ["--existing-created-at", self.config.existing_created_at]):
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as directory, \
                    mock.patch.object(guard.os, "environ") as environment, \
                    mock.patch.object(guard, "HttpTransport") as transport, \
                    mock.patch("sys.stdout", new_callable=io.StringIO) as output:
                path = Path(directory) / "receipt.json"
                self.assertEqual(guard.main(base + extra + ["--receipt", str(path)]), 1)
                self.assertEqual(json.loads(path.read_text())["failure_reason"], "existing_id_and_created_at_must_be_paired")
                self.assertEqual(json.loads(output.getvalue())["status"], "failed")
                self.assertNotIn("failure_reason", json.loads(output.getvalue()))
                self.assertNotIn(mock.call(guard.TOKEN_ENV), environment.get.call_args_list)
                transport.assert_not_called()

    def test_existing_id_requires_a_strict_positive_decimal_integer(self):
        for value in ("0", "-1", "+1", "01", " 1", "1 ", "1.0", "1e3", "True", "１２３", str(2**63), "9" * 100):
            with self.subTest(value=value), mock.patch("sys.stderr", new_callable=io.StringIO), \
                    mock.patch.object(guard, "HttpTransport") as transport:
                argv = self.argv()
                argv[argv.index("--existing-id") + 1] = value
                with self.assertRaises(SystemExit) as failure:
                    guard.main(argv + ["--watch"])
                self.assertEqual(failure.exception.code, 2)
                transport.assert_not_called()
        for value in (True, 1.0, "1", 0, -1, 2**63):
            with self.subTest(config_value=value), self.assertRaisesRegex(guard.GuardError, "^invalid_existing_id$"):
                guard.Config(self.config.name, CONFIG.size, CONFIG.image_id, value, self.config.existing_created_at).validate()

    def test_existing_created_at_requires_strict_valid_utc_provider_time(self):
        for value in ("", "invalid", "2026-02-30T12:00:00Z", "2026-10-21T12:00:00+00:00",
                      "2026-10-21 12:00:00Z", "2026-10-21T12:00:00.1234567Z", "2026-10-21T12:00:00z",
                      " 2026-10-21T12:00:00Z", "２０２６-10-21T12:00:00Z"):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory, \
                    mock.patch.object(guard.os, "environ") as environment, \
                    mock.patch.object(guard, "HttpTransport") as transport, \
                    mock.patch("sys.stdout", new_callable=io.StringIO) as output:
                argv = self.argv()
                argv[argv.index("--existing-created-at") + 1] = value
                path = Path(directory) / "receipt.json"
                self.assertEqual(guard.main(argv + ["--watch", "--receipt", str(path)]), 1)
                self.assertEqual(json.loads(path.read_text())["failure_reason"], "invalid_existing_created_at")
                self.assertEqual(json.loads(output.getvalue())["status"], "failed")
                self.assertNotIn("failure_reason", json.loads(output.getvalue()))
                self.assertNotIn(mock.call(guard.TOKEN_ENV), environment.get.call_args_list)
                transport.assert_not_called()

    def test_existing_name_allowance_does_not_weaken_fresh_name_validation(self):
        self.config.validate()
        with self.assertRaisesRegex(guard.GuardError, "^invalid_unique_name$"):
            guard.Config(self.config.name, CONFIG.size, CONFIG.image_id).validate()
        for value in ("", "-invalid", "invalid-", "a..b", "a/b", "a b", "x" * 254, "x" * 64, "☃"):
            with self.subTest(name=value), self.assertRaisesRegex(guard.GuardError, "^invalid_existing_name$"):
                guard.Config(value, CONFIG.size, CONFIG.image_id, self.config.existing_id,
                             self.config.existing_created_at).validate()


class WatchdogPrivacyTests(unittest.TestCase):
    def assert_private_env_reads(self, environment, expected):
        reads = [call for call in environment.get.call_args_list
                 if call.args and call.args[0] in (guard.CONFIG_ENV, guard.TOKEN_ENV)]
        self.assertEqual(reads, [mock.call(name) for name in expected])

    def private_config(self, recovering=False):
        result = {"planned_name": CONFIG.name, "size_slug": CONFIG.size, "image_id": CONFIG.image_id}
        if recovering:
            result.update(existing_droplet_id=987654321, existing_created_at=guard.utc(BASE - 3600))
        return result

    def sensitive_receipt(self):
        receipt = guard.initial_receipt(CONFIG, True)
        receipt.update(status="bound", identity_verified=True, requests=7,
                       bound={"id": 987654321, "created_at": guard.utc(BASE - 3600),
                              "configuration_verified": True},
                       delete_at_utc=guard.utc(BASE - 3600 + guard.DELETE_AGE),
                       failure_reason="private-failure-sentinel", host="private-host.invalid",
                       token="private-token-sentinel", extra={"url": "https://private-url.invalid/secret"})
        return receipt

    def assert_public_only(self, serialized):
        allowed = {"status", "billing_cap_guaranteed", "provider_api_contacted", "armed",
                   "pre_creation_armed", "identity_verified", "deletion_verified",
                   "deletion_before_deadline", "manual_deletion_required", "requests",
                   "delete_attempts", "transient_errors"}
        for line in serialized.splitlines():
            result = json.loads(line)
            self.assertLessEqual(set(result), allowed)
            self.assertIn(result["status"], guard.PUBLIC_STATUSES | {"unknown"})
            self.assertFalse(result["billing_cap_guaranteed"])
            for key, value in result.items():
                if key != "status":
                    self.assertIn(type(value), (bool, int))
        for forbidden in (CONFIG.name, CONFIG.size, str(CONFIG.image_id), "987654321",
                          guard.utc(BASE - 3600), "private-", "failure_reason", "created_at"):
            self.assertNotIn(forbidden, serialized)

    def test_stdout_omits_private_fields_and_receipts_stay_private_across_updates(self):
        receipt = self.sensitive_receipt()
        with tempfile.TemporaryDirectory() as directory, mock.patch("sys.stdout", new_callable=io.StringIO) as output:
            path = Path(directory) / "receipt.json"
            sink = guard.ReceiptSink(path)
            for status in ("bound", "failed"):
                receipt["status"] = status
                sink(receipt)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(json.loads(path.read_text()), receipt)
            self.assert_public_only(output.getvalue())
            self.assertTrue(json.loads(output.getvalue().splitlines()[0])["identity_verified"])

    def test_public_projection_rejects_hostile_values_under_allowed_keys(self):
        receipt = self.sensitive_receipt()
        receipt.update(status={"private-status-sentinel": True}, identity_verified="private-identity-sentinel",
                       armed=["private-armed-sentinel"], requests="private-counter-sentinel",
                       delete_attempts=True, transient_errors=-1, billing_cap_guaranteed=True)
        projected = guard.public_receipt(receipt)
        self.assertEqual(projected["status"], "unknown")
        self.assertFalse(projected["identity_verified"])
        self.assertFalse(projected["armed"])
        self.assertNotIn("requests", projected)
        self.assertNotIn("delete_attempts", projected)
        self.assertNotIn("transient_errors", projected)
        self.assert_public_only(json.dumps(projected))
        self.assert_public_only(json.dumps(guard.public_receipt(["private-unexpected-report"])))

    def test_environment_preflight_accepts_fresh_and_recovery_without_token_access(self):
        for recovering in (False, True):
            with self.subTest(recovering=recovering), tempfile.TemporaryDirectory() as directory, \
                    mock.patch.object(guard.os, "environ") as environment, \
                    mock.patch.object(guard, "HttpTransport") as transport, \
                    mock.patch("sys.stdout", new_callable=io.StringIO) as output:
                document = self.private_config(recovering)
                environment.get.side_effect = lambda name: json.dumps(document) if name == guard.CONFIG_ENV else None
                path = Path(directory) / "receipt.json"
                self.assertEqual(guard.main(["--config-from-env", "--receipt", str(path)]), 0)
                receipt = json.loads(path.read_text())
                self.assertEqual(receipt["expected"]["name"], CONFIG.name)
                if recovering:
                    self.assertEqual(receipt["expected"]["id"], document["existing_droplet_id"])
                    self.assertEqual(receipt["expected"]["created_at"], document["existing_created_at"])
                self.assertFalse(receipt["identity_verified"])
                self.assert_private_env_reads(environment, [guard.CONFIG_ENV])
                transport.assert_not_called()
                self.assert_public_only(output.getvalue())

    def test_invalid_private_configuration_fails_before_token_access_or_api(self):
        valid = self.private_config()
        recovery = self.private_config(True)
        documents = [None, "", "private-invalid-json", "[]", "null", "{\"private\": NaN}",
                     "{\"planned_name\": \"private-duplicate\", \"planned_name\": \"private-second\"}",
                     " " * (guard.MAX_CONFIG_BYTES + 1), "[" * 1500 + "]" * 1500,
                     "\ud800", json.dumps(dict(valid, private_extra="private-extra-sentinel")),
                     json.dumps({key: value for key, value in valid.items() if key != "image_id"}),
                     json.dumps(dict(valid, existing_droplet_id=987654321)),
                     json.dumps(dict(valid, existing_created_at=recovery["existing_created_at"])),
                     json.dumps(dict(recovery, existing_droplet_id=None, existing_created_at=None))]
        for field, bad in (("planned_name", []), ("size_slug", None), ("image_id", True),
                           ("image_id", 1.5), ("image_id", "12345"), ("image_id", -1)):
            documents.append(json.dumps(dict(valid, **{field: bad})))
        for field, bad in (("existing_droplet_id", True), ("existing_droplet_id", 1.5),
                           ("existing_droplet_id", "987654321"), ("existing_droplet_id", 2**63),
                           ("existing_droplet_id", 0), ("existing_created_at", "private-invalid-time"),
                           ("existing_created_at", "2026-10-21T12:00:00+00:00"),
                           ("existing_created_at", None)):
            documents.append(json.dumps(dict(recovery, **{field: bad})))
        for index, document in enumerate(documents):
            with self.subTest(case=index), tempfile.TemporaryDirectory() as directory, \
                    mock.patch.object(guard.os, "environ") as environment, \
                    mock.patch.object(guard, "HttpTransport") as transport, \
                    mock.patch("sys.stdout", new_callable=io.StringIO) as output:
                environment.get.side_effect = lambda name: document if name == guard.CONFIG_ENV else None
                path = Path(directory) / "receipt.json"
                self.assertEqual(guard.main(["--config-from-env", "--watch", "--receipt", str(path)]), 1)
                self.assert_private_env_reads(environment, [guard.CONFIG_ENV])
                transport.assert_not_called()
                self.assertEqual(json.loads(path.read_text())["status"], "failed")
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assert_public_only(output.getvalue())

    def test_mixed_sources_and_argument_errors_never_echo_private_values(self):
        cases = [["--config-from-env", "--name", "private-mixed-sentinel"],
                 ["--image-id", "private-invalid-integer"],
                 ["--existing-id", "private-invalid-id"], ["--private-unknown-argument"]]
        for argv in cases:
            with self.subTest(argv=argv), mock.patch.object(guard.os, "environ") as environment, \
                    mock.patch.object(guard, "HttpTransport") as transport, \
                    mock.patch("sys.stderr", new_callable=io.StringIO) as error:
                with self.assertRaises(SystemExit) as failure:
                    guard.main(argv + ["--watch"])
                self.assertEqual(failure.exception.code, 2)
                self.assertNotIn("private-", error.getvalue())
                self.assert_private_env_reads(environment, [])
                transport.assert_not_called()

    def test_secret_recovery_preserves_exact_identity_and_original_deadlines(self):
        clock = FakeClock()
        provider = SyntheticProvider(clock)
        document = self.private_config(True)
        provider.preexisting = True
        provider.droplet["created_at"] = document["existing_created_at"]
        environment = {guard.CONFIG_ENV: json.dumps(document), guard.TOKEN_ENV: "synthetic-token-only"}
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(guard.os.environ, environment), \
                mock.patch.object(guard, "HttpTransport", return_value=provider), \
                mock.patch.object(guard, "Clock", return_value=clock), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as output:
            path = Path(directory) / "receipt.json"
            self.assertEqual(guard.main(["--config-from-env", "--watch", "--receipt", str(path)]), 0)
            receipt = json.loads(path.read_text())
            self.assertEqual(receipt["bound"]["id"], document["existing_droplet_id"])
            self.assertEqual(receipt["bound"]["created_at"], document["existing_created_at"])
            self.assertEqual(receipt["delete_at_utc"], guard.utc(BASE - 3600 + guard.DELETE_AGE))
            self.assertEqual(receipt["verify_before_utc"], guard.utc(BASE - 3600 + guard.VERIFY_AGE))
            self.assertTrue(receipt["identity_verified"])
            self.assertFalse(receipt["pre_creation_armed"])
            self.assertTrue(all(path == "/v2/droplets/987654321" for _, path, _ in provider.calls))
            self.assert_public_only(output.getvalue())

    def test_receipt_io_failure_emits_only_public_evidence(self):
        argv = ["--name", CONFIG.name, "--size", CONFIG.size, "--image-id", str(CONFIG.image_id), "--watch"]
        with mock.patch.object(guard.ReceiptSink, "__call__", side_effect=OSError("private-path-sentinel")), \
                mock.patch.object(guard.os, "environ") as environment, \
                mock.patch.object(guard, "HttpTransport") as transport, \
                mock.patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(guard.main(argv), 1)
            self.assert_public_only(output.getvalue())
            self.assert_private_env_reads(environment, [])
            transport.assert_not_called()

    def test_actual_workflow_summary_filters_private_fields_and_dispatch_has_no_inputs(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/droplet-watchdog.yml").read_text()
        self.assertNotIn("inputs.", workflow)
        self.assertIn("workflow_dispatch: {}", workflow)
        before_supervision, supervision = workflow.split("      - name: Supervise the Droplet using the selected binding mode", 1)
        self.assertNotIn("secrets.", before_supervision)
        self.assertIn("${{ secrets.SQUADPAD_DO_WATCHDOG_CONFIG }}", supervision)
        self.assertIn("if: success() && vars.SQUADPAD_DO_WATCHDOG_ENABLED == 'true'", supervision)
        summary = workflow.split("      - name: Summarize observed cleanup without a billing-cap claim", 1)[1]
        source = textwrap.dedent(summary.split("python3 -B - <<'PY'\n", 1)[1].rsplit("\n          PY", 1)[0])
        for invalid_values in (False, True):
            with self.subTest(invalid_values=invalid_values), tempfile.TemporaryDirectory() as directory:
                receipt = self.sensitive_receipt()
                if invalid_values:
                    receipt.update(status="private-hostile-status", requests="private-hostile-counter",
                                   identity_verified={"private-hostile-boolean": True})
                path, result = Path(directory) / "receipt.json", Path(directory) / "summary.md"
                path.write_text(json.dumps(receipt))
                with mock.patch.dict(guard.os.environ, {"SQUADPAD_WATCHDOG_RECEIPT": str(path),
                                                       "GITHUB_STEP_SUMMARY": str(result)}), \
                        mock.patch.object(sys, "path", list(sys.path)):
                    exec(compile(source, "workflow-summary", "exec"), {"__name__": "__main__"})
                rendered = result.read_text()
                clean = rendered.split("```json\n", 1)[1].split("\n```", 1)[0]
                self.assert_public_only(json.dumps(json.loads(clean)))
                self.assertNotIn("private-", rendered)
                self.assertNotIn("987654321", rendered)


if __name__ == "__main__":
    unittest.main()
