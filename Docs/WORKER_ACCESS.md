# Inspect an existing Linux worker through GitHub

`inspect-worker.yml` is a manual, read-only connection check for the approved
worker. The workflow runs only in `robobeirne/squadpaddrills` on `main`. It does
not create or register a worker, install packages, change server configuration,
download Unreal, start a build or contact the provider API.

The workflow must be reviewed and merged onto the default branch before manual
dispatch is available. Source tests do not prove network access or authentication.
A refusal from another controller does not establish a broken SSH service, and
this workflow's presence does not establish GitHub-to-worker reachability.

## Required repository Actions secrets

| Secret | Value |
| --- | --- |
| `SQUADPAD_REMOTE_SSH_HOST` | The approved worker's public address or hostname |
| `SQUADPAD_REMOTE_SSH_KEY` | The complete temporary client private key whose public key is authorized on the worker |
| `SQUADPAD_REMOTE_SSH_KNOWN_HOSTS` | A known_hosts entry for that exact address with the server's Ed25519 public key, verified through the provider console |

Keep these values in the Actions secret store. Never paste the private key into
source, issues, workflow inputs or chat. No additional provider token, username
secret, readiness variable or paid service is needed for this inspection. Root
is the fixed administrative login for the read-only operation.

After configuring the three secrets, select **Actions → Inspect Linux worker
access → Run workflow**, with `main` selected. The workflow accepts no command,
host or other dispatch input. Dispatch remains an explicit operator action.

## Operation and public output

The controller makes one SSH connection on port 22. It requires the pinned host
key, disables ambient SSH configuration, agents, passwords and forwarding, and
limits connection establishment to ten seconds. The SSH transport has a
45-second timeout, followed by bounded process cleanup. The GitHub job has a
five-minute limit, including setup.

The fixed remote Python program reads operating-system identification,
architecture, logical CPU count, memory, swap and root filesystem capacity and
availability. It disables Python site hooks and bytecode writes. It does not
read credentials, hostnames, network addresses or provider metadata. Successful
SSH login produces normal SSH authentication records on the server.

Only a validated summary reaches public logs and the job summary. OS and
architecture strings use a small allowlist. Resource measurements are bounded
integers. Raw SSH stdout/stderr, additional remote fields and arbitrary error
text are excluded. Temporary credentials and inspection files are removed by
an `always()` step, subject to the runner remaining available. No artifacts are
uploaded.

`status: passed` establishes this pinned root SSH inspection and its recorded
resource readings. It does not establish compatibility, sufficient free space
for installation, native success or active cleanup. Unsupported OS/architecture
values appear as `other`; a successful inspection is not worker readiness.

Failure categories distinguish invalid local configuration, timeout, oversized
or invalid output, an unexpected remote account, and an SSH/remote-command
failure. The last category does not identify a specific network, host-key or
authentication cause because raw diagnostics stay unpublished.

## Work still required after access succeeds

Independent provider cleanup remains a separate prerequisite. After inspection,
the manual [worker bootstrap](WORKER_BOOTSTRAP.md) can prepare the OS and an
unprivileged `squadpad` account, then transfer and validate the engine. It needs
its own private configuration and explicit readiness flag. The later
`native-build.yml` requires that prepared worker and installed engine. Both
execution workflows support [encrypted evidence collection](PRIVATE_NATIVE_EVIDENCE.md).
This inspection does not perform those steps or enable either workflow.

The focused offline tests are:

```sh
python3 -B -m unittest Tests.test_inspect_remote_worker -v
```

These tests use synthetic responses and exercise output restrictions and failure
handling. They do not connect to a worker or establish live access.
