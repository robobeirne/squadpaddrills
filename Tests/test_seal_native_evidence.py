"""Evidence privacy checks. Real encryption uses temporary synthetic SSH keys only."""
from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Tools/Unreal"))
import seal_native_evidence as evidence

PRIVATE = "SYNTHETIC_PRIVATE_EVIDENCE_DO_NOT_PUBLISH"


def unpack(raw):
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        members = archive.getmembers()
        contents = {item.name: archive.extractfile(item).read() for item in members}
    return members, contents, json.loads(contents["manifest.json"])


class EvidenceFixture:
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="squadpad-evidence-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "input"
        self.source.mkdir(mode=0o700)
        self.output = self.root / "sealed"
        self.output.mkdir(mode=0o700)
        self.key = self.root / "synthetic-key"
        self.key.write_text("SYNTHETIC INVALID KEY, NOT A CREDENTIAL\n")
        self.key.chmod(0o600)

    def write(self, name, value=PRIVATE):
        target = self.source / name
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.write_text(value)
        return target


class EvidencePackagingTests(EvidenceFixture, unittest.TestCase):
    def test_archive_contains_only_literal_allowlist_and_private_manifest(self):
        for name in evidence.FILE_LIMITS["native"]:
            self.write(name, PRIVATE + name)
        self.write("id_ed25519", "EXCLUDED KEY")
        self.write("download-url.txt", "EXCLUDED URL")
        self.write("remote-summary.json", "EXCLUDED SUMMARY")
        self.write("other/nested.log", "EXCLUDED DIRECTORY")
        raw, manifest = evidence.evidence_archive("native", self.source)
        members, contents, decoded = unpack(raw)
        self.assertEqual(set(contents), set(evidence.FILE_LIMITS["native"]) | {"manifest.json"})
        self.assertEqual(decoded, manifest)
        self.assertTrue(decoded["allowlist_complete"])
        self.assertFalse(decoded["native_success_established"])
        for item in members:
            self.assertTrue(item.isfile())
            self.assertEqual((item.uid, item.gid, item.uname, item.gname, item.mode, item.mtime),
                             (0, 0, "", "", 0o600, 0))
        for record in decoded["files"]:
            self.assertEqual(record["size_bytes"], len(contents[record["path"]]))
            self.assertEqual(record["sha256"], hashlib.sha256(contents[record["path"]]).hexdigest())
        self.assertNotIn(b"EXCLUDED", raw)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_partial_evidence_declares_missing_files_without_claiming_success(self):
        self.write("receipt.json", "{}")
        _, manifest = evidence.evidence_archive("native", self.source)
        self.assertFalse(manifest["allowlist_complete"])
        self.assertFalse(manifest["native_success_established"])
        self.assertEqual(manifest["missing_files"], sorted(set(evidence.FILE_LIMITS["native"]) - {"receipt.json"}))
        self.assertEqual([record["path"] for record in manifest["files"]], ["receipt.json"])

    def test_missing_oversized_and_escaping_inputs_are_rejected(self):
        self.write("unrelated.log")
        with self.assertRaisesRegex(evidence.EvidenceError, "evidence_missing"):
            evidence.evidence_archive("native", self.source)
        target = self.write("receipt.json", "")
        with target.open("wb") as stream:
            stream.truncate(evidence.FILE_LIMITS["native"]["receipt.json"] + 1)
        with self.assertRaisesRegex(evidence.EvidenceError, "evidence_too_large"):
            evidence.evidence_archive("native", self.source)
        with self.assertRaisesRegex(evidence.EvidenceError, "input_invalid"):
            evidence.evidence_archive("native", self.source / ".." / "input")

    def test_symlinks_hardlinks_and_special_files_are_not_followed(self):
        outside = self.root / "outside"
        outside.write_text(PRIVATE)
        target = self.source / "receipt.json"
        for create in (lambda: target.symlink_to(outside),
                       lambda: os.link(outside, target),
                       lambda: os.mkfifo(target)):
            create()
            with self.assertRaisesRegex(evidence.EvidenceError, "evidence_invalid"):
                evidence.evidence_archive("native", self.source)
            target.unlink()
        (self.source / "automation").symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(evidence.EvidenceError, "evidence_invalid"):
            evidence.evidence_archive("native", self.source)
        linked_source = self.root / "linked-input"
        linked_source.symlink_to(self.source, target_is_directory=True)
        with self.assertRaisesRegex(evidence.EvidenceError, "input_invalid"):
            evidence.evidence_archive("native", linked_source)

    def test_identity_rejects_public_permissions_symlinks_hardlinks_and_fifos(self):
        self.key.chmod(0o644)
        with self.assertRaisesRegex(evidence.EvidenceError, "identity_invalid"):
            with evidence.identity_fd(self.key):
                self.fail("identity accepted")
        self.key.chmod(0o600)
        alias = self.root / "identity-alias"
        for create in (lambda: alias.symlink_to(self.key),
                       lambda: os.link(self.key, alias),
                       lambda: os.mkfifo(alias, mode=0o600)):
            create()
            with self.assertRaisesRegex(evidence.EvidenceError, "identity_invalid"):
                with evidence.identity_fd(alias):
                    self.fail("identity accepted")
            alias.unlink()

    def test_existing_or_symlinked_output_is_never_overwritten(self):
        target = self.output / "native-evidence.tar.age"
        target.write_bytes(b"KEEP EXISTING")
        for path in (target, self.output / ".." / "native-evidence.tar.age"):
            with self.assertRaises(evidence.EvidenceError):
                evidence.encrypt_to_file("unused", "unused", b"data", path, 1024)
        self.assertEqual(target.read_bytes(), b"KEEP EXISTING")
        linked = self.root / "linked-output"
        linked.symlink_to(self.output, target_is_directory=True)
        with self.assertRaisesRegex(evidence.EvidenceError, "output_invalid"):
            evidence.encrypt_to_file("unused", "unused", b"data", linked / target.name, 1024)

    def test_encryption_failure_removes_partial_output_and_never_echoes_private_text(self):
        self.write("receipt.json")
        target = self.output / "native-evidence.tar.age"

        def failed_encrypt(_command, *, data, output, **_kwargs):
            os.write(output, data)  # Simulate a broken external tool writing plaintext.
            raise evidence.EvidenceError("encryption_failed")

        public = io.StringIO()
        with patch.object(evidence, "executable", return_value="synthetic-tool"), \
             patch.object(evidence, "recipient", return_value="ssh-ed25519 synthetic"), \
             patch.object(evidence, "crypto_command", side_effect=failed_encrypt), redirect_stdout(public):
            code = evidence.main(["--kind", "native", "--input-dir", str(self.source),
                                  "--identity-file", str(self.key), "--output-file", str(target)])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(public.getvalue())["reason_code"], "encryption_failed")
        self.assertNotIn(PRIVATE, public.getvalue())
        self.assertNotIn(str(self.root), public.getvalue())
        self.assertEqual(list(self.output.iterdir()), [])

    def test_parser_and_unexpected_errors_have_only_fixed_public_output(self):
        cases = (["--unknown", PRIVATE], ["--kind", PRIVATE, "--identity-file", str(self.key)])
        for arguments in cases:
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(evidence.main(arguments), 2)
            self.assertEqual(json.loads(output.getvalue())["reason_code"], "arguments_invalid")
            self.assertNotIn(PRIVATE, output.getvalue())
        output = io.StringIO()
        with patch.object(evidence, "preflight", side_effect=ValueError(PRIVATE)), redirect_stdout(output):
            self.assertEqual(evidence.main(["--preflight", "--identity-file", str(self.key)]), 2)
        self.assertNotIn(PRIVATE, output.getvalue())
        self.assertEqual(json.loads(output.getvalue())["reason_code"], "operation_failed")


@unittest.skipUnless(shutil.which("age") and shutil.which("ssh-keygen"), "official age and OpenSSH required")
class EvidenceEncryptionTests(EvidenceFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.key.unlink()
        self.generate_key(self.key)

    def generate_key(self, path, kind="ed25519", password=""):
        command = ["ssh-keygen", "-q", "-t", kind, "-N", password,
                   "-C", "synthetic-evidence-test", "-f", str(path)]
        if kind == "rsa":
            command += ["-b", "1024"]
        subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=True, timeout=10)

    def decrypt(self, payload, identity=None):
        return subprocess.run(["age", "--decrypt", "--identity", str(identity or self.key)],
                              input=payload, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              check=False, timeout=10)

    def test_real_preflight_and_native_roundtrip_preserve_contents_and_manifest(self):
        probe = evidence.preflight(self.key)
        self.assertEqual(probe["status"], "ready")
        self.assertTrue(probe["encryption_ready"])
        self.assertFalse(probe["ciphertext_created"])
        for name in evidence.FILE_LIMITS["native"]:
            self.write(name)
        destination = self.output / "native-evidence.tar.age"
        report = evidence.seal_evidence("native", self.source, self.key, destination)
        encrypted = destination.read_bytes()
        self.assertNotIn(PRIVATE.encode(), encrypted)
        self.assertNotIn(PRIVATE, json.dumps(report))
        self.assertEqual(report["ciphertext_sha256"], hashlib.sha256(encrypted).hexdigest())
        self.assertEqual(report["evidence_file_count"], 5)
        self.assertTrue(report["allowlist_complete"])
        self.assertFalse(report["decrypted_evidence_verified"])
        self.assertFalse(report["native_success_established"])
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
        decoded = self.decrypt(encrypted)
        self.assertEqual(decoded.returncode, 0)
        _, contents, manifest = unpack(decoded.stdout)
        self.assertEqual(len(manifest["files"]), 5)
        self.assertEqual(contents["receipt.json"], PRIVATE.encode())
        self.assertEqual(list(self.output.iterdir()), [destination])

    def test_tampered_ciphertext_and_wrong_identity_fail_authentication(self):
        for name in evidence.FILE_LIMITS["bootstrap"]:
            self.write(name)
        destination = self.output / "bootstrap-evidence.tar.age"
        evidence.seal_evidence("bootstrap", self.source, self.key, destination)
        encrypted = destination.read_bytes()
        tampered = encrypted[:-1] + bytes([encrypted[-1] ^ 1])
        self.assertNotEqual(self.decrypt(tampered).returncode, 0)
        other = self.root / "different-synthetic-key"
        self.generate_key(other)
        self.assertNotEqual(self.decrypt(encrypted, other).returncode, 0)

    def test_malformed_non_ed25519_and_passphrase_protected_identities_are_rejected(self):
        malformed = self.root / "malformed"
        malformed.write_text("NOT A KEY")
        malformed.chmod(0o600)
        rsa = self.root / "synthetic-rsa"
        self.generate_key(rsa, kind="rsa")
        protected = self.root / "synthetic-protected"
        self.generate_key(protected, password="temporary-synthetic-password")
        for key in (malformed, rsa, protected):
            with self.subTest(key=key.name):
                with self.assertRaisesRegex(evidence.EvidenceError, "identity_unsupported"):
                    evidence.preflight(key)


if __name__ == "__main__":
    unittest.main()
