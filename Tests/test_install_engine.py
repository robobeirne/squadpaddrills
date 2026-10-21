"""Small synthetic archives exercise installation failures without network or Unreal.

The library's account/path/clock injections are fixture-only. Production CLI has
one fixed engine root and requires the actual unprivileged squadpad account.
"""
from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import warnings
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Tools/Unreal"))
import install_engine as installer

PRIVATE_URL = "https://download.example.invalid/archive.zip?synthetic-private-signature=do-not-print"
COMPILER = "Engine/Extras/ThirdPartyNotUE/SDKs/HostLinux/Linux_x64/fixture/bin/clang"
FILE = stat.S_IFREG | 0o644
EXECUTABLE = stat.S_IFREG | 0o755
LINK = stat.S_IFLNK | 0o777
DIRECTORY = stat.S_IFDIR | 0o755


def sha(data):
    return hashlib.sha256(data).hexdigest()


def archive_fixture(extra=(), modes=None):
    version = {"synthetic_fixture": "build"}
    sdk = {"synthetic_fixture": "sdk"}
    files = [
        (installer.BUILD_VERSION, json.dumps(version).encode(), FILE),
        (installer.SDK_MANIFEST, json.dumps(sdk).encode(), FILE),
        (installer.INSTALLED_MARKER, b"UE_5.8", FILE),
        ("Engine/Binaries/Linux/UnrealEditor", b"SYNTHETIC. NEVER EXECUTE.", EXECUTABLE),
        ("Engine/Binaries/Linux/UnrealEditor-Cmd", b"SYNTHETIC. NEVER EXECUTE.", EXECUTABLE),
        ("Engine/Binaries/DotNET/UnrealBuildTool/UnrealBuildTool.dll", b"SYNTHETIC DLL", FILE),
        ("Engine/Build/BatchFiles/Linux/Build.sh", b"SYNTHETIC. NEVER EXECUTE.", EXECUTABLE),
        (COMPILER, b"SYNTHETIC. NEVER EXECUTE.", EXECUTABLE),
    ]
    modes = {} if modes is None else modes
    files = [(name, data, modes.get(name, mode)) for name, data, mode in files] + list(extra)
    stream = io.BytesIO()
    with warnings.catch_warnings(), zipfile.ZipFile(stream, "w") as archive:
        warnings.simplefilter("ignore", UserWarning)
        for name, data, mode in files:
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = mode << 16
            info.compress_type = zipfile.ZIP_STORED
            archive.writestr(info, data)
    blob = stream.getvalue()
    observed = {name: {"present": True, "uncompressed_bytes": len(data)} for name, data, _ in files[:8]}
    observed["Engine/Build/BatchFiles/Linux/SetupToolchain.sh"] = {
        "present": False, "uncompressed_bytes": None}
    hashes = {"build_version": sha(files[0][1]), "sdk_manifest": sha(files[1][1]),
              "installed_build_marker": sha(files[2][1])}
    policy = installer.ArchivePolicy(len(blob), len(files), sum(len(data) for _, data, _ in files),
                                     observed, hashes, version, sdk, b"UE_5.8", COMPILER)
    return blob, policy


class Response(io.BytesIO):
    def __init__(self, data, status=200, headers=None, on_read=None):
        super().__init__(data)
        self.status = status
        self.headers = {"Content-Length": str(len(data))}
        if headers:
            self.headers.update(headers)
        self.on_read = on_read

    def read(self, size=-1):
        if self.on_read:
            self.on_read()
        return super().read(size)


class InstallEngineTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="squadpad-offline-install-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.number = 0

    def layout(self):
        self.number += 1
        base = self.base / str(self.number)
        base.mkdir(mode=0o700)
        root, work = base / "engine", base / "work"
        root.mkdir(mode=0o750)
        work.mkdir(mode=0o700)
        return root, work

    def run_install(self, blob, policy, *, layout=None, response=None, opener=None, **changes):
        root, work = self.layout() if layout is None else layout
        def fixture_opener(request, timeout):
            self.assertEqual(request.full_url, PRIVATE_URL)
            self.assertGreater(timeout, 0)
            self.assertLessEqual(timeout, 15)
            return Response(blob) if response is None else response
        receipt = installer.install_engine(
            policy, root, work, work / "install-result.json", PRIVATE_URL, 10,
            opener=fixture_opener if opener is None else opener,
            free_space=changes.pop("free_space", lambda _: 1 << 50),
            # The fixture never invokes the production account check or real root.
            identity_checker=lambda: os.geteuid(), **changes)
        return receipt, root, work

    def test_valid_archive_preserves_executables_and_safe_link_chain_without_execution(self):
        blob, policy = archive_fixture([
            ("Assets/", b"", DIRECTORY), ("Assets/data.bin", b"SYNTHETIC DATA", FILE),
            ("Assets/one", b"data.bin", LINK), ("Assets/two", b"one", LINK)],
            modes={"Engine/Build/BatchFiles/Linux/Build.sh": stat.S_IFREG | 0o6755})
        result, root, work = self.run_install(blob, policy)
        self.assertEqual(result["status"], "passed", result)
        self.assertEqual(result["archive_sha256"], sha(blob))
        self.assertEqual(result["archive_bytes"], len(blob))
        self.assertEqual(result["metadata_sha256"], policy.metadata_hashes)
        self.assertEqual((root / "Assets/two").read_bytes(), b"SYNTHETIC DATA")
        self.assertTrue((root / "Assets/two").is_symlink())
        for name in policy.executable_entries:
            self.assertEqual(stat.S_IMODE((root / name).stat().st_mode), 0o755)
        self.assertTrue(all(result["evidence"][name] is True for name in installer.TRUE_ON_SUCCESS))
        self.assertTrue(all(result["evidence"][name] is False for name in installer.ALWAYS_FALSE))
        self.assertEqual({path.name for path in work.iterdir()}, {"install-result.json"})
        receipt_file = work / "install-result.json"
        self.assertEqual(stat.S_IMODE(receipt_file.stat().st_mode), 0o600)
        self.assertEqual(json.loads(receipt_file.read_text()), result)
        self.assertNotIn(PRIVATE_URL, receipt_file.read_text())
        public = installer.public_summary(result)
        self.assertTrue(public["engine_installed"])
        self.assertNotIn(str(root), json.dumps(public))
        self.assertNotIn(result["archive_sha256"], json.dumps(public))

    def test_zip64_record_is_checked_before_loading_members(self):
        blob, policy = archive_fixture()
        position = blob.rfind(b"PK\x05\x06")
        legacy = bytearray(blob[position:])
        _, _, _, _, count, size, offset, _ = struct.unpack_from("<4sHHHHIIH", legacy)
        record = struct.pack("<4sQHHIIQQQQ", b"PK\x06\x06", 44, 45, 45, 0, 0,
                             count, count, size, offset)
        locator = struct.pack("<4sIQI", b"PK\x06\x07", 0, position, 1)
        struct.pack_into("<HHII", legacy, 8, 0xffff, 0xffff, 0xffffffff, 0xffffffff)
        zip64 = blob[:position] + record + locator + legacy
        result, _, _ = self.run_install(zip64, replace(policy, compressed_bytes=len(zip64)))
        self.assertEqual(result["status"], "passed", result)
        bad = bytearray(zip64)
        struct.pack_into("<Q", bad, position + 4, 4097)
        with patch.object(installer.zipfile, "ZipFile", wraps=zipfile.ZipFile) as loader:
            result, _, _ = self.run_install(bytes(bad), replace(policy, compressed_bytes=len(bad)))
        self.assertEqual(result["reason_code"], "archive_invalid")
        loader.assert_not_called()

    def test_path_traversal_ambiguity_duplicates_and_parent_collisions_fail_before_extraction(self):
        variants = [
            [("../outside", b"x", FILE)], [("/outside", b"x", FILE)],
            [("C:/outside", b"x", FILE)], [("a\\outside", b"x", FILE)],
            [("a//outside", b"x", FILE)], [("a/./outside", b"x", FILE)],
            [("a/ outside", b"x", FILE)], [("a/\noutside", b"x", FILE)],
            [("a", b"x", FILE), ("a", b"y", FILE)],
            [("a", b"x", FILE), ("a/child", b"y", FILE)],
            [("a/", b"", DIRECTORY), ("a", b"x", FILE)],
            [("a", b"", stat.S_IFIFO | 0o600)],
        ]
        for extra in variants:
            with self.subTest(extra=extra):
                blob, policy = archive_fixture(extra)
                result, root, work = self.run_install(blob, policy)
                self.assertEqual(result["status"], "failed")
                self.assertFalse(result["evidence"]["extraction_complete"])
                self.assertEqual(list(root.iterdir()), [])
                self.assertTrue((work / "engine.zip.part").is_file())

    def test_symlinks_cannot_escape_dangle_cycle_or_supply_write_parents(self):
        variants = [
            [("a", b"/etc/passwd", LINK)], [("a", b"../outside", LINK)],
            [("a", b"missing", LINK)], [("a", b"b", LINK), ("b", b"a", LINK)],
            [("a/back", b".", LINK)],
            [("a", b"Engine", LINK), ("a/child", b"x", FILE)],
            [("other/file", b"x", FILE), ("outside", b"x", FILE),
             ("dir/alias", b"../other", LINK), ("dir/attack", b"alias/../../outside", LINK)],
        ]
        for extra in variants:
            with self.subTest(extra=extra):
                blob, policy = archive_fixture(extra)
                result, root, _ = self.run_install(blob, policy)
                self.assertIn(result["reason_code"], {"archive_link_invalid", "archive_collision"})
                self.assertEqual(list(root.iterdir()), [])

    def test_header_limits_and_declared_size_mismatch_fail_closed(self):
        blob, policy = archive_fixture()
        position = blob.rfind(b"PK\x05\x06")
        corrupt = bytearray(blob)
        struct.pack_into("<I", corrupt, position + 12, installer.MAX_CENTRAL_BYTES + 1)
        for data, expectations in ((bytes(corrupt), policy),
                                   (blob, replace(policy, entry_count=policy.entry_count + 1)),
                                   (blob, replace(policy, expanded_bytes=policy.expanded_bytes + 1)),
                                   (blob[:-1] + b"x", policy)):
            with self.subTest(expectations=expectations):
                result, root, _ = self.run_install(data, expectations)
                self.assertEqual(result["reason_code"], "archive_invalid")
                self.assertEqual(list(root.iterdir()), [])

    def test_crc_and_reviewed_hash_mismatch_keep_archive_for_private_diagnosis(self):
        blob, policy = archive_fixture([("Assets/data.bin", b"UNIQUE SYNTHETIC PAYLOAD", FILE)])
        corrupt = blob.replace(b"UNIQUE SYNTHETIC PAYLOAD", b"BROKEN SYNTHETIC PAYLOAD")
        result, _, work = self.run_install(corrupt, policy)
        self.assertEqual(result["reason_code"], "archive_invalid", result)
        self.assertFalse(result["evidence"]["archive_integrity_verified"])
        self.assertTrue((work / "engine.zip.part").is_file())
        hashes = dict(policy.metadata_hashes, build_version="0" * 64)
        result, _, work = self.run_install(blob, replace(policy, metadata_hashes=hashes))
        self.assertEqual(result["reason_code"], "archive_metadata_mismatch")
        self.assertTrue(result["evidence"]["archive_integrity_verified"])
        self.assertFalse(result["evidence"]["metadata_verified"])
        self.assertTrue((work / "engine.zip.part").is_file())

    def test_encrypted_unsupported_and_nul_truncated_member_names_are_refused(self):
        blob, policy = archive_fixture()
        position = blob.find(b"PK\x01\x02")
        encrypted = bytearray(blob)
        struct.pack_into("<H", encrypted, position + 8, 1)
        unsupported = bytearray(blob)
        struct.pack_into("<H", unsupported, position + 10, 99)
        nul_name = bytearray(blob)
        nul_name[position + 46 + 6] = 0
        for corrupted in (encrypted, unsupported, nul_name):
            result, root, _ = self.run_install(bytes(corrupted), policy)
            self.assertEqual(result["reason_code"], "archive_invalid")
            self.assertEqual(list(root.iterdir()), [])

    def test_missing_executable_mode_is_not_silently_repaired(self):
        blob, policy = archive_fixture(modes={COMPILER: FILE})
        result, root, _ = self.run_install(blob, policy)
        self.assertEqual(result["reason_code"], "permissions_invalid")
        self.assertEqual(list(root.iterdir()), [])

    def test_short_extra_partial_encoded_and_network_failures_never_leak_url(self):
        blob, policy = archive_fixture()
        responses = [
            Response(blob[:-1], headers={"Content-Length": str(len(blob))}),
            Response(blob + b"extra", headers={"Content-Length": str(len(blob))}),
            Response(blob, status=206), Response(blob, headers={"Content-Encoding": "gzip"}),
            Response(blob, headers={"Content-Length": "unknown"}),
        ]
        for response in responses:
            with self.subTest(status=response.status, headers=response.headers):
                result, root, work = self.run_install(blob, policy, response=response)
                self.assertEqual(result["reason_code"], "download_invalid")
                self.assertEqual(list(root.iterdir()), [])
                self.assertFalse(result["evidence"]["download_complete"])
                self.assertNotIn(PRIVATE_URL, (work / "install-result.json").read_text())
        def failing_opener(*_args, **_kwargs):
            raise urllib.error.URLError(PRIVATE_URL)
        result, _, work = self.run_install(blob, policy, opener=failing_opener)
        self.assertEqual(result["reason_code"], "download_failed")
        self.assertNotIn(PRIVATE_URL, json.dumps(result))
        self.assertNotIn(PRIVATE_URL, json.dumps(installer.public_summary(result)))
        self.assertNotIn(PRIVATE_URL, (work / "install-result.json").read_text())

    def test_timeout_during_stream_preserves_partial_and_stops_before_extraction(self):
        blob, policy = archive_fixture()
        state = {"time": 0, "reads": 0}
        def on_read():
            state["reads"] += 1
            if state["reads"] == 2:
                state["time"] = 11
        response = Response(blob, on_read=on_read)
        with patch.object(installer, "CHUNK_BYTES", 8):
            result, root, work = self.run_install(blob, policy, response=response,
                                                   clock=lambda: state["time"])
        self.assertEqual(result["reason_code"], "installer_timeout")
        self.assertEqual((work / "engine.zip.part").stat().st_size, 8)
        self.assertEqual(list(root.iterdir()), [])
        self.assertFalse(result["evidence"]["download_complete"])

    def test_timeout_during_extraction_keeps_partial_install_and_cannot_pass(self):
        blob, policy = archive_fixture()
        state = {"time": 0, "space_checks": 0}
        def free_space(_):
            state["space_checks"] += 1
            if state["space_checks"] == 3:
                state["time"] = 11
            return 1 << 50
        result, root, work = self.run_install(blob, policy, clock=lambda: state["time"],
                                               free_space=free_space)
        self.assertEqual(result["reason_code"], "installer_timeout")
        self.assertEqual(result["stage"], "extraction")
        self.assertTrue(result["evidence"]["archive_structure_verified"])
        self.assertFalse(result["evidence"]["extraction_complete"])
        self.assertTrue(any(root.iterdir()))
        self.assertTrue((work / "engine.zip.part").is_file())
        self.assertFalse(installer.public_summary(result)["engine_installed"])

    def test_disk_reserve_includes_compressed_expanded_and_safety_bytes(self):
        blob, policy = archive_fixture()
        available = policy.compressed_bytes + policy.expanded_bytes + installer.DISK_RESERVE_BYTES - 1
        with patch.object(installer, "direct_opener") as opener:
            result, root, work = self.run_install(blob, policy, free_space=lambda _: available)
        opener.assert_not_called()
        self.assertEqual(result["reason_code"], "insufficient_disk")
        self.assertEqual(list(root.iterdir()), [])
        self.assertEqual({path.name for path in work.iterdir()}, {"install-result.json"})

    def test_existing_or_symlink_paths_are_never_overwritten_or_deleted(self):
        blob, policy = archive_fixture()
        for occupied in ("engine", "work"):
            root, work = self.layout()
            protected = (root if occupied == "engine" else work) / "existing"
            protected.write_text("KEEP")
            result, _, _ = self.run_install(blob, policy, layout=(root, work))
            self.assertEqual(result["reason_code"], "existing_state")
            self.assertEqual(protected.read_text(), "KEEP")
            self.assertFalse((work / "install-result.json").exists())
        root, work = self.layout()
        alias = root.with_name("alias")
        alias.symlink_to(root, target_is_directory=True)
        result, _, _ = self.run_install(blob, policy, layout=(alias, work))
        self.assertEqual(result["reason_code"], "path_invalid")
        self.assertTrue(alias.is_symlink())
        self.assertEqual(list(root.iterdir()), [])

    def test_private_stdin_and_cli_do_not_accept_urls_or_path_overrides_as_arguments(self):
        self.assertEqual(installer.read_download_input(io.BytesIO(
            json.dumps({"download_url": PRIVATE_URL}).encode())), PRIVATE_URL)
        invalid = [b'{"download_url":"https://a.invalid/","download_url":"https://b.invalid/"}',
                   b'{"download_url":"http://a.invalid/"}', b'{"download_url":"https://user:pass@a.invalid/"}',
                   b'{"download_url":"https://a.invalid/#fragment"}',
                   b'{"download_url":"https://a.invalid/", "extra":true}',
                   b'{"download_url":NaN}', b" " * (installer.MAX_INPUT_BYTES + 1)]
        for raw in invalid:
            with self.subTest(raw=raw[:80]), self.assertRaises(installer.InstallError):
                installer.read_download_input(io.BytesIO(raw))
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            status = installer.main(["--download-url", PRIVATE_URL, "--engine-root", str(self.base)])
        self.assertEqual(status, 1)
        self.assertEqual(json.loads(stdout.getvalue())["reason_code"], "input_invalid")
        self.assertNotIn(PRIVATE_URL, stdout.getvalue())
        self.assertNotIn(str(self.base), stdout.getvalue())
        with self.assertRaisesRegex(installer.InstallError, "download_invalid"):
            installer.NoRedirect().redirect_request(None, None, 302, PRIVATE_URL, {}, PRIVATE_URL)

    def test_production_identity_and_reviewed_metadata_are_required(self):
        with patch.object(installer.os, "geteuid", return_value=0):
            with self.assertRaisesRegex(installer.InstallError, "account_invalid"):
                installer.require_build_account()
        with patch.object(installer.os, "geteuid", return_value=1234), \
                patch.object(installer.os, "getuid", return_value=1234), \
                patch.object(installer.pwd, "getpwuid") as account:
            account.return_value.pw_name = "unexpected-account"
            with self.assertRaisesRegex(installer.InstallError, "account_invalid"):
                installer.require_build_account()
        metadata = json.loads((ROOT / "Docs/ENGINE_ARCHIVE.json").read_text())
        policy = installer.policy_from_metadata(metadata)
        self.assertEqual(policy.compressed_bytes, 39819212076)
        self.assertEqual(policy.entry_count, 299959)
        self.assertEqual(policy.expanded_bytes, 76967950798)
        metadata["zip_entry_count"] = True
        with self.assertRaisesRegex(installer.InstallError, "metadata_invalid"):
            installer.policy_from_metadata(metadata)

    def test_status_alone_and_arbitrary_diagnostic_strings_cannot_become_public_success(self):
        receipt = installer.initial_receipt(PRIVATE_URL)
        receipt.update(status="passed", reason_code="installation_passed", stage=PRIVATE_URL)
        public = installer.public_summary(receipt)
        self.assertFalse(public["engine_installed"])
        self.assertEqual(public["reason_code"], "installer_error")
        receipt["reason_code"] = PRIVATE_URL
        self.assertNotIn(PRIVATE_URL, json.dumps(installer.public_summary(receipt)))


if __name__ == "__main__":
    unittest.main()
