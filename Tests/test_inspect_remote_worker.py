"""Offline worker-inspection tests. No SSH, provider API or Unreal is used."""
from __future__ import annotations

import copy
import io
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
from types import SimpleNamespace
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Tools/Unreal"))
import inspect_remote_worker as inspection

PRIVATE_MARKER = "private-worker-name.example.invalid"


def observation():
    gib = 1024**3
    return {
        "schema": inspection.TRANSPORT_SCHEMA, "root_session": True,
        "system": {
            "os": "ubuntu", "os_version": "22.04", "architecture": "x86_64",
            "logical_cpus": 4, "memory_total_bytes": 8*gib,
            "memory_available_bytes": 7*gib, "swap_total_bytes": 0, "swap_free_bytes": 0,
            "root_total_bytes": 160*gib, "root_available_bytes": 150*gib,
        },
    }


class InspectRemoteWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="squadpad-inspection-test-")
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        key = root / "identity"
        key.write_text("SYNTHETIC KEY, NOT USABLE", encoding="utf-8")
        key.chmod(0o600)
        known_hosts = root / "known_hosts"
        known_hosts.write_text("SYNTHETIC PIN, NOT USABLE", encoding="utf-8")
        self.environment = {
            "SQUADPAD_REMOTE_SSH_HOST": PRIVATE_MARKER,
            "SQUADPAD_REMOTE_SSH_KEY_FILE": str(key),
            "SQUADPAD_REMOTE_SSH_KNOWN_HOSTS_FILE": str(known_hosts),
        }

    def inspect(self, data=None, **response_changes):
        response = {"stdout": json.dumps(observation()).encode() if data is None else data,
                    "returncode": 0, "timed_out": False, "over_limit": False}
        response.update(response_changes)

        def transport(command, payload, **limits):
            self.assertEqual(command[-5:], ["python3", "-I", "-B", "-S", "-"])
            self.assertEqual(payload, inspection.REMOTE_PROGRAM.encode())
            self.assertEqual(limits, {"timeout_seconds": 45, "maximum_bytes": 8192})
            return response

        return inspection.inspect_worker(self.environment, transport)

    def assert_private_fields_absent(self, summary):
        encoded = json.dumps(summary)
        for value in self.environment.values():
            self.assertNotIn(value, encoded)
        self.assertNotIn("PRIVATE_RESPONSE_TEXT", encoded)
        self.assertEqual(set(summary), set(inspection.empty_summary()))
        for flag in ("server_setup_performed", "unreal_executed", "native_reader_contract_passed", "cleanup_verified"):
            self.assertIs(summary[flag], False)

    def test_success_verifies_only_pinned_access_and_allowlisted_resources(self):
        summary = self.inspect()
        self.assertEqual(summary["status"], "passed")
        self.assertEqual(summary["reason_code"], "inspection_passed")
        self.assertTrue(summary["ssh_access_verified"])
        self.assertEqual(summary["system"], observation()["system"])
        self.assert_private_fields_absent(summary)

    def test_missing_config_and_invalid_key_mode_stop_before_transport(self):
        for environment in ({}, dict(self.environment)):
            if environment:
                Path(environment["SQUADPAD_REMOTE_SSH_KEY_FILE"]).chmod(0o644)
            with patch.object(inspection.native, "bounded_transport") as transport:
                summary = inspection.inspect_worker(environment)
            transport.assert_not_called()
            self.assertEqual(summary["reason_code"], "config_invalid")
            self.assert_private_fields_absent(summary)

    def test_fixed_root_command_requires_trust_and_disables_alternate_auth(self):
        environment = dict(self.environment, SQUADPAD_REMOTE_SSH_USER="untrusted;command")
        captured = []

        def transport(command, _payload, **_limits):
            captured.append(command)
            return {"stdout": json.dumps(observation()).encode(), "returncode": 0,
                    "timed_out": False, "over_limit": False}

        self.assertEqual(inspection.inspect_worker(environment, transport)["status"], "passed")
        command = captured[0]
        self.assertEqual(command[command.index("-l") + 1], "root")
        self.assertEqual(command[command.index("-F") + 1], "/dev/null")
        self.assertEqual(command[command.index("-p") + 1], "22")
        self.assertEqual(command[-5:], ["python3", "-I", "-B", "-S", "-"])
        for option in ("StrictHostKeyChecking=yes", "HostKeyAlgorithms=ssh-ed25519",
                       "IdentitiesOnly=yes", "IdentityAgent=none", "UpdateHostKeys=no",
                       "PasswordAuthentication=no", "KbdInteractiveAuthentication=no",
                       "GlobalKnownHostsFile=/dev/null", "ForwardAgent=no", "ForwardX11=no",
                       "ClearAllForwardings=yes", "PermitLocalCommand=no", "ProxyCommand=none",
                       "ProxyJump=none", "ConnectTimeout=10", "ConnectionAttempts=1",
                       "ControlMaster=no", "ControlPath=none"):
            self.assertIn(option, command)
        self.assertIn("UserKnownHostsFile=" + self.environment["SQUADPAD_REMOTE_SSH_KNOWN_HOSTS_FILE"], command)
        self.assertNotIn(environment["SQUADPAD_REMOTE_SSH_USER"], command)

    def test_private_extra_fields_arbitrary_strings_and_invalid_measurements_are_rejected(self):
        cases = []
        extra = observation()
        extra["hostname"] = PRIVATE_MARKER
        cases.append(extra)
        for field, value in (("hostname", PRIVATE_MARKER), ("os", PRIVATE_MARKER),
                             ("os_version", PRIVATE_MARKER), ("architecture", PRIVATE_MARKER),
                             ("logical_cpus", True), ("memory_total_bytes", -1),
                             ("swap_total_bytes", 2**63), ("swap_free_bytes", 1),
                             ("root_available_bytes", 2**63-1)):
            changed = observation()
            changed["system"][field] = value
            cases.append(changed)
        encoded = [json.dumps(value).encode() for value in cases]
        encoded += [b'{"schema":1,"schema":2}', b'{"system":NaN}',
                    b'PRIVATE_RESPONSE_TEXT', b'[]', b'\xff']
        for value in encoded:
            with self.subTest(value=value[:80]):
                summary = self.inspect(value)
                self.assertEqual(summary["status"], "failed")
                self.assertEqual(summary["reason_code"], "result_invalid")
                self.assert_private_fields_absent(summary)

    def test_transport_failures_do_not_copy_output_or_establish_access(self):
        for changes, reason in (({"returncode": 255}, "ssh_or_remote_failure"),
                                ({"timed_out": True}, "transport_timeout"),
                                ({"over_limit": True}, "result_too_large")):
            with self.subTest(reason=reason):
                summary = self.inspect(b"PRIVATE_RESPONSE_TEXT " + PRIVATE_MARKER.encode(), **changes)
                self.assertEqual(summary["reason_code"], reason)
                self.assertFalse(summary["ssh_access_verified"])
                self.assert_private_fields_absent(summary)
        wrong_account = observation()
        wrong_account["root_session"] = False
        self.assertEqual(self.inspect(json.dumps(wrong_account).encode())["reason_code"], "unexpected_remote_user")

    def test_exception_messages_are_never_public(self):
        for error in (OSError(PRIVATE_MARKER), inspection.native.CoordinatorError(PRIVATE_MARKER)):
            with patch.object(inspection.native, "bounded_transport", side_effect=error):
                summary = inspection.inspect_worker(self.environment)
            self.assertEqual(summary["reason_code"], "controller_error")
            self.assert_private_fields_absent(summary)

    def test_workflow_summary_revalidates_files_and_refuses_false_claims(self):
        passed = self.inspect()
        changes = ({"host": PRIVATE_MARKER}, {"reason_code": PRIVATE_MARKER},
                   {"native_reader_contract_passed": True}, {"cleanup_verified": True},
                   {"status": [PRIVATE_MARKER]}, {"ssh_access_verified": "true"})
        for change in changes:
            report = copy.deepcopy(passed)
            report.update(change)
            summary = inspection.public_summary(report)
            self.assertEqual(summary["reason_code"], "result_invalid")
            self.assert_private_fields_absent(summary)
        self.assertEqual(inspection.public_summary(passed), passed)

    def test_fixed_remote_program_reads_resources_and_normalizes_unknown_strings(self):
        memory = "MemTotal: 8192 kB\nMemAvailable: 4096 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n"
        output = io.StringIO()
        with patch.object(platform, "freedesktop_os_release", return_value={"ID": PRIVATE_MARKER, "VERSION_ID": PRIVATE_MARKER}), \
             patch.object(platform, "machine", return_value=PRIVATE_MARKER), \
             patch.object(Path, "read_text", autospec=True, return_value=memory) as read, \
             patch.object(os, "statvfs", return_value=SimpleNamespace(f_blocks=100, f_frsize=4096, f_bavail=80)) as disk, \
             patch.object(os, "cpu_count", return_value=4), \
             patch.object(os, "geteuid", return_value=0), redirect_stdout(output):
            exec(compile(inspection.REMOTE_PROGRAM, "fixed-inspection-payload", "exec"), {"__name__": "__main__"})
        read.assert_called_once_with(Path("/proc/meminfo"), encoding="ascii")
        disk.assert_called_once_with("/")
        summary = self.inspect(output.getvalue().encode())
        self.assertEqual(summary["status"], "passed")
        self.assertEqual(summary["system"]["os"], "other")
        self.assertEqual(summary["system"]["os_version"], "other")
        self.assertEqual(summary["system"]["architecture"], "other")
        self.assert_private_fields_absent(summary)


if __name__ == "__main__":
    unittest.main()
