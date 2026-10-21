# Public portable playback checks

The `Portable playback` workflow executes the Python reference and the portable
C++17 playback core from a clean public checkout. The project lives at the
repository root. It needs Python 3.12, a C++17 compiler and the committed JSON
fixtures; it does not install Unreal, Blender or Python packages.

## What a passing check proves

The Python suite validates and evaluates `Fixtures/Playback/first_pass.playback.json`
and checks its binding document. Its 15 current tests cover the original contact
clocks, deterministic seeking, possession boundaries, animation-section boundaries,
playback controls, invalid inputs and binding-metadata consistency.

The unchanged `Tools/Playback/verify_native.py` then builds and executes the
portable core using `g++ -std=c++17 -O2 -Wall -Wextra -Wpedantic -Werror`. The current
fixture produces 84 core assertions and 354 comparisons with the Python reference:
316 first-pass fixture samples, 12 samples across three independent arithmetic
cases, and 26 playback-controller states. The verifier checks numeric tolerances,
preserves contact times and hashes its actual inputs before and after execution.

Run these same checks from the repository root:

```sh
python3 -B -m unittest Tests.test_playback -v
python3 -B Tools/Playback/verify_native.py --report /tmp/squadpad-portable-validation.json
```

The verifier creates and removes its build products in a temporary directory.
The workflow writes its execution receipt to the runner's temporary directory,
then includes it in the job summary. It does not overwrite the historical checked-in
reports, upload an artifact or commit generated results.

## Native launcher and deletion watchdog tests

The same Python 3.12 job additionally runs:

```sh
python3 -B -m unittest Tests.test_native_gate Tests.test_remote_native_gate Tests.test_droplet_watchdog -v
```

A separate five-minute job repeats these 51 launcher and watchdog tests using Python 3.10,
the proposed Ubuntu 22.04 worker's interpreter family. The cases exercise report
rejection, process timeouts and child cleanup, source identity, fixed SSH
arguments, bounded output, receipt validation and failure evidence preservation.
The subprocess cases use ordinary local Python programs. Controlled test
fixtures stand in for the engine files, reports and transport. No actual Unreal
process or SSH connection runs in either job, and these tests produce no native
acceptance receipt.

The 19 watchdog cases use a synthetic clock and controlled provider responses.
They cover fresh-name arming, exact-ID binding, configuration rejection, timed
deletion, retries, permission errors, incomplete discovery, cancellation, stale
receipts, clock changes and explicit absence verification. They make no provider
API calls and cannot establish live account access, deletion or billing results.

## Evidence this workflow cannot provide

A green portable check does **not** mean the Unreal project was compiled or run.
It also does not validate the native JSON reader, import a skeletal mesh or FBX,
evaluate a skeletal pose, measure foot-to-ball contact, assess rendered appearance
or run on an iPhone. These remain distinct, outstanding execution gates.

The public fixture retains its historical source identifiers and hashes. Checking
those fields for internal consistency does not re-establish the identity,
availability, redistribution rights or quality of the original assets. The
portable workflow cannot regenerate the fixture from absent source assets.

The five tests that require original source/export files live in
`Tests/test_playback_source.py`. They were moved out of the portable module without
changing their assertions. Restore the separately approved input bundle before
running:

```sh
python3 -B -m unittest Tests.test_playback_source -v
```

This checks exact fixture regeneration, original input/track preservation,
root-motion composition, binding/source agreement and stale source-hash rejection.
Missing source files cause errors. There is no automatic skip or generated
stand-in for missing assets. Other asset-dependent suites, including
`Tests.test_runtime_probe`, remain outside the portable CI command. The public
workflow's green status makes no claim about these unavailable stages.

## Workflow operation

The workflow runs for pull requests, pushes to `main` and manual dispatch. It uses
the standard `ubuntu-24.04` runner, a ten-minute job limit and cancellation of older
runs on the same branch or pull request. The token has read-only `contents`
permission, checkout does not persist credentials, and LFS downloading is disabled.
There are no secrets, paid/custom runners, package publications or deployment steps.

This description applies to `portable-playback.yml`. The separate
[`native-build.yml`](../.github/workflows/native-build.yml) is a dormant,
manual-only SSH coordinator for an operator-prepared worker. It does not run
on a pull request or register a self-hosted runner. Its prerequisites,
credential handling, private evidence collection and execution limits are
documented in [Native execution](NATIVE_EXECUTION.md).

The independent [`droplet-watchdog.yml`](../.github/workflows/droplet-watchdog.yml)
is also manual-only and dormant. It requires separate provider credentials and
live arming before a temporary machine is created. Its 330-minute timeout and
cleanup responsibilities are described in [Deletion watchdog](DROPLET_WATCHDOG.md).
Neither manual workflow is started by the portable tests.

Actions are pinned to full commit hashes. The following version tags and commit
objects were verified against their official GitHub repositories on 2026-10-20:

| Action | Version | Pinned commit | Official tag lookup |
| --- | --- | --- | --- |
| `actions/checkout` | `v7.0.1` | `3d3c42e5aac5ba805825da76410c181273ba90b1` | [GitHub API](https://api.github.com/repos/actions/checkout/git/ref/tags/v7.0.1) |
| `actions/setup-python` | `v7.0.0` | `5fda3b95a4ea91299a34e894583c3862153e4b97` | [GitHub API](https://api.github.com/repos/actions/setup-python/git/ref/tags/v7.0.0) |

Changing either action version requires verifying the new official tag's commit
and updating both the SHA and its version comment. A local verifier result is
separate from a GitHub-hosted workflow result; neither substitutes for Unreal or
device validation.
