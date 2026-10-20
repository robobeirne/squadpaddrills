"""Offline coordinator rejection tests; all native evidence here is synthetic.

No SSH connection, provider operation, Unreal build or native acceptance occurs.
Only two bounded transport checks launch local Python subprocesses.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Tools/Unreal"))
import remote_native_gate as gate

COMMIT = "a" * 40
RUN_ID = "gha-12345-1"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def synthetic_result():
    repo = gate.RUN_BASE + "/" + RUN_ID + "/repo"
    artifacts = {"build.log": b"SYNTHETIC build log; no UBT executed.\n",
                 "editor-stdout.log": b"SYNTHETIC editor stdout; no Unreal executed.\n",
                 "editor.log": b"SYNTHETIC editor log.\n",
                 "automation/index.json": b'{"synthetic_unit_fixture":true}\n'}
    hashes = {name: "b" * 64 for name in ("build_version", "build_wrapper", "editor", "unreal_build_tool")}
    receipt = {"schema": "squadpad.native-gate.v1", "run_id": RUN_ID,
               "project": repo + "/SquadPad3D.uproject",
               "output_directory": repo + "/Saved/SquadPad/NativeGate/" + RUN_ID,
               "git_before": {"commit": COMMIT, "dirty": False, "status_porcelain": ""},
               "git_after": {"commit": COMMIT, "dirty": False, "status_porcelain": ""},
               "status": "passed", "mode": "run",
               "evidence": {name: True for name in ("toolchain_verified", "editor_target_built",
                            "unreal_editor_executed", "native_reader_contract_passed")},
               "source_sha256_before": {"Source/example.cpp": "c" * 64},
               "source_sha256_after": {"Source/example.cpp": "c" * 64},
               "engine": {"root": gate.ENGINE_ROOT, "host": "Linux",
                          "files": {name: {"sha256": value} for name, value in hashes.items()}},
               "engine_sha256_after": hashes,
               "runtime_engine_identity": {"matches_observed_build_version": True},
               "phases": {phase: {"launched": True, "timed_out": False, "returncode": 0,
                           "log_sha256": digest(artifacts[name])}
                          for phase, name in (("build", "build.log"), ("editor", "editor-stdout.log"))},
               "output_sha256": {name: digest(value) for name, value in artifacts.items()},
               "automation": {"status": "passed", "test_path": gate.TEST_PATH, "test_count": 1,
                              "state": "Success", "report_sha256": digest(artifacts["automation/index.json"])}}
    artifacts["receipt.json"] = json.dumps(receipt).encode()
    return receipt, artifacts


def encoded_response(artifacts, **changes):
    result = {"schema": gate.TRANSPORT_SCHEMA, "commit": COMMIT, "run_id": RUN_ID,
              "stage": "completed", "gate_returncode": 0, "errors": [],
              "artifacts": {name: {"size_bytes": len(value), "sha256": digest(value),
                           "base64": base64.b64encode(value).decode()} for name, value in artifacts.items()}}
    result.update(changes)
    return json.dumps(result).encode()


class RemoteNativeGateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="squadpad-remote-gate-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        key = self.root / "operator-key"
        key.write_text("SYNTHETIC KEY; NOT USABLE", encoding="utf-8")
        key.chmod(0o600)
        known_hosts = self.root / "known-hosts"
        known_hosts.write_text("SYNTHETIC PIN; NOT USABLE", encoding="utf-8")
        self.environment = dict(zip(gate.ENV_NAMES.values(),
                                    ("worker.example.invalid", "worker_operator_319", str(key), str(known_hosts))))
        self.output = self.root / "evidence"

    def coordinate(self, data, returncode=0, timed_out=False):
        def transport(command, payload):
            self.assertEqual(command[-4:], ["python3", "-", COMMIT, RUN_ID])
            self.assertEqual(payload, gate.REMOTE_PROGRAM.encode())
            return {"stdout": data, "returncode": returncode, "timed_out": timed_out, "over_limit": False}
        return gate.coordinate(COMMIT, RUN_ID, self.output, self.environment, transport)

    def test_pass_requires_correlated_native_evidence_and_saves_only_allowlist(self):
        _, artifacts = synthetic_result()
        summary = self.coordinate(encoded_response(artifacts))
        self.assertEqual(summary["status"], "passed")
        self.assertEqual(summary["artifact_count"], 5)
        self.assertTrue(summary["native_reader_contract_passed"])
        self.assertFalse(summary["vm_cleanup_performed"])
        files = {str(path.relative_to(self.output)) for path in self.output.rglob("*") if path.is_file()}
        self.assertEqual(files, set(gate.FILE_LIMITS) | {"remote-summary.json"})
        for value in self.environment.values():
            self.assertNotIn(value, json.dumps(summary))

    def test_missing_config_fails_before_transport_with_saved_safe_summary(self):
        with patch.object(gate, "bounded_transport") as transport:
            summary = gate.coordinate(COMMIT, RUN_ID, self.output, {})
        transport.assert_not_called()
        self.assertEqual(summary["reason_code"], "config_invalid")
        self.assertEqual(json.loads((self.output / "remote-summary.json").read_text()), summary)

    def test_shell_values_and_ssh_credentials_are_rejected(self):
        for commit, run_id in (("main", RUN_ID), ("a" * 39 + ";", RUN_ID), (COMMIT, "a;id"),
                               (COMMIT, "../old"), (COMMIT, "-option"), (COMMIT, "a\nvalue")):
            with self.subTest(commit=commit, run_id=run_id), self.assertRaises(gate.CoordinatorError):
                gate.validate_identity(commit, run_id)
        for name, value in (("host", "-oProxyCommand=id"), ("host", "worker;id"),
                            ("user", "user@other"), ("known_hosts", "/tmp/pin\noption")):
            environment = dict(self.environment)
            environment[gate.ENV_NAMES[name]] = value
            with self.subTest(name=name, value=value), self.assertRaises(gate.CoordinatorError):
                gate.ssh_settings(environment)
        Path(self.environment[gate.ENV_NAMES["key"]]).chmod(0o644)
        with self.assertRaises(gate.CoordinatorError):
            gate.ssh_settings(self.environment)

    def test_ssh_pins_identity_and_disables_ambient_auth_and_forwarding(self):
        settings = gate.ssh_settings(self.environment)
        command = gate.ssh_command(settings, COMMIT, RUN_ID)
        for option in ("BatchMode=yes", "StrictHostKeyChecking=yes", "IdentitiesOnly=yes",
                       "IdentityAgent=none", "PasswordAuthentication=no", "KbdInteractiveAuthentication=no",
                       "ForwardAgent=no", "ClearAllForwardings=yes", "PermitLocalCommand=no",
                       "ControlMaster=no", "ControlPath=none"):
            self.assertIn(option, command)
        self.assertEqual(command[command.index("-F") + 1], "/dev/null")
        self.assertIn("UserKnownHostsFile=" + settings["known_hosts"], command)
        self.assertEqual(command[-5], settings["host"])

    def test_native_failure_preserves_receipt_and_logs_without_claiming_success(self):
        receipt, artifacts = synthetic_result()
        receipt["status"] = "failed"
        receipt["evidence"]["native_reader_contract_passed"] = False
        artifacts["receipt.json"] = json.dumps(receipt).encode()
        summary = self.coordinate(encoded_response(artifacts, gate_returncode=1), returncode=1)
        self.assertEqual(summary["reason_code"], "native_failed")
        self.assertFalse(summary["native_reader_contract_passed"])
        self.assertEqual((self.output / "receipt.json").read_bytes(), artifacts["receipt.json"])
        self.assertEqual((self.output / "build.log").read_bytes(), artifacts["build.log"])

    def test_zero_exit_never_substitutes_for_receipt_or_real_run(self):
        for mode, status in (("preflight", "preflight_passed"), ("run", "running")):
            receipt, artifacts = synthetic_result()
            receipt.update(mode=mode, status=status)
            artifacts["receipt.json"] = json.dumps(receipt).encode()
            with self.subTest(mode=mode), self.assertRaisesRegex(gate.CoordinatorError, "native_failed"):
                gate.validate_native_receipt(artifacts, COMMIT, RUN_ID)
        summary = self.coordinate(encoded_response({}))
        self.assertEqual(summary["reason_code"], "native_receipt_missing")

    def test_mismatched_commit_run_source_or_native_result_cannot_pass(self):
        alterations = (
            lambda receipt: receipt["git_after"].update(commit="d" * 40),
            lambda receipt: receipt["git_before"].update(dirty=True, status_porcelain=" M Source/example.cpp"),
            lambda receipt: receipt.update(run_id="gha-old-1"),
            lambda receipt: receipt["source_sha256_after"].update({"Source/example.cpp": "d" * 64}),
            lambda receipt: receipt["evidence"].update(toolchain_verified=False),
            lambda receipt: receipt["automation"].update(test_count=0),
            lambda receipt: receipt["automation"].update(test_path="Unrelated.Test"),
            lambda receipt: receipt["phases"]["editor"].update(timed_out=True),
            lambda receipt: receipt["engine_sha256_after"].update(editor="e" * 64),
            lambda receipt: receipt["runtime_engine_identity"].update(matches_observed_build_version=False),
        )
        for index, alter in enumerate(alterations):
            receipt, artifacts = synthetic_result()
            alter(receipt)
            artifacts["receipt.json"] = json.dumps(receipt).encode()
            with self.subTest(case=index), self.assertRaises(gate.CoordinatorError):
                gate.validate_native_receipt(artifacts, COMMIT, RUN_ID)

    def test_swapped_native_log_or_report_is_rejected(self):
        for name in ("build.log", "editor-stdout.log", "editor.log", "automation/index.json"):
            _, artifacts = synthetic_result()
            artifacts[name] += b"changed"
            with self.subTest(name=name), self.assertRaises(gate.CoordinatorError):
                gate.validate_native_receipt(artifacts, COMMIT, RUN_ID)

    def test_transport_identity_paths_sizes_hashes_and_duplicate_keys_are_checked(self):
        _, artifacts = synthetic_result()
        encoded = json.loads(encoded_response(artifacts))
        cases = []
        for field, value in (("commit", "f" * 40), ("run_id", "other-run")):
            changed = copy.deepcopy(encoded)
            changed[field] = value
            cases.append(json.dumps(changed).encode())
        for name in ("../key", "/etc/passwd", "engine.zip"):
            changed = copy.deepcopy(encoded)
            changed["artifacts"][name] = changed["artifacts"].pop("build.log")
            cases.append(json.dumps(changed).encode())
        for field, value in (("size_bytes", gate.FILE_LIMITS["build.log"] + 1),
                             ("sha256", "0" * 64), ("base64", "not valid base64!")):
            changed = copy.deepcopy(encoded)
            changed["artifacts"]["build.log"][field] = value
            cases.append(json.dumps(changed).encode())
        cases.append(b'{"schema":1,"schema":2}')
        for index, data in enumerate(cases):
            with self.subTest(case=index), self.assertRaises(gate.CoordinatorError):
                gate.decode_artifacts(data, COMMIT, RUN_ID)

    def test_transport_failure_cannot_pass_and_exceptions_do_not_expose_settings(self):
        _, artifacts = synthetic_result()
        summary = self.coordinate(encoded_response(artifacts), returncode=255)
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["artifact_count"], 5)
        second_output = self.root / "error-evidence"
        def transport(_command, _payload):
            raise OSError("secret connection details " + " ".join(self.environment.values()))
        summary = gate.coordinate(COMMIT, RUN_ID, second_output, self.environment, transport)
        self.assertEqual(summary["reason_code"], "controller_error")
        for value in self.environment.values():
            self.assertNotIn(value, json.dumps(summary))

    def test_existing_local_directory_is_never_reused(self):
        self.output.mkdir()
        sentinel = self.output / "receipt.json"
        sentinel.write_text("old evidence", encoding="utf-8")
        with patch.object(gate, "bounded_transport") as transport:
            summary = gate.coordinate(COMMIT, RUN_ID, self.output, self.environment)
        transport.assert_not_called()
        self.assertEqual(summary["reason_code"], "local_output_exists")
        self.assertEqual(sentinel.read_text(), "old evidence")

    def test_fixed_payload_compiles_for_python310_and_contains_no_connection_data(self):
        import ast
        ast.parse(gate.REMOTE_PROGRAM, feature_version=(3, 10))
        self.assertIn(gate.REPOSITORY, gate.REMOTE_PROGRAM)
        self.assertIn(gate.ENGINE_ROOT, gate.REMOTE_PROGRAM)
        self.assertIn('"--build-timeout-seconds", "1800"', gate.REMOTE_PROGRAM)
        self.assertIn('"--test-timeout-seconds", "600"', gate.REMOTE_PROGRAM)
        for value in self.environment.values():
            self.assertNotIn(value, gate.REMOTE_PROGRAM)

    def test_local_transport_caps_output_without_an_ssh_connection(self):
        command = [sys.executable, "-c", "import sys; sys.stdin.buffer.read(); sys.stdout.buffer.write(b'x'*4096)"]
        result = gate.bounded_transport(command, b"synthetic payload", timeout_seconds=5, maximum_bytes=1024)
        self.assertTrue(result["over_limit"])
        self.assertLessEqual(len(result["stdout"]), 1024)

    def test_local_transport_timeout_is_not_vm_cleanup(self):
        command = [sys.executable, "-c", "import sys,time; sys.stdin.buffer.read(); time.sleep(5)"]
        result = gate.bounded_transport(command, b"synthetic payload", timeout_seconds=0.1, maximum_bytes=1024)
        self.assertTrue(result["timed_out"])
        self.assertNotEqual(result["returncode"], 0)
        summary = self.coordinate(b"", returncode=-9, timed_out=True)
        self.assertEqual(summary["reason_code"], "transport_timeout")
        self.assertFalse(summary["vm_cleanup_performed"])


if __name__ == "__main__":
    unittest.main()
