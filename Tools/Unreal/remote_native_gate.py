"""Coordinate one source-only native Unreal gate on an operator-provided worker.

This does not provision, register, stop or delete a machine. A controller timeout
stops its local SSH process; the provider must separately enforce VM deletion.
Only a fixed public repository, exact commit and five bounded result files are
supported. SSH connection settings never enter the summary or console output.
Requires Python 3.10+ and OpenSSH on the Linux GitHub-hosted controller/worker.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import time

SCHEMA = "squadpad.remote-native-gate.v1"
TRANSPORT_SCHEMA = "squadpad.remote-native-transport.v1"
REPOSITORY = "https://github.com/robobeirne/squadpaddrills.git"
ENGINE_ROOT = "/opt/unreal/UE_5.8"
RUN_BASE = "/srv/squadpad/runs"
TEST_PATH = "SquadPad.Runtime.PlaybackReader.Contract"
CONTROLLER_TIMEOUT_SECONDS = 2700
FILE_LIMITS = {
    "receipt.json": 1024 * 1024,
    "build.log": 8 * 1024 * 1024,
    "editor-stdout.log": 8 * 1024 * 1024,
    "editor.log": 8 * 1024 * 1024,
    "automation/index.json": 10 * 1024 * 1024,
}
MAX_WIRE_BYTES = 4 * ((sum(FILE_LIMITS.values()) + 2) // 3) + 65536
ENV_NAMES = {
    "host": "SQUADPAD_REMOTE_SSH_HOST",
    "user": "SQUADPAD_REMOTE_SSH_USER",
    "key": "SQUADPAD_REMOTE_SSH_KEY_FILE",
    "known_hosts": "SQUADPAD_REMOTE_SSH_KNOWN_HOSTS_FILE",
}

# The only variable remote-shell arguments are validated commit/run ID tokens.
# All program logic travels as this fixed Python program over SSH stdin.
REMOTE_PROGRAM = r'''
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

REPOSITORY = "https://github.com/robobeirne/squadpaddrills.git"
ENGINE_ROOT = "/opt/unreal/UE_5.8"
RUN_BASE = Path("/srv/squadpad/runs")
FILE_LIMITS = {
    "receipt.json": 1024 * 1024,
    "build.log": 8 * 1024 * 1024,
    "editor-stdout.log": 8 * 1024 * 1024,
    "editor.log": 8 * 1024 * 1024,
    "automation/index.json": 10 * 1024 * 1024,
}
commit, run_id = sys.argv[1:]
if re.fullmatch(r"[0-9a-f]{40}", commit) is None or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", run_id) is None:
    sys.exit(2)
result = {"schema": "squadpad.remote-native-transport.v1", "commit": commit,
          "run_id": run_id, "stage": "setup", "gate_returncode": None,
          "errors": [], "artifacts": {}}
run_dir = RUN_BASE / run_id
repo = run_dir / "repo"
output = repo / "Saved/SquadPad/NativeGate" / run_id
deadline = time.monotonic() + 2620
environment = os.environ.copy()
environment.update(GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1",
                   GIT_CONFIG_GLOBAL="/dev/null", TZ="UTC")
git_prefix = ["git", "-c", "credential.helper=", "-c", "core.hooksPath=/dev/null",
              "-c", "core.fsmonitor=false"]

def command(argv, timeout, capture=False):
    return subprocess.run(argv, cwd=run_dir, env=environment,
                          stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, timeout=timeout, check=True)

try:
    if not RUN_BASE.is_dir() or RUN_BASE.is_symlink() or RUN_BASE.resolve() != RUN_BASE:
        raise ValueError("invalid_run_base")
    # mkdir without exist_ok is the run-reuse/stale-evidence boundary.
    run_dir.mkdir(mode=0o700)
    result["stage"] = "checkout"
    command(git_prefix + ["init", "--template=", str(repo)], 15)
    command(git_prefix + ["-C", str(repo), "remote", "add", "origin", REPOSITORY], 15)
    command(git_prefix + ["-C", str(repo), "fetch", "--depth=1", "origin", commit], 180)
    command(git_prefix + ["-C", str(repo), "checkout", "--detach", "FETCH_HEAD"], 30)
    observed = command(git_prefix + ["-C", str(repo), "rev-parse", "HEAD"], 15, True)
    if observed.stdout.decode("ascii").strip() != commit:
        raise ValueError("checkout_commit_mismatch")
    result["stage"] = "gate"
    argv = [sys.executable, str(repo / "Tools/Unreal/native_gate.py"),
            "--project", str(repo / "SquadPad3D.uproject"),
            "--engine-root", ENGINE_ROOT, "--run", "--run-id", run_id,
            "--build-timeout-seconds", "1800", "--test-timeout-seconds", "600"]
    gate = subprocess.Popen(argv, cwd=repo, env=environment, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)
    try:
        result["gate_returncode"] = gate.wait(timeout=max(1, min(2500, deadline - time.monotonic())))
    except subprocess.TimeoutExpired:
        # Give native_gate.py a chance to stop its process groups and write a
        # failure receipt. This remains best effort, not provider VM cleanup.
        gate.send_signal(signal.SIGTERM)
        try:
            gate.wait(timeout=10)
        except subprocess.TimeoutExpired:
            gate.kill()
            gate.wait(timeout=5)
        result["gate_returncode"] = gate.returncode
        raise
    result["stage"] = "completed"
except FileExistsError:
    result["errors"].append("run_exists")
except subprocess.TimeoutExpired:
    result["errors"].append("remote_command_timeout")
except (OSError, ValueError, subprocess.SubprocessError):
    result["errors"].append("remote_command_failed")

# Never retrieve a previous run when creating this run failed.
if result["stage"] in ("gate", "completed"):
    for name, limit in FILE_LIMITS.items():
        path = output / name
        try:
            if not path.exists() and not path.is_symlink():
                continue
            if path.is_symlink() or not path.is_file() or path.resolve() != path:
                raise ValueError("invalid_artifact_path")
            if path.stat().st_size > limit:
                raise ValueError("artifact_too_large")
            with path.open("rb") as source:
                data = source.read(limit + 1)
            if len(data) > limit:
                raise ValueError("artifact_too_large")
            result["artifacts"][name] = {"size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "base64": base64.b64encode(data).decode("ascii")}
        except (OSError, ValueError):
            result["errors"].append("artifact_rejected")
print(json.dumps(result, separators=(",", ":"), allow_nan=False), flush=True)
sys.exit(0 if result["stage"] == "completed" and result["gate_returncode"] == 0 and not result["errors"] else 1)
'''


class CoordinatorError(RuntimeError):
    """Only fixed, non-sensitive reason codes may be used as messages."""


def require(condition, reason):
    if not condition:
        raise CoordinatorError(reason)


def validate_identity(commit, run_id):
    require(isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}", commit) is not None,
            "identity_invalid")
    require(isinstance(run_id, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", run_id) is not None,
            "identity_invalid")


def ssh_settings(environment):
    values = {key: environment.get(name, "") for key, name in ENV_NAMES.items()}
    require(all(isinstance(value, str) and value for value in values.values()), "config_invalid")
    host = values["host"]
    try:
        ipaddress.ip_address(host)
    except ValueError:
        require(len(host) <= 253 and all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                                        for label in host.split(".")), "config_invalid")
    require(re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", values["user"]) is not None, "config_invalid")
    for key in ("key", "known_hosts"):
        value = values[key]
        require(not any(char.isspace() for char in value) and "\x00" not in value, "config_invalid")
        path = Path(value)
        require(path.is_absolute() and path.is_file() and not path.is_symlink()
                and path.resolve() == path, "config_invalid")
        info = path.stat()
        require(stat.S_ISREG(info.st_mode) and info.st_size > 0, "config_invalid")
        if key == "key":
            require(info.st_mode & 0o077 == 0, "config_invalid")
    return values


def ssh_command(settings, commit, run_id):
    validate_identity(commit, run_id)
    options = ["BatchMode=yes", "StrictHostKeyChecking=yes",
               "UserKnownHostsFile=" + settings["known_hosts"], "GlobalKnownHostsFile=/dev/null",
               "IdentitiesOnly=yes", "IdentityAgent=none", "AddKeysToAgent=no",
               "PasswordAuthentication=no", "KbdInteractiveAuthentication=no",
               "PreferredAuthentications=publickey", "HostbasedAuthentication=no",
               "GSSAPIAuthentication=no", "ForwardAgent=no", "ForwardX11=no",
               "ClearAllForwardings=yes", "PermitLocalCommand=no",
               "ControlMaster=no", "ControlPath=none", "ProxyCommand=none", "ProxyJump=none",
               "ConnectTimeout=15", "ConnectionAttempts=1",
               "ServerAliveInterval=15", "ServerAliveCountMax=2", "LogLevel=ERROR"]
    command = ["ssh", "-F", "/dev/null", "-T", "-p", "22"]
    for value in options:
        command.extend(["-o", value])
    command.extend(["-i", settings["key"], "-l", settings["user"], "--", settings["host"],
                    "python3", "-", commit, run_id])
    return command


def stop_local_process(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=5)


def bounded_transport(command, payload, timeout_seconds=CONTROLLER_TIMEOUT_SECONDS,
                      maximum_bytes=MAX_WIRE_BYTES):
    """Bound stdin/write, stdout/read and runtime; never print SSH diagnostics.

    Killing this local process is not remote VM cleanup and cannot establish that
    all remote compiler/editor descendants stopped. Provider cleanup is separate.
    """
    require(os.name == "posix", "controller_platform_unsupported")
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, start_new_session=True,
                               env={"PATH": os.defpath, "LANG": "C"})
    collected = bytearray()
    timed_out = False
    over_limit = False
    deadline = time.monotonic() + timeout_seconds
    offset = 0
    try:
        with selectors.DefaultSelector() as selector:
            os.set_blocking(process.stdin.fileno(), False)
            os.set_blocking(process.stdout.fileno(), False)
            selector.register(process.stdin, selectors.EVENT_WRITE)
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map() or process.poll() is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                for key, event in selector.select(min(remaining, 1.0)):
                    if event & selectors.EVENT_WRITE:
                        try:
                            offset += os.write(key.fileobj.fileno(), payload[offset:offset + 65536])
                        except BrokenPipeError:
                            offset = len(payload)
                        if offset == len(payload):
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
                    elif event & selectors.EVENT_READ:
                        data = os.read(key.fileobj.fileno(), 65536)
                        if not data:
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
                        elif len(collected) + len(data) > maximum_bytes:
                            over_limit = True
                            break
                        else:
                            collected.extend(data)
                if over_limit:
                    break
        if timed_out or over_limit:
            stop_local_process(process)
        else:
            process.wait(timeout=max(0.1, deadline - time.monotonic()))
        return {"stdout": bytes(collected), "returncode": process.returncode,
                "timed_out": timed_out, "over_limit": over_limit}
    finally:
        if process.poll() is None:
            stop_local_process(process)
        for stream in (process.stdin, process.stdout):
            if not stream.closed:
                stream.close()


def strict_json(data):
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "result_invalid")
            result[key] = value
        return result

    def invalid_number(_value):
        raise CoordinatorError("result_invalid")

    try:
        return json.loads(data.decode("utf-8-sig"), object_pairs_hook=object_pairs,
                          parse_constant=invalid_number)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise CoordinatorError("result_invalid") from error


def decode_artifacts(data, commit, run_id):
    require(len(data) <= MAX_WIRE_BYTES, "result_too_large")
    result = strict_json(data)
    require(isinstance(result, dict) and result.get("schema") == TRANSPORT_SCHEMA, "result_invalid")
    require(result.get("commit") == commit and result.get("run_id") == run_id, "result_identity_mismatch")
    require(result.get("stage") in ("setup", "checkout", "gate", "completed"), "result_invalid")
    require(result.get("gate_returncode") is None or type(result.get("gate_returncode")) is int, "result_invalid")
    errors = result.get("errors")
    require(isinstance(errors, list) and len(errors) <= 10 and all(value in
            ("run_exists", "remote_command_timeout", "remote_command_failed", "artifact_rejected")
            for value in errors), "result_invalid")
    encoded = result.get("artifacts")
    require(isinstance(encoded, dict) and set(encoded).issubset(FILE_LIMITS), "artifact_name_rejected")
    artifacts = {}
    for name, record in encoded.items():
        require(isinstance(record, dict) and set(record) == {"size_bytes", "sha256", "base64"}, "artifact_invalid")
        size = record.get("size_bytes")
        require(type(size) is int and 0 <= size <= FILE_LIMITS[name], "artifact_too_large")
        text = record.get("base64")
        require(isinstance(text, str) and len(text) <= 4 * ((FILE_LIMITS[name] + 2) // 3), "artifact_too_large")
        try:
            raw = base64.b64decode(text, validate=True)
        except (ValueError, binascii.Error) as error:
            raise CoordinatorError("artifact_invalid") from error
        require(len(raw) == size and hashlib.sha256(raw).hexdigest() == record.get("sha256"), "artifact_digest_mismatch")
        artifacts[name] = raw
    return result, artifacts


def validate_native_receipt(artifacts, commit, run_id):
    require("receipt.json" in artifacts, "native_receipt_missing")
    receipt = strict_json(artifacts["receipt.json"])
    require(isinstance(receipt, dict) and receipt.get("schema") == "squadpad.native-gate.v1", "native_receipt_invalid")
    require(receipt.get("run_id") == run_id, "native_run_mismatch")
    expected_repo = RUN_BASE + "/" + run_id + "/repo"
    require(receipt.get("project") == expected_repo + "/SquadPad3D.uproject"
            and receipt.get("output_directory") == expected_repo + "/Saved/SquadPad/NativeGate/" + run_id,
            "native_workspace_mismatch")
    for field in ("git_before", "git_after"):
        value = receipt.get(field)
        require(isinstance(value, dict) and value.get("commit") == commit, "native_commit_mismatch")
        require(value.get("dirty") is False and value.get("status_porcelain") == "", "native_checkout_dirty")
    require(receipt.get("status") == "passed" and receipt.get("mode") == "run", "native_failed")
    evidence = receipt.get("evidence")
    require(isinstance(evidence, dict) and all(evidence.get(name) is True for name in
            ("toolchain_verified", "editor_target_built", "unreal_editor_executed", "native_reader_contract_passed")),
            "native_evidence_incomplete")
    before = receipt.get("source_sha256_before")
    require(isinstance(before, dict) and bool(before) and before == receipt.get("source_sha256_after")
            and all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) for value in before.values()),
            "native_source_changed")
    engine = receipt.get("engine")
    require(isinstance(engine, dict) and engine.get("root") == ENGINE_ROOT and engine.get("host") == "Linux",
            "native_engine_mismatch")
    engine_files = engine.get("files")
    require(isinstance(engine_files, dict) and set(engine_files) ==
            {"build_version", "build_wrapper", "editor", "unreal_build_tool"}, "native_engine_mismatch")
    hashes = {}
    for name, record in engine_files.items():
        require(isinstance(record, dict) and isinstance(record.get("sha256"), str)
                and re.fullmatch(r"[0-9a-f]{64}", record["sha256"]), "native_engine_mismatch")
        hashes[name] = record["sha256"]
    require(hashes == receipt.get("engine_sha256_after"), "native_engine_changed")
    identity = receipt.get("runtime_engine_identity")
    require(isinstance(identity, dict) and identity.get("matches_observed_build_version") is True,
            "native_engine_mismatch")
    require({"build.log", "editor-stdout.log", "automation/index.json"}.issubset(artifacts), "native_artifact_missing")
    phases = receipt.get("phases")
    require(isinstance(phases, dict), "native_evidence_incomplete")
    for phase, name in (("build", "build.log"), ("editor", "editor-stdout.log")):
        value = phases.get(phase)
        require(isinstance(value, dict) and value.get("launched") is True and value.get("timed_out") is False
                and type(value.get("returncode")) is int and value["returncode"] == 0, "native_phase_failed")
        require(value.get("log_sha256") == hashlib.sha256(artifacts[name]).hexdigest(), "native_log_digest_mismatch")
    automation = receipt.get("automation")
    require(isinstance(automation, dict) and automation.get("test_path") == TEST_PATH
            and type(automation.get("test_count")) is int and automation["test_count"] == 1
            and automation.get("state") == "Success" and automation.get("status") == "passed",
            "native_test_mismatch")
    require(automation.get("report_sha256") == hashlib.sha256(artifacts["automation/index.json"]).hexdigest(),
            "native_report_digest_mismatch")
    output_hashes = receipt.get("output_sha256")
    expected_output_hashes = {name: hashlib.sha256(raw).hexdigest()
                             for name, raw in artifacts.items() if name != "receipt.json"}
    require(isinstance(output_hashes, dict) and output_hashes == expected_output_hashes,
            "native_output_digest_mismatch")
    return receipt


def coordinate(commit, run_id, output_dir, environment=None, transport=None):
    environment = os.environ if environment is None else environment
    transport = bounded_transport if transport is None else transport
    summary = {"schema": SCHEMA, "status": "failed", "reason_code": "not_started",
               "commit": None, "run_id": None, "native_reader_contract_passed": False,
               "artifact_count": 0, "artifact_sha256": {}, "transport_returncode": None,
               "transport_timed_out": False, "vm_cleanup_performed": False}
    created_output = False
    try:
        validate_identity(commit, run_id)
        summary.update(commit=commit, run_id=run_id)
        output_dir = Path(output_dir)
        require(not output_dir.exists() and not output_dir.is_symlink(), "local_output_exists")
        output_dir.mkdir(parents=True, mode=0o700)
        created_output = True
        settings = ssh_settings(environment)
        response = transport(ssh_command(settings, commit, run_id), REMOTE_PROGRAM.encode("utf-8"))
        summary["transport_returncode"] = response["returncode"]
        summary["transport_timed_out"] = response["timed_out"]
        require(not response["over_limit"], "result_too_large")
        # Completed result files are retained even if SSH ultimately exits nonzero.
        if response["stdout"]:
            result, artifacts = decode_artifacts(response["stdout"], commit, run_id)
            for name, raw in artifacts.items():
                destination = output_dir / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("xb") as target:
                    target.write(raw)
                summary["artifact_sha256"][name] = hashlib.sha256(raw).hexdigest()
            summary["artifact_count"] = len(artifacts)
        else:
            result, artifacts = None, {}
        require(not response["timed_out"], "transport_timeout")
        require(result is not None, "transport_failed")
        require(result["stage"] in ("gate", "completed"), "remote_setup_failed")
        require(not result["errors"], "remote_execution_failed")
        validate_native_receipt(artifacts, commit, run_id)
        require(result["gate_returncode"] == 0 and response["returncode"] == 0, "transport_or_gate_failed")
        summary.update(status="passed", reason_code="native_gate_passed", native_reader_contract_passed=True)
    except CoordinatorError as error:
        summary["reason_code"] = str(error)
    except KeyboardInterrupt:
        summary["reason_code"] = "controller_cancelled"
    except (OSError, ValueError, subprocess.SubprocessError):
        # Exception text can include hostnames, keys or full SSH argv. Never emit it.
        summary["reason_code"] = "controller_error"
    if created_output:
        try:
            with (output_dir / "remote-summary.json").open("x", encoding="utf-8") as target:
                json.dump(summary, target, indent=2, allow_nan=False)
                target.write("\n")
        except OSError:
            summary.update(status="failed", reason_code="summary_write_failed", native_reader_contract_passed=False)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True, help="Exact lowercase 40-character source commit")
    parser.add_argument("--run-id", required=True, help="Unique run slug, normally gha-RUNID-ATTEMPT")
    parser.add_argument("--output-dir", type=Path, required=True, help="New local evidence directory; never reused")
    args = parser.parse_args(argv)

    def interrupted(_signum, _frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        summary = coordinate(args.commit, args.run_id, args.output_dir)
        print(json.dumps(summary, sort_keys=True, allow_nan=False))
        return 0 if summary["status"] == "passed" else 1
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    raise SystemExit(main())
