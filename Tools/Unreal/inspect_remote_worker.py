"""Read-only, bounded SSH inspection of the explicitly configured Linux worker.

No provider operation, guest setup, engine download or Unreal execution occurs.
Only a fixed root inspection runs. Public output excludes connection settings,
remote diagnostics, hostnames, addresses and provider identifiers.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import remote_native_gate as native

SCHEMA = "squadpad.worker-inspection.v1"
TRANSPORT_SCHEMA = "squadpad.worker-inspection-transport.v1"
TIMEOUT_SECONDS = 45
MAXIMUM_BYTES = 8192
REASONS = frozenset({
    "not_started", "inspection_passed", "config_invalid", "result_invalid",
    "controller_platform_unsupported", "controller_error", "controller_cancelled",
    "transport_timeout", "result_too_large", "ssh_or_remote_failure",
    "unexpected_remote_user",
})
SYSTEM_FIELDS = frozenset({
    "os", "os_version", "architecture", "logical_cpus", "memory_total_bytes",
    "memory_available_bytes", "swap_total_bytes", "swap_free_bytes",
    "root_total_bytes", "root_available_bytes",
})

REMOTE_PROGRAM = r'''
import json
import os
from pathlib import Path
import platform
import sys

try:
    release = platform.freedesktop_os_release()
    memory = {}
    for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
        name, value = line.split(":", 1)
        if name in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}:
            number, unit = value.split()
            if unit != "kB":
                raise ValueError()
            memory[name] = int(number) * 1024
    disk = os.statvfs("/")
    machine = platform.machine()
    ubuntu = release.get("ID") == "ubuntu"
    version = release.get("VERSION_ID")
    system = {
        "os": "ubuntu" if ubuntu else "other",
        "os_version": version if ubuntu and version in {"22.04", "24.04"} else "other",
        "architecture": machine if machine in {"x86_64", "aarch64"} else "other",
        "logical_cpus": os.cpu_count(),
        "memory_total_bytes": memory["MemTotal"],
        "memory_available_bytes": memory["MemAvailable"],
        "swap_total_bytes": memory["SwapTotal"],
        "swap_free_bytes": memory["SwapFree"],
        "root_total_bytes": disk.f_blocks * disk.f_frsize,
        "root_available_bytes": disk.f_bavail * disk.f_frsize,
    }
    print(json.dumps({"schema": "squadpad.worker-inspection-transport.v1",
                      "root_session": os.geteuid() == 0, "system": system}, allow_nan=False))
except (OSError, ValueError, KeyError, TypeError):
    sys.exit(1)
'''


def empty_summary(reason="not_started"):
    return {
        "schema": SCHEMA, "status": "failed", "reason_code": reason,
        "ssh_access_verified": False, "system": None,
        "server_setup_performed": False, "unreal_executed": False,
        "native_reader_contract_passed": False, "cleanup_verified": False,
    }


def validated_system(value):
    require = native.require
    require(isinstance(value, dict) and set(value) == SYSTEM_FIELDS, "result_invalid")
    require(value["os"] in ("ubuntu", "other") and
            value["os_version"] in ("22.04", "24.04", "other") and
            value["architecture"] in ("x86_64", "aarch64", "other"), "result_invalid")
    require(value["os"] == "ubuntu" or value["os_version"] == "other", "result_invalid")
    require(type(value["logical_cpus"]) is int and 1 <= value["logical_cpus"] <= 4096,
            "result_invalid")
    for name in SYSTEM_FIELDS - {"os", "os_version", "architecture", "logical_cpus"}:
        require(type(value[name]) is int and 0 <= value[name] <= 2**63 - 1, "result_invalid")
    for total, available in (("memory_total_bytes", "memory_available_bytes"),
                             ("root_total_bytes", "root_available_bytes")):
        require(0 < value[total] and value[available] <= value[total], "result_invalid")
    require(value["swap_free_bytes"] <= value["swap_total_bytes"], "result_invalid")
    return {name: value[name] for name in sorted(SYSTEM_FIELDS)}


def public_summary(value):
    """Validate again before a workflow prints a temporary summary file."""
    try:
        native.require(isinstance(value, dict) and set(value) == set(empty_summary()), "result_invalid")
        native.require(value["schema"] == SCHEMA, "result_invalid")
        native.require(value["status"] in ("passed", "failed"), "result_invalid")
        native.require(isinstance(value["reason_code"], str) and value["reason_code"] in REASONS,
                       "result_invalid")
        for name in ("server_setup_performed", "unreal_executed", "native_reader_contract_passed", "cleanup_verified"):
            native.require(value[name] is False, "result_invalid")
        clean = empty_summary(value["reason_code"])
        if value["status"] == "passed":
            native.require(value["reason_code"] == "inspection_passed" and
                           value["ssh_access_verified"] is True, "result_invalid")
            clean.update(status="passed", ssh_access_verified=True, system=validated_system(value["system"]))
        else:
            native.require(value["reason_code"] != "inspection_passed" and
                           value["ssh_access_verified"] is False and value["system"] is None,
                           "result_invalid")
        return clean
    except (native.CoordinatorError, TypeError, ValueError):
        return empty_summary("result_invalid")


def inspection_command(settings):
    # Keep the native coordinator's credential validation and bounded transport.
    # This command has a different fixed, read-only payload and shorter lifetime.
    options = [
        "BatchMode=yes", "StrictHostKeyChecking=yes", "HostKeyAlgorithms=ssh-ed25519",
        "UserKnownHostsFile=" + settings["known_hosts"], "GlobalKnownHostsFile=/dev/null",
        "IdentitiesOnly=yes", "IdentityAgent=none", "AddKeysToAgent=no", "UpdateHostKeys=no",
        "PasswordAuthentication=no", "KbdInteractiveAuthentication=no", "PreferredAuthentications=publickey",
        "HostbasedAuthentication=no", "GSSAPIAuthentication=no", "ForwardAgent=no", "ForwardX11=no",
        "ClearAllForwardings=yes", "PermitLocalCommand=no", "ControlMaster=no", "ControlPath=none",
        "ProxyCommand=none", "ProxyJump=none", "ConnectTimeout=10", "ConnectionAttempts=1",
        "ServerAliveInterval=5", "ServerAliveCountMax=1", "LogLevel=ERROR",
    ]
    command = ["ssh", "-F", "/dev/null", "-T", "-p", "22"]
    for option in options:
        command.extend(["-o", option])
    command.extend(["-i", settings["key"], "-l", "root", "--", settings["host"],
                    "python3", "-I", "-B", "-S", "-"])
    return command


def inspect_worker(environment=None, transport=None):
    environment = dict(os.environ if environment is None else environment)
    # Administrative login is fixed for this inspection. No user-supplied command
    # or username reaches the worker, and Python disables site hooks/bytecode writes.
    environment[native.ENV_NAMES["user"]] = "root"
    transport = native.bounded_transport if transport is None else transport
    summary = empty_summary()
    try:
        settings = native.ssh_settings(environment)
        response = transport(inspection_command(settings), REMOTE_PROGRAM.encode("utf-8"),
                             timeout_seconds=TIMEOUT_SECONDS, maximum_bytes=MAXIMUM_BYTES)
        native.require(not response["timed_out"], "transport_timeout")
        native.require(not response["over_limit"], "result_too_large")
        native.require(type(response["returncode"]) is int and response["returncode"] == 0,
                       "ssh_or_remote_failure")
        raw = response["stdout"]
        native.require(isinstance(raw, bytes) and len(raw) <= MAXIMUM_BYTES, "result_invalid")
        result = native.strict_json(raw)
        native.require(isinstance(result, dict) and set(result) == {"schema", "root_session", "system"}
                       and result["schema"] == TRANSPORT_SCHEMA, "result_invalid")
        native.require(result["root_session"] is True, "unexpected_remote_user")
        system = validated_system(result["system"])
        summary.update(status="passed", reason_code="inspection_passed", ssh_access_verified=True, system=system)
    except native.CoordinatorError as error:
        reason = str(error)
        summary["reason_code"] = reason if reason in REASONS else "controller_error"
    except KeyboardInterrupt:
        summary["reason_code"] = "controller_cancelled"
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        summary["reason_code"] = "controller_error"
    return public_summary(summary)


def main():
    summary = inspect_worker()
    print(json.dumps(summary, sort_keys=True, indent=2, allow_nan=False))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
