# First native Unreal build and reader test

Status on 21 October 2026: launchers, an independent deletion watchdog and offline
tests are implemented. The user approved up to US$5 including tax for one native
build attempt. Bounded metadata reads of the user-supplied
`Linux_Unreal_Engine_5.8.4.zip` succeeded; the observed package identity, sizes and
toolchain manifest are recorded in [ENGINE_ARCHIVE.json](ENGINE_ARCHIVE.json).
The complete archive transfer, full-file checksum, integrity and publisher
authenticity remain unverified. Provider access and live watchdog configuration
are still outstanding. No Unreal build, native automation test or worker connection
has been verified. Provider resource state and charges require live verification.
The project still has no verified native binary or visual result.

This is the first part of SP2. It compiles `SquadPad3DEditor` and executes
`SquadPad.Runtime.PlaybackReader.Contract` using a real installed Unreal engine.
The test reads the public `Fixtures/Playback/first_pass.playback.json` directly.
It does not need the private athlete models, capture pack or imported content.

## The first attempt

Use the cloud workspace as the SSH controller and one temporary Ubuntu 22.04
x86-64 worker: DigitalOcean Basic, Regular, with 4 shared vCPU, 8 GiB RAM and
160 GiB SSD in LON1. The [compute plan](REMOTE_BUILD_COSTS.md) records the
$0.07143/hour rate and existing US$5 approval. Before launch, confirm actual
account eligibility, catalogue size/image, capacity and tax. Establish provider
access, a usable private route for transferring the complete inspected archive to the worker, and live
independent deletion. Successful metadata access does not establish that the
full transfer or build session can complete. The user's Mac remains outside
this execution path.

If the cloud workspace cannot complete an SSH connection, use the manual
[worker access inspection](WORKER_ACCESS.md) from a GitHub-hosted runner first.
That bounded, read-only check verifies authentication and reports selected
guest resources. It does not install packages, create the build account,
transfer Unreal, establish provider cleanup or run the native test. The separate
native workflow below still requires a fully prepared worker and private
evidence collection.

Epic [recommends 32 GB RAM for Linux development](https://dev.epicgames.com/documentation/en-us/unreal-engine/linux-development-requirements-for-unreal-engine).
This smaller first attempt is limited to one project module with seven authored
`.cpp` files against an installed engine, followed by one NullRHI reader test.
Eight GiB sufficiency and completion time are unverified. The proposed fixed
16 GiB swap file and requested one-action build configuration below require
measured resources and real worker observations.

Install the inspected Unreal 5.8.4 Linux package at the actual directory
`/opt/unreal/UE_5.8`. That path must resolve to itself: the remote coordinator
rejects a symlink that resolves to a differently named installation directory.
The project's engine association remains `5.8`; the existing family check
accepts patch version `5.8.4`. This budget does not include compiling Unreal
itself from source.

The worker needs Python 3.10 or newer, Git, OpenSSH, outbound HTTPS access to the
public repository, an accurate clock, and an unprivileged build account with
write access to `/srv/squadpad/runs`. Install the engine and toolchain with the
permissions needed by that account. Do not run the Editor as root. The worker
does not need repository write access, GitHub credentials, provider administrator
credentials, Apple credentials or a GitHub runner registration.

Record the worker resource ID, creation time, CPU, memory, disk and OS in the
private execution record. The separate [deletion watchdog](DROPLET_WATCHDOG.md)
must already be running and visibly armed before planned provisioning. Confirm
it binds the new provider ID and creation time immediately afterward. If a worker
already exists, follow the watchdog's explicit exact-ID recovery procedure before
native execution. Recovery verifies the original provider creation timestamp and
retains the original age-based deadlines; it does not claim pre-creation arming.
Native and SSH timeouts do not delete the VM. Collect the evidence before the
deletion deadline, including failure evidence if the build fails.

## Inspected archive and worker installation

The [sanitized archive receipt](ENGINE_ARCHIVE.json) records bounded HTTPS range
reads and ZIP metadata inspection. It contains no download URL or credentials.

| Observed property | Value |
| --- | --- |
| Filename | `Linux_Unreal_Engine_5.8.4.zip` |
| Complete ZIP length | 39,819,212,076 bytes, approximately 37.08 GiB |
| Listed expanded content | 76,967,950,798 bytes, approximately 71.68 GiB |
| ZIP entries | 299,959 |
| Engine version | `5.8.4` |
| Changelist / compatible changelist | `58902052` / `55116800` |
| Branch | `++UE5+Release-5.8` |
| SDK MainVersion, MinVersion and MaxVersion | `v26_clang-20.1.8-rockylinux8` |
| AutoSDK platform | `Linux_x64` |

The ZIP contains the installed-build marker, `UnrealEditor`, `UnrealEditor-Cmd`,
`UnrealBuildTool.dll` and the Linux `Build.sh` wrapper. It also lists the bundled
native compiler shown below. Presence in the archive does not establish that
these binaries execute successfully on the worker. The expanded byte count is
the sum of ZIP member sizes; allocated disk space, filesystem overhead, build
outputs and caches can require more.

Compare the installed metadata with these already observed raw-file hashes:

- Raw `Engine/Build/Build.version` SHA-256:
  `80a6af0f427f373b43a1c373082acf2b3de07305d215a67c27f18272e6dff24f`.
- Raw `Engine/Config/Linux/Linux_SDK.json` SHA-256:
  `2aae9f3a9f092886f5426a5482ce2ceec812061a63de49be76fb6cbfa7f26e0d`.

Use this installation sequence after the access and independent-deletion
prerequisites are satisfied:

1. Record actual available disk, RAM and swap. Prepare a fixed 16 GiB swap file
   on the included root disk only if measured space permits. The ZIP plus listed
   expanded content total 108.77 GiB; with swap, the nominal remaining space is
   35.23 GiB before the OS, filesystem overhead, workspace and caches. Stop if
   measured resources are insufficient; no extra volume is part of this attempt.
2. Transfer the complete archive privately onto the prepared worker. Verify its
   exact length and compute and record its full SHA-256 before extraction or
   execution. The full-file checksum is currently unavailable; it is obtained
   on the worker after transfer, not required before provisioning. A locally
   computed checksum records identity but does not by itself authenticate the
   publisher.
3. Validate full ZIP integrity and extract into the real `/opt/unreal/UE_5.8` directory so that
   `Engine/Build/Build.version` is directly beneath it. Give the unprivileged
   build account the required access. Compare the extracted `Build.version`
   and `Linux_SDK.json` hashes with the values above and the archive receipt.
   Only after successful integrity/extraction checks, discard the worker's
   temporary downloaded ZIP before building to reclaim approximately 37.08 GiB.
   Retain its size/hash record and the original supplied archive.
4. Check the bundled compiler's actual version before native execution:

   ```sh
   SQUADPAD_ENGINE_ROOT=/opt/unreal/UE_5.8
   SQUADPAD_CLANG="$SQUADPAD_ENGINE_ROOT/Engine/Extras/ThirdPartyNotUE/SDKs/HostLinux/Linux_x64/v26_clang-20.1.8-rockylinux8/x86_64-unknown-linux-gnu/bin/clang"
   "$SQUADPAD_CLANG" --version
   ```

5. Inspect the native preflight receipt, then run the build and reader gate.
   Confirm in the real UBT log that it selects the expected bundled v26 compiler.
   Compiler startup, UBT selection and a successful native build are separate
   checks; none has been executed during archive inspection.

`Engine/Build/BatchFiles/Linux/SetupToolchain.sh` is absent from this archive;
there is no such setup command to run for this package. Its manifest and bundled
SDK identify clang 20.1.8 / v26, matching Epic's current
[UE 5.7–5.8 requirements table](https://dev.epicgames.com/documentation/en-us/unreal-engine/linux-development-requirements-for-unreal-engine).
The Linux quickstart's clang 18.1.0 / `SetupToolchain.sh` instructions do not
describe this inspected 5.8.4 package. Verify the bundled toolchain on the worker
and retain any failure evidence instead of substituting that older toolchain.

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

For `--run`, the driver prepares the project's
`Saved/UnrealBuildTool/BuildConfiguration.xml`, requesting `MaxParallelActions=1`
and setting `bAllowUBAExecutor`, `bAllowUBALocalExecutor`, `bAllowXGE`,
`bAllowFASTBuild` and `bAllowSNDBS` to `false`. An existing conflicting project
configuration is refused. Preflight reads/plans this configuration without
creating or modifying it. Epic documents the
[project XML location and executor settings](https://dev.epicgames.com/documentation/en-us/unreal-engine/build-configuration-for-unreal-engine).

The receipt records the requested configuration and its hash. It does not
establish actual scheduler behavior or peak memory, and the setting is not a
process-memory limit. Observe compiler/PCH generation, linking, Editor startup
and swap pressure on the worker. Stop on insufficient storage or memory, a broad
engine rebuild, unrecoverable failure or the workload deadline; retain evidence
and leave the existing timeouts and independent deletion deadlines in force.

The Editor invocation uses the documented
[`Automation RunTest ...;Quit` and `ReportExportPath`](https://dev.epicgames.com/documentation/en-us/unreal-engine/run-automation-tests-in-unreal-engine)
arguments with unattended mode and NullRHI. The first installed-engine run must
still establish actual UE 5.8.4 command, report and API compatibility.

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

The portable workflow tests both launchers and the deletion watchdog on Python
3.12 and 3.10 without Unreal, SSH or provider API calls. Those jobs can run on the
draft PR. They do not enable or invoke either manual workflow.

## After this gate passes

Restore the reviewed source bundle, perform the native asset import, save and
reopen the content, and follow [Runtime probe](RUNTIME_PROBE.md). Then inspect
real player poses, controls and ball contact. Production-athlete comparison,
shoulder and kit quality, rendered performance, Swift hosting and physical
iPhone validation remain later acceptance steps.
