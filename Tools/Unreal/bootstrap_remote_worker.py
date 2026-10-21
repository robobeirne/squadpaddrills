"""Prepare one fresh, explicitly configured worker through two bounded SSH stages.

Root performs OS preparation only. A separate squadpad session installs the
archive without running its binaries. Provider cleanup remains independent.
The private download URL travels in SSH stdin, never argv or public output.
"""
from __future__ import annotations

import argparse
import base64
import binascii
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

import inspect_remote_worker as inspection
import remote_native_gate as native

SCHEMA = "squadpad.worker-bootstrap.v1"
RECEIPT_SCHEMA = "squadpad.worker-bootstrap-receipt.v1"
WIRE_SCHEMA = "squadpad.worker-bootstrap-transport.v1"
ROOT_STAGE_SECONDS = 1500
INSTALL_STAGE_SECONDS = 5400
# Leave room for bootstrap evidence recovery, the 75-minute native workflow,
# and the operator handoff, within the unchanged four-hour workload deadline.
NATIVE_RESERVE_SECONDS = 95 * 60
MAX_LOG_BYTES = 2 * 1024 * 1024
MAX_WIRE_BYTES = 8 * 1024 * 1024
SOURCE_NAMES = ("prepare_native_worker.sh", "install_engine.py", "ENGINE_ARCHIVE.json")
REASONS = frozenset({
    "not_started", "bootstrap_passed", "config_invalid", "deadline_insufficient",
    "output_invalid", "source_invalid", "key_invalid", "controller_error",
    "controller_cancelled", "controller_platform_unsupported", "transport_timeout",
    "result_too_large", "ssh_or_remote_failure", "result_invalid",
    "os_preparation_failed", "engine_installation_failed", "stage_timeout",
    "stage_output_limit", "remote_environment_invalid", "staging_failed",
    "unexpected_remote_user", "clock_mismatch", "install_receipt_invalid",
})

# Only encoded, validated data is substituted into these fixed programs. The
# remote child logs are bounded and returned privately inside the SSH envelope.
REMOTE_COMMON = r'''
import base64
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import selectors
import signal
import stat
import subprocess
import sys
import time

CONFIG = json.loads(base64.b64decode("__PAYLOAD__", validate=True))
MAX_LOG_BYTES = 2097152
process = None
result = {"schema": "squadpad.worker-bootstrap-transport.v1", "phase": CONFIG["phase"],
          "status": "failed", "reason_code": "remote_environment_invalid", "returncode": None,
          "timed_out": False, "stdout": None, "stderr": None, "install_receipt": None}
out = b""
err = b""

def stop_child():
    global process
    if process is not None and process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=3)

def interrupt(*_):
    raise KeyboardInterrupt()

for name in ("SIGINT", "SIGTERM", "SIGHUP"):
    signal.signal(getattr(signal, name), interrupt)

def require(condition, reason):
    if not condition:
        raise ValueError(reason)

def real_path(path):
    require(path.is_absolute() and path.resolve() == path, "remote_environment_invalid")
    require(not any(item.is_symlink() for item in (path, *path.parents)), "remote_environment_invalid")

def run_child(argv, payload, cwd):
    global process, out, err
    seconds = min(CONFIG["stage_timeout_seconds"], CONFIG["install_deadline_epoch"] - time.time())
    require(seconds > 0, "stage_timeout")
    deadline = time.monotonic() + seconds
    environment = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
    process = subprocess.Popen(argv, cwd=cwd, env=environment, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    offset = 0
    try:
        with selectors.DefaultSelector() as selector:
            for stream, name in ((process.stdin, "stdin"), (process.stdout, "stdout"), (process.stderr, "stderr")):
                os.set_blocking(stream.fileno(), False)
                if name == "stdin" and not payload:
                    stream.close()
                else:
                    selector.register(stream, selectors.EVENT_WRITE if name == "stdin" else selectors.EVENT_READ, name)
            while selector.get_map() or process.poll() is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    result["timed_out"] = True
                    raise ValueError("stage_timeout")
                for key, event in selector.select(min(remaining, 0.5)):
                    if key.data == "stdin":
                        try:
                            offset += os.write(key.fileobj.fileno(), payload[offset:offset + 65536])
                        except BrokenPipeError:
                            offset = len(payload)
                        if offset == len(payload):
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
                    else:
                        chunk = os.read(key.fileobj.fileno(), 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
                        else:
                            remaining_bytes = MAX_LOG_BYTES - len(buffers[key.data])
                            buffers[key.data].extend(chunk[:remaining_bytes])
                            if len(chunk) > remaining_bytes:
                                raise ValueError("stage_output_limit")
            try:
                process.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                result["timed_out"] = True
                raise ValueError("stage_timeout") from None
            return process.returncode
    finally:
        out, err = bytes(buffers["stdout"]), bytes(buffers["stderr"])
        stop_child()
        if process is not None:
            result["returncode"] = process.returncode
            for stream in (process.stdin, process.stdout, process.stderr):
                if not stream.closed:
                    stream.close()

def record(data):
    return {"size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "base64": base64.b64encode(data).decode("ascii")}

try:
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", CONFIG["run_id"]) is not None,
            "remote_environment_invalid")
    require(abs(time.time() - CONFIG["sent_at_epoch"]) <= 60, "clock_mismatch")
    require(time.time() < CONFIG["install_deadline_epoch"], "stage_timeout")
__ACTION__
except KeyboardInterrupt:
    result["reason_code"] = "controller_cancelled"
except ValueError as error:
    allowed = {"remote_environment_invalid", "staging_failed", "unexpected_remote_user", "clock_mismatch",
               "stage_timeout", "stage_output_limit", "os_preparation_failed", "engine_installation_failed"}
    result["reason_code"] = str(error) if str(error) in allowed else "remote_environment_invalid"
except (OSError, KeyError, TypeError, subprocess.SubprocessError):
    result["reason_code"] = "remote_environment_invalid"
finally:
    stop_child()
    result["stdout"], result["stderr"] = record(out), record(err)
    print(json.dumps(result, separators=(",", ":"), allow_nan=False), flush=True)
sys.exit(0 if result["status"] == "passed" else 1)
'''

ROOT_ACTION = r'''
    require(os.geteuid() == 0, "unexpected_remote_user")
    stage = Path("/root") / ("squadpad-bootstrap-" + CONFIG["run_id"])
    real_path(stage)
    stage.mkdir(mode=0o700)
    sources = CONFIG["sources"]
    require(set(sources) == {"prepare_native_worker.sh", "install_engine.py", "ENGINE_ARCHIVE.json"}, "staging_failed")
    for name, entry in sources.items():
        data = base64.b64decode(entry["base64"], validate=True)
        require(len(data) <= 1048576 and hashlib.sha256(data).hexdigest() == entry["sha256"], "staging_failed")
        descriptor = os.open(stage / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as target:
            target.write(data)
    key_path = stage / "build-key.pub"
    descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="ascii") as target:
        target.write(CONFIG["public_key"] + "\n")
    code = run_child(["bash", str(stage / "prepare_native_worker.sh"), "--apply", "--public-key-file", str(key_path)], b"", stage)
    require(code == 0, "os_preparation_failed")
    account = pwd.getpwnam("squadpad")
    base = Path("/srv/squadpad/bootstrap")
    real_path(base)
    if not base.exists():
        base.mkdir(mode=0o755)
        base.chmod(0o755)
    info = base.stat()
    require(base.is_dir() and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o755, "staging_failed")
    install_stage = base / CONFIG["run_id"]
    install_stage.mkdir(mode=0o750)
    os.chown(install_stage, 0, account.pw_gid)
    install_stage.chmod(0o750)
    for name in ("install_engine.py", "ENGINE_ARCHIVE.json"):
        destination = install_stage / name
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
        with os.fdopen(descriptor, "wb") as target:
            os.fchown(target.fileno(), 0, account.pw_gid)
            os.fchmod(target.fileno(), 0o640)
            target.write((stage / name).read_bytes())
    work = install_stage / "work"
    work.mkdir(mode=0o700)
    os.chown(work, account.pw_uid, account.pw_gid)
    work.chmod(0o700)
    result.update(status="passed", reason_code="os_prepared")
'''

INSTALL_ACTION = r'''
    require(os.geteuid() != 0 and pwd.getpwuid(os.geteuid()).pw_name == "squadpad", "unexpected_remote_user")
    stage = Path("/srv/squadpad/bootstrap") / CONFIG["run_id"]
    real_path(stage)
    require(stage.is_dir() and stage.stat().st_uid == 0, "remote_environment_invalid")
    for name in ("install_engine.py", "ENGINE_ARCHIVE.json"):
        path = stage / name
        real_path(path)
        info = path.stat()
        require(path.is_file() and info.st_uid == 0 and not info.st_mode & 0o022, "remote_environment_invalid")
        require(hashlib.sha256(path.read_bytes()).hexdigest() == CONFIG["source_sha256"][name], "remote_environment_invalid")
    work = stage / "work"
    real_path(work)
    receipt_path = work / "install-result.json"
    seconds = max(1, int(min(CONFIG["stage_timeout_seconds"] - 10,
                             CONFIG["install_deadline_epoch"] - time.time() - 10)))
    argv = ["python3", "-I", "-B", "-S", str(stage / "install_engine.py"),
            "--metadata-file", str(stage / "ENGINE_ARCHIVE.json"), "--work-dir", str(work),
            "--receipt-file", str(receipt_path), "--timeout-seconds", str(seconds)]
    code = run_child(argv, json.dumps({"download_url": CONFIG["download_url"]}).encode("utf-8"), stage)
    if receipt_path.exists() and not receipt_path.is_symlink():
        info = receipt_path.stat()
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and info.st_size <= 131072,
                "remote_environment_invalid")
        result["install_receipt"] = json.loads(receipt_path.read_text(encoding="utf-8"))
    require(code == 0, "engine_installation_failed")
    result.update(status="passed", reason_code="engine_installed")
'''


def empty_summary(reason="not_started"):
    return {"schema": SCHEMA, "status": "failed", "reason_code": reason,
            "os_prepared": False, "engine_installed": False, "archive_integrity_verified": False,
            "metadata_verified": False, "unprivileged_install_verified": False,
            "partial_setup_possible": False, "unreal_executed": False,
            "native_reader_contract_passed": False, "cleanup_verified": False}


def public_summary(value):
    try:
        native.require(isinstance(value, dict) and set(value) == set(empty_summary()), "result_invalid")
        native.require(value["schema"] == SCHEMA and value["status"] in ("passed", "failed"), "result_invalid")
        native.require(isinstance(value["reason_code"], str) and value["reason_code"] in REASONS, "result_invalid")
        for key in set(empty_summary()) - {"schema", "status", "reason_code"}:
            native.require(type(value[key]) is bool, "result_invalid")
        for key in ("unreal_executed", "native_reader_contract_passed", "cleanup_verified"):
            native.require(value[key] is False, "result_invalid")
        proofs = ("os_prepared", "engine_installed", "archive_integrity_verified", "metadata_verified", "unprivileged_install_verified")
        if value["status"] == "passed":
            native.require(value["reason_code"] == "bootstrap_passed" and all(value[key] for key in proofs), "result_invalid")
        else:
            native.require(value["reason_code"] != "bootstrap_passed" and not value["engine_installed"], "result_invalid")
        return dict(value)
    except (native.CoordinatorError, TypeError, ValueError):
        return empty_summary("result_invalid")


def parse_config(raw, now):
    native.require(isinstance(raw, str) and 0 < len(raw.encode("utf-8")) <= 16384, "config_invalid")
    try:
        value = native.strict_json(raw.encode("utf-8"))
        native.require(isinstance(value, dict) and set(value) == {"download_url", "workload_deadline_utc"}, "config_invalid")
        url = value["download_url"]
        native.require(isinstance(url, str) and 0 < len(url) <= 8192 and
                       all(32 < ord(character) < 127 for character in url), "config_invalid")
        parsed = urlsplit(url)
        native.require(parsed.scheme == "https" and parsed.hostname and not parsed.username and
                       not parsed.password and not parsed.fragment and parsed.port in (None, 443), "config_invalid")
        native.require(parsed.netloc in (parsed.hostname, parsed.hostname + ":443"), "config_invalid")
        stamp = value["workload_deadline_utc"]
        native.require(isinstance(stamp, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", stamp), "config_invalid")
        deadline = datetime.fromisoformat(stamp[:-1] + "+00:00").timestamp()
        native.require(now < deadline <= now + 4 * 3600, "config_invalid")
        native.require(deadline - now > NATIVE_RESERVE_SECONDS + 60, "deadline_insufficient")
        return {"download_url": url, "workload_deadline_utc": stamp, "deadline_epoch": deadline}
    except native.CoordinatorError as error:
        if str(error) == "deadline_insufficient":
            raise
        raise native.CoordinatorError("config_invalid") from None
    except (ValueError, TypeError, OverflowError):
        raise native.CoordinatorError("config_invalid") from None


def derive_public_key(path):
    try:
        result = subprocess.run(["ssh-keygen", "-y", "-f", path], stdin=subprocess.DEVNULL,
                                capture_output=True, timeout=10, check=True,
                                env={"PATH": os.defpath, "LANG": "C"})
        parts = result.stdout.decode("ascii").strip().split()
        native.require(len(parts) >= 2 and parts[0] == "ssh-ed25519", "key_invalid")
        raw = base64.b64decode(parts[1], validate=True)
        native.require(len(raw) == 51 and raw[:19] == bytes.fromhex("0000000b7373682d6564323535313900000020"), "key_invalid")
        return " ".join(parts[:2])
    except (OSError, UnicodeError, ValueError, subprocess.SubprocessError):
        raise native.CoordinatorError("key_invalid") from None


def load_sources():
    root = Path(__file__).resolve().parents[2]
    paths = {"prepare_native_worker.sh": root / "Tools/Unreal/prepare_native_worker.sh",
             "install_engine.py": root / "Tools/Unreal/install_engine.py",
             "ENGINE_ARCHIVE.json": root / "Docs/ENGINE_ARCHIVE.json"}
    sources = {}
    for name, path in paths.items():
        native.require(path.is_file() and not path.is_symlink() and path.resolve() == path and
                       0 < path.stat().st_size <= 1048576, "source_invalid")
        data = path.read_bytes()
        sources[name] = {"base64": base64.b64encode(data).decode("ascii"), "sha256": hashlib.sha256(data).hexdigest()}
    metadata = native.strict_json(base64.b64decode(sources["ENGINE_ARCHIVE.json"]["base64"]))
    native.require(isinstance(metadata, dict) and metadata.get("schema") == "squadpad.engine-archive.v1", "source_invalid")
    return sources, metadata


def stage_program(phase, payload):
    native.require(phase in ("os", "engine"), "config_invalid")
    action = ROOT_ACTION if phase == "os" else INSTALL_ACTION
    encoded = base64.b64encode(json.dumps(payload, separators=(",", ":"), allow_nan=False).encode()).decode("ascii")
    return REMOTE_COMMON.replace("__ACTION__", action).replace("__PAYLOAD__", encoded).encode("utf-8")


def stage_command(settings, user):
    native.require(user in ("root", "squadpad"), "config_invalid")
    command = inspection.inspection_command(settings)
    command[command.index("-l") + 1] = user
    return command


def decode_stage(response, phase):
    native.require(not response["timed_out"], "transport_timeout")
    native.require(not response["over_limit"], "result_too_large")
    raw = response["stdout"]
    native.require(isinstance(raw, bytes) and len(raw) <= MAX_WIRE_BYTES, "result_invalid")
    if not raw:
        raise native.CoordinatorError("ssh_or_remote_failure")
    result = native.strict_json(raw)
    fields = {"schema", "phase", "status", "reason_code", "returncode", "timed_out", "stdout", "stderr", "install_receipt"}
    native.require(isinstance(result, dict) and set(result) == fields and result["schema"] == WIRE_SCHEMA and
                   result["phase"] == phase and result["status"] in ("passed", "failed"), "result_invalid")
    native.require(type(result["timed_out"]) is bool and (result["returncode"] is None or type(result["returncode"]) is int), "result_invalid")
    native.require(isinstance(result["reason_code"], str) and result["reason_code"] in REASONS | {"os_prepared", "engine_installed"}, "result_invalid")
    logs = {}
    for name in ("stdout", "stderr"):
        entry = result[name]
        native.require(isinstance(entry, dict) and set(entry) == {"size_bytes", "sha256", "base64"}, "result_invalid")
        native.require(type(entry["size_bytes"]) is int and 0 <= entry["size_bytes"] <= MAX_LOG_BYTES and
                       isinstance(entry["base64"], str) and len(entry["base64"]) <= 4 * ((MAX_LOG_BYTES + 2) // 3), "result_invalid")
        try:
            data = base64.b64decode(entry["base64"], validate=True)
        except (ValueError, binascii.Error):
            raise native.CoordinatorError("result_invalid") from None
        native.require(len(data) == entry["size_bytes"] and hashlib.sha256(data).hexdigest() == entry["sha256"], "result_invalid")
        logs[name] = data
    result = {key: value for key, value in result.items() if key not in ("stdout", "stderr")}
    if result["status"] == "passed":
        native.require(type(response["returncode"]) is int and response["returncode"] == 0 and
                       result["returncode"] == 0 and result["timed_out"] is False and
                       result["reason_code"] == ("os_prepared" if phase == "os" else "engine_installed"), "result_invalid")
    return result, logs


def verify_install_receipt(receipt, metadata):
    native.require(isinstance(receipt, dict) and receipt.get("schema") == "squadpad.engine-install.v1" and
                   receipt.get("status") == "passed" and receipt.get("stage") == "complete" and
                   receipt.get("reason_code") == "installation_passed" and
                   receipt.get("engine_root") == "/opt/unreal/UE_5.8", "install_receipt_invalid")
    for observed, expected in (("archive_bytes", "compressed_archive_bytes"), ("entry_count", "zip_entry_count"),
                               ("expanded_bytes", "listed_expanded_bytes")):
        native.require(type(receipt.get(observed)) is int and receipt[observed] == metadata[expected], "install_receipt_invalid")
    native.require(isinstance(receipt.get("archive_sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", receipt["archive_sha256"]), "install_receipt_invalid")
    expected_hashes = {"build_version": metadata["build_version_sha256"],
                       "sdk_manifest": metadata["toolchain"]["sdk_manifest_sha256"],
                       "installed_build_marker": metadata["installed_build_marker"]["sha256"]}
    native.require(receipt.get("metadata_sha256") == expected_hashes, "install_receipt_invalid")
    evidence = receipt.get("evidence")
    native.require(isinstance(evidence, dict), "install_receipt_invalid")
    for name in ("unprivileged_account_verified", "download_complete", "full_archive_sha256_recorded",
                 "archive_structure_verified", "archive_integrity_verified", "extraction_complete",
                 "metadata_verified", "executable_permissions_preserved", "temporary_archive_removed"):
        native.require(evidence.get(name) is True, "install_receipt_invalid")
    for name in ("publisher_authenticity_verified", "engine_executed", "compiler_executed", "cleanup_verified"):
        native.require(evidence.get(name) is False, "install_receipt_invalid")


def write_receipt(directory, report):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, prefix="receipt-", delete=False) as target:
        json.dump(report, target, indent=2, allow_nan=False)
        target.write("\n")
        temporary = target.name
    os.replace(temporary, directory / "bootstrap-result.json")


def bootstrap(run_id, output_dir, environment=None, transport=None, clock=None, source_loader=None, public_key_loader=None):
    environment = dict(os.environ if environment is None else environment)
    transport = native.bounded_transport if transport is None else transport
    clock = time.time if clock is None else clock
    source_loader = load_sources if source_loader is None else source_loader
    public_key_loader = derive_public_key if public_key_loader is None else public_key_loader
    summary = empty_summary()
    report = {"schema": RECEIPT_SCHEMA, "run_id": None, "summary": summary,
              "operator_deadline_provider_verified": False, "phases": {}, "source_sha256": {},
              "engine_install_receipt": None, "logs_truncated": False}
    directory = None
    download_url = ""
    try:
        native.require(isinstance(run_id, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", run_id), "config_invalid")
        path = Path(output_dir)
        native.require(path.is_absolute() and path.resolve() == path and not path.exists() and not path.is_symlink(), "output_invalid")
        path.mkdir(parents=True, mode=0o700)
        directory = path
        report["run_id"] = run_id
        for name in ("bootstrap-stdout.log", "bootstrap-stderr.log"):
            descriptor = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
        write_receipt(directory, report)
        config = parse_config(environment.get("SQUADPAD_BOOTSTRAP_CONFIG", ""), clock())
        download_url = config["download_url"]
        report["operator_workload_deadline_utc"] = config["workload_deadline_utc"]
        environment[native.ENV_NAMES["user"]] = "root"
        settings = native.ssh_settings(environment)
        public_key = public_key_loader(settings["key"])
        sources, metadata = source_loader()
        report["source_sha256"] = {name: value["sha256"] for name, value in sources.items()}
        install_deadline = config["deadline_epoch"] - NATIVE_RESERVE_SECONDS
        for phase, maximum, user in (("os", ROOT_STAGE_SECONDS, "root"), ("engine", INSTALL_STAGE_SECONDS, "squadpad")):
            now = clock()
            budget = min(maximum, math.floor(install_deadline - now - 30))
            native.require(budget >= 30, "deadline_insufficient")
            payload = {"phase": phase, "run_id": run_id, "sent_at_epoch": now,
                       "stage_timeout_seconds": budget, "install_deadline_epoch": install_deadline}
            if phase == "os":
                payload.update(sources=sources, public_key=public_key)
            else:
                payload.update(download_url=download_url, source_sha256=report["source_sha256"])
            summary["partial_setup_possible"] = True
            write_receipt(directory, report)
            response = transport(stage_command(settings, user), stage_program(phase, payload),
                                 timeout_seconds=budget + 20, maximum_bytes=MAX_WIRE_BYTES)
            result, logs = decode_stage(response, phase)
            report["phases"][phase] = {key: value for key, value in result.items() if key != "install_receipt"}
            for channel, data in logs.items():
                data = data.replace(download_url.encode("utf-8"), b"[redacted engine download URL]")
                target = directory / ("bootstrap-" + channel + ".log")
                heading = ("\n[" + phase + "]\n").encode()
                remaining = max(0, 2 * MAX_LOG_BYTES - target.stat().st_size)
                content = heading + data
                with target.open("ab") as stream:
                    stream.write(content[:remaining])
                report["logs_truncated"] |= len(content) > remaining
            if phase == "engine" and result["install_receipt"] is not None:
                encoded = json.dumps(result["install_receipt"], allow_nan=False)
                native.require(len(encoded.encode("utf-8")) <= 131072 and download_url not in encoded,
                               "install_receipt_invalid")
                report["engine_install_receipt"] = result["install_receipt"]
            if result["status"] != "passed":
                reason = result["reason_code"]
                raise native.CoordinatorError(reason if reason in REASONS else "ssh_or_remote_failure")
            if phase == "os":
                summary["os_prepared"] = True
            else:
                verify_install_receipt(result["install_receipt"], metadata)
                summary.update(engine_installed=True, archive_integrity_verified=True,
                               metadata_verified=True, unprivileged_install_verified=True)
            write_receipt(directory, report)
        summary.update(status="passed", reason_code="bootstrap_passed")
    except native.CoordinatorError as error:
        reason = str(error)
        summary["reason_code"] = reason if reason in REASONS else "controller_error"
    except KeyboardInterrupt:
        summary["reason_code"] = "controller_cancelled"
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        summary["reason_code"] = "controller_error"
    summary = public_summary(summary)
    report["summary"] = summary
    if directory is not None:
        try:
            write_receipt(directory, report)
        except (OSError, ValueError):
            summary = empty_summary("controller_error")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    summary = bootstrap(args.run_id, args.output_dir)
    print(json.dumps(summary, sort_keys=True, indent=2, allow_nan=False))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
