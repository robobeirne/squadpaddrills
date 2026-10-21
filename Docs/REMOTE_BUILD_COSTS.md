# SP2 first native run: approved remote compute budget

Initial pricing research: 20 October 2026; the selected Basic plan and public resource limits were checked on 21 October 2026. The user approved up to **US$5 including tax for one attempt** on 21 October 2026. No paid resource, worker connection or purchase has been created. Bounded metadata access to the user-supplied `Linux_Unreal_Engine_5.8.4.zip` succeeded; [ENGINE_ARCHIVE.json](ENGINE_ARCHIVE.json) records the inspected package. Provider access, a complete private archive transfer and live watchdog configuration remain outstanding. Mac access remains paused.

## Recommendation

Use one temporary **DigitalOcean Basic, Regular** Linux VM in **LON1**, reached over direct SSH: **4 shared vCPU, 8 GiB RAM and 160 GiB SSD at $0.07143/hour**. The included disk and ordinary public IPv4 add no charge. Confirm the exact live catalogue size, Ubuntu image, account eligibility and capacity before launch. It will not be registered as a self-hosted GitHub runner. [1,2,14]

The approved allowance remains **up to US$5 in provider charges, including applicable tax, for one attempt**. Four hours cost **$0.28572**; a further one-hour cleanup allowance makes **$0.35715 before tax**. Illustrative 25% tax would make $0.4464375, leaving $4.5535625 within the authorization. Actual tax and account charges remain unverified. Authenticated provider access, a usable private full-file transfer route and live independent deletion are still prerequisites; no repeat budget approval or promotional credit is assumed.

DigitalOcean's published Tier 2 policy permits Basic plans through $56/month; this Regular Basic plan lists $48/month. That public policy does not verify an account's eligibility or live capacity. The earlier 32 GiB/600 GiB Storage-Optimized plan lists $262/month and requires higher eligibility or a provider exception under those limits. It is a fallback, not the selected first attempt. [1,16]

Epic recommends **32 GB RAM for Linux development**. The 8 GiB selection is an unproven, narrowly scoped attempt against an installed engine: one project module with seven authored `.cpp` files and one NullRHI reader test. A proposed fixed 16 GiB swap file provides bounded backing storage for memory pressure; `MaxParallelActions=1` requests reduced build concurrency. Neither establishes memory sufficiency or completion time. Shared CPU performance, compiler/PCH generation, linking and Editor startup must be observed on the worker. [15,17]

Four and five hours are workload and deletion deadlines, **not a provider-enforced billing cap**. Independent teardown is implemented in source but has not been configured or verified against an account. No paid resource should start until the access and live deletion prerequisites below are satisfied. Completion within the allowance is unproven; the authorization buys one attempt, not a guaranteed successful Unreal build.

The inspected ZIP is **39,819,212,076 bytes (about 37.08 GiB)** and its 299,959 entries list **76,967,950,798 expanded bytes (about 71.68 GiB)**. Keeping both takes **108.77 GiB**; adding the proposed 16 GiB swap leaves **35.23 GiB nominally** on a 160 GiB disk before the OS, filesystem allocation, workspace and caches. Measure real available disk and memory before transfer/extraction. After full archive integrity checks and validated extraction, discard only the worker's temporary downloaded ZIP before building, reclaiming approximately 37.08 GiB. Retain its recorded size/hash and the original supplied archive. Actual peak resource use remains unmeasured; stop if measured space or memory is insufficient. [ENGINE_ARCHIVE.json](ENGINE_ARCHIVE.json) records the size evidence.

## First attempt and fallback CPU options

| Option | CPU / memory | Included root disk | Published charge | Four-hour estimate | Five-hour deletion-deadline estimate | Selection condition |
| --- | --- | --- | --- | --- | --- | --- |
| DigitalOcean Basic, Regular | 4 shared vCPU / 8 GiB | 160 GiB SSD | $0.07143/hour including disk and ordinary IPv4 | $0.28572 | $0.35715 | Selected first attempt, subject to live eligibility, image/capacity and measured resources; memory sufficiency is unproven. |
| DigitalOcean Storage-Optimized, Regular | 4 dedicated vCPU / 32 GiB | 600 GiB NVMe | $0.38988/hour including disk and ordinary IPv4 | $1.55952 | $1.94940 | Fallback requiring higher eligibility or a provider exception; not an automatic substitution. |
| Hetzner CCX33, Germany or Finland | 8 dedicated AMD vCPU / 32 GB | 240 GB NVMe | €0.2219/hour VM + €0.0008/hour Primary IPv4 = €0.2227/hour | €0.8908 | €1.1135 | Separate fallback; confirm eligibility, capacity and measured working space. The researched public product page rendered an unavailable message. |

These are resource bundles: providers do not separately price their included CPU, RAM and root disk. No extra volume is assumed. Hetzner uses whole-hour rounding; delete with margin before the fifth hour ends to stay within five billed hours. DigitalOcean bills bundled CPU plans per second with a 60-second or $0.01 minimum. [1,3,4,5,6]

Hetzner's CX53 would be still cheaper at €0.0481/hour including IPv4, with 16 shared vCPU, 32 GB RAM and 320 GB disk. Its official cost-optimized page explicitly reports limited availability and marks CX53 unavailable. Shared CPU performance is also variable. Do not make it the execution dependency. [4,6,7]

## Before the paid clock starts

1. Establish a usable private route for transferring the complete inspected **5.8.4 Linux installed ZIP** to the worker. Saved metadata access succeeded, but the complete transfer, full-archive SHA-256, integrity and publisher authenticity have not been verified. The transfer method must be ready before provisioning; the archive is downloaded onto the prepared worker afterward. Do not substitute a full engine-source build within this budget.
2. Pin the observed `Build.version` and `Linux_SDK.json` hashes from [ENGINE_ARCHIVE.json](ENGINE_ARCHIVE.json), and the chosen project commit. Prepare the worker-side length and full-SHA-256 checks for after transfer and before extraction/execution; a full-archive checksum is not a pre-provisioning requirement. The first test uses the public playback fixture; private athlete models and mocap are not prerequisites for this narrow gate.
3. Confirm provider access, actual eligibility, billing currency, applicable tax, the Regular Basic plan price, exact catalogue size/image and LON1 capacity. Public tier policy is not authenticated account evidence. No paid VM has been provisioned during preparation.
4. Make the build script, exact test invocation, evidence destination and deletion mechanism reviewable. Use one VM, with no autoscaling, backups, snapshot, block volume or reserved IP added. Configure an independent deletion process outside the guest; a workflow timeout or operating-system shutdown alone does not stop provider billing. Provider administration credentials stay outside the guest and the public repository.

## Independent deletion implementation, still to configure live

The [deletion watchdog](DROPLET_WATCHDOG.md) and its dormant manual GitHub workflow now implement the separate controller. The workflow must be available on the default branch, configured with a restricted cleanup token, started and observed in its `armed` state before the VM is created. Its source and controlled tests do not establish that a live guard is running. At provisioning, it binds the exact provider resource ID and creation timestamp. The job runs independently of the SSH session, native test and guest. Keep the provider deletion credential in the external controller, not on the guest.

Set the workload deadline at four hours from creation. Export allowlisted reports and logs promptly. The watchdog requests deletion of the bound ID at four hours forty-five minutes, with verification and retries before the five-hour deadline. Only a fresh exact-ID API response confirming absence establishes deletion. Verify separately that no optional paid resources were added or retained. The account owner needs a direct provider-console fallback if the controller fails. No live deletion has been tested; neither watchdog timeouts nor native-driver timeouts guarantee a billing cap.

The watchdog requires provider fields **`vcpus=4`, `memory=8192` and `disk=160`** for the selected machine and accepts an observed catalogue rate of at most **$0.072/hour**. The estimates use the published $0.07143 rate; at the watchdog ceiling, five hours would be $0.36 before tax. The approved $5 total still governs the attempt; actual tax and account charges must fit it before launch. Exact size slug and image ID remain live-verification inputs.

## What the attempt should do

Start Ubuntu 22.04 x86-64, record actual available disk/RAM and prepare a fixed **16 GiB swap file on the included root disk**, subject to measured space. No added volume is included. Transfer the complete inspected archive privately, verify its exact length and record its full SHA-256 before extraction or execution. Validate full ZIP integrity and extraction, and compare the installed metadata hashes before removing only the temporary downloaded ZIP. Extract to the actual directory **`/opt/unreal/UE_5.8`**, which must resolve to that exact path. [NATIVE_EXECUTION.md](NATIVE_EXECUTION.md#inspected-archive-and-worker-installation) gives the installation sequence; [ENGINE_ARCHIVE.json](ENGINE_ARCHIVE.json) pins version **5.8.4**, changelist **58902052**, compatible changelist **55116800**, branch **`++UE5+Release-5.8`** and the metadata hashes.

This archive already lists a bundled **v26 clang 20.1.8** toolchain, and `Linux_SDK.json` names `v26_clang-20.1.8-rockylinux8` for its main, minimum and maximum versions. `SetupToolchain.sh` is absent. Follow the specific [worker installation sequence](NATIVE_EXECUTION.md#inspected-archive-and-worker-installation), check the bundled compiler with `--version`, and verify UBT selects it during the actual build. Epic's current UE 5.7–5.8 requirements table agrees with this compiler/SDK pairing; the quickstart's older clang 18.1.0 and setup-script instructions do not describe this archive. No compiler, UBT selection or native build has been verified yet. [8,15]

Then compile the `SquadPad3DEditor` Development target. For `--run`, the launcher prepares project `Saved/UnrealBuildTool/BuildConfiguration.xml` requesting `MaxParallelActions=1` with UBA, local UBA, XGE, FASTBuild and SN-DBS disabled. It refuses a conflicting existing project configuration. Preflight does not mutate that file. The receipt records the requested configuration and hash; actual executor scheduling and peak memory still require worker evidence. This is not an enforced process-memory limit. [17]

Run only `SquadPad.Runtime.PlaybackReader.Contract` through the native Unreal automation runner with NullRHI. Acceptance requires an actual successful process exit and an exported report identifying that exact successful test. A missing test, zero discovered tests, an absent report or a timed-out process is a failure. Retain the compiler log, test report, engine version, project commit and machine resource information.

This gate establishes that the C++ module compiles and the native reader accepts the fixture. It does not establish that a mesh, animation, shoulder shape, ball contact, frame rate or rendered scene is correct. Stop and export diagnostics on insufficient disk or memory, persistent swap pressure, incompatible engine/toolchain, a broad engine rebuild, unrecoverable failure or the workload deadline. Keep the existing build/test timeouts and independent deletion deadlines; do not add volumes or expand the attempt to compensate.

Before the five-hour deadline, export small evidence files to the authorized destination and destroy the VM. Verify deletion. Keep the engine distribution and credentials out of public GitHub artifacts. The estimate assumes no transfer overage, no paid retained storage, and successful cleanup by the deadline. It is not a provider-enforced dollar cap; alerts alone do not cap charges.

## Shutdown and persistence

DigitalOcean continues charging while a VM is powered off; destroy it to stop compute billing. The included root disk persists for the VM's lifetime and is lost when the VM is destroyed. Optional snapshots are separately priced at $0.06/GB-month; this proposal retains none. [1,3]

Hetzner also bills powered-off instances. Delete both the server and its separately billed Primary IPv4; an unassigned surviving IP still incurs charges. Snapshots and backups also remain billable if retained. The quoted CCX33 monthly cap is €138.49 plus the IPv4 cap of €0.50, excluding VAT, so leaving the machine idle is materially different from the proposed five-hour attempt. [4,5,6]

## Later GPU and Apple stages

For a separate visual trial, DigitalOcean publishes an on-demand RTX 4000 Ada VM in **TOR1**, with 20 GB VRAM, 8 vCPU, 32 GiB RAM and a 500 GiB NVMe boot disk at **$0.76/hour**. Four hours would be **$3.04 before tax**, requiring separate approval. Stock Linux images are supported with manually installed drivers. This is a candidate GPU workstation environment, not a promise of an included Unreal desktop, remote-desktop product or Windows licence. [9,10,11]

The first GPU acceptance checks should be a suitable NVIDIA graphics/Vulkan driver, `vulkaninfo`, an actual Unreal rendered frame, and successful capture/review of that frame. Epic's current Linux guidance calls for NVIDIA 570+ and recommends at least 8 GB VRAM. An AI/CUDA-ready image alone does not prove graphical compatibility. Interactive remote desktop, if required, needs its own tested setup. CPU-only results cannot substitute for this stage. [11,15]

After the native-reader gate, privately restore the original assets, validate hashes, run import/save/reopen receipts and then review the athlete comparison under the agreed camera, lighting and motion. Do not spend the initial CPU allowance preparing a graphics session that the narrow reader test does not need.

The iOS C++ build remains a separate Apple stage: an authorized remote Mac with suitable Xcode and signing access, followed by a real iPhone run. Neither Linux provider above replaces Apple signing/build infrastructure or establishes physical-device performance. No Mac rental, Apple membership or device cost is included here. [12,13]

## Official sources

1. DigitalOcean CPU prices, specifications, bundled IPv4 and snapshot pricing: https://www.digitalocean.com/pricing/droplets
2. DigitalOcean regional availability: https://docs.digitalocean.com/products/droplets/details/availability/
3. DigitalOcean billing rules: https://docs.digitalocean.com/products/droplets/details/pricing/
4. Hetzner current June 2026 cloud rates: https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/
5. Hetzner specifications and rendered availability: https://www.hetzner.com/cloud/general-purpose/
6. Hetzner IPv4 rates and billing: https://docs.hetzner.com/cloud/servers/primary-ips/overview/ and https://docs.hetzner.com/cloud/billing/faq/
7. Hetzner shared CX53 specifications and limited availability: https://www.hetzner.com/cloud/cost-optimized/
8. Epic Linux installed-build quickstart; its older toolchain instructions do not describe the inspected 5.8.4 archive: https://dev.epicgames.com/documentation/en-us/unreal-engine/linux-development-quickstart-for-unreal-engine
9. DigitalOcean GPU specifications and prices: https://www.digitalocean.com/pricing/gpu-droplets
10. DigitalOcean GPU region table: https://docs.digitalocean.com/products/droplets/details/gpu-availability/
11. DigitalOcean GPU operating systems and driver setup: https://docs.digitalocean.com/products/droplets/getting-started/recommended-gpu-setup/
12. Epic remote Mac iOS builds: https://dev.epicgames.com/documentation/unreal-engine/creating-remote-builds-of-unreal-engine-projects-for-ios?lang=en-US
13. Apple simulator and physical device guidance: https://developer.apple.com/documentation/xcode/running-your-app-on-simulated-or-physical-devices
14. DigitalOcean dedicated CPU classification: https://docs.digitalocean.com/products/droplets/concepts/choosing-a-plan/
15. Epic current Linux requirements and UE 5.7–5.8 compiler/toolchain table: https://dev.epicgames.com/documentation/en-us/unreal-engine/linux-development-requirements-for-unreal-engine
16. DigitalOcean published resource tiers and plan limits: https://docs.digitalocean.com/platform/resource-limits/
17. Epic UBT project configuration, parallel actions and executor switches: https://dev.epicgames.com/documentation/en-us/unreal-engine/build-configuration-for-unreal-engine

Source URLs and arithmetic assumptions are preserved in [REMOTE_BUILD_COSTS.json](REMOTE_BUILD_COSTS.json). The implemented launch path and evidence requirements are in [NATIVE_EXECUTION.md](NATIVE_EXECUTION.md).
