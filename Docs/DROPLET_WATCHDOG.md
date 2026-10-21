# Independent deletion of the first native-build worker

Status on 21 October 2026: the driver, manual workflow and controlled tests
are implemented. The expected first-attempt profile is regular Basic
4 vCPU / 8 GiB RAM / 160 GiB disk, to fit published Tier 2 limits. All 46
controlled watchdog tests passed locally, including the original 21 tests,
17 tests for explicit recovery and eight public-output/private-configuration
tests. No provider API supervision or deletion has been verified by this source
change. The approved
budget is US$5 including tax for one attempt; see
[the compute plan](REMOTE_BUILD_COSTS.md).

## Purpose and boundaries

[`Tools/Cloud/droplet_watchdog.py`](../Tools/Cloud/droplet_watchdog.py) supervises
one explicitly identified DigitalOcean Droplet from a separate controller.
Its default binding mode supervises a freshly created, uniquely named resource.
An explicit recovery mode verifies one already-created resource by exact ID and
provider creation timestamp. It never creates a resource. Default invocation performs local
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
read-only repository permissions, pinned actions and no artifact uploads or
resource-specific dispatch inputs.
Adding it to a draft PR does not make it dispatchable.

Store a DigitalOcean token with only `droplet:read` and `droplet:delete` as the
repository Actions secret `SQUADPAD_DO_CLEANUP_TOKEN`. These scopes cover filtered
listing, exact-ID lookup and deletion in the
[official Droplet API](https://docs.digitalocean.com/products/droplets/reference/api/droplets/).
The driver does not need resource-creation permission. It cannot prove that a
token has no additional permissions; the account owner must set its scopes.
The token belongs in the secret store, never in chat, source, workflow inputs,
the guest, a public issue or an uploaded artifact.

Store the expected resource configuration as a separate repository Actions
secret named `SQUADPAD_DO_WATCHDOG_CONFIG`. The workflow reads both secrets only
in its supervision step after the readiness gate succeeds. Resource names,
IDs and timestamps must not enter dispatch inputs, repository variables, source
or public logs. Invalid configuration is rejected before token access or any
provider request. Neither secret has been configured by this source change.

For a new resource, after account access, fallback deletion, billing/tax, engine
access and the exact live plan/image have been checked, set repository variable
`SQUADPAD_DO_WATCHDOG_ENABLED` to exactly `true`. This enables execution; it does
not establish that a watchdog is currently running. No secret or readiness
variable has been set by this source increment.

For an already-running resource, establish its exact ID, provider creation
timestamp, expected name, full configuration, price, restricted cleanup
credentials and provider-console fallback, then enable and start recovery
immediately. Recovery does not need SSH access or an engine transfer. Those
remain separate prerequisites for native execution and must not delay cleanup
protection for a resource which already exists.

The configuration secret is one JSON object with exactly these fields. Numbers
must be JSON integers, not quoted strings, floats or booleans. Unknown and
duplicate keys, non-finite values, malformed JSON and documents over 8 KiB are
rejected. Omit both recovery fields for fresh mode. Include both with non-null
values for existing-resource recovery.

| Private JSON field | Required value |
| --- | --- |
| `planned_name` | Exact expected name. Fresh mode requires a new `squadpad-native-` name with an 8–40 character lowercase alphanumeric/hyphen suffix. |
| `size_slug` | The live-verified catalogue slug for the planned regular Basic 4 vCPU / 8 GiB / 160 GiB plan |
| `image_id` | The live-verified numeric Ubuntu 22.04 x86-64 image ID |
| `existing_droplet_id` | Optional exact positive integer provider ID, paired with `existing_created_at`. Omit both for fresh mode. |
| `existing_created_at` | Optional exact provider UTC `created_at`, paired with `existing_droplet_id`. Use the provider's value unchanged, including any fractional seconds. |

A synthetic fresh-mode example, not a usable provider configuration:

```json
{
  "planned_name": "squadpad-native-synthetic-0001",
  "size_slug": "synthetic-catalogue-slug",
  "image_id": 12345
}
```

Record the verified live values and calculated deadlines privately before
dispatch. Dispatch one intended run without resource inputs. The job reads the
configuration secret when it starts, so do not replace that secret while a run
is queued or active. `identity_verified: true` means the controller compared the
provider response successfully against that run's private configuration. It does
not establish independent operator verification or guarantee future cleanup.

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

This sequence applies to fresh-resource mode. Use the recovery procedure below
for a resource which already exists.

1. Save the verified fresh-resource configuration in the secret, then dispatch
   the watchdog on the reviewed `main` commit without resource inputs.
   Inspect the live log. It must show a successful initial query with no existing
   name match, `status: "armed"`, and `pre_creation_armed: true`. Confirm the job
   remains running. Do not create a resource based on a preflight, queued run,
   stale receipt or readiness variable alone.
2. Create exactly one approved VM with that exact name within the 15-minute
   window. Immediately verify `status: "bound"` and `identity_verified: true`
   for this run. The controller checks the expected configuration and records
   the exact bound ID and creation time privately. Wrong configuration or
   ambiguous discovery fails without adopting a resource; delete the newly created machine through
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
Do not rerun fresh mode against an existing machine: initial discovery
deliberately rejects pre-existing names. Recovery is a separate, explicit mode,
not a new lifetime allowance or a claim of earlier protection.

## Recover supervision of one existing resource

Use recovery only for the explicitly authorized resource after verifying its
identity through the provider account. Store the normal expected name, size
slug and image ID, plus both `existing_droplet_id` and `existing_created_at`,
in the private configuration secret.
The CLI equivalents are `--existing-id` and `--existing-created-at`. Supplying
only one is rejected. IDs must be positive decimal integers without signs,
padding, whitespace or exponent notation. Creation time must be a valid UTC
RFC 3339 timestamp ending in `Z`, with at most six fractional-second digits.

Recovery sends its first request directly to `/v2/droplets/{id}`. It never
lists resources or selects a candidate by name. A successful response must
match the supplied ID and creation timestamp exactly, plus the expected name,
image ID, size, region, CPU, memory, disk, hourly-price ceiling and resource
checks used by fresh mode. An initial 404 fails binding rather than claiming
successful cleanup of an identity which was never verified.

An existing default provider hostname is accepted only in recovery mode. It
must be a valid ASCII hostname of at most 253 characters, with labels of at
most 63 characters. The returned name must equal the expected name exactly,
including case. Fresh mode retains its narrower `squadpad-native-` rule.

The original provider creation time defines every deadline:

| Observed resource age after identity and configuration checks | Recovery behavior |
| --- | --- |
| Less than 4h45 | Bind the exact resource and supervise until its original deletion time. |
| At least 4h45 and less than 5h | Bind and request exact-ID deletion immediately, without an initial wait. |
| At least 5h | Refuse adoption, set `manual_deletion_required: true`, and instruct the operator to delete the verified resource through the provider console immediately. |
| Creation time is in the future | Reject recovery and investigate the supplied identity or controller clock. |

Lookup and verification time consume the existing allowance. Clock rollback
cannot extend it. Recovery never subtracts elapsed time, backdates arming or
starts a fresh five-hour period. Deletion still requires a subsequent exact-ID
GET returning 404 before the original five-hour deadline. A DELETE response
alone does not establish absence.

If the original deadline expires after binding while absence remains
unverified, recovery also reports `manual_deletion_required: true`. A late
GET 404 records the observed absence but fails the deadline result. It does
not request deletion of a resource already verified absent.

Recovery receipts record `binding_mode: "existing_exact_id"`, the actual
`recovery_started_at_utc`, and `pre_creation_armed: false`. The legacy `armed`
field remains false, and no `armed_at_utc` is fabricated. Inspect the live
`bound` status with `identity_verified: true` for the intended run, or the
terminal verified-deletion result if cleanup was already due. Compare the
private expected configuration and original deadlines before dispatch. Exact
`delete_at_utc` and `verify_before_utc` values stay in the private receipt. A
`recovering` status alone does not establish binding or protection.

If identity, configuration, credentials or the remaining lifetime prevent
recovery, use the account owner's provider-console fallback immediately.
This feature does not authorize unrelated resources, additional spending,
native execution, or a replacement VM.

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

To preflight an explicitly identified existing resource, add the paired
arguments to the same command:

```sh
  --existing-id "$SQUADPAD_EXISTING_DROPLET_ID" \
  --existing-created-at "$SQUADPAD_EXISTING_CREATED_AT"
```

Preflight checks syntax and produces a receipt without reading the token or
contacting the provider. It does not verify identity, resource age, configuration
or live supervision. Only `--watch` performs those live checks.

The existing CLI flags remain supported for a private external controller.
The GitHub workflow instead passes only `--config-from-env --receipt ... --watch`.
`--config-from-env` reads `SQUADPAD_DO_WATCHDOG_CONFIG` and must not be mixed with
any resource-specific CLI flag. Without `--watch`, this mode validates the private
configuration without reading the provider token or contacting the API.

## Evidence and limits

The full receipt records the driver hash, expected configuration, exact bound
ID/time, delete/verification deadlines, request counts and outcome in a private
file. New and replacement receipt files use mode 0600. Failure reasons stay in
this private receipt. Invalid argument errors are fixed messages which never
echo supplied values.

Driver stdout and the GitHub summary share one strict public projection. It
contains only an enumerated status, boolean evidence, nonnegative integer
request/retry counters and `identity_verified`. It excludes names, IDs, exact
timestamps, arbitrary failure text, paths, nested provider fields and unexpected
extra fields, including when such values appear under allowlisted keys with
invalid types. A successful binding keeps the original identity checks and
deadlines unchanged.

The workflow does not upload receipt artifacts. Full receipts remain only in
temporary private runner files and disappear with the runner unless an explicitly
private collection route has been arranged. Keep the expected configuration,
original deadlines and operational records privately outside public Actions
logs. No engine or native build evidence is copied into the cleanup job.

Some response fields need permissions beyond `droplet:read`. Missing backup,
snapshot or volume ID fields are reported as unavailable, not as proof that
those resources do not exist. The provisioner and account owner must verify
the no-extras requirement independently.

`billing_cap_guaranteed` is always `false`, including after confirmed deletion.
The focused tests establish controlled timing, identity and response-handling
behavior. Actual restricted-token responses, live deletion, account billing
and the native Unreal build remain unverified.
