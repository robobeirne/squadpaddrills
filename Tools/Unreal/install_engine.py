"""Bounded, unprivileged installation of the reviewed Unreal ZIP on the worker.

The private download URL is read from stdin, never from arguments or output.
This module downloads and extracts files only. It never starts an engine,
compiler, shell, package manager, provider operation or cleanup controller.
"""
from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import signal
import stat
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

SCHEMA = "squadpad.engine-install.v1"
ENGINE_ROOT = Path("/opt/unreal/UE_5.8")
CHUNK_BYTES = 1024 * 1024
DISK_RESERVE_BYTES = 16 * 1024**3
MAX_CENTRAL_BYTES = 256 * 1024**2
MAX_METADATA_BYTES = 65536
MAX_INPUT_BYTES = 16384
MAX_TIMEOUT_SECONDS = 5400
BUILD_VERSION = "Engine/Build/Build.version"
INSTALLED_MARKER = "Engine/Build/InstalledBuild.txt"
SDK_MANIFEST = "Engine/Config/Linux/Linux_SDK.json"
TRUE_ON_SUCCESS = (
    "unprivileged_account_verified", "download_complete", "full_archive_sha256_recorded",
    "archive_structure_verified", "archive_integrity_verified", "extraction_complete",
    "metadata_verified", "executable_permissions_preserved", "temporary_archive_removed",
)
ALWAYS_FALSE = (
    "publisher_authenticity_verified", "engine_executed", "compiler_executed", "cleanup_verified",
)
REASONS = frozenset({
    "not_started", "installation_passed", "input_invalid", "metadata_invalid",
    "account_invalid", "path_invalid", "existing_state", "insufficient_disk",
    "installer_timeout", "installer_interrupted", "download_failed", "download_invalid",
    "archive_invalid", "archive_path_invalid", "archive_collision", "archive_link_invalid",
    "archive_metadata_mismatch", "permissions_invalid", "filesystem_failure", "installer_error",
})


class InstallError(Exception):
    """Only allowlisted reason codes may cross the private/public boundary."""

    def __init__(self, code):
        self.code = code if code in REASONS else "installer_error"
        super().__init__(self.code)


def require(condition, code):
    if not condition:
        raise InstallError(code)


def strict_json(data):
    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value, "input_invalid")
            value[key] = item
        return value

    def constant(_):
        raise InstallError("input_invalid")

    try:
        return json.loads(data, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, TypeError, RecursionError):
        raise InstallError("input_invalid") from None


def validate_url(value):
    require(isinstance(value, str) and 1 <= len(value) <= MAX_INPUT_BYTES, "input_invalid")
    require(all(32 < ord(char) < 127 for char in value), "input_invalid")
    try:
        url = urllib.parse.urlsplit(value)
        require(url.scheme == "https" and url.hostname and not url.username and not url.password
                and not url.fragment and url.port in (None, 443), "input_invalid")
        require(url.netloc == url.hostname or url.netloc == url.hostname + ":443", "input_invalid")
    except ValueError:
        raise InstallError("input_invalid") from None
    return value


def read_download_input(stream):
    raw = stream.read(MAX_INPUT_BYTES + 1)
    require(isinstance(raw, bytes) and 0 < len(raw) <= MAX_INPUT_BYTES, "input_invalid")
    value = strict_json(raw)
    require(isinstance(value, dict) and set(value) == {"download_url"}, "input_invalid")
    return validate_url(value["download_url"])


def checked_path(value):
    path = Path(value)
    require(path.is_absolute() and str(path) == str(value) and path.resolve(strict=True) == path,
            "path_invalid")
    for parent in (path, *path.parents):
        require(not stat.S_ISLNK(parent.lstat().st_mode), "path_invalid")
    return path


def require_build_account():
    uid = os.geteuid()
    require(sys.platform == "linux" and uid != 0 and os.getuid() == uid
            and pwd.getpwuid(uid).pw_name == "squadpad", "account_invalid")
    return uid


@dataclass(frozen=True)
class ArchivePolicy:
    compressed_bytes: int
    entry_count: int
    expanded_bytes: int
    observed_entries: dict
    metadata_hashes: dict
    build_version: dict
    sdk_manifest: dict
    marker: bytes
    compiler_entry: str

    @property
    def executable_entries(self):
        return ("Engine/Binaries/Linux/UnrealEditor", "Engine/Binaries/Linux/UnrealEditor-Cmd",
                "Engine/Build/BatchFiles/Linux/Build.sh", self.compiler_entry)


def policy_from_metadata(value):
    """Read expectations from the reviewed, root-owned ENGINE_ARCHIVE.json."""
    try:
        require(isinstance(value, dict) and value["schema"] == "squadpad.engine-archive.v1"
                and value["archive_root_prefix"] == ""
                and value["native_installation"]["engine_root"] == str(ENGINE_ROOT)
                and value["native_installation"]["root_must_resolve_to_this_exact_path"] is True,
                "metadata_invalid")
        sizes = [value["compressed_archive_bytes"], value["zip_entry_count"],
                 value["listed_expanded_bytes"]]
        for number, limit in zip(sizes, (64 * 1024**3, 500000, 128 * 1024**3)):
            require(type(number) is int and 0 < number <= limit, "metadata_invalid")
        toolchain = value["toolchain"]
        require(toolchain["sdk_manifest_path"] == SDK_MANIFEST
                and toolchain["compiler_entry_present"] is True
                and value["inspection"]["full_archive_sha256"] is None
                and value["inspection"]["publisher_authenticity_verified"] is False,
                "metadata_invalid")
        hashes = {"build_version": value["build_version_sha256"],
                  "sdk_manifest": toolchain["sdk_manifest_sha256"],
                  "installed_build_marker": value["installed_build_marker"]["sha256"]}
        require(all(isinstance(item, str) and re.fullmatch(r"[0-9a-f]{64}", item)
                    for item in hashes.values()), "metadata_invalid")
        require(isinstance(value["build_version"], dict) and
                (value["build_version"].get("MajorVersion"), value["build_version"].get("MinorVersion"),
                 value["build_version"].get("PatchVersion")) == (5, 8, 4)
                and isinstance(toolchain["sdk_manifest"], dict)
                and value["installed_build_marker"]["value"] == "UE_5.8", "metadata_invalid")
        observed = value["observed_engine_entries"]
        require(isinstance(observed, dict) and 1 <= len(observed) <= 64, "metadata_invalid")
        for path, details in observed.items():
            safe_name(path)
            require(isinstance(details, dict) and set(details) == {"present", "uncompressed_bytes"}
                    and type(details["present"]) is bool, "metadata_invalid")
            size = details["uncompressed_bytes"]
            require((details["present"] and type(size) is int and 0 <= size <= sizes[2])
                    or (not details["present"] and size is None), "metadata_invalid")
        compiler = toolchain["bundled_native_compiler_entry"]
        safe_name(compiler)
        require(compiler.startswith("Engine/Extras/ThirdPartyNotUE/SDKs/HostLinux/Linux_x64/")
                and compiler.endswith("/bin/clang"), "metadata_invalid")
        return ArchivePolicy(*sizes, observed, hashes, value["build_version"],
                             toolchain["sdk_manifest"], b"UE_5.8", compiler)
    except (KeyError, TypeError, ValueError, InstallError):
        raise InstallError("metadata_invalid") from None


def load_policy(path):
    path = checked_path(path)
    details = path.lstat()
    require(stat.S_ISREG(details.st_mode) and details.st_uid == 0
            and not details.st_mode & 0o022 and details.st_nlink == 1
            and 0 < details.st_size <= MAX_METADATA_BYTES, "metadata_invalid")
    with path.open("rb") as stream:
        raw = stream.read(MAX_METADATA_BYTES + 1)
    require(len(raw) <= MAX_METADATA_BYTES, "metadata_invalid")
    return policy_from_metadata(strict_json(raw))


class Deadline:
    def __init__(self, seconds, clock=time.monotonic):
        require(type(seconds) is int and 1 <= seconds <= MAX_TIMEOUT_SECONDS, "input_invalid")
        self.clock = clock
        self.end = clock() + seconds

    def remaining(self):
        remaining = self.end - self.clock()
        require(remaining > 0, "installer_timeout")
        return remaining

    def check(self):
        self.remaining()


def available_space(path):
    details = os.statvfs(path)
    return details.f_bavail * details.f_frsize


def check_space(path, required, free_space):
    require(free_space(path) >= required + DISK_RESERVE_BYTES, "insufficient_disk")


def initial_receipt(engine_root):
    return {"schema": SCHEMA, "status": "running", "stage": "preflight", "reason_code": "not_started",
            "engine_root": str(engine_root), "archive_bytes": 0, "archive_sha256": None,
            "entry_count": 0, "expanded_bytes": 0, "metadata_sha256": {},
            "evidence": {name: False for name in (*TRUE_ON_SUCCESS, *ALWAYS_FALSE)}}


def save_receipt(path, receipt, first=False):
    destination = path if first else path.with_name(".install-result.tmp")
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(receipt, stream, sort_keys=True, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    if not first:
        existing = path.lstat()
        require(stat.S_ISREG(existing.st_mode) and existing.st_uid == os.geteuid()
                and existing.st_nlink == 1 and stat.S_IMODE(existing.st_mode) == 0o600,
                "path_invalid")
        os.replace(destination, path)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        raise InstallError("download_invalid")


def direct_opener():
    # A signed URL must not be forwarded through ambient proxy configuration.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open


def download_archive(url, target, policy, deadline, receipt, opener, free_space):
    request = urllib.request.Request(url, headers={"Accept-Encoding": "identity"})
    digest = hashlib.sha256()
    received = 0
    try:
        with opener(request, timeout=min(15, deadline.remaining())) as response:
            require(response.status == 200 and
                    response.headers.get("Content-Encoding", "identity").lower() == "identity",
                    "download_invalid")
            length = response.headers.get("Content-Length", "")
            require(re.fullmatch(r"[0-9]{1,12}", length) is not None
                    and int(length) == policy.compressed_bytes, "download_invalid")
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                while True:
                    deadline.check()
                    chunk = response.read(min(CHUNK_BYTES, policy.compressed_bytes - received + 1))
                    deadline.check()
                    if not chunk:
                        break
                    require(isinstance(chunk, bytes) and received + len(chunk) <= policy.compressed_bytes,
                            "download_invalid")
                    check_space(target.parent, policy.compressed_bytes - received + policy.expanded_bytes,
                                free_space)
                    output.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)
                    receipt["archive_bytes"] = received
                require(received == policy.compressed_bytes, "download_invalid")
                output.flush()
                os.fsync(output.fileno())
    except InstallError:
        raise
    except (urllib.error.URLError, OSError, ValueError):
        raise InstallError("download_failed") from None
    receipt["archive_sha256"] = digest.hexdigest()
    receipt["evidence"]["download_complete"] = True
    receipt["evidence"]["full_archive_sha256_recorded"] = True


def inspect_zip_bounds(path, policy, deadline):
    """Bound the central directory before ZipFile allocates its entry objects."""
    deadline.check()
    size = path.stat().st_size
    require(size == policy.compressed_bytes and size >= 22, "archive_invalid")
    with path.open("rb") as stream:
        require(stream.read(4) == b"PK\x03\x04", "archive_invalid")
        tail_size = min(size, 65557)
        stream.seek(size - tail_size)
        tail = stream.read(tail_size)
        index = tail.rfind(b"PK\x05\x06")
        require(index >= 0 and index + 22 <= len(tail), "archive_invalid")
        _, disk, cd_disk, disk_entries, count, cd_size, cd_offset, comment_size = struct.unpack_from(
            "<4sHHHHIIH", tail, index)
        require(index + 22 + comment_size == len(tail) and disk == cd_disk == 0,
                "archive_invalid")
        eocd_offset = size - tail_size + index
        directory_end = eocd_offset
        if count == 0xffff or disk_entries == 0xffff or cd_size == 0xffffffff or cd_offset == 0xffffffff:
            require(eocd_offset >= 20, "archive_invalid")
            stream.seek(eocd_offset - 20)
            locator = stream.read(20)
            require(len(locator) == 20, "archive_invalid")
            signature, zip64_disk, zip64_offset, disks = struct.unpack("<4sIQI", locator)
            require(signature == b"PK\x06\x07" and zip64_disk == 0 and disks == 1
                    and 0 <= zip64_offset < eocd_offset - 20, "archive_invalid")
            stream.seek(zip64_offset)
            record = stream.read(56)
            require(len(record) == 56, "archive_invalid")
            signature, record_size, _, _, z_disk, z_cd_disk, z_disk_count, z_count, z_size, z_offset = (
                struct.unpack("<4sQHHIIQQQQ", record))
            require(signature == b"PK\x06\x06" and 44 <= record_size <= 4096
                    and zip64_offset + 12 + record_size == eocd_offset - 20
                    and z_disk == z_cd_disk == 0 and z_disk_count == z_count,
                    "archive_invalid")
            for legacy, actual, sentinel in ((count, z_count, 0xffff), (disk_entries, z_count, 0xffff),
                                              (cd_size, z_size, 0xffffffff), (cd_offset, z_offset, 0xffffffff)):
                require(legacy == sentinel or legacy == actual, "archive_invalid")
            count, disk_entries, cd_size, cd_offset = z_count, z_count, z_size, z_offset
            directory_end = zip64_offset
        require(count == disk_entries == policy.entry_count and 0 < cd_size <= MAX_CENTRAL_BYTES
                and 0 < cd_offset < directory_end and cd_offset + cd_size == directory_end,
                "archive_invalid")
    deadline.check()
    return cd_offset


def safe_name(raw, directory=False):
    require(isinstance(raw, str) and 0 < len(raw.encode("utf-8")) <= 1024
            and "\\" not in raw and ":" not in raw
            and all(ord(char) >= 32 and ord(char) != 127 for char in raw), "archive_path_invalid")
    require(not raw.startswith("/") and (raw.endswith("/") == directory), "archive_path_invalid")
    name = raw[:-1] if directory else raw
    parts = name.split("/")
    require(all(part and part not in (".", "..") and part == part.strip() for part in parts),
            "archive_path_invalid")
    return name


def link_destination(name, payload):
    require(0 < len(payload) <= 4096, "archive_link_invalid")
    try:
        target = payload.decode("utf-8")
    except UnicodeError:
        raise InstallError("archive_link_invalid") from None
    require(not target.startswith("/") and "\\" not in target and ":" not in target
            and all(ord(char) >= 32 and ord(char) != 127 for char in target), "archive_link_invalid")
    for part in target.rstrip("/").split("/"):
        require(part and part == part.strip(), "archive_link_invalid")
    return target, target.rstrip("/")


def resolve_link_path(name, kinds, destinations):
    pending = deque(name.split("/"))
    parts = []
    visited = set()
    while pending:
        part = pending.popleft()
        if part == ".":
            continue
        if part == "..":
            require(parts, "archive_link_invalid")
            parts.pop()
            continue
        prefix = "/".join([*parts, part])
        require(prefix in kinds, "archive_link_invalid")
        if kinds[prefix] == "link":
            require(prefix not in visited and len(visited) < 64, "archive_link_invalid")
            visited.add(prefix)
            pending.extendleft(reversed(destinations[prefix].split("/")))
        else:
            require(not pending or kinds[prefix] == "directory", "archive_link_invalid")
            parts.append(part)
    require(parts, "archive_link_invalid")
    return "/".join(parts)


def plan_archive(archive, policy, central_offset, deadline):
    infos = archive.infolist()
    require(len(infos) == policy.entry_count, "archive_invalid")
    entries, kinds, links, destinations = {}, {}, {}, {}
    expanded = 0
    offsets = set()
    for info in infos:
        deadline.check()
        require(info.orig_filename == info.filename and not info.flag_bits & 1
                and info.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                and 0 <= info.compress_size <= policy.compressed_bytes
                and 0 <= info.header_offset < central_offset and info.header_offset not in offsets,
                "archive_invalid")
        offsets.add(info.header_offset)
        mode = info.external_attr >> 16 if info.create_system == 3 else 0
        file_type = stat.S_IFMT(mode)
        require(file_type in (0, stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK), "archive_invalid")
        directory = info.is_dir()
        require((file_type != stat.S_IFDIR or directory) and
                (not directory or file_type in (0, stat.S_IFDIR)), "archive_invalid")
        kind = "directory" if directory else ("link" if file_type == stat.S_IFLNK else "file")
        name = safe_name(info.filename, directory)
        require(name not in entries, "archive_collision")
        require(info.file_size >= 0 and (kind != "directory" or info.file_size == 0), "archive_invalid")
        expanded += info.file_size
        require(expanded <= policy.expanded_bytes, "archive_invalid")
        entries[name], kinds[name] = info, kind
    require(expanded == policy.expanded_bytes, "archive_invalid")
    for name in entries:
        deadline.check()
        parts = name.split("/")
        for length in range(1, len(parts)):
            parent = "/".join(parts[:length])
            require(kinds.get(parent, "directory") == "directory", "archive_collision")
            kinds.setdefault(parent, "directory")
    for name, info in entries.items():
        if kinds[name] == "link":
            deadline.check()
            require(0 < info.file_size <= 4096, "archive_link_invalid")
            with archive.open(info) as stream:
                payload = stream.read(4097)
                require(len(payload) == info.file_size and not stream.read(1), "archive_link_invalid")
            links[name], destinations[name] = link_destination(name, payload)
    for name in links:
        deadline.check()
        resolved = resolve_link_path(name, kinds, destinations)
        # Directory links to their own ancestors create traversal cycles.
        require(kinds[resolved] != "directory" or not name.startswith(resolved + "/"),
                "archive_link_invalid")
        require(not links[name].endswith("/") or kinds[resolved] == "directory", "archive_link_invalid")
    for name, details in policy.observed_entries.items():
        if details["present"]:
            require(name in entries and kinds[name] == "file"
                    and entries[name].file_size == details["uncompressed_bytes"], "archive_metadata_mismatch")
        else:
            require(name not in kinds, "archive_metadata_mismatch")
    for name in (BUILD_VERSION, SDK_MANIFEST, INSTALLED_MARKER):
        require(name in entries and kinds[name] == "file"
                and entries[name].file_size <= MAX_METADATA_BYTES, "archive_metadata_mismatch")
    for name in policy.executable_entries:
        require(name in entries, "archive_metadata_mismatch")
        resolved = resolve_link_path(name, kinds, destinations)
        require(kinds[resolved] == "file" and (entries[resolved].external_attr >> 16) & 0o111,
                "permissions_invalid")
    return entries, kinds, links


def extract_archive(archive, plan, root, policy, deadline, free_space):
    entries, kinds, links = plan
    directories = sorted((name for name, kind in kinds.items() if kind == "directory"),
                         key=lambda name: (name.count("/"), name))
    for name in directories:
        deadline.check()
        (root / name).mkdir(mode=0o700)
    remaining = policy.expanded_bytes
    for name, info in entries.items():
        deadline.check()
        if kinds[name] == "directory":
            # Even an empty directory member has a local header and CRC to check.
            with archive.open(info) as stream:
                require(not stream.read(1), "archive_invalid")
        elif kinds[name] == "file":
            descriptor = os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            written = 0
            with os.fdopen(descriptor, "wb") as output, archive.open(info) as source:
                while True:
                    deadline.check()
                    chunk = source.read(min(CHUNK_BYTES, info.file_size - written + 1))
                    deadline.check()
                    if not chunk:
                        break
                    require(written + len(chunk) <= info.file_size, "archive_invalid")
                    check_space(root, remaining, free_space)
                    output.write(chunk)
                    written += len(chunk)
                    remaining -= len(chunk)
                require(written == info.file_size, "archive_invalid")
            # Strip special and write bits for other users, preserving executable bits.
            os.chmod(root / name, 0o600 | ((info.external_attr >> 16) & 0o155))
    # No link is materialized while regular files are being written.
    for name, target in links.items():
        deadline.check()
        check_space(root, remaining, free_space)
        os.symlink(target, root / name)
        remaining -= entries[name].file_size
    require(remaining == 0, "archive_invalid")
    for name in reversed(directories):
        deadline.check()
        mode = entries[name].external_attr >> 16 if name in entries else 0o750
        os.chmod(root / name, 0o700 | (mode & 0o055))


def validate_installed_metadata(root, policy, deadline):
    actual = {}
    for key, name in (("build_version", BUILD_VERSION), ("sdk_manifest", SDK_MANIFEST),
                      ("installed_build_marker", INSTALLED_MARKER)):
        deadline.check()
        path = root / name
        details = path.lstat()
        require(stat.S_ISREG(details.st_mode) and details.st_size <= MAX_METADATA_BYTES,
                "archive_metadata_mismatch")
        with path.open("rb") as stream:
            raw = stream.read(MAX_METADATA_BYTES + 1)
        require(len(raw) <= MAX_METADATA_BYTES, "archive_metadata_mismatch")
        actual[key] = hashlib.sha256(raw).hexdigest()
        require(actual[key] == policy.metadata_hashes[key], "archive_metadata_mismatch")
        expected = policy.build_version if key == "build_version" else policy.sdk_manifest
        if key == "installed_build_marker":
            require(raw == policy.marker, "archive_metadata_mismatch")
        else:
            require(strict_json(raw) == expected, "archive_metadata_mismatch")
    for name in policy.executable_entries:
        deadline.check()
        path = root / name
        resolved = path.resolve(strict=True)
        require(resolved.is_relative_to(root) and resolved.is_file()
                and stat.S_IMODE(resolved.stat().st_mode) & 0o111, "permissions_invalid")
    return actual


def install_engine(policy, engine_root, work_dir, receipt_file, download_url, timeout_seconds,
                   *, opener=None, clock=time.monotonic, free_space=available_space,
                   identity_checker=require_build_account):
    """Library injections are for offline fixtures; the CLI has no policy/path overrides."""
    receipt = initial_receipt(engine_root)
    safe_receipt = False
    try:
        deadline = Deadline(timeout_seconds, clock)
        uid = identity_checker()
        receipt["evidence"]["unprivileged_account_verified"] = True
        root, work = checked_path(engine_root), checked_path(work_dir)
        for path, private in ((root, False), (work, True)):
            details = path.lstat()
            require(stat.S_ISDIR(details.st_mode) and details.st_uid == uid
                    and not details.st_mode & 0o022
                    and (not private or stat.S_IMODE(details.st_mode) == 0o700), "path_invalid")
            require(not any(path.iterdir()), "existing_state")
        require(root.stat().st_dev == work.stat().st_dev and root != work
                and not root.is_relative_to(work) and not work.is_relative_to(root), "path_invalid")
        receipt_path = Path(receipt_file)
        require(receipt_path == work / "install-result.json", "path_invalid")
        require(not receipt_path.exists() and not receipt_path.is_symlink(), "existing_state")
        validate_url(download_url)
        save_receipt(receipt_path, receipt, first=True)
        safe_receipt = True
        check_space(work, policy.compressed_bytes + policy.expanded_bytes, free_space)
        receipt["stage"] = "download"
        save_receipt(receipt_path, receipt)
        archive_path = work / "engine.zip.part"
        download_archive(download_url, archive_path, policy, deadline, receipt,
                         direct_opener() if opener is None else opener, free_space)
        archive_identity = archive_path.lstat()
        receipt["stage"] = "archive_validation"
        save_receipt(receipt_path, receipt)
        central_offset = inspect_zip_bounds(archive_path, policy, deadline)
        with zipfile.ZipFile(archive_path, "r") as archive:
            plan = plan_archive(archive, policy, central_offset, deadline)
            receipt["entry_count"], receipt["expanded_bytes"] = policy.entry_count, policy.expanded_bytes
            receipt["evidence"]["archive_structure_verified"] = True
            receipt["stage"] = "extraction"
            save_receipt(receipt_path, receipt)
            extract_archive(archive, plan, root, policy, deadline, free_space)
        receipt["evidence"]["archive_integrity_verified"] = True
        receipt["evidence"]["extraction_complete"] = True
        receipt["stage"] = "metadata_validation"
        save_receipt(receipt_path, receipt)
        receipt["metadata_sha256"] = validate_installed_metadata(root, policy, deadline)
        receipt["evidence"]["metadata_verified"] = True
        receipt["evidence"]["executable_permissions_preserved"] = True
        deadline.check()
        current = archive_path.lstat()
        require(stat.S_ISREG(current.st_mode) and current.st_nlink == 1 and current.st_uid == uid
                and (current.st_dev, current.st_ino, current.st_size) ==
                    (archive_identity.st_dev, archive_identity.st_ino, archive_identity.st_size), "path_invalid")
        archive_path.unlink()
        receipt["evidence"]["temporary_archive_removed"] = True
        receipt.update(status="passed", stage="complete", reason_code="installation_passed")
        save_receipt(receipt_path, receipt)
        return receipt
    except InstallError as error:
        reason = error.code
    except (zipfile.BadZipFile, zipfile.LargeZipFile, NotImplementedError, EOFError):
        reason = "archive_invalid"
    except (OSError, UnicodeError):
        reason = "filesystem_failure"
    except KeyboardInterrupt:
        reason = "installer_interrupted"
    except Exception:
        # Exception text can contain a signed URL, pathname or remote response.
        reason = "installer_error"
    receipt.update(status="failed", reason_code=reason)
    if safe_receipt:
        try:
            save_receipt(receipt_path, receipt)
        except (OSError, InstallError):
            receipt["reason_code"] = "filesystem_failure"
    return receipt


def public_summary(receipt):
    evidence = receipt.get("evidence", {})
    passed = (receipt.get("status") == "passed" and receipt.get("reason_code") == "installation_passed"
              and isinstance(evidence, dict) and all(evidence.get(name) is True for name in TRUE_ON_SUCCESS)
              and all(evidence.get(name) is False for name in ALWAYS_FALSE))
    reason = receipt.get("reason_code")
    if reason not in REASONS or (reason == "installation_passed" and not passed):
        reason = "installer_error"
    return {"schema": SCHEMA, "status": "passed" if passed else "failed", "reason_code": reason,
            "engine_installed": passed, **{name: False for name in ALWAYS_FALSE}}


class PrivateArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise InstallError("input_invalid")


def main(argv=None):
    receipt = initial_receipt(ENGINE_ROOT)
    previous_handlers = {}
    try:
        parser = PrivateArgumentParser(description=__doc__)
        parser.add_argument("--metadata-file", required=True)
        parser.add_argument("--work-dir", required=True)
        parser.add_argument("--receipt-file", required=True)
        parser.add_argument("--timeout-seconds", required=True, type=int)
        args = parser.parse_args(argv)
        require(type(args.timeout_seconds) is int and 1 <= args.timeout_seconds <= MAX_TIMEOUT_SECONDS,
                "input_invalid")
        require_build_account()
        def timeout_handler(signum, _frame):
            raise InstallError("installer_timeout" if signum == signal.SIGALRM else "installer_interrupted")
        for signum in (signal.SIGALRM, signal.SIGTERM):
            previous_handlers[signum] = signal.signal(signum, timeout_handler)
        signal.setitimer(signal.ITIMER_REAL, args.timeout_seconds)
        staged_script = checked_path(str(Path(__file__).absolute()))
        script_details = staged_script.lstat()
        require(stat.S_ISREG(script_details.st_mode) and script_details.st_uid == 0
                and not script_details.st_mode & 0o022, "path_invalid")
        policy = load_policy(args.metadata_file)
        url = read_download_input(sys.stdin.buffer)
        receipt = install_engine(policy, ENGINE_ROOT, args.work_dir, args.receipt_file, url,
                                 args.timeout_seconds)
    except InstallError as error:
        receipt.update(status="failed", reason_code=error.code)
    except KeyboardInterrupt:
        receipt.update(status="failed", reason_code="installer_interrupted")
    except Exception:
        receipt.update(status="failed", reason_code="installer_error")
    finally:
        if previous_handlers:
            signal.setitimer(signal.ITIMER_REAL, 0)
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)
    summary = public_summary(receipt)
    print(json.dumps(summary, sort_keys=True, allow_nan=False))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
