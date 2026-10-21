# SP2 first native run: approved remote compute budget

Prices checked against official sources on 20 October 2026. The user approved up to **US$5 including tax for one attempt** on 21 October 2026. No account, paid resource, worker connection or purchase has been created. Provider and engine access remain unverified. Mac access remains paused.

## Recommendation

Use one temporary DigitalOcean Storage-Optimized Linux VM in **LON1**, reached over direct SSH, for the first native build and reader-contract test. Choose the regular 4 dedicated vCPU, 32 GiB RAM, 600 GiB NVMe plan at **$0.38988/hour**. The bundled public IPv4 and disk have no additional charge. The provider lists this plan family in LON1, but the exact size's account eligibility and live capacity must be confirmed. It will not be registered as a self-hosted GitHub runner. [1,2,14]

The approved allowance is **up to US$5 in provider charges, including applicable tax, for one attempt**. Authenticated provider/Epic access remains a launch prerequisite; the budget does not need to be approved again. Four hours for installation and execution cost $1.55952; a further one-hour cleanup allowance is $0.38988, making **$1.9494 before tax**. As an illustrative calculation only, 25% tax would make that $2.43675, leaving $2.56325 within the authorization. The actual tax must be checked in the account; no exchange-rate or bank-fee guarantee is implied. Promotional credits are not assumed.

Four and five hours are workload and deletion deadlines, **not a provider-enforced billing cap**. Independent teardown is implemented in source but has not been configured or verified against an account. No paid resource should start until the access and live deletion prerequisites below are satisfied. Completion within the allowance is unproven; the authorization buys one attempt, not a guaranteed successful Unreal build.

The reason to favor this plan is storage headroom: the exact Unreal 5.8 archive and extracted-install size have not been inspected. The extra disk avoids betting the first native run on a 240 GB system volume. This is an engineering choice, not an Unreal minimum-storage specification.

## Two suitable CPU options

| Option | CPU / memory | Included root disk | Published charge | Four-hour estimate | Five-hour deletion-deadline estimate | Selection condition |
| --- | --- | --- | --- | --- | --- | --- |
| DigitalOcean Storage-Optimized, regular | 4 dedicated vCPU / 32 GiB | 600 GiB NVMe | $0.38988/hour including disk and bundled IPv4 | $1.55952 | $1.94940 | Preferred; confirm exact size, Ubuntu image and capacity in the account before launch. |
| Hetzner CCX33, Germany or Finland | 8 dedicated AMD vCPU / 32 GB | 240 GB NVMe | €0.2219/hour VM + €0.0008/hour Primary IPv4 = €0.2227/hour | €0.8908 | €1.1135 | Cheaper alternative once the engine footprint fits with working space; capacity must be confirmed. The public product page currently renders an unavailable message. |

These are resource bundles: providers do not separately price their included CPU, RAM and root disk. No extra volume is assumed. Hetzner uses whole-hour rounding; delete with margin before the fifth hour ends to stay within five billed hours. DigitalOcean bills bundled CPU plans per second with a 60-second or $0.01 minimum. [1,3,4,5,6]

Hetzner's CX53 would be still cheaper at €0.0481/hour including IPv4, with 16 shared vCPU, 32 GB RAM and 320 GB disk. Its official cost-optimized page explicitly reports limited availability and marks CX53 unavailable. Shared CPU performance is also variable. Do not make it the execution dependency. [4,6,7]

## Before the paid clock starts

1. Confirm an authorized Epic account can obtain the exact **5.8 Linux installed ZIP**. Epic documents precompiled Linux installations and sign-in-protected downloads, plus `SetupToolchain.sh` for C++ development. The public documentation does not establish which exact archive is accessible in this user's account. Do not substitute a full engine-source build within this budget. [8]
2. Record the archive version, checksum, compressed size and, where available, expanded-size estimate. Pin the chosen project commit. The first test uses the public playback fixture; private athlete models and mocap are not prerequisites for this narrow gate.
3. Confirm the provider account, billing currency, applicable tax, regular plan price, image availability and capacity. No account creation, card entry or resource launch has occurred during preparation.
4. Make the build script, exact test invocation, evidence destination and deletion mechanism reviewable. Use one VM, with no autoscaling, backups, snapshot, block volume or reserved IP added. Configure an independent deletion process outside the guest; a workflow timeout or operating-system shutdown alone does not stop provider billing. Provider administration credentials stay outside the guest and the public repository.

## Independent deletion implementation, still to configure live

The [deletion watchdog](DROPLET_WATCHDOG.md) and its dormant manual GitHub workflow now implement the separate controller. The workflow must be available on the default branch, configured with a restricted cleanup token, started and observed in its `armed` state before the VM is created. Its source and controlled tests do not establish that a live guard is running. At provisioning, it binds the exact provider resource ID and creation timestamp. The job runs independently of the SSH session, native test and guest. Keep the provider deletion credential in the external controller, not on the guest.

Set the workload deadline at four hours from creation. Export allowlisted reports and logs promptly. The watchdog requests deletion of the bound ID at four hours forty-five minutes, with verification and retries before the five-hour deadline. Only a fresh exact-ID API response confirming absence establishes deletion. Verify separately that no optional paid resources were added or retained. The account owner needs a direct provider-console fallback if the controller fails. No live deletion has been tested; neither watchdog timeouts nor native-driver timeouts guarantee a billing cap.

The watchdog accepts an observed catalogue rate of at most **$0.39/hour** to allow for price representation, while this estimate retains the published $0.38988 rate. Even at that ceiling, five hours would be $1.95 before tax. The approved $5 total still governs the attempt; actual tax and account charges must fit it before launch.

## What the attempt should do

Start Ubuntu 22.04 x86-64, install the official engine ZIP and matching Epic toolchain, verify `Build.version`, then compile the `SquadPad3DEditor` Development target through Unreal's build tools. The worker has four vCPUs and 32 GiB RAM. The launcher leaves compiler parallelism to the installed Unreal Build Tool configuration and automatic CPU/memory scheduling; it does not configure a fixed action cap.

Run only `SquadPad.Runtime.PlaybackReader.Contract` through the native Unreal automation runner with NullRHI. Acceptance requires an actual successful process exit and an exported report identifying that exact successful test. A missing test, zero discovered tests, an absent report or a timed-out process is a failure. Retain the compiler log, test report, engine version, project commit and machine resource information.

This gate establishes that the C++ module compiles and the native reader accepts the fixture. It does not establish that a mesh, animation, shoulder shape, ball contact, frame rate or rendered scene is correct. The four-hour work window is an allowance; stop early on a missing engine, incompatible version, exhausted disk or unrecoverable build failure, and export a diagnostic report.

Before the five-hour deadline, export small evidence files to the authorized destination and destroy the VM. Verify deletion. Keep the engine distribution and credentials out of public GitHub artifacts. The estimate assumes no transfer overage, no paid retained storage, and successful cleanup by the deadline. It is not a provider-enforced dollar cap; alerts alone do not cap charges.

## Shutdown and persistence

DigitalOcean continues charging while a VM is powered off; destroy it to stop compute billing. The included root disk persists for the VM's lifetime and is lost when the VM is destroyed. Optional snapshots are separately priced at $0.06/GB-month; this proposal retains none. [1,3]

Hetzner also bills powered-off instances. Delete both the server and its separately billed Primary IPv4; an unassigned surviving IP still incurs charges. Snapshots and backups also remain billable if retained. The quoted CCX33 monthly cap is €138.49 plus the IPv4 cap of €0.50, excluding VAT, so leaving the machine idle is materially different from the proposed five-hour attempt. [4,5,6]

## Later GPU and Apple stages

For a separate visual trial, DigitalOcean publishes an on-demand RTX 4000 Ada VM in **TOR1**, with 20 GB VRAM, 8 vCPU, 32 GiB RAM and a 500 GiB NVMe boot disk at **$0.76/hour**. Four hours would be **$3.04 before tax**, requiring separate approval. Stock Linux images are supported with manually installed drivers. This is a candidate GPU workstation environment, not a promise of an included Unreal desktop, remote-desktop product or Windows licence. [9,10,11]

The first GPU acceptance checks should be a suitable NVIDIA graphics/Vulkan driver, `vulkaninfo`, an actual Unreal rendered frame, and successful capture/review of that frame. Epic's current Linux guidance calls for NVIDIA 570+ and recommends at least 8 GB VRAM. An AI/CUDA-ready image alone does not prove graphical compatibility. Interactive remote desktop, if required, needs its own tested setup. CPU-only results cannot substitute for this stage. [8,11]

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
8. Epic Linux installed builds, C++ toolchain and graphics requirements: https://dev.epicgames.com/documentation/unreal-engine/linux-development-quickstart-for-unreal-engine
9. DigitalOcean GPU specifications and prices: https://www.digitalocean.com/pricing/gpu-droplets
10. DigitalOcean GPU region table: https://docs.digitalocean.com/products/droplets/details/gpu-availability/
11. DigitalOcean GPU operating systems and driver setup: https://docs.digitalocean.com/products/droplets/getting-started/recommended-gpu-setup/
12. Epic remote Mac iOS builds: https://dev.epicgames.com/documentation/unreal-engine/creating-remote-builds-of-unreal-engine-projects-for-ios?lang=en-US
13. Apple simulator and physical device guidance: https://developer.apple.com/documentation/xcode/running-your-app-on-simulated-or-physical-devices
14. DigitalOcean dedicated CPU classification: https://docs.digitalocean.com/products/droplets/concepts/choosing-a-plan/

Source URLs and arithmetic assumptions are preserved in [REMOTE_BUILD_COSTS.json](REMOTE_BUILD_COSTS.json). The implemented launch path and evidence requirements are in [NATIVE_EXECUTION.md](NATIVE_EXECUTION.md).
