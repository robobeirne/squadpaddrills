# Encrypted worker evidence

The repository is public. Its Actions artifacts are **not private storage**:
signed-in users with repository read access can download them. These workflows
upload only an encrypted file. Confidentiality depends on retaining the matching
private SSH identity securely; artifact visibility, retention and access controls
do not replace encryption.

This is a prepared evidence handoff, not a record of an executed Unreal build.
An encryption preflight, successful upload, complete file list or green bootstrap
job does not establish that private evidence was retrieved or that a native build
passed. The native readiness gate remains disabled unless separately enabled
after the execution prerequisites in [NATIVE_EXECUTION.md](NATIVE_EXECUTION.md)
and the independent deletion procedure are satisfied.

## What is encrypted

`Tools/Unreal/seal_native_evidence.py` accepts only these literal paths, relative
to the selected evidence directory. A MiB is 1,048,576 bytes.

| Kind | Path | Maximum size |
| --- | --- | ---: |
| `native` | `receipt.json` | 1 MiB |
| `native` | `build.log` | 8 MiB |
| `native` | `editor-stdout.log` | 8 MiB |
| `native` | `editor.log` | 8 MiB |
| `native` | `automation/index.json` | 10 MiB |
| `bootstrap` | `bootstrap-result.json` | 1 MiB |
| `bootstrap` | `bootstrap-stdout.log` | 8 MiB |
| `bootstrap` | `bootstrap-stderr.log` | 8 MiB |

The native limits are imported from `remote_native_gate.py`, preserving its
existing five-file contract. The bootstrap coordinator applies its own tighter
4 MiB limit to each log before sealing. All other files and directories are
ignored: there is no recursive directory upload. In particular, identity files,
known-hosts files, signed-URL files, configuration files and `remote-summary.json`
are not added to the archive. An allowlisted log can still contain sensitive
worker information; it remains encrypted rather than being published as text.

The helper rejects symlinks in input paths, hard-linked or nonregular evidence
files, path traversal, oversized files and unknown kinds. It rejects private keys
with group/world permissions, a different owner, multiple hard links, symlinked
paths, malformed contents, or an unsupported key type. The existing client
identity must be an unencrypted OpenSSH Ed25519 private key suitable for the
noninteractive SSH workflow. Do not replace it with a server host key, an SSH
agent handle, a hardware-backed key or a public key. Do not generate a second
persistent identity for evidence.

The archive includes a small internal `manifest.json` containing the exact
collected names, byte counts, SHA-256 hashes and missing names. It records
`allowlist_complete` and always records `native_success_established: false`.
Missing files allow a **partial failure bundle** when at least one allowlisted
file exists. A present but invalid or oversized file fails sealing; it is not
silently omitted. A complete allowlist proves only that the named files were
collected, not that their contents report success. An absent optional editor log
can leave the allowlist incomplete even when the separate native receipt
validator accepts a successful run.

## Encryption and workflow behavior

The helper invokes official `age` and `ssh-keygen`; it implements no encryption
algorithm. `ssh-keygen` derives the public recipient from the existing client
identity through a validated open file descriptor. The private key is not copied
into Python memory or an archive. The SSH public-key comment is discarded.

Before any remote work, the workflow encrypts and decrypts a small synthetic
probe with that identity. A missing dependency or unusable identity stops the
workflow before worker mutation. This check demonstrates that the runner can
use the supplied key at that moment; it does not establish that an operator has
retained a recoverable copy outside GitHub Secrets.

The evidence tar archive exists only in bounded process memory. `age` writes
ciphertext to a temporary mode-0600 file, which is published under a fixed name
only after encryption succeeds. Failed temporary outputs are removed. There is
no plaintext artifact fallback and no command that uploads an evidence
directory. The workflow removes the temporary SSH credentials, collected runner
evidence and local ciphertext after the upload attempt. Removal is ordinary
filesystem cleanup, not a guarantee of physical secure erasure.

| Workflow | Artifact name | Only uploaded file |
| --- | --- | --- |
| Native reader gate | `native-evidence-encrypted` | `native-evidence.tar.age` |
| Worker bootstrap | `bootstrap-evidence-encrypted` | `bootstrap-evidence.tar.age` |

Artifacts retain for **three days**, use fixed nonsensitive names and contain
ciphertext only. The upload action's normal outer ZIP is not encryption; the
inner `.tar.age` file is encrypted before the action receives it. Public helper
output is restricted to fixed status/reason fields, counts, completeness and a
ciphertext checksum. Tool diagnostics are suppressed. No raw exception, key
path, hostname, signed URL or log body is intentionally printed by the helper.

The sealing step runs after remote failure when its encryption preflight
succeeded. A killed runner, hard job timeout, cancellation, transport failure
before evidence arrives or upload failure can still prevent recovery. The
workflow does not guarantee an artifact will exist. A successful upload does
not establish successful download, decryption or review. Native and bootstrap
timeouts, runner cleanup and artifact retention do not delete a cloud worker or
stop provider billing.

The workflows install `age` from the official Ubuntu package repository on the
GitHub-hosted Ubuntu runner. `actions/upload-artifact` is pinned to upstream
**v7.0.2**, commit
`cf430e030ddbb5b0abf93d22962f4752f3646cd9`. This was checked against the official
tag and action definition on 21 October 2026. Repository permissions remain
`contents: read`, workflow dispatch remains manual, and execution remains
restricted to the canonical repository's `main` branch.

For a local synthetic check, use a separate output directory and an existing
test identity. These placeholders do not refer to live credentials:

```sh
python3 -B Tools/Unreal/seal_native_evidence.py \
  --preflight --identity-file /private/path/existing-client-key

python3 -B Tools/Unreal/seal_native_evidence.py \
  --kind native \
  --input-dir /private/path/native-input \
  --identity-file /private/path/existing-client-key \
  --output-file /private/path/sealed/native-evidence.tar.age
```

For bootstrap evidence, use `--kind bootstrap` and the exact output basename
`bootstrap-evidence.tar.age`. The input and output directories must already
exist; the output must be outside the input directory. Existing output files
are never overwritten. Keys must also be outside the input directory.

## Retrieve and verify privately

Retain the existing client private key until the evidence has been downloaded,
decrypted and reviewed. Repository-secret values cannot be read back through
the GitHub UI. Losing the only private-key copy can make an uploaded artifact
unrecoverable. Do not rotate away or delete that key merely because upload
succeeded.

1. Within three days, sign in to GitHub and open the intended workflow run in
   `robobeirne/squadpaddrills`. Check the workflow, commit, run ID and attempt.
   Download its named encrypted artifact into a private review location. If
   the download is a ZIP, copy only its expected `.tar.age` member into that
   location; do not extract arbitrary paths from an unfamiliar archive.
2. Compare the `.tar.age` file's SHA-256 with the helper's
   `ciphertext_sha256` in that run's fixed public sealing result. This binds the
   download to the observed upload input. It does not authenticate Unreal or
   prove that the worker ran the test.
3. Decrypt with the same existing SSH client identity. Use a new private
   directory, refuse an existing destination, and check `age`'s exit status
   before opening the output. An unsuccessful streaming decryption can leave
   a partial plaintext file; remove it and stop.

   ```sh
   umask 077
   mkdir private-review || exit 1
   if age --decrypt --identity /private/path/existing-client-key \
       --output private-review/evidence.tar native-evidence.tar.age; then
     printf '%s\n' 'Decryption completed; verify the internal manifest next.'
   else
     rm -f private-review/evidence.tar
     printf '%s\n' 'Decryption failed; evidence is not verified.' >&2
     exit 1
   fi
   ```

4. Validate the tar members and manifest before extracting anything. From the
   reviewed repository checkout, the following checks the bounded native
   archive without writing its contents or printing private data. Change
   `kind` to `bootstrap` for bootstrap evidence.

   ```sh
   python3 -B - <<'PY'
   import hashlib
   import json
   from pathlib import Path
   import sys
   import tarfile

   sys.path.insert(0, "Tools/Unreal")
   from seal_native_evidence import FILE_LIMITS, MANIFEST_SCHEMA

   kind = "native"
   path = Path("private-review/evidence.tar")
   limits = FILE_LIMITS[kind]
   def check(condition):
       if not condition:
           raise SystemExit("Evidence manifest verification failed.")
   def unique_object(pairs):
       result = {}
       for key, value in pairs:
           check(key not in result)
           result[key] = value
       return result
   check(path.is_file() and not path.is_symlink())
   check(path.stat().st_size <= sum(limits.values()) + 65536)
   with tarfile.open(path, mode="r:") as archive:
       members = archive.getmembers()
       names = [member.name for member in members]
       check(len(names) == len(set(names)) and "manifest.json" in names)
       check(set(names) <= set(limits) | {"manifest.json"})
       contents = {}
       for member in members:
           check(member.isfile() and not member.pax_headers)
           check(0 <= member.size <= limits.get(member.name, 16384))
           contents[member.name] = archive.extractfile(member).read(member.size + 1)
           check(len(contents[member.name]) == member.size)
   manifest = json.loads(contents.pop("manifest.json"), object_pairs_hook=unique_object)
   check(set(manifest) == {"schema", "kind", "files", "missing_files",
                           "allowlist_complete", "native_success_established"})
   check(manifest["schema"] == MANIFEST_SCHEMA and manifest["kind"] == kind)
   expected = [{"path": name, "size_bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest()}
               for name, raw in sorted(contents.items())]
   missing = sorted(set(limits) - set(contents))
   check(bool(contents) and manifest["files"] == expected)
   check(manifest["missing_files"] == missing)
   check(manifest["allowlist_complete"] is (not missing))
   check(manifest["native_success_established"] is False)
   print("Evidence manifest verified. Native success remains a separate check.")
   PY
   ```

5. Review the verified evidence privately. For a claimed native pass, pass the
   validated file-byte mapping to
   `remote_native_gate.validate_native_receipt(artifacts, expected_commit,
   expected_run_id)`, using the independently selected 40-character run commit
   and `gha-<run-id>-<attempt>` identifier. That validator checks the native
   receipt, clean source state, engine identity, process results, exact reader
   test and log/report hashes. Retain the coordinator's fixed result from the
   same run as the transport outcome. A failed or partial bundle remains useful
   for diagnosis and must not be presented as a native pass. Bootstrap evidence
   describes preparation only; it cannot establish a native build.

An age recipient public key is not a sender signature: anyone with that public
key could encrypt a new file to it. Authenticated decryption protects the file
being decrypted against tampering; the correct GitHub run, reviewed source,
manifest and native receipt provide the additional provenance checks. Even a
verified native reader pass does not establish rendered animation, athlete
anatomy, ball contact, rendering performance or iPhone execution.

## Focused verification and primary sources

The focused test module is `Tests.test_seal_native_evidence`. It covers the
allowlist, manifest hashes, partial bundles, size/path/link constraints, fixed
failure output, failed-output cleanup, real age encryption/decryption, tamper
rejection, wrong-key rejection and unsupported identities. Real encryption
tests use temporary synthetic keys and skip explicitly when `age` or
`ssh-keygen` is unavailable. They do not use or verify live task credentials.

- [Official age README: SSH public-key recipients and private-key decryption](https://github.com/FiloSottile/age/blob/main/README.md).
- [GitHub: downloading workflow artifacts and repository read access](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/download-workflow-artifacts).
- [Official upload-artifact v7.0.2 tag reference](https://api.github.com/repos/actions/upload-artifact/git/ref/tags/v7.0.2).
- [Pinned upload-artifact action definition and supported inputs](https://github.com/actions/upload-artifact/blob/cf430e030ddbb5b0abf93d22962f4752f3646cd9/action.yml).
