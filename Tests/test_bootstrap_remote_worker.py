"""Offline bootstrap control tests. No worker, SSH or engine is executed."""
from __future__ import annotations

import base64
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import textwrap
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Tools/Unreal"))
import bootstrap_remote_worker as bootstrap

PRIVATE_HOST = "private-worker.example.invalid"
PRIVATE_URL = "https://archive.example.invalid/engine.zip?private=synthetic-secret"
PRIVATE_LOG = b"private diagnostic and worker identity"
NOW = datetime(2030, 1, 1, tzinfo=timezone.utc).timestamp()
METADATA = json.loads((ROOT / "Docs/ENGINE_ARCHIVE.json").read_text(encoding="utf-8"))


def config(deadline=NOW + 4 * 3600):
    stamp = datetime.fromtimestamp(deadline, timezone.utc).isoformat().replace("+00:00", "Z")
    return json.dumps({"download_url": PRIVATE_URL, "workload_deadline_utc": stamp})


def install_receipt():
    true_fields = ("unprivileged_account_verified", "download_complete", "full_archive_sha256_recorded",
                   "archive_structure_verified", "archive_integrity_verified", "extraction_complete",
                   "metadata_verified", "executable_permissions_preserved", "temporary_archive_removed")
    false_fields = ("publisher_authenticity_verified", "engine_executed", "compiler_executed", "cleanup_verified")
    return {
        "schema": "squadpad.engine-install.v1", "status": "passed", "stage": "complete",
        "reason_code": "installation_passed", "engine_root": "/opt/unreal/UE_5.8",
        "archive_bytes": METADATA["compressed_archive_bytes"], "archive_sha256": "a" * 64,
        "entry_count": METADATA["zip_entry_count"], "expanded_bytes": METADATA["listed_expanded_bytes"],
        "metadata_sha256": {"build_version": METADATA["build_version_sha256"],
                            "sdk_manifest": METADATA["toolchain"]["sdk_manifest_sha256"],
                            "installed_build_marker": METADATA["installed_build_marker"]["sha256"]},
        "evidence": dict.fromkeys(true_fields, True) | dict.fromkeys(false_fields, False),
    }


def response(phase, passed=True, reason=None, receipt=None, stdout=PRIVATE_LOG, stderr=b""):
    def log(data):
        return {"size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                "base64": base64.b64encode(data).decode("ascii")}
    envelope = {
        "schema": bootstrap.WIRE_SCHEMA, "phase": phase, "status": "passed" if passed else "failed",
        "reason_code": reason or ("os_prepared" if phase == "os" else "engine_installed"),
        "returncode": 0 if passed else 1, "timed_out": False,
        "stdout": log(stdout), "stderr": log(stderr),
        "install_receipt": (install_receipt() if receipt is None else receipt) if phase == "engine" else None,
    }
    return {"stdout": json.dumps(envelope).encode(), "returncode": 0 if passed else 1,
            "timed_out": False, "over_limit": False}


class BootstrapRemoteWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="squadpad-bootstrap-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.key = self.root / "identity"
        self.key.write_text("SYNTHETIC IDENTITY, NOT USABLE", encoding="utf-8")
        self.key.chmod(0o600)
        self.known_hosts = self.root / "known_hosts"
        self.known_hosts.write_text("SYNTHETIC HOST PIN, NOT USABLE", encoding="utf-8")
        self.known_hosts.chmod(0o600)
        self.environment = {
            "SQUADPAD_REMOTE_SSH_HOST": PRIVATE_HOST,
            "SQUADPAD_REMOTE_SSH_KEY_FILE": str(self.key),
            "SQUADPAD_REMOTE_SSH_KNOWN_HOSTS_FILE": str(self.known_hosts),
            "SQUADPAD_BOOTSTRAP_CONFIG": config(),
        }
        data = b"synthetic source; not executable"
        self.sources = {name: {"base64": base64.b64encode(data).decode(),
                               "sha256": hashlib.sha256(data).hexdigest()}
                        for name in bootstrap.SOURCE_NAMES}
        self.output_index = 0

    def run_bootstrap(self, responses=None, environment=None, clock=None, transport=None):
        self.output_index += 1
        output = self.root / ("output-" + str(self.output_index))
        captured = []
        pending = iter(responses or [response("os"), response("engine")])

        def simulated(command, payload, **limits):
            captured.append((command, payload, limits))
            return next(pending)

        summary = bootstrap.bootstrap(
            "offline-fixture", str(output), self.environment if environment is None else environment,
            simulated if transport is None else transport, clock=(lambda: NOW) if clock is None else clock,
            source_loader=lambda: (copy.deepcopy(self.sources), copy.deepcopy(METADATA)),
            public_key_loader=lambda _path: "SYNTHETIC PUBLIC KEY, NOT USABLE")
        return summary, output, captured

    def assert_public_safe(self, summary):
        encoded = json.dumps(summary)
        for marker in (PRIVATE_HOST, PRIVATE_URL, PRIVATE_LOG.decode(), str(self.root), "SYNTHETIC", "PRIVATE_EXCEPTION"):
            self.assertNotIn(marker, encoded)
        self.assertEqual(set(summary), set(bootstrap.empty_summary()))
        for name in ("unreal_executed", "native_reader_contract_passed", "cleanup_verified"):
            self.assertIs(summary[name], False)

    def test_public_key_derivation_discards_comments_with_any_number_of_words(self):
        wire = bytes.fromhex("0000000b7373682d6564323535313900000020") + b"x" * 32
        public = "ssh-ed25519 " + base64.b64encode(wire).decode("ascii")
        for comment in ("", " synthetic", " synthetic key comment with spaces"):
            returned = SimpleNamespace(stdout=(public + comment + "\n").encode("ascii"))
            with patch.object(bootstrap.subprocess, "run", return_value=returned) as command:
                self.assertEqual(bootstrap.derive_public_key(str(self.key)), public)
            self.assertEqual(command.call_args.args[0], ["ssh-keygen", "-y", "-f", str(self.key)])

    def test_new_cross_account_staging_permissions_ignore_umask_without_changing_existing_base(self):
        # Exercise the fixed staging action against a temporary filesystem only.
        # The OS preparation call, identity and ownership changes are simulated.
        source = compile(textwrap.dedent(bootstrap.ROOT_ACTION), "fixed-root-staging", "exec")
        original_stat = Path.stat
        for existing_base in (False, True):
            with self.subTest(existing_base=existing_base):
                fixture = self.root / ("existing-base" if existing_base else "new-base")
                private_root = fixture / "root"
                base = fixture / "srv/squadpad/bootstrap"
                private_root.mkdir(parents=True)
                base.parent.mkdir(parents=True)
                if existing_base:
                    base.mkdir(mode=0o700)
                    (base / "unrelated.txt").write_text("preserve", encoding="utf-8")

                def mapped_path(value):
                    return {"/root": private_root, "/srv/squadpad/bootstrap": base}[value]

                def simulated_stat(path, *args, **kwargs):
                    result = original_stat(path, *args, **kwargs)
                    if path == base:
                        fields = list(result)
                        fields[4] = 0  # Simulate only the root-owned base check.
                        return os.stat_result(fields)
                    return result

                operations = SimpleNamespace(**{name: getattr(os, name) for name in
                    ("open", "fdopen", "fchmod", "O_WRONLY", "O_CREAT", "O_EXCL")},
                    geteuid=lambda: 0, chown=Mock(), fchown=Mock())
                account = SimpleNamespace(pw_uid=12345, pw_gid=12345)
                run_child = Mock(return_value=0)
                result = {}
                namespace = {
                    "CONFIG": {"run_id": "umask-fixture", "sources": self.sources,
                               "public_key": "SYNTHETIC PUBLIC KEY, NOT USABLE"},
                    "Path": mapped_path, "os": operations, "stat": stat,
                    "pwd": SimpleNamespace(getpwnam=lambda _name: account),
                    "base64": base64, "hashlib": hashlib, "result": result,
                    "require": bootstrap.native.require, "run_child": run_child,
                    "real_path": lambda path: self.assertEqual(path.resolve(), path),
                }
                previous_umask = os.umask(0o077)
                try:
                    with patch.object(Path, "stat", simulated_stat):
                        if existing_base:
                            with self.assertRaisesRegex(bootstrap.native.CoordinatorError, "staging_failed"):
                                exec(source, namespace)
                        else:
                            exec(source, namespace)
                finally:
                    os.umask(previous_umask)
                run_child.assert_called_once()
                self.assertEqual(run_child.call_args.args[0][0], "bash")
                if existing_base:
                    self.assertEqual(stat.S_IMODE(base.stat().st_mode), 0o700)
                    self.assertEqual((base / "unrelated.txt").read_text(), "preserve")
                    self.assertFalse((base / "umask-fixture").exists())
                    operations.chown.assert_not_called()
                    operations.fchown.assert_not_called()
                else:
                    stage = base / "umask-fixture"
                    for path, mode in ((base, 0o755), (stage, 0o750), (stage / "work", 0o700),
                                       (stage / "install_engine.py", 0o640), (stage / "ENGINE_ARCHIVE.json", 0o640)):
                        self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)
                    self.assertEqual(result, {"status": "passed", "reason_code": "os_prepared"})
                    self.assertEqual(operations.fchown.call_count, 2)
                    for call in operations.fchown.call_args_list:
                        self.assertEqual(call.args[1:], (0, account.pw_gid))

    def test_success_separates_root_from_unprivileged_install_and_retains_private_evidence(self):
        summary, output, calls = self.run_bootstrap(responses=[
            response("os"), response("engine", stdout=PRIVATE_LOG + b" " + PRIVATE_URL.encode())])
        self.assertEqual(summary["status"], "passed")
        self.assertEqual(summary["reason_code"], "bootstrap_passed")
        self.assertTrue(summary["engine_installed"])
        self.assert_public_safe(summary)
        self.assertEqual(len(calls), 2)
        for index, (command, payload, limits) in enumerate(calls):
            self.assertEqual(command[command.index("-l") + 1], ("root", "squadpad")[index])
            self.assertEqual(command[-5:], ["python3", "-I", "-B", "-S", "-"])
            self.assertNotIn(PRIVATE_URL, " ".join(command))
            compile(payload, "fixed-bootstrap-payload", "exec")
            encoded = re.search(rb'CONFIG = json.loads\(base64.b64decode\("([A-Za-z0-9+/=]+)"', payload).group(1)
            private_config = json.loads(base64.b64decode(encoded))
            self.assertEqual(private_config["phase"], ("os", "engine")[index])
            self.assertEqual(private_config.get("download_url"), None if index == 0 else PRIVATE_URL)
            self.assertEqual(limits["maximum_bytes"], bootstrap.MAX_WIRE_BYTES)
            self.assertLessEqual(limits["timeout_seconds"], (bootstrap.ROOT_STAGE_SECONDS, bootstrap.INSTALL_STAGE_SECONDS)[index] + 20)
        evidence = json.loads((output / "bootstrap-result.json").read_text())
        self.assertFalse(evidence["operator_deadline_provider_verified"])
        self.assertEqual(evidence["summary"], summary)
        self.assertEqual(evidence["engine_install_receipt"]["archive_sha256"], "a" * 64)
        self.assertIn(PRIVATE_LOG, (output / "bootstrap-stdout.log").read_bytes())
        for path in output.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertNotIn(PRIVATE_URL.encode(), path.read_bytes())

    def test_both_stages_preserve_pinned_trust_and_reject_unsafe_credentials(self):
        summary, _output, calls = self.run_bootstrap()
        self.assertEqual(summary["status"], "passed")
        for command, _payload, _limits in calls:
            self.assertEqual(command[command.index("-F") + 1], "/dev/null")
            for option in ("StrictHostKeyChecking=yes", "HostKeyAlgorithms=ssh-ed25519", "UpdateHostKeys=no",
                           "IdentityAgent=none", "IdentitiesOnly=yes", "PasswordAuthentication=no",
                           "KbdInteractiveAuthentication=no", "ForwardAgent=no", "ForwardX11=no",
                           "ClearAllForwardings=yes", "ProxyCommand=none", "ProxyJump=none", "ConnectTimeout=10",
                           "UserKnownHostsFile=" + str(self.known_hosts), "GlobalKnownHostsFile=/dev/null"):
                self.assertIn(option, command)
        self.key.chmod(0o644)
        summary, _output, calls = self.run_bootstrap()
        self.assertEqual(summary["reason_code"], "config_invalid")
        self.assertFalse(calls)
        self.assert_public_safe(summary)

    def test_private_config_requires_bounded_exact_https_and_utc_deadline(self):
        cases = ["", "[1]", '{"download_url":1,"download_url":2}', "x" * 16385]
        for change in ({"download_url": "http://archive.example.invalid/file"},
                       {"download_url": "https://user:password@archive.example.invalid/file"},
                       {"download_url": "https://archive.example.invalid:444/file"},
                       {"download_url": "https://archive.example.invalid/\nfile"},
                       {"workload_deadline_utc": "2030-01-01T03:00:00+00:00"},
                       {"workload_deadline_utc": "2030-01-01T06:00:00Z"},
                       {"extra_command": "PRIVATE_EXCEPTION"}):
            value = json.loads(config())
            value.update(change)
            cases.append(json.dumps(value))
        for raw in cases:
            with self.subTest(raw=raw[:80]):
                environment = dict(self.environment, SQUADPAD_BOOTSTRAP_CONFIG=raw)
                summary, _output, calls = self.run_bootstrap(environment=environment)
                self.assertEqual(summary["reason_code"], "config_invalid")
                self.assertFalse(calls)
                self.assert_public_safe(summary)

    def test_deadline_reserves_native_window_and_prevents_second_stage_when_exhausted(self):
        environment = dict(self.environment, SQUADPAD_BOOTSTRAP_CONFIG=config(NOW + bootstrap.NATIVE_RESERVE_SECONDS + 30))
        summary, _output, calls = self.run_bootstrap(environment=environment)
        self.assertEqual(summary["reason_code"], "deadline_insufficient")
        self.assertFalse(calls)
        times = iter((NOW, NOW, NOW + 4 * 3600 - bootstrap.NATIVE_RESERVE_SECONDS - 59))
        summary, _output, calls = self.run_bootstrap(clock=lambda: next(times))
        self.assertEqual(summary["reason_code"], "deadline_insufficient")
        self.assertEqual(len(calls), 1)
        self.assertTrue(summary["os_prepared"])
        self.assertFalse(summary["engine_installed"])

    def test_root_failure_stops_before_engine_and_keeps_failure_logs_private(self):
        summary, output, calls = self.run_bootstrap(responses=[
            response("os", passed=False, reason="os_preparation_failed", stderr=PRIVATE_LOG)])
        self.assertEqual(summary["reason_code"], "os_preparation_failed")
        self.assertEqual(len(calls), 1)
        self.assertTrue(summary["partial_setup_possible"])
        self.assertIn(PRIVATE_LOG, (output / "bootstrap-stderr.log").read_bytes())
        self.assert_public_safe(summary)

    def test_installer_success_requires_integrity_metadata_and_no_execution_claims(self):
        changes = [lambda value: value.update(archive_sha256="not-a-hash"),
                   lambda value: value.update(archive_bytes=True),
                   lambda value: value["metadata_sha256"].update(build_version="a" * 64),
                   lambda value: value["evidence"].update(archive_integrity_verified=False),
                   lambda value: value["evidence"].update(engine_executed=True),
                   lambda value: value["evidence"].update(publisher_authenticity_verified=True),
                   lambda value: value["evidence"].update(cleanup_verified=True)]
        for change in changes:
            receipt = install_receipt()
            change(receipt)
            summary, _output, _calls = self.run_bootstrap(responses=[response("os"), response("engine", receipt=receipt)])
            self.assertEqual(summary["reason_code"], "install_receipt_invalid")
            self.assertFalse(summary["engine_installed"])
            self.assert_public_safe(summary)

    def test_transport_timeout_output_limit_and_untrusted_response_never_leak(self):
        cases = []
        for change, reason in (({"timed_out": True}, "transport_timeout"),
                               ({"over_limit": True}, "result_too_large"),
                               ({"stdout": b"", "returncode": 255}, "ssh_or_remote_failure"),
                               ({"stdout": PRIVATE_LOG}, "result_invalid")):
            item = response("os")
            item.update(change)
            cases.append((item, reason))
        item = response("os")
        body = json.loads(item["stdout"])
        body["hostname"] = PRIVATE_HOST
        item["stdout"] = json.dumps(body).encode()
        cases.append((item, "result_invalid"))
        for item, reason in cases:
            summary, _output, calls = self.run_bootstrap(responses=[item])
            self.assertEqual(summary["reason_code"], reason)
            self.assertEqual(len(calls), 1)
            self.assert_public_safe(summary)
        for error in (OSError("PRIVATE_EXCEPTION"), bootstrap.native.CoordinatorError("PRIVATE_EXCEPTION")):
            with patch.object(bootstrap.native, "bounded_transport", side_effect=error) as transport:
                summary, _output, _calls = self.run_bootstrap(transport=transport)
            self.assertEqual(summary["reason_code"], "controller_error")
            self.assert_public_safe(summary)

    def test_summary_validation_rejects_arbitrary_strings_extra_fields_and_false_claims(self):
        passed, _output, _calls = self.run_bootstrap()
        for change in ({"host": PRIVATE_HOST}, {"reason_code": PRIVATE_HOST}, {"status": [PRIVATE_HOST]},
                       {"cleanup_verified": True}, {"native_reader_contract_passed": True},
                       {"os_prepared": "true"}, {"unprivileged_install_verified": False}):
            summary = bootstrap.public_summary(passed | change)
            self.assertEqual(summary["reason_code"], "result_invalid")
            self.assert_public_safe(summary)
        self.assertEqual(bootstrap.public_summary(passed), passed)

    def test_existing_or_symlinked_output_is_not_overwritten(self):
        existing = self.root / "occupied"
        existing.mkdir()
        marker = existing / "unrelated.txt"
        marker.write_text("keep this", encoding="utf-8")
        link = self.root / "link"
        link.symlink_to(existing, target_is_directory=True)
        for path in (existing, link):
            with patch.object(bootstrap.native, "bounded_transport") as transport:
                summary = bootstrap.bootstrap("offline-fixture", str(path), self.environment, transport,
                                              clock=lambda: NOW)
            transport.assert_not_called()
            self.assertEqual(summary["reason_code"], "output_invalid")
        self.assertEqual(marker.read_text(), "keep this")


if __name__ == "__main__":
    unittest.main()
