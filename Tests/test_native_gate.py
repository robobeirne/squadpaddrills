"""Launcher/report tests only: synthetic fixtures never constitute Unreal evidence.

The only real subprocesses here are the local Python interpreter and its child.
No test runs UBT, Unreal, an engine download or a native-success gate end to end.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Tools/Unreal"))
import native_gate as gate


class NativeGateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="squadpad native gate tests ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project_root = self.root / "synthetic project"
        self.project_root.mkdir()
        self.project = self.project_root / "SquadPad3D.uproject"
        self.project.write_text(json.dumps({"EngineAssociation": "5.8"}))
        for name in gate.REQUIRED_INPUTS:
            path = self.project_root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("synthetic input used by launcher unit tests only\n")
        driver = self.project_root / "Tools/Unreal/native_gate.py"
        driver.parent.mkdir(parents=True)
        driver.write_text(Path(gate.__file__).read_text())
        patcher = mock.patch.object(gate, "__file__", str(driver))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.engine_root = self.root / "synthetic engine"
        self.version = {"MajorVersion": 5, "MinorVersion": 8, "PatchVersion": 0,
                        "Changelist": 12345, "BranchName": "synthetic-test-only"}
        self.engine_files = {
            "Engine/Build/Build.version": json.dumps(self.version).encode(),
            "Engine/Build/BatchFiles/Linux/Build.sh": b"synthetic wrapper; never execute\n",
            "Engine/Binaries/Linux/UnrealEditor": b"\x7fELFsynthetic binary header; never execute",
            "Engine/Binaries/DotNET/UnrealBuildTool/UnrealBuildTool.dll": b"synthetic UBT; never execute",
        }
        for name, data in self.engine_files.items():
            path = self.engine_root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            path.chmod(0o700)
        self.args = SimpleNamespace(project=self.project, engine_root=self.engine_root,
                                    run=False, run_id="synthetic-launcher-test",
                                    build_timeout_seconds=10, test_timeout_seconds=10)
        self.git = {"commit": "a" * 40, "dirty": False, "status_porcelain": ""}

    def report_document(self):
        return {"reportCreatedOn": datetime.now(timezone.utc).strftime("%Y.%m.%d-%H.%M.%S"),
                "succeeded": 1, "succeededWithWarnings": 0, "failed": 0, "notRun": 0, "inProcess": 0,
                "totalDuration": 0.001,
                "devices": [{"instance": "synthetic-instance", "platform": "LinuxEditor"}],
                "tests": [{"fullTestPath": gate.TEST, "state": "Success", "errors": 0,
                           "warnings": 0, "duration": 0.001, "entries": [],
                           "deviceInstance": ["synthetic-instance"]}]}

    def validate(self, document):
        path = self.root / "synthetic-index.json"
        path.write_text(json.dumps(document))
        now = time.time_ns()
        return gate.validate_report(path, now - 1_000_000_000, now + 1_000_000_000, "Linux")

    def phase(self, returncode=0):
        now = time.time_ns()
        return {"launched": True, "timed_out": False, "returncode": returncode,
                "started_epoch_ns": now, "finished_epoch_ns": now}

    def make_module(self):
        directory = self.project_root / "Binaries/Linux"
        directory.mkdir(parents=True)
        (directory / "UnrealEditor.modules").write_text(json.dumps({
            "BuildId": "synthetic-build-id", "Modules": {"SquadPadRuntime": "libUnrealEditor-SquadPadRuntime.so"}}))
        binary = directory / "libUnrealEditor-SquadPadRuntime.so"
        binary.write_bytes(b"synthetic module; never execute")
        return binary

    def execute(self):
        with mock.patch.object(gate, "git_identity", return_value=copy.deepcopy(self.git)), \
             mock.patch.object(gate.platform, "system", return_value="Linux"):
            return gate.execute_gate(self.args)

    def test_single_exact_fresh_structured_result_is_parseable(self):
        result = self.validate(self.report_document())
        self.assertEqual(result["test_path"], gate.TEST)
        self.assertEqual(result["test_count"], 1)
        self.assertEqual(result["state"], "Success")
        self.assertEqual(len(result["report_sha256"]), 64)
        # Parsing a synthetic fixture does not create a gate receipt or run Unreal.
        self.assertFalse((self.project_root / "Saved").exists())

    def test_wrong_zero_extra_skipped_or_unfinished_tests_are_rejected(self):
        cases = []
        for state in ("Skipped", "NotRun", "InProcess", "Fail", "Unknown"):
            document = self.report_document()
            document["tests"][0]["state"] = state
            cases.append(document)
        for tests in ([], [{"fullTestPath": "Wrong.Test", "state": "Success"}],
                      self.report_document()["tests"] * 2):
            document = self.report_document()
            document["tests"] = tests
            cases.append(document)
        for document in cases:
            with self.subTest(tests=document["tests"]), self.assertRaises(gate.GateError):
                self.validate(document)

    def test_success_warnings_are_preserved_and_inconsistent_warning_evidence_fails(self):
        document = self.report_document()
        document.update(succeeded=0, succeededWithWarnings=1)
        test = document["tests"][0]
        test["warnings"] = 2
        test["entries"] = [{"event": {"type": "Warning", "message": message}}
                           for message in ("First synthetic warning", "Second synthetic warning")]
        result = self.validate(document)
        self.assertEqual(result["warning_count"], 2)
        self.assertEqual(result["warning_messages"], ["First synthetic warning", "Second synthetic warning"])
        mutations = [
            lambda d: d.update(succeeded=1),
            lambda d: d.update(succeeded=1, succeededWithWarnings=0),
            lambda d: d["tests"][0].update(warnings=1),
            lambda d: d["tests"][0].update(warnings=True),
            lambda d: d["tests"][0]["entries"][0]["event"].pop("message"),
            lambda d: d["tests"][0]["entries"][0]["event"].update(type="Error"),
        ]
        for mutate in mutations:
            invalid = copy.deepcopy(document)
            mutate(invalid)
            with self.subTest(document=invalid), self.assertRaises(gate.GateError):
                self.validate(invalid)

    def test_inconsistent_missing_or_nonintegral_totals_are_rejected(self):
        for key in ("succeeded", "succeededWithWarnings", "failed", "notRun", "inProcess"):
            for value in (None, True, 1.0, -1, 2):
                document = self.report_document()
                if value is None:
                    del document[key]
                else:
                    document[key] = value
                with self.subTest(key=key, value=value), self.assertRaises(gate.GateError):
                    self.validate(document)
        document = self.report_document()
        document["skipped"] = 1
        with self.assertRaises(gate.GateError):
            self.validate(document)

    def test_error_events_invalid_durations_and_wrong_device_fail_even_with_success_state(self):
        mutations = [
            lambda d: d["tests"][0].update(errors=1),
            lambda d: d["tests"][0].update(warnings=1),
            lambda d: d["tests"][0].update(entries=[{"event": {"type": "Error"}}]),
            lambda d: d["tests"][0].update(entries=[{"event": {"type": "Warning"}}]),
            lambda d: d["tests"][0].update(entries=None),
            lambda d: d["tests"][0].update(duration=-1),
            lambda d: d.update(totalDuration=float("nan")),
            lambda d: d["devices"][0].update(platform="WindowsEditor"),
            lambda d: d["tests"][0].update(deviceInstance=["other-device"]),
        ]
        for mutate in mutations:
            document = self.report_document()
            mutate(document)
            with self.subTest(document=document), self.assertRaises(gate.GateError):
                self.validate(document)

    def test_old_internal_timestamp_fresh_copy_and_old_file_mtime_are_both_rejected(self):
        document = self.report_document()
        document["reportCreatedOn"] = "2000.01.01-12.00.00"
        with self.assertRaisesRegex(gate.GateError, "stale"):
            self.validate(document)
        path = self.root / "synthetic-index.json"
        path.write_text(json.dumps(self.report_document()))
        old = time.time_ns() - 3_600_000_000_000
        os.utime(path, ns=(old, old))
        now = time.time_ns()
        with self.assertRaisesRegex(gate.GateError, "stale"):
            gate.validate_report(path, now - 1_000_000_000, now, "Linux")
        document["reportCreatedOn"] = "2999.01.01-12.00.00"
        with self.assertRaisesRegex(gate.GateError, "future"):
            self.validate(document)

    def test_missing_malformed_and_duplicate_report_cannot_pass(self):
        path = self.root / "synthetic-index.json"
        with self.assertRaises(gate.GateError):
            gate.validate_report(path, 0, time.time_ns(), "Linux")
        for text in ('{"succeeded":1,"succeeded":0}', '{', '[]'):
            path.write_text(text)
            with self.subTest(text=text), self.assertRaises(gate.GateError):
                gate.validate_report(path, 0, time.time_ns(), "Linux")

    def test_preflight_never_launches_or_claims_native_or_toolchain_execution(self):
        with mock.patch.object(gate, "run_process") as process:
            report, path = self.execute()
        process.assert_not_called()
        self.assertEqual(report["status"], "preflight_passed")
        self.assertEqual(report["mode"], "preflight")
        self.assertTrue(all(value is False for value in report["evidence"].values()))
        self.assertEqual(report["source_sha256_before"], report["source_sha256_after"])
        self.assertIn("unverified", report["engine"]["toolchain_status"])
        self.assertTrue(path.is_file())
        command = report["commands"]["editor"]
        self.assertIn(f"-ExecCmds=Automation RunTest {gate.TEST};Quit", command)
        self.assertIn(str(self.project), command)
        self.assertNotIn("-game", command)
        self.assertFalse(any(arg.lower().startswith("-testexit") for arg in command))
        configuration = report["build_configuration"]
        self.assertEqual(configuration["status"], "absent_would_create_on_run")
        self.assertFalse(configuration["configured"])
        self.assertFalse(configuration["runtime_policy_verified"])
        self.assertFalse(Path(configuration["path"]).exists())
        self.assertEqual(len(configuration["expected_sha256"]), 64)

    def test_run_creates_then_reuses_exact_single_action_project_configuration(self):
        self.args.run = True
        path = self.project_root / "Saved/UnrealBuildTool/BuildConfiguration.xml"
        namespace = {"ue": "https://www.unrealengine.com/BuildConfiguration"}
        expected = {"MaxParallelActions": "1", "bAllowUBAExecutor": "false",
                    "bAllowUBALocalExecutor": "false", "bAllowXGE": "false",
                    "bAllowFASTBuild": "false", "bAllowSNDBS": "false"}

        def failed_build(*_args):
            # The real launcher must prepare this before invoking its build runner.
            configuration = ET.fromstring(path.read_bytes()).find("ue:BuildConfiguration", namespace)
            self.assertIsNotNone(configuration)
            actual = {child.tag.split("}")[-1]: child.text for child in configuration}
            self.assertEqual(actual, expected)
            return self.phase(6)  # Synthetic failure: never create native-success evidence.

        for index, expected_status in enumerate(("created", "reused_exact")):
            self.args.run_id = f"configuration-{index}"
            if index:
                os.utime(path, ns=(1_000_000_000, 1_000_000_000))
            with mock.patch.object(gate, "run_process", side_effect=failed_build) as process:
                report, receipt_path = self.execute()
            self.assertEqual(process.call_count, 1)
            self.assertEqual(report["status"], "failed")
            self.assertFalse(report["evidence"]["unreal_editor_executed"])
            self.assertFalse(report["evidence"]["native_reader_contract_passed"])
            record = report["build_configuration"]
            self.assertEqual(record["status"], expected_status)
            self.assertEqual(record["path"], str(path))
            self.assertTrue(record["configured"])
            self.assertFalse(record["runtime_policy_verified"])
            self.assertEqual(record["expected_sha256"], gate.digest(path))
            self.assertEqual(record["file"]["sha256"], record["sha256_after"])
            self.assertEqual(record["requested_policy"]["max_parallel_actions"], 1)
            self.assertEqual(json.loads(receipt_path.read_text())["build_configuration"], record)
            if index:
                self.assertEqual(path.stat().st_mtime_ns, 1_000_000_000)

    def test_conflicting_or_symlinked_project_configuration_stops_before_build(self):
        path = self.project_root / "Saved/UnrealBuildTool/BuildConfiguration.xml"
        path.parent.mkdir(parents=True)
        original = b"Existing user configuration must remain unchanged.\n"
        path.write_bytes(original)
        for run in (False, True):
            self.args.run = run
            self.args.run_id = f"conflicting-configuration-{run}"
            with mock.patch.object(gate, "run_process") as process:
                report, _ = self.execute()
            process.assert_not_called()
            self.assertIn("refusing to overwrite", report["failure"])
            self.assertEqual(report["build_configuration"]["status"], "conflict")
            self.assertFalse(report["build_configuration"]["configured"])
            self.assertFalse(report["evidence"]["unreal_editor_executed"])
            self.assertEqual(path.read_bytes(), original)
        path.unlink()
        outside = self.root / "outside.xml"
        outside.write_bytes(gate.BUILD_CONFIGURATION_XML)
        path.symlink_to(outside)
        self.args.run_id = "symlinked-configuration"
        with mock.patch.object(gate, "run_process") as process:
            report, _ = self.execute()
        process.assert_not_called()
        self.assertIn("symlink", report["failure"])
        self.assertEqual(outside.read_bytes(), gate.BUILD_CONFIGURATION_XML)
        self.assertFalse(report["evidence"]["native_reader_contract_passed"])

    def test_configuration_mutation_during_build_invalidates_receipt_before_editor(self):
        self.args.run = True
        path = self.project_root / "Saved/UnrealBuildTool/BuildConfiguration.xml"

        def changed_configuration(*_args):
            path.write_bytes(b"changed during synthetic build")
            return self.phase()

        with mock.patch.object(gate, "run_process", side_effect=changed_configuration) as process:
            report, _ = self.execute()
        self.assertEqual(process.call_count, 1)
        self.assertEqual(report["status"], "failed")
        self.assertIn("Project build configuration changed", report["failure"])
        record = report["build_configuration"]
        self.assertNotEqual(record["expected_sha256"], record["sha256_after"])
        self.assertFalse(record["runtime_policy_verified"])
        self.assertFalse(report["evidence"]["unreal_editor_executed"])
        self.assertFalse(report["evidence"]["native_reader_contract_passed"])

    def test_missing_engine_ubt_wrong_version_and_script_editor_stop_before_build(self):
        cases = ("Engine/Build/Build.version", "Engine/Binaries/DotNET/UnrealBuildTool/UnrealBuildTool.dll",
                 "Engine/Build/BatchFiles/Linux/Build.sh")
        for index, name in enumerate(cases):
            path = self.engine_root / name
            original = path.read_bytes()
            path.unlink()
            self.args.run_id = f"missing-{index}"
            with mock.patch.object(gate, "run_process") as process:
                report, _ = self.execute()
            process.assert_not_called()
            self.assertEqual(report["status"], "preflight_failed")
            path.write_bytes(original)
        version = self.engine_root / "Engine/Build/Build.version"
        version.write_text(json.dumps(dict(self.version, MinorVersion=7)))
        self.args.run_id = "wrong-version"
        with mock.patch.object(gate, "run_process") as process:
            report, _ = self.execute()
        process.assert_not_called()
        self.assertIn("does not match", report["failure"])
        version.write_text(json.dumps(self.version))
        (self.engine_root / "Engine/Binaries/Linux/UnrealEditor").write_bytes(b"#!/bin/sh\nexit 0\n")
        self.args.run_id = "script-editor"
        report, _ = self.execute()
        self.assertIn("script stand-ins", report["failure"])

    def test_failed_or_timed_out_build_never_starts_editor(self):
        self.args.run = True
        for index, result in enumerate((self.phase(6), dict(self.phase(-15), timed_out=True),
                                       dict(self.phase(), launched=False, launch_error="missing toolchain"))):
            self.args.run_id = f"failed-build-{index}"
            with mock.patch.object(gate, "run_process", return_value=result) as process:
                report, _ = self.execute()
            self.assertEqual(process.call_count, 1)
            self.assertEqual(report["status"], "failed")
            self.assertFalse(report["evidence"]["toolchain_verified"])
            self.assertFalse(report["evidence"]["unreal_editor_executed"])
            self.assertFalse(report["evidence"]["native_reader_contract_passed"])

    def test_editor_failure_or_no_report_cannot_become_native_success(self):
        self.args.run = True
        self.make_module()
        for index, editor_phase in enumerate((self.phase(1), self.phase(0))):
            self.args.run_id = f"editor-without-result-{index}"
            with mock.patch.object(gate, "run_process", side_effect=[self.phase(), editor_phase]) as process:
                report, _ = self.execute()
            self.assertEqual(process.call_count, 2)
            self.assertEqual(report["status"], "failed")
            self.assertFalse(report["evidence"]["native_reader_contract_passed"])
            self.assertIsNone(report["automation"])

    def test_missing_build_product_blocks_editor_and_old_valid_product_is_allowed(self):
        self.args.run = True
        with mock.patch.object(gate, "run_process", return_value=self.phase()) as process:
            report, _ = self.execute()
        self.assertEqual(process.call_count, 1)
        self.assertEqual(report["status"], "failed")
        self.assertFalse(report["evidence"]["editor_target_built"])
        binary = self.make_module()
        os.utime(binary, (1, 1))
        product = gate.built_module(self.project, "Linux")
        self.assertEqual(product["binary"]["sha256"], gate.digest(binary))

    def test_source_or_commit_changes_invalidate_result_and_run_id_is_never_reused(self):
        self.args.run = True

        def changed_source(*_args):
            (self.project_root / gate.REQUIRED_INPUTS[0]).write_text("changed during synthetic build")
            return self.phase(1)

        with mock.patch.object(gate, "run_process", side_effect=changed_source):
            report, _ = self.execute()
        self.assertIn("Source inputs changed", report["failure"])
        self.assertFalse(report["evidence"]["native_reader_contract_passed"])
        with self.assertRaisesRegex(gate.GateError, "already exists"):
            self.execute()
        self.args.run = False
        self.args.run_id = "changed-commit"
        with mock.patch.object(gate, "git_identity", side_effect=[self.git, dict(self.git, commit="b" * 40)]), \
             mock.patch.object(gate.platform, "system", return_value="Linux"):
            report, _ = gate.execute_gate(self.args)
        self.assertEqual(report["status"], "preflight_failed")
        self.assertIn("Source commit changed", report["failure"])

    def test_engine_identity_must_be_observed_and_match_major_minor_patch_and_changelist(self):
        engine = {"build_version": self.version}
        output = self.root / "identity"
        output.mkdir()
        with self.assertRaises(gate.GateError):
            gate.runtime_engine_identity(output, engine)
        path = output / "editor.log"
        for version in ("5.8.0-999", "5.7.0-12345", "5.8.1-12345"):
            path.write_text(f"LogInit: Engine Version: {version}+synthetic\n")
            with self.subTest(version=version), self.assertRaises(gate.GateError):
                gate.runtime_engine_identity(output, engine)
        path.write_text("LogInit: Engine Version: 5.8.0-12345+synthetic\n")
        self.assertTrue(gate.runtime_engine_identity(output, engine)["matches_observed_build_version"])

    def test_real_python_subprocess_preserves_literal_arguments_and_logs_failure(self):
        if os.name != "posix":
            with self.assertRaises(gate.GateError):
                gate.run_process([sys.executable, "-c", "pass"], self.root, self.root / "unsupported.log", 1)
            return
        literal = "space ; $(not-a-shell) & text"
        result = gate.run_process([sys.executable, "-c", "import sys; print(sys.argv[1]); sys.exit(7)", literal],
                                  self.root, self.root / "python process.log", 10)
        self.assertTrue(result["launched"])
        self.assertEqual(result["returncode"], 7)
        self.assertEqual((self.root / "python process.log").read_text().strip(), literal)
        with self.assertRaisesRegex(gate.GateError, "exit code 7"):
            gate.require_process_success(result, "synthetic Python process")

    def test_real_process_timeout_kills_child_process_group(self):
        if os.name != "posix":
            with self.assertRaises(gate.GateError):
                gate.run_process([sys.executable, "-c", "pass"], self.root, self.root / "unsupported.log", 1)
            return
        metadata_path = self.root / "timeout-child.json"
        self.addCleanup(self.stop_sleeper, metadata_path)
        result = gate.run_process([sys.executable, "-c", self.sleeper_program(), str(metadata_path), "sleep"], self.root,
                                  self.root / "timeout.log", 1)
        self.assertTrue(result["timed_out"])
        self.assertLess(result["duration_seconds"], 8)
        self.assert_child_stopped(json.loads(metadata_path.read_text()))

    def test_real_parent_exit_stops_remaining_child_group_and_preserves_returncode(self):
        if os.name != "posix":
            with self.assertRaises(gate.GateError):
                gate.run_process([sys.executable, "-c", "pass"], self.root, self.root / "unsupported.log", 1)
            return
        for returncode in (0, 7):
            with self.subTest(returncode=returncode):
                metadata_path = self.root / f"orphan-{returncode}.json"
                self.addCleanup(self.stop_sleeper, metadata_path)
                result = gate.run_process([sys.executable, "-c", self.sleeper_program(), str(metadata_path), str(returncode)],
                                          self.root, self.root / f"orphan-{returncode}.log", 10)
                self.assertEqual(result["returncode"], returncode)
                self.assertFalse(result["timed_out"])
                self.assert_child_stopped(json.loads(metadata_path.read_text()))

    def test_posix_hangup_interrupts_gate_and_restores_both_signal_handlers(self):
        if os.name != "posix":
            self.assertFalse(hasattr(signal, "SIGHUP"))
            return
        previous = {signum: signal.getsignal(signum) for signum in (signal.SIGTERM, signal.SIGHUP)}

        def hangup(_args):
            self.assertIs(signal.getsignal(signal.SIGHUP), signal.getsignal(signal.SIGTERM))
            os.kill(os.getpid(), signal.SIGHUP)
            self.fail("SIGHUP did not interrupt the gate")

        with mock.patch.object(gate, "execute_gate", side_effect=hangup), \
             mock.patch.object(sys, "stderr", new_callable=io.StringIO) as error:
            returncode = gate.main(["--engine-root", str(self.engine_root), "--project", str(self.project)])
        self.assertEqual(returncode, 1)
        self.assertIn("Interrupted", error.getvalue())
        self.assertEqual({signum: signal.getsignal(signum) for signum in previous}, previous)

    def sleeper_program(self):
        # A sandbox's /proc mount can use an outer PID namespace. The sleeper
        # records its own proc identity, rather than guessing /proc/<Popen.pid>.
        child_program = (
            "import json, os, sys, time\n"
            "from pathlib import Path\n"
            "data = {'pid': os.getpid(), 'pgid': os.getpgrp(), 'expected_pgid': int(sys.argv[2])}\n"
            "if Path('/proc/self/stat').is_file():\n"
            "    raw = Path('/proc/self/stat').read_text()\n"
            "    data.update(proc_pid=int(raw.split()[0]), proc_start=raw.split(') ', 1)[1].split()[19])\n"
            "path = Path(sys.argv[1])\n"
            "temporary = path.with_suffix('.tmp')\n"
            "temporary.write_text(json.dumps(data))\n"
            "temporary.replace(path)\n"
            "time.sleep(60)\n")
        return (
            "import os, subprocess, sys, time\n"
            "from pathlib import Path\n"
            f"child = subprocess.Popen([sys.executable, '-c', {child_program!r}, sys.argv[1], str(os.getpgrp())])\n"
            "Path(sys.argv[1] + '.pid').write_text(str(child.pid))\n"
            "deadline = time.monotonic() + 5\n"
            "while not Path(sys.argv[1]).is_file():\n"
            "    if time.monotonic() > deadline: raise RuntimeError('sleeper did not start')\n"
            "    time.sleep(0.005)\n"
            "if sys.argv[2] == 'sleep': time.sleep(60)\n"
            "else: sys.exit(int(sys.argv[2]))\n")

    def stop_sleeper(self, metadata_path):
        # Runs even if an assertion fails, without trusting any outer /proc PID.
        pidfile = Path(str(metadata_path) + ".pid")
        if pidfile.is_file():
            try:
                os.kill(int(pidfile.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass

    def assert_child_stopped(self, child):
        self.assertEqual(child["pgid"], child["expected_pgid"], "Sleeper left the parent's process group")
        if "proc_pid" in child:
            proc_status = Path(f"/proc/{child['proc_pid']}/stat")
            deadline = time.monotonic() + 2
            while True:
                try:
                    fields = proc_status.read_text().split(") ", 1)[1].split()
                except FileNotFoundError:
                    break
                if fields[19] != child["proc_start"]:
                    break  # The original child exited and its outer PID was reused.
                # SIGKILL delivery is asynchronous. Dead children may await the host's reaper.
                if fields[0] in ("Z", "X"):
                    break
                self.assertLess(time.monotonic(), deadline, "Completed process left a child running")
                time.sleep(0.01)
        else:
            deadline = time.monotonic() + 2
            while True:
                try:
                    os.kill(child["pid"], 0)
                except ProcessLookupError:
                    break
                self.assertLess(time.monotonic(), deadline, "Child remains present on this host")
                time.sleep(0.01)


if __name__ == "__main__":
    unittest.main(verbosity=2)
