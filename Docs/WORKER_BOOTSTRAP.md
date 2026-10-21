# Preparing the temporary Linux worker

The manual **Prepare temporary Linux Unreal worker** workflow closes the gap
between a fresh approved VM and the existing native reader gate. It prepares
Ubuntu, creates the unprivileged build account, downloads the engine directly to
the worker and validates its extraction. It does not start Unreal, its compiler
or the native test. These scripts have offline test coverage; a real bootstrap
run and GitHub-to-worker SSH reachability remain unverified until an approved
worker is available.

No VM is created by this workflow. Provider access, the independent deletion
controller and the private transfer route must be ready before another paid
worker is created. The existing cost and lifecycle constraints are in
[REMOTE_BUILD_COSTS.md](REMOTE_BUILD_COSTS.md) and
[DROPLET_WATCHDOG.md](DROPLET_WATCHDOG.md).

## What the implementation does

| Component | Fixed responsibility |
| --- | --- |
| `.github/workflows/bootstrap-worker.yml` | Manual canonical-repository `main` job, temporary credentials, evidence encryption and ciphertext upload. |
| `Tools/Unreal/bootstrap_remote_worker.py` | Two bounded, host-key-pinned SSH sessions, private configuration transfer and validated public summary. |
| `Tools/Unreal/prepare_native_worker.sh` | Root-only OS preparation for the documented fresh Ubuntu 22.04 x86_64 worker. Its default mode checks without making setup changes; the workflow explicitly uses `--apply`. |
| `Tools/Unreal/install_engine.py` | Download, full-file hashing, ZIP validation and extraction as `squadpad`. It never invokes an engine or compiler binary. |
| `Docs/ENGINE_ARCHIVE.json` | Reviewed archive size, entry count, expanded size, version metadata and expected small-file hashes. |
| `Tools/Unreal/seal_native_evidence.py` | Encrypt the bounded bootstrap evidence files for the existing Ed25519 identity. |

The root stage checks the four-CPU, 8 GiB RAM, included-disk profile and requires
a fresh account, engine destination, run directory and swap path. It installs
basic Ubuntu packages, creates `squadpad` without sudo, installs the public half
of the existing SSH identity, creates `/srv/squadpad/runs` and the actual
`/opt/unreal/UE_5.8` directory, and activates a 16 GiB swap file. It does not add
swap to `/etc/fstab`, alter SSH or firewall policy, or receive the private archive
URL. It repeats its resource checks after package installation.

A separate SSH session authenticates as `squadpad` and runs the staged installer.
The installer and metadata are root-owned and checked against the runner's
source hashes. The install work directory belongs to `squadpad`; the engine
destination must be empty and must resolve to its exact expected path.

## Setup sequence

1. Review and merge the bootstrap and private-evidence changes. Check the
   portable control tests before creating a paid worker. The workflow only runs
   from `robobeirne/squadpaddrills` on `main` and checks out that dispatch's exact
   commit. It has no command, address or download-URL inputs.
2. Establish provider access and prepare the independent deletion controller
   under the existing approved budget. Prepare an unexpired private HTTPS URL
   for the exact archive described in `ENGINE_ARCHIVE.json`, and retain the
   private Ed25519 identity needed to decrypt the later evidence. An expired
   transfer link cannot be repaired by bootstrap.
3. Create only the already approved temporary worker profile. Record its
   creation time in the independent controller, bind the exact worker identity
   to deletion, and obtain the SSH host public key from the provider console or
   another independently trusted channel. The inspection and bootstrap
   workflows must not learn a new host key from their first SSH connection.
4. Set the repository Actions secrets below. Dispatch **Inspect Linux worker
   access** first; its fixed read-only result tests the runner's actual SSH
   route and reports the allowed resource measurements. A failure does not
   prove that the worker's SSH service is broken, and a successful local or
   console login does not prove GitHub reachability.
5. Set repository variable `SQUADPAD_BOOTSTRAP_ENABLED` to the exact string
   `true` after the worker, private transfer and independent deletion are ready.
   Dispatch **Prepare temporary Linux Unreal worker** from `main`. This
   variable records operator readiness; it is not evidence of active cleanup.
6. Require a passed bootstrap summary, retrieve and decrypt the evidence, and
   check the complete installer receipt. Then follow
   [NATIVE_EXECUTION.md](NATIVE_EXECUTION.md) for the separately enabled native
   gate. Set its SSH user to `squadpad`. Bootstrap does not dispatch the native
   job or establish its success.
7. Disable bootstrap readiness after the attempt. The independent controller
   must still delete the VM and verify its absence within the existing
   lifecycle. Runner cancellation, SSH failure, a successful extraction and
   removal of a local ZIP do not stop provider billing.

The connector used to publish source cannot set Actions secrets or dispatch
workflows. Those operations still require authorized GitHub settings/Actions
access. Provider authentication, a fresh transfer URL and independent cleanup
readiness are separate access requirements; this change does not invent or
store any of them.

## Private Actions configuration

Use **Settings → Secrets and variables → Actions** in the canonical repository.
Do not put any of these values in a commit, workflow dispatch input, issue, PR
description or public log.

| Repository secret | Required content |
| --- | --- |
| `SQUADPAD_REMOTE_SSH_HOST` | The approved worker's single address or DNS host, without a username, port, URL or shell command. |
| `SQUADPAD_REMOTE_SSH_KEY` | The existing private, unencrypted Ed25519 SSH identity authorized on the fresh worker. Keep a separate private copy for evidence decryption. |
| `SQUADPAD_REMOTE_SSH_KNOWN_HOSTS` | The matching OpenSSH `known_hosts` line with the independently verified Ed25519 host key. Never use an automatically accepted first-use key. |
| `SQUADPAD_BOOTSTRAP_CONFIG` | A small JSON object containing exactly `download_url` and `workload_deadline_utc`, as described below. |

`SQUADPAD_REMOTE_SSH_USER` is not used by bootstrap: its two usernames are fixed
to `root` and `squadpad`. The later native workflow requires that existing secret
to contain `squadpad`.

The bootstrap config is one private JSON secret. `download_url` must be a direct
HTTPS full-file endpoint, with no embedded username/password or fragment.
Redirects and ambient HTTP proxies are disabled for the worker download. The
server must return HTTP 200, identity encoding and the exact expected
`Content-Length`; interrupted downloads are not resumed. Use a URL whose access
window supports the planned start of the transfer.

`workload_deadline_utc` must be a valid UTC timestamp ending in `Z`, such as the
format `YYYY-MM-DDTHH:MM:SSZ`. Set it to the approved worker's creation time plus
four hours, using the independent controller's record. The bootstrap parser
rejects duplicate or extra fields, malformed values, oversized JSON and a
deadline more than four hours in the future. This check cannot prove the
provider's creation time or deletion configuration; the private receipt states
that limitation explicitly.

The runner receives this JSON through the secret environment. The archive URL
is sent inside SSH stdin only to the unprivileged install stage, then to the
installer through its stdin. It is absent from process arguments, stored
receipts and workflow inputs. Worker stdout/stderr stay private, are bounded,
and have the exact URL redacted before runner storage. SSH transport diagnostic
stderr is suppressed; a transport failure can therefore have only a fixed
failure category and incomplete private evidence.

## Archive and resource checks

The currently reviewed archive has 39,819,212,076 compressed bytes, 299,959 ZIP
entries and 76,967,950,798 bytes of listed uncompressed content. These values and
the recorded hashes come from [ENGINE_ARCHIVE.json](ENGINE_ARCHIVE.json). The
small metadata inspection did not establish a trusted whole-file hash or
publisher authenticity.

The installer streams the full file to the worker, checks its exact length and
records its SHA-256 as an observation. It bounds the ZIP central directory before
loading entries, requires the expected total entry count and expanded byte sum,
rejects encrypted or unsupported compression types, and checks each extracted
member's integrity. Absolute paths, traversal, duplicate destinations,
file/directory collisions and special filesystem objects are rejected. Relative
symlinks are supported only when every target resolves within the validated
archive tree without cycles or an escaping parent traversal. Links are created
after regular files have been written.

Extraction preserves the executable bits needed by the recorded engine tools
while stripping special bits and group/other write access. The installed
`Build.version`, Linux SDK manifest and installed-build marker must match the
recorded SHA-256 values and expected contents. The engine path and required
executables are checked without starting them. The complete receipt keeps
`publisher_authenticity_verified`, `engine_executed`, `compiler_executed` and
`cleanup_verified` false.

Before any download, the worker needs room for both the compressed archive and
its extraction plus a 16 GiB free-space reserve; OS preparation also accounts for
the 16 GiB swap file. Available disk is rechecked while downloading and
extracting. Only the just-downloaded archive is removed after full installation
and metadata checks pass. No engine installation, partial extraction or
unrelated state is deleted to make space.

The root stage is limited to 25 minutes and the installer stage to 90 minutes.
Both receive a shorter bound when the private deadline is closer. Bootstrap
reserves the final 95 minutes before the four-hour workload cutoff for its
evidence recovery, the separately bounded 75-minute native workflow and the
operator handoff. The bootstrap job has a 150-minute outer limit to accommodate
setup, its two remote stages and evidence recovery; the private lifecycle
deadline can stop its remote work sooner. A worker/runner clock difference
over one minute prevents the stage from starting. Local SSH and remote child
processes have bounded waits; timeout and signal handling attempt to terminate
the known child process group. None of this substitutes for the independent
provider deletion deadline.

## Results and private evidence

The public result contains a fixed status, an allowlisted reason code and
boolean preparation/integrity flags. It contains no worker address, hostname,
provider identifier, archive URL, credential or raw command output. Even a
passed bootstrap result keeps `unreal_executed`,
`native_reader_contract_passed` and `cleanup_verified` false.

Private bootstrap evidence consists only of:

- `bootstrap-result.json`: public summary, source hashes, stage categories and
  the installer receipt, including the observed whole-file SHA-256.
- `bootstrap-stdout.log`: bounded private child output, at most 4 MiB.
- `bootstrap-stderr.log`: bounded private child error output, at most 4 MiB.

The runner verifies encryption and decryption with the existing identity before
the first guest change. After an attempted bootstrap it encrypts these files
using the shared evidence helper. The official upload action is pinned to the
reviewed commit and receives only `bootstrap-evidence.tar.age`, with three-day
retention. There is no plaintext artifact fallback. The runner removes its
temporary identity and evidence after sealing/upload are attempted. Cancellation
or runner failure can still prevent evidence recovery.

Follow [PRIVATE_NATIVE_EVIDENCE.md](PRIVATE_NATIVE_EVIDENCE.md) to retrieve,
decrypt and validate the internal manifest. Ciphertext upload is not evidence
of successful retrieval, decryption, full bootstrap completion or native
execution. Keep the private identity until recovery is verified.

If a stage fails, inspect the encrypted partial receipt before deciding on the
next action. The account, swap or some engine files may already exist. The
preparation script and installer deliberately refuse a repeat over existing
state; they do not roll back or silently overwrite a partial attempt. Do not
extend the paid worker's lifecycle while troubleshooting. Finish independent
deletion on schedule and use the failure evidence to prepare the next approved
attempt.

## Offline verification

The focused tests use temporary synthetic ZIPs, injected HTTP responses and
simulated SSH transport. They cover unsafe archive paths and symlinks, ZIP
metadata and integrity failures, disk/time limits, strict private input,
unprivileged stage separation, pinned SSH trust, receipt proof validation,
no-overwrite behavior and the absence of private values in public summaries.

```sh
python3 -B -m unittest discover -s Tests -p 'test_install_engine.py' -v
python3 -B -m unittest discover -s Tests -p 'test_bootstrap_remote_worker.py' -v
bash -n Tools/Unreal/prepare_native_worker.sh
```

These checks do not execute the preparation shell script, contact a worker,
download the full engine, establish archive compatibility in a real install or
measure native build performance. The real bounded bootstrap and native gate
remain the necessary execution evidence.
