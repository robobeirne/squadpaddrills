"""Seal bounded worker evidence for a public-repository Actions artifact.

Only ciphertext leaves the runner. Encryption uses the official age executable
and the public recipient derived from a private Ed25519 SSH identity. This does
not establish native success, artifact retrieval or private GitHub storage.
Requires Python 3.10+, Linux, OpenSSH and age; no custom cryptography is used.
"""
from __future__ import annotations

import argparse
import base64
import binascii
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import subprocess
import sys
import tarfile

from remote_native_gate import FILE_LIMITS as NATIVE_FILE_LIMITS

SCHEMA = "squadpad.sealed-evidence.v1"
MANIFEST_SCHEMA = "squadpad.evidence-manifest.v1"
FILE_LIMITS = {
    "native": dict(NATIVE_FILE_LIMITS),
    "bootstrap": {
        "bootstrap-result.json": 1024 * 1024,
        "bootstrap-stdout.log": 8 * 1024 * 1024,
        "bootstrap-stderr.log": 8 * 1024 * 1024,
    },
}
IDENTITY_LIMIT = 16 * 1024
CRYPTO_TIMEOUT_SECONDS = 120
PROBE = b"SquadPad evidence encryption preflight v1\n"
SAFE_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C",
            "SSH_ASKPASS_REQUIRE": "never"}
ERROR_CODES = {
    "arguments_invalid", "input_invalid", "evidence_invalid", "evidence_missing",
    "evidence_too_large", "identity_invalid", "identity_unsupported",
    "encryption_unavailable", "encryption_failed", "preflight_failed",
    "output_invalid", "output_exists", "operation_failed", "interrupted",
}


class EvidenceError(Exception):
    pass


def require(condition, code):
    if not condition:
        raise EvidenceError(code)


def absolute_path(value, code):
    path = Path(value)
    require(".." not in path.parts and "\x00" not in str(path), code)
    return Path(os.path.abspath(path))


@contextmanager
def directory_fd(path, code):
    """Open each path component without following a symlink."""
    path = absolute_path(path, code)
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                              dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_fd
        yield descriptor
    except OSError:
        raise EvidenceError(code) from None
    finally:
        os.close(descriptor)


@contextmanager
def identity_fd(path):
    path = absolute_path(path, "identity_invalid")
    with directory_fd(path.parent, "identity_invalid") as parent:
        try:
            descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        except OSError:
            raise EvidenceError("identity_invalid") from None
        try:
            info = os.fstat(descriptor)
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                    and info.st_uid == os.geteuid() and not info.st_mode & 0o077
                    and 0 < info.st_size <= IDENTITY_LIMIT, "identity_invalid")
            yield descriptor
        finally:
            os.close(descriptor)


def executable(name):
    path = shutil.which(name)
    require(path is not None, "encryption_unavailable")
    return path


def crypto_command(command, *, data=None, output=subprocess.PIPE, pass_fds=(),
                   timeout=10, failure="encryption_failed"):
    """Capture or discard diagnostics; never let a tool echo key paths publicly."""
    try:
        kwargs = {"input": data} if data is not None else {"stdin": subprocess.DEVNULL}
        result = subprocess.run(command, stdout=output, stderr=subprocess.DEVNULL,
                                env=SAFE_ENV, pass_fds=pass_fds, timeout=timeout,
                                check=False, **kwargs)
    except (OSError, subprocess.SubprocessError):
        raise EvidenceError(failure) from None
    require(result.returncode == 0, failure)
    return result.stdout


def recipient(descriptor):
    # Inherit the already validated descriptor, avoiding a second pathname lookup.
    public = crypto_command(
        [executable("ssh-keygen"), "-y", "-P", "", "-f", f"/proc/self/fd/{descriptor}"],
        pass_fds=(descriptor,), failure="identity_unsupported")
    require(isinstance(public, bytes) and len(public) <= 2048, "identity_unsupported")
    try:
        fields = public.decode("ascii").split()
        require(len(fields) >= 2 and fields[0] == "ssh-ed25519", "identity_unsupported")
        wire = base64.b64decode(fields[1], validate=True)
    except (UnicodeError, ValueError, binascii.Error):
        raise EvidenceError("identity_unsupported") from None
    require(len(wire) == 51 and wire[:19] == b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20",
            "identity_unsupported")
    # SSH comments can contain private account or machine names. Discard them.
    return "ssh-ed25519 " + fields[1]


def result(status="failed", reason="operation_failed", kind=None):
    return {"schema": SCHEMA, "status": status, "reason_code": reason,
            "kind": kind if kind in FILE_LIMITS else None,
            "encryption_ready": False, "ciphertext_created": False,
            "evidence_file_count": 0, "allowlist_complete": False,
            "ciphertext_sha256": None, "decrypted_evidence_verified": False,
            "native_success_established": False}


def preflight(identity_file):
    age = executable("age")
    with identity_fd(identity_file) as descriptor:
        public = recipient(descriptor)
        sealed = crypto_command([age, "--encrypt", "--recipient", public], data=PROBE,
                                failure="preflight_failed")
        require(isinstance(sealed, bytes) and 32 < len(sealed) < 16384, "preflight_failed")
        plain = crypto_command([age, "--decrypt", "--identity", f"/proc/self/fd/{descriptor}"],
                               data=sealed, pass_fds=(descriptor,), failure="preflight_failed")
        require(plain == PROBE, "preflight_failed")
    report = result("ready", "preflight_passed")
    report["encryption_ready"] = True
    return report


def read_evidence(root_fd, name, limit):
    """Read one literal allowlisted file; missing files permit partial evidence."""
    parts = Path(name).parts
    parent = os.dup(root_fd)
    descriptor = None
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = next_fd
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "evidence_invalid")
        require(info.st_size <= limit, "evidence_too_large")
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            raw = stream.read(limit + 1)
        after = os.fstat(descriptor)
        require(len(raw) <= limit, "evidence_too_large")
        require(len(raw) == info.st_size and info.st_size == after.st_size
                and info.st_mtime_ns == after.st_mtime_ns, "evidence_invalid")
        return raw
    except FileNotFoundError:
        return None
    except OSError:
        raise EvidenceError("evidence_invalid") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)


def evidence_archive(kind, input_dir):
    require(kind in FILE_LIMITS, "arguments_invalid")
    limits = FILE_LIMITS[kind]
    files = {}
    with directory_fd(input_dir, "input_invalid") as root_fd:
        for name, limit in limits.items():
            raw = read_evidence(root_fd, name, limit)
            if raw is not None:
                files[name] = raw
    require(files, "evidence_missing")
    missing = sorted(set(limits) - set(files))
    manifest = {
        "schema": MANIFEST_SCHEMA, "kind": kind,
        "allowlist_complete": not missing, "missing_files": missing,
        "files": [{"path": name, "size_bytes": len(raw),
                   "sha256": hashlib.sha256(raw).hexdigest()}
                  for name, raw in sorted(files.items())],
        "native_success_established": False,
    }
    # The temporary plaintext archive exists only in bounded process memory.
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w", format=tarfile.USTAR_FORMAT) as bundle:
        entries = {"manifest.json": (json.dumps(manifest, sort_keys=True) + "\n").encode()}
        entries.update(files)
        for name, raw in sorted(entries.items()):
            member = tarfile.TarInfo(name)
            member.size = len(raw)
            member.mode = 0o600
            member.mtime = member.uid = member.gid = 0
            member.uname = member.gname = ""
            bundle.addfile(member, io.BytesIO(raw))
    data = archive.getvalue()
    require(len(data) <= sum(limits.values()) + 65536, "evidence_too_large")
    return data, manifest


def encrypt_to_file(age, public, data, output_file, maximum_bytes):
    path = absolute_path(output_file, "output_invalid")
    require(path.name in ("native-evidence.tar.age", "bootstrap-evidence.tar.age"), "output_invalid")
    with directory_fd(path.parent, "output_invalid") as parent:
        try:
            os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise EvidenceError("output_exists")
        temporary = ".sealed-evidence-" + secrets.token_hex(12) + ".tmp"
        descriptor = os.open(temporary, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=parent)
        try:
            crypto_command([age, "--encrypt", "--recipient", public], data=data,
                           output=descriptor, timeout=CRYPTO_TIMEOUT_SECONDS)
            size = os.fstat(descriptor).st_size
            require(32 < size <= maximum_bytes, "encryption_failed")
            os.fsync(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            require(os.read(descriptor, 22) == b"age-encryption.org/v1\n", "encryption_failed")
            os.lseek(descriptor, 0, os.SEEK_SET)
            digest = hashlib.sha256()
            while block := os.read(descriptor, 1024 * 1024):
                digest.update(block)
            # A same-directory hard link publishes atomically without overwriting.
            try:
                os.link(temporary, path.name, src_dir_fd=parent, dst_dir_fd=parent,
                        follow_symlinks=False)
            except FileExistsError:
                raise EvidenceError("output_exists") from None
            return digest.hexdigest()
        finally:
            os.close(descriptor)
            os.unlink(temporary, dir_fd=parent)


def seal_evidence(kind, input_dir, identity_file, output_file):
    require(kind in FILE_LIMITS, "arguments_invalid")
    source = absolute_path(input_dir, "input_invalid")
    identity = absolute_path(identity_file, "identity_invalid")
    destination = absolute_path(output_file, "output_invalid")
    require(source not in identity.parents and source != identity, "identity_invalid")
    require(source not in destination.parents and source != destination, "output_invalid")
    require(destination.name == kind + "-evidence.tar.age", "output_invalid")
    age = executable("age")
    with identity_fd(identity) as descriptor:
        public = recipient(descriptor)
        data, manifest = evidence_archive(kind, source)
        digest = encrypt_to_file(age, public, data, destination,
                                 sum(FILE_LIMITS[kind].values()) + 1024 * 1024)
    report = result("sealed", "evidence_sealed", kind)
    report.update(encryption_ready=True, ciphertext_created=True,
                  evidence_file_count=len(manifest["files"]),
                  allowlist_complete=manifest["allowlist_complete"], ciphertext_sha256=digest)
    return report


class QuietParser(argparse.ArgumentParser):
    def error(self, message):
        raise EvidenceError("arguments_invalid")


def main(argv=None):
    kind = None
    try:
        parser = QuietParser(description=__doc__)
        parser.add_argument("--preflight", action="store_true")
        parser.add_argument("--kind", choices=tuple(FILE_LIMITS))
        parser.add_argument("--input-dir", type=Path)
        parser.add_argument("--identity-file", type=Path, required=True)
        parser.add_argument("--output-file", type=Path)
        args = parser.parse_args(argv)
        kind = args.kind
        if args.preflight:
            require(args.kind is None and args.input_dir is None and args.output_file is None,
                    "arguments_invalid")
            report = preflight(args.identity_file)
        else:
            require(args.kind is not None and args.input_dir is not None
                    and args.output_file is not None, "arguments_invalid")
            report = seal_evidence(args.kind, args.input_dir, args.identity_file, args.output_file)
        code = 0
    except EvidenceError as error:
        reason = str(error) if str(error) in ERROR_CODES else "operation_failed"
        report, code = result(reason=reason, kind=kind), 2
    except KeyboardInterrupt:
        report, code = result(reason="interrupted", kind=kind), 2
    except Exception:
        report, code = result(kind=kind), 2
    print(json.dumps(report, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
