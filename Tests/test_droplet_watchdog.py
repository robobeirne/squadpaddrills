"""Synthetic clock/provider tests only. No account, token or network is used."""
from __future__ import annotations

import copy
import io
import json
from pathlib import Path
import signal
import sys
import tempfile
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
                        "memory": 32768, "disk": 600, "region": {"slug": "lon1"},
                        "image": {"id": CONFIG.image_id}, "size": {"slug": CONFIG.size,
                        "price_hourly": 0.38988000000000002}, "features": ["private_networking"],
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

    def test_arms_then_binds_then_deletes_exact_id_at_provider_age(self):
        receipt = self.run_guard()
        self.assertEqual([e["status"] for e in self.events], ["armed", "bound", "deleting", "deleted_verified"])
        self.assertIsNone(self.events[0]["bound"])
        self.assertEqual(receipt["bound"]["id"], self.provider.droplet["id"])
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
            lambda d: d["size"].update(price_hourly=0.391), lambda d: d["size"].update(price_hourly="NaN"),
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


if __name__ == "__main__":
    unittest.main()
