# First native Unreal build and reader test

Status on 20 October 2026: launchers and offline tests are implemented. No Unreal
build, native automation test, worker connection or paid provisioning has occurred.
The project still has no verified native binary or visual result.

This is the first part of SP2. It compiles `SquadPad3DEditor` and executes
`SquadPad.Runtime.PlaybackReader.Contract` using a real installed Unreal engine.
The test reads the public `Fixtures/Playback/first_pass.playback.json` directly.
It does not need the private athlete models, capture pack or imported content.

## The first attempt

Use the cloud workspace as the SSH controller and one temporary Ubuntu 22.04
x86-64 worker. The [compute proposal](REMOTE_BUILD_COSTS.md) identifies the
machine and proposed budget. Provider and Epic access, the exact installed
engine archive and independent deletion still need to be arranged before launch.
The user's Mac remains outside this execution path.

The engine must be the official Unreal 5.8 Linux installed build at
`/opt/unreal/UE_5.8`, with Epic's C++ toolchain installed. Verify the archive
version, checksum and disk footprint before provisioning. Epic documents the
[precompiled Linux installation and C++ toolchain](https://dev.epicgames.com/documentation/en-us/unreal-engine/linux-development-quickstart-for-unreal-engine).
This budget does not include compiling Unreal itself from source.

The worker needs Python 3.10 or newer, Git, OpenSSH, outbound HTTPS access to the
public repository, an accurate clock, and an unprivileged build account with
write access to `/srv/squadpad/runs`. Install the engine and toolchain with the
permissions needed by that account. Do not run the Editor as root. The worker
does not need repository write access, GitHub credentials, provider administrator
credentials, Apple credentials or a GitHub runner registration.

Record the worker resource ID, creation time, CPU, memory, disk and OS in the
private execution record. Configure and verify deletion from a separate trusted
controller. Native and SSH timeouts do not delete the VM. Collect the evidence
before the deletion deadline, including failure evidence if the build fails.

## Execute on an already prepared worker

The gate defaults to preflight. A preflight observes files and prepares commands.
It cannot establish compiler compatibility or count as native execution.

```sh
python3 -B Tools/Unreal/native_gate.py \
  --engine-root /opt/unreal/UE_5.8 \
  --preflight --run-id first-native-preflight
```

An actual run builds the Editor target and then starts the exact native test:

```sh
python3 -B Tools/Unreal/native_gate.py \
  --engine-root /opt/unreal/UE_5.8 \
  --run --run-id first-native-run \
  --build-timeout-seconds 1800 --test-timeout-seconds 600
```

Each run writes a new `Saved/SquadPad/NativeGate/<run-id>/` directory. Reusing
one is an error. Linux and macOS execution use bounded process groups so the
driver can terminate compiler or Editor children after a timeout. Windows
preflight is supported, but Windows execution is not implemented. The remote
coordinator below supports Linux only.

UBT uses its installed parallelism configuration and automatic resource
scheduling. The driver does not claim or configure a fixed compiler-action cap.

The Editor invocation uses the documented
[`Automation RunTest ...;Quit` and `ReportExportPath`](https://dev.epicgames.com/documentation/en-us/unreal-engine/run-automation-tests-in-unreal-engine)
arguments with unattended mode and NullRHI. The first installed-engine run must
still establish actual UE 5.8 command, report and API compatibility.

## Execute from the cloud controller

Set these four environment variables through the approved credential channel:

| Variable | Meaning |
| --- | --- |
| `SQUADPAD_REMOTE_SSH_HOST` | The prepared worker's host or address |
| `SQUADPAD_REMOTE_SSH_USER` | Its unprivileged build account |
| `SQUADPAD_REMOTE_SSH_KEY_FILE` | Absolute path to a private SSH identity, mode 0600 |
| `SQUADPAD_REMOTE_SSH_KNOWN_HOSTS_FILE` | Absolute path to independently verified host keys |

Use a newly generated identity for the temporary worker. Host keys must be
verified through the trusted provisioning or provider console channel.
`ssh-keyscan` alone does not establish trust. Neither credentials nor host
values belong in Git, the command line, a public issue or a chat message.

```sh
python3 -B Tools/Unreal/remote_native_gate.py \
  --commit FULL_40_CHARACTER_REVIEWED_COMMIT \
  --run-id first-native-001 \
  --output-dir /absolute/private/path/first-native-001
```

The controller sends a fixed Python program over SSH on port 22. It creates a
fresh worker checkout of `robobeirne/squadpaddrills` at the exact commit, then
runs the native gate. No branch name, arbitrary command or alternate repository
can be supplied. SSH requires the verified host key and public-key authentication.
Agent, password, X11 and port forwarding are disabled. The local transport is
bounded to 45 minutes, with bounded output size and a fixed evidence allowlist.

The controller preserves returned failure files before marking the attempt
failed. Its console output is a small sanitized `remote-summary.json`, containing
the commit, run ID, result, fixed reason code and artifact hashes. Connection
settings and native log contents are excluded from that summary.

## Evidence and acceptance

| File | Purpose | Remote transfer limit |
| --- | --- | --- |
| `receipt.json` | Engine, source, commands, phases, module hashes and evidence flags | 1 MiB |
| `build.log` | Actual UBT/UHT/compiler output | 8 MiB |
| `editor-stdout.log` | Editor process output | 8 MiB |
| `editor.log` | Editor log when produced | 8 MiB |
| `automation/index.json` | Native test report | 10 MiB |

Every transferred file has a checked size and SHA-256 hash. No engine binaries,
models, native content, archive or complete workspace are transferred. Review
the detailed evidence privately before publishing excerpts.

A passing native receipt requires the actual Editor target build to succeed,
the project module manifest and binary to exist, and the Editor process to
exit successfully. It also requires exactly one fresh, completed `Success`
result for the named reader test, consistent report totals and no test errors.
Warnings are retained in the receipt. A zero-test, skipped, unfinished, missing,
wrong-name, stale or failing report cannot pass.

The running Editor's logged version and changelist must match the observed
`Build.version`. Engine files, source inputs and built module hashes must stay
stable. The SSH controller additionally requires clean before/after checkouts
at the requested commit. An up-to-date module is allowed when UBT accepts it,
provided its identity is recorded and unchanged.

`status: passed` and the four native evidence flags establish this narrow build
and reader gate. They do not establish skeletal pose evaluation, shoulder
anatomy, contact quality, a rendered frame, performance or iOS execution. The
offline launcher tests use controlled process/report fixtures and cannot
produce genuine native acceptance evidence.

## Optional GitHub-hosted coordination

[`native-build.yml`](../.github/workflows/native-build.yml) is a dormant,
manual-only coordinator on standard `ubuntu-24.04`. It never installs Unreal
on the GitHub runner or registers the worker as a self-hosted runner. The
workflow checks out the dispatch commit and only permits the canonical
repository's `main` ref. It has read-only repository permission, no arbitrary
command inputs, a 50-minute job timeout and serialized native runs.

The workflow must first be reviewed and merged onto the default branch before
GitHub exposes manual dispatch. This is a
[GitHub requirement](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow),
not evidence that the draft PR has been merged.

An operator must prepare the worker, private evidence collection and independent
provider deletion before setting repository variable `SQUADPAD_NATIVE_ENABLED`
to exactly `true`. Repository secrets hold `SQUADPAD_REMOTE_SSH_HOST`,
`SQUADPAD_REMOTE_SSH_USER`, `SQUADPAD_REMOTE_SSH_KEY` and
`SQUADPAD_REMOTE_SSH_KNOWN_HOSTS`. No secrets or readiness variable have been set
as part of this source increment. Missing configuration fails explicitly.

Only the sanitized result appears in the public job summary. Detailed files
are not uploaded as public Actions artifacts. They remain on the worker at
`/srv/squadpad/runs/gha-<run-id>-<attempt>/repo/Saved/SquadPad/NativeGate/gha-<run-id>-<attempt>/`
and temporarily on the GitHub runner. Private collection from the worker is
required before deletion. The first attempt uses the direct cloud controller
above so its returned evidence is immediately available for review.

The portable workflow tests both launchers on Python 3.12 and 3.10 without
Unreal or an SSH connection. Those jobs can run on the draft PR. They do not
enable or invoke this native workflow.

## After this gate passes

Restore the reviewed source bundle, perform the native asset import, save and
reopen the content, and follow [Runtime probe](RUNTIME_PROBE.md). Then inspect
real player poses, controls and ball contact. Production-athlete comparison,
shoulder and kit quality, rendered performance, Swift hosting and physical
iPhone validation remain later acceptance steps.
