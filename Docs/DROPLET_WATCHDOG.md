# Independent deletion of the first native-build worker

Status on 21 October 2026: the driver, manual workflow and controlled tests
are implemented. The expected first-attempt profile is now regular Basic
4 vCPU / 8 GiB RAM / 160 GiB disk, to fit published Tier 2 limits. All 21
controlled watchdog tests passed locally alongside the native and SSH launcher
tests. No provider token has been configured, no live watchdog has been
armed, and no VM has been created or deleted. The approved budget is US$5
including tax for one attempt; see [the compute plan](REMOTE_BUILD_COSTS.md).

## Purpose and boundaries

[`Tools/Cloud/droplet_watchdog.py`](../Tools/Cloud/droplet_watchdog.py) supervises
one freshly created, uniquely named DigitalOcean Droplet from a separate
controller. It never creates a resource. Default invocation performs local
preflight, without reading a token or contacting the API. Only `--watch`
authenticates and can delete the exact bound Droplet ID.

[`droplet-watchdog.yml`](../.github/workflows/droplet-watchdog.yml) provides an
independent standard GitHub-hosted `ubuntu-24.04` job. It does not depend on
the native build job, an SSH connection, or a process inside the paid worker.
Its timeout is 330 minutes, below GitHub's documented
[six-hour hosted-job limit](https://docs.github.com/en/actions/reference/limits).
The 15-minute creation window plus a five-hour resource-age deadline fit within
that job allowance. Start and observe this job before provisioning; a queued
job or a successful preflight provides no cleanup protection.

The watchdog is an operational cleanup mechanism, not a provider-enforced
dollar cap. Controller cancellation, service/network failure, unauthorized
configuration changes and unrelated account charges still require operator
intervention. An account owner must retain a working provider-console fallback.

## Account setup before provisioning

Review the workflow on the repository's default branch before dispatch. GitHub
[requires the workflow to exist on that branch](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow).
The committed workflow permits only `robobeirne/squadpaddrills` on `main` and uses
read-only repository permissions, pinned actions and no artifact uploads.
Adding it to a draft PR does not make it dispatchable.

Store a DigitalOcean token with only `droplet:read` and `droplet:delete` as the
repository Actions secret `SQUADPAD_DO_CLEANUP_TOKEN`. These scopes cover filtered
listing, exact-ID lookup and deletion in the
[official Droplet API](https://docs.digitalocean.com/products/droplets/reference/api/droplets/).
The driver does not need resource-creation permission. It cannot prove that a
token has no additional permissions; the account owner must set its scopes.
The token belongs in the secret store, never in chat, source, workflow inputs,
the guest, a public issue or an uploaded artifact.

After account access, fallback deletion, billing/tax, engine access and the exact
live plan/image have been checked, set repository variable
`SQUADPAD_DO_WATCHDOG_ENABLED` to exactly `true`. This enables execution; it does
not establish that a watchdog is currently running. No secret or readiness
variable has been set by this source increment.

Required dispatch inputs are:

| Input | Required value |
| --- | --- |
| `planned_name` | A new `squadpad-native-` name with an 8–40 character lowercase alphanumeric/hyphen suffix |
| `size_slug` | The live-verified catalogue slug for the planned regular Basic 4 vCPU / 8 GiB / 160 GiB plan |
| `image_id` | The live-verified numeric Ubuntu 22.04 x86-64 image ID |

The exact slug and image ID are intentionally not guessed. The driver additionally
checks `lon1`, 4 vCPU, memory `8192`, disk `160`, no backup feature, and a positive
catalogue hourly price no greater than $0.072. The published regular Basic rate
is $0.07143/hour, or $0.35715 for five hours before tax; even the accepted ceiling
is $0.36 for five hours before tax. The old 32 GiB / 600 GiB profile is rejected.
These values change the expected machine and price only; resource identity,
creation window, deletion deadlines and API permissions retain their existing
behavior.

DigitalOcean's published Tier 2 limits include Basic plans up to $56/month; the
planned regular Basic size is listed at $48/month. This makes it a candidate
under the published tier rules, not evidence of live account eligibility or
regional capacity. Verify that exact size, current price, LON1 availability and
the Ubuntu 22.04 x86-64 image in the account before dispatch. The image-ID check
depends on the operator having verified that ID's OS and architecture. Sources:
[resource limits](https://docs.digitalocean.com/platform/resource-limits/) and
[Droplet pricing](https://www.digitalocean.com/pricing/droplets).

The first attempt permits no extra volumes, snapshots, backups or reserved IPs.
Bounded swap, if configured by the worker preparation, occupies the included
disk and does not add another provider resource. The watchdog does not prove
that available memory or disk is sufficient for Unreal; the worker must pass
the [native execution prerequisites](NATIVE_EXECUTION.md) before the build.

## Operate one bounded attempt

1. Dispatch the watchdog on the reviewed `main` commit with the three inputs.
   Inspect the live log. It must show a successful initial query with no existing
   name match, `status: "armed"`, and a current `armed_at_utc`. Confirm the job
   remains running. Do not create a resource based on a preflight, queued run,
   stale receipt or readiness variable alone.
2. Create exactly one approved VM with that exact name within the 15-minute
   window. Immediately verify the watchdog's `bound` receipt matches the new
   provider ID and creation time. Wrong configuration or ambiguous discovery
   fails without adopting a resource; delete the newly created machine through
   the provider immediately if binding fails.
3. Run the [native build procedure](NATIVE_EXECUTION.md), stopping the workload
   at four hours from provider creation. Collect the allowlisted evidence
   privately and delete early when finished. Keep the watchdog running until
   it independently confirms absence.
4. If the VM still exists at age 4h45, the watchdog issues exact-ID DELETE
   requests and retries transient failures. It requires a fresh GET returning
   404 before age 5h. A DELETE response by itself is not proof of absence.
5. Check terminal `status: "deleted_verified"` and
   `deletion_before_deadline: true`. Verify the provider account has no added or
   retained paid resources and inspect the actual charges. Revoke the temporary
   cleanup token and disable the readiness variable after the session.

Do not cancel the watchdog while a bound VM exists. A failed, cancelled or
timed-out job requires immediate provider-console deletion and verification.
Do not rerun it against an existing machine: initial discovery deliberately
rejects pre-existing names, and a rerun would not resume the previous guard.

## Direct controller and local preflight

An equivalent trusted external controller can run the driver directly. Supply
the verified values as environment variables and use a new private receipt path:

```sh
python3 -B Tools/Cloud/droplet_watchdog.py \
  --name "$SQUADPAD_DROPLET_NAME" \
  --size "$SQUADPAD_DROPLET_SIZE" \
  --image-id "$SQUADPAD_DROPLET_IMAGE_ID" \
  --receipt "$SQUADPAD_WATCHDOG_RECEIPT"
```

This command is preflight only. Actual supervision requires `--watch` and a
token supplied through the controller's secret channel as `SQUADPAD_DO_TOKEN`.
An existing receipt path is rejected before credentials or API access. The
controller needs Python 3.10+, stdlib only, and accurate time. Its monotonic
clock prevents wall-clock rollback from extending the lifetime allowance.

## Evidence and limits

The sanitized receipt records the driver hash, expected configuration, exact
bound ID/time, delete/verification deadlines, request counts and outcome. The
workflow publishes that receipt in its log and summary; it excludes IP addresses,
account details, credentials and raw provider responses. No engine or native
build evidence is copied into the cleanup job.

Some response fields need permissions beyond `droplet:read`. Missing backup,
snapshot or volume ID fields are reported as unavailable, not as proof that
those resources do not exist. The provisioner and account owner must verify
the no-extras requirement independently.

`billing_cap_guaranteed` is always `false`, including after confirmed deletion.
The focused tests establish controlled timing, identity and response-handling
behavior. Actual restricted-token responses, live deletion, account billing
and the native Unreal build remain unverified.
