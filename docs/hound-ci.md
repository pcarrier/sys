# Hound's disposable CI pool

`feat/hound-ci.nix` is imported **only by hound**. It does not change the legacy
`github-runner-hound.service`, crab/crab-win, indentbox, or the deployment runner.

Since generation **nspawn-20261007** each job runs in a fresh **systemd-nspawn
NixOS container** (Pierre, 10-06 21:02 UTC: "straight NixOS, no qemu"). The
Ubuntu KVM VM design below ("VM generations") is kept for history and rollback:
its units, `base-cache-v2.qcow2` and GC roots stay on hound.

## Containers (generation nspawn-20261007)

### One job

1. The slot's controller (`hound-ci-N.service`, root, hardened, 1 GiB/1 CPU)
   stops any leftover job unit, then reconciles previous registrations, POSTs `generate-jitconfig` (labels as
   before: slots 1–3 `[self-hosted, Linux, X64, hound-ci, hound-ci-main]`, slot 4
   `[self-hosted, Linux, X64, hound-ci-main]`), writes the single-use JIT config
   to `/run/hound-ci-N/jit` (root 0600) and asks PID 1 for the transient job unit
   `hound-ci-job-N.service` (`systemd-run --wait`). It keeps the gh credential
   (`LoadCredential`); the container never sees it.
2. The job unit (`Slice=hound-ci.slice`, `BindsTo=` the slot, `MemoryMax=18G`,
   `MemoryHigh=17G`, `CPUQuota=600%`, `TasksMax=16384`, `RuntimeMaxSec=8h`,
   `Delegate=yes`, `PrivateMounts=yes`, `DevicePolicy=closed` with nspawn's
   device list plus `/dev/zfs` for its own dataset work, and a cgroup
   `IPAddressDeny=` of private, link-local, multicast and IPv6 destinations
   except loopback and the inner Docker networks, a second layer beside nft):
   - `ExecStartPre` `job-prepare`: refuses unless the `hound_ci` table holds the
     slot's job chains (fail closed), destroys the slot's leftovers, reaps
     datasets set aside earlier, checks free space, then creates a fresh dataset
     `tank/hound-ci/job-N` (quota 120G, `devices=off`, **`canmount=noauto`**).
   - `ExecStart` `job-run`, in its own private mount namespace: mounts the
     dataset (`/var/lib/hound-ci/job-N`, an empty root) and the **closure-only
     store view** (below), then execs `systemd-nspawn --keep-unit --register=no
     --private-users=pick --network-veth --bind-ro=/run/hound-ci-N/store:/nix/store
     --system-call-filter='~io_uring_setup io_uring_enter io_uring_register'
     --load-credential=jit:… <system>/init`; its console goes to
     `job-N/console.log` on the job's dataset, never the host journal.
   - `ExecStartPost` `job-network`: the container's sysfs (below), the veth
     pair `ve-hci-job-N` 10.231.N.1/30 ↔ `host0` 10.231.N.2 with a default
     route, then the JIT file is deleted (nspawn holds it as a credential).
   - `ExecStopPost` `job-cleanup`: the console's last 64 KiB to
     `/run/hound-ci-N/console.tail`, then `zfs destroy -r` of the job dataset;
     if the kernel still holds it (busy), it is renamed aside
     (`tank/hound-ci/reap-job-N-<UTC>-<id>`) for a later `job-prepare` to reap,
     so the slot's next job never waits on it.

   Job datasets are never mounted in hound's own mount namespace: a namespace
   copied from it while one is mounted there (Nix keeps one per build sandbox)
   pins that mount until the build ends, and `zfs destroy` says busy (10-07:
   three times on the canary slot, once failing its next start).
3. In the container (`feat/hound-ci/container.nix`, built from this flake's
   nixpkgs), `hound-ci-job.service` waits for the route, runs the preflight
   (Docker, Chrome **with** its sandbox, host/private addresses unreachable,
   HTTPS to github.com) and then nixpkgs' `github-runner` (2.337.0)
   `Runner.Listener run --jitconfig` as `runner`; when it ends the container
   powers off. The controller reads the advisory markers
   (`HOUND_CI_GUEST_PREFLIGHT_OK`, `HOUND_CI_GUEST_JOB_FINISHED`) from the tail,
   DELETEs the runner by id and starts the next job.

A DELETE GitHub refuses (422 for a runner it still sees busy after a killed job
or a controller restart, 5xx) never ends the controller: the record moves to
`/var/lib/hound-ci/slot-N-stale-<id>.json`, later iterations retry it (dropped
on 204 or 404), and the slot carries on with a fresh JIT name. Only a container
that never passes its preflight ends the controller (systemd's start limit, 4
per hour, then holds the slot); a job killed after preflight (OOM, timeout) is
the job's, and the next container starts. That was the 10-07 03:58 UTC outage:
after `pkill -9 qemu`, every restart raised on the 422 and the slots hit their
start limit.

The slots `Want` (not `Require`) `hound-ci-firewall.service`: a firewall
restart doesn't restart the slots and kill their jobs; `job-prepare` refuses a
container while the slot's chains are missing, and the job units' cgroup
`IPAddressDeny=` covers running jobs during the table's recreation. The
firewall always makes chains for slots 1–4 and the canary slot 5.

Failed gh calls are logged as `gh api failed: METHOD path exit=N http=S`
(no body, fields or token): the VM controller's bare `CalledProcessError`
couldn't say which call failed (slot 3, 10-06 19:44/19:45 UTC).

### What jobs get

NixOS, not Ubuntu: Docker 29 (the job's own `dockerd`, overlay2, crun),
Google Chrome at `/usr/bin/google-chrome`, `/usr/bin/python3` with GTK/WebKit
introspection, Xvfb, Node 26, rustup (jobs install their toolchain, which
nixpkgs' rustup patches), the C toolchain, CMake, pkg-config, clang/libclang
(`LIBCLANG_PATH`), OpenSSL, mkcert, D-Bus, Helm 4, gh, PowerShell, and nix-ld
for downloaded binaries (setup-node's Node, sccache). `sudo` works (root in the
container is unprivileged on hound). The app's CI is compatible with both
images since xmit-dev/ultimator#381 (no APT on NixOS; Chrome found on PATH;
the Flower cache key uses the container's system closure instead of dpkg).

### Trust boundary (weaker than the VMs)

A job is root in a container whose user namespace maps to an unprivileged,
per-boot host UID range (`--private-users=pick`): no host UID, no host
capability, no host file it can write. Its `/nix/store` holds **only the
container system's closure** (851 paths at nspawn-20261007, from
`closureInfo`'s `store-paths`): `job-run` builds a tmpfs of read-only,
nosuid, nodev binds of exactly those paths in nspawn's private mount namespace,
so hound's other store paths (flake sources of private repositories, other
sessions' build outputs and `.drv` environments, units with credentials) are
not visible. There is no Nix daemon socket, Docker socket, `/src`, credential or
other host path in it. Its network namespace's only link is its veth; the `hound_ci` nft
table rejects everything from `ve-hci-job-*` to the host itself (input), to
private/CGNAT/link-local/LAN/other-slot addresses, IPv6 and spoofed sources
(forward), and new connections into the container, and masquerades the rest.

But it shares hound's **kernel**: a kernel bug reachable from an unprivileged
user namespace (and jobs can create nested ones: Chrome's sandbox, Docker)
reaches the host, which a KVM guest couldn't. nspawn's seccomp filter and
capability set apply, and **io_uring is denied** (`io_uring_setup`, `_enter`,
`_register` return EPERM in the container and everything under it); the kernel
is the boundary. Pierre accepted the shared kernel on 10-07 on those two
conditions: "Accept shared kernel, with the io_uring block and a store limited
to the container's own packages". Treat hound-ci as running
code from anyone who can open a PR on xmit-dev/ultimator, as before.

The container also gets a read-only **sysfs of an empty, host-owned network
namespace** at `/run/hound-ci-sysfs` (root 0700). Docker (crun, and the
privileged `docker:dind` the failover test runs) mounts a fresh sysfs per
container, which the kernel permits in a user namespace only if a fully
visible sysfs is already mounted there, and nspawn's `/sys` is a tmpfs of
read-only sysfs subdirectories. That mount shows the same global device and
kernel information as nspawn's `/sys` and no host interfaces; container root
could remount it read-write, but its files are owned by the host's root.

### Deploying and rolling back

`feat/hound-ci/deploy-nspawn.py` (root): `--check` (read-only), `--apply`,
`--rollback`. Like earlier rollouts it changes attached unit links
(`/etc/systemd/system.attached`), never the host profile: never
`nixos-rebuild switch` hound for this (its live profile carries changes not on
main). `--apply` refuses unless all four slots and the canary controller
`hound-ci-5` are down (it never stops a slot, so it can't kill a job; the
firewall restart would cost the canary its job), writes the rollback ledger once
(`/var/lib/hound-ci/rollout-nspawn-20261007/rollback.json`), roots the new units,
the container system and its closure list in `/nix/var/nix/gcroots/hound-ci-rollout-sources-20261007`,
replaces the firewall's and the slots' links (staged symlink + rename), runs one
`daemon-reload`, restarts the firewall (it keeps the QEMU UID rules too) and
starts the slots one by one. `--rollback` restores the ledger's links and
reloads; stop the container slots first (between jobs), then start the VM
slots. Every step is in `record.jsonl` next to the ledger.

# VM generations (October 5–7, 2026)

## Intended operation

- Four repository-bound slots for `xmit-dev/ultimator`. With
  `reservedMainSlots = 0` every slot registers the original
  `[self-hosted, Linux, X64, hound-ci]` (no `--labels` argument). Hound sets
  `reservedMainSlots = 1`: slot 4 registers only
  `[self-hosted, Linux, X64, hound-ci-main]`, slots 1–3
  `[self-hosted, Linux, X64, hound-ci, hound-ci-main]` (see "Reserved main
  slot" below).
- Each job gets a fresh Ubuntu **24.04 amd64 KVM VM**, six vCPUs, **16 GiB** RAM,
  and a **120 GiB sparse qcow2 disk**. Its dedicated `tank/hound-ci` dataset has
  hard quota/refquota **512 GiB**; admission requires 1 TiB shared-pool free and
  128 GiB remaining CI quota per new VM. Existing mismatched datasets fail
  without mutation. All four slots together have a 72 GiB host
  cgroup ceiling and 24 CPU-equivalents; each slot has an 18 GiB host ceiling.
  CPU/IO weights are 20 to yield to interactive/development work.
- Private Docker daemon/network/storage **inside each VM**; native Chrome
  namespace sandbox, Rust 1.99.0/rustfmt/Clippy, Node 24, Helm, gh, Python,
  PowerShell and the native libraries required by Ultimator's setup action.
- The root controller requests a **single-runner JIT configuration**, not a
  reusable one-hour registration token. It receives the exact runner id and
  uses that id for orphan cleanup. The existing host gh credential stays in a
  root-only systemd `LoadCredential` directory; it never enters a VM or disk.
- An independently verified official Ubuntu image SHA256 and runner archive
  SHA256 are source-pinned. Helm is version-pinned; Node 24 resolves once during
  baking and records its exact version/checksum. Signed APT repositories and
  rustup bootstrap hashes are recorded in `/etc/hound-ci/image-versions`.
  These latter packages are not a fully reproducible source-pinned closure.
- Guest preflight runs before runner startup: OS/architecture/compiler/tools,
  Docker access, Chrome **without** `--no-sandbox`, and denied host/private TCP.
- On job completion the guest powers off. The root controller removes its
  overlay/seed and starts a new VM. A host-enforced eight-hour VM lifetime,
  64 MiB capped private serial log, and bounded failed-start rate protect the
  host from an unresponsive/hostile guest. Successful jobs do not consume the
  failed-start allowance.

## Isolation boundary

Guest jobs deliberately have root/sudo/Docker privileges **inside their own VM**.
QEMU runs under a different unprivileged uid/group per slot and for the baker,
with only KVM device access, dropped capabilities and its seccomp sandbox.
There is no 9p/virtiofs/shared host directory, host Docker socket, SSH forwarding,
vsock control channel, monitor socket, or host operator credential mount.

The host's separate `inet hound_ci` nftables table rejects those QEMU uids'
traffic to host-local addresses (`fib ... type local`), private/CGNAT/link-local,
multicast and connected LAN/Docker prefixes. The controller atomically installs
only its own table, never flushes the host/Docker ruleset, and fails startup
unless every QEMU uid is denied a connection to a **known listening** host
loopback socket. The guest uses public DNS directly; SLIRP IPv6 is disabled.
Systemd cgroup IP denies are additional defense in depth, not the sole boundary.

This is **public-internet egress**, not a GitHub-only destination allowlist.
Host public NAT/hairpin endpoints or additional remote management addresses
need explicit denial before use if they expose non-public services. A kernel,
KVM/QEMU exploit is still a residual risk; VMs are not a promise of perfect
isolation. Keep host/QEMU and the pinned runner patched. Disabling the runner's
auto-update means a stale/required-security-update runner can stop taking jobs;
rebake/update deliberately, rather than falling back to billable hosted jobs.

## Storage/readiness gate — October 5, 2026

At implementation time hound had a serious ongoing storage incident: very high
I/O pressure and a previously reported ZFS pool with 11 data errors/TRIM activity.
The parent initially held image baking/activation because of that incident.
Pierre explicitly replied **“Worry not about hound” at 03:55 UTC**, releasing
that precautionary hold and reauthorizing the narrow CI rollout. This is not
storage-health proof or authority for TRIM/ZFS repair, pool changes, or changes
to existing datasets. Source tests and bounded, low-priority unit derivation
builds do not establish storage health.

## Verified operational readiness — October 5, 2026

The core pool is **qualified and active**, not just Nix-built:

- **06:16:36 UTC:** trusted Ubuntu 24.04.5 image baked successfully; independent
  child exit `0`, actual native Docker/namespace-Chrome/WebKit/tool preflights,
  cloud-init clean exit `0`, and the sealing marker all passed. The base is
  root:root **0444**, never contains host credentials or runner registrations.
- Actual QEMU `/proc` attestation passed for the baker and all four slots:
  expected separate uid/gid, KVM-only supplementary membership, **all five
  capability sets zero**, `NoNewPrivs=1`, and seccomp active. Unknown/pre-exec
  metadata fails closed. The root controller carries only the already-bounded
  UID-transition bit across interpreter exec, then drops it before QEMU.
- **06:29:58 UTC GitHub API:** four repository runners simultaneously
  **online/busy**, exact labels `[self-hosted, Linux, X64, hound-ci]`:
  ids **25, 27, 26, 28** for slots 1, 2, 3, 4. Actual Rust and TypeScript jobs
  were assigned, not merely queued or inferred from service state.
- Slot 1 executed the connector, Workflow validation, and Pinned dependencies
  in **distinct fresh VMs/JIT names**, erased each disk/seed and replaced it.
  GitHub independently confirmed Workflow validation and dependencies succeeded.
- The connector initialized real PostgreSQL/ClickHouse containers successfully,
  then failed an application relay-backpressure assertion (240 pass/1 fail/1
  skip). **This is not an all-green CI claim or a proven baseline issue.** No
  fixture assertions, buffers, retries or deadlines were weakened to hide it.
- All four users were denied host gh-credential reads and host Docker-socket
  writes. A real slot-2 seed had an own-user positive read control while slot 1
  was denied. Guest pre-JIT host/private denial checks passed; the dedicated
  nft table recorded **47 host-local + 14 private-address rejects** by 06:28:58.
- Actual `multi-user.target` Wants includes only the five new firewall/slot
  services; attachments and GC roots persist without replacing Nix's `/etc`
  tree. Aggregate 72 GiB/24 CPU-equivalent ceilings and the 512 GiB quota were
  verified. Legacy hound runner **PID 942213** and host **profile 230** unchanged;
  no shared Docker/YAS/devbox/production restart or whole-host switch.

The companion workflow PR **xmit-dev/ultimator#231 was externally merged at
06:40:30 UTC**, GitHub-confirmed main SHA
`50a66188a38d75dac2203a24efac15b4232d7656`. This session did not merge it.
New main workflows use the qualified pool; queued jobs continue as slots free.
The graph has ten expanded core jobs (including the aggregate gate created
when needs finish), plus the connector. Full test-suite success is distinct
from infrastructure readiness. **The sys PR remains open, ready for review;
no sys/main merge was performed here.**

### Sealed image fingerprint and versions

Public ledger recovered from a separate, bounded **no-JIT** clone; the base was
never mounted on the host and running worker disks were not disturbed:

- Baked **2026-10-05 06:16:32 UTC**; base SHA256
  `0e856d33b2e9c08e54863b3916d7a3aef7fce69d513f7f163450e3a015d9056b`.
- Node **24.21.0**, Rust **1.99.0**, Helm **3.19.0**, GitHub runner **2.337.0**.
- Docker **29.1.3**, gh **2.102.0**, Google Chrome **154.0.8037.97**,
  PowerShell **7.6.6**, Python **3.12.3**.
- Official upstream Ubuntu-cloud-image SHA256 remains separately pinned in
  `supervisor.py`; the baked-image fingerprint is not that upstream hash.

### Preserved first failures and limitations

The first pre-VM chmod-owner failure and missing effective UID-transition
capability were measured, fixed without broadening the bounding set, and kept
under CI-owned failure directories. A later real guest completed native APT
then exited before sealing. Its original wrapper masked the child status and
serial-only output omitted stderr. Those diagnostics were corrected with an
independent child, private regular-file/result witnesses, explicit status and
separate shutdown. The next instrumented bake passed. **Needrestart causality
was not established; its policy was not disabled or changed.**

Host I/O/ZFS health was not certified by this rollout; Pierre explicitly
released the precautionary CI hold at 03:55 UTC. No global storage repair was
performed. Public-internet egress and kernel/QEMU residual risks remain as
stated above. Exact-name recovery for an uncertain JIT POST can still miss a
registration appearing only after a single empty reconciliation response; this
nonblocking unused-runner orphan limitation is not a permission to poll or
broadly delete registrations.

## Narrow deployment after clearance

1. Inspect current hound system generation, `/src/sys`, existing runners and
   other sys PRs first. Preserve SSH admission changes and indentbox's separate
   production CD restoration. **Do not rebuild/switch an old whole-host config.**
2. Build just these Nix `config.systemd.units.<name>.unit` derivations:
   `hound-ci-storage.service`, `hound-ci-firewall.service`, `hound-ci-image.service`, `hound-ci-1.service`
   through `hound-ci-4.service`, and `hound-ci.slice`. Record their exact store
   paths/source SHA. Use an owned, bounded system-manager build unit as the
   regular user if the user systemd bus is unavailable; do not repair that bus.
3. Create only the five declared system users/groups (baker and slots), not in
   wheel/docker, with KVM group. Create `/var/lib/hound-ci` root-owned mode 0751.
   Before changing an existing account, verify it is the matching CI account.
4. Install links to just those eight unit files and explicit Nix GC roots for
   their store paths; use `/etc/systemd/system.attached` because Nix owns the
   read-only `/etc/systemd/system` tree. `daemon-reload` is not a host generation
   switch. Verify
   unit syntax and keep an activation/rollback manifest. Enable only the new
   firewall/slot units for persistence; don't touch legacy runner/Docker/YAS.
5. Start **only the firewall**; verify all five uid negative-connect checks,
   table rules/counters, and denied credential/Docker/other-slot access.
6. Start the **owned storage unit** after verifying the mountpoint is empty and
   no existing dataset belongs to another workload. It creates only
   `tank/hound-ci` with the stated hard quota, never changes pool properties or
   an existing dataset. Then start **only the image baker**, bounded to two hours.
   Inspect its trusted
   private console on failure; do not blindly retry an existing staging image.
   Wait for successful image preflight/shutdown and immutable base publication.
7. Start **one slot**, verify GitHub repo runner online/JIT labels and native
   guest preflight. Run a smoke job proving Docker, Chrome sandbox, host denial,
   and job completion/new overlay/replacement. Then start the other three and
   prove concurrent readiness/jobs without exceeding the resource envelope.
8. Record UTC activation, exact runner ids/labels and first completed jobs.
   Only then mark infrastructure ready or allow the workflow PR to be merged.

This narrow installation keeps the current boot/system profile unchanged. The
module remains the source of truth for an eventual ordinary hound rebuild;
never deploy indentbox as part of this operation.

### Recovery/rollback

Stop only `hound-ci-{1,2,3,4}.service` and disable their enable links. Remove exact
orphan runner ids owned by this controller. Preserve failed private evidence
before inspecting/removing **only its own** `/var/lib/hound-ci/slot-*` and image
staging directories. Stop/disable the new firewall unit and delete only
`table inet hound_ci`; do not flush the host firewall, stop shared Docker/YAS,
kill unrelated PIDs, delete Cargo targets, or run Nix GC. Restore only new-unit
links/GC roots from the activation manifest. Legacy runners stay untouched.

## Source checks

```sh
PYTHONDONTWRITEBYTECODE=1 python3 feat/hound-ci/test_supervisor.py
bash -n feat/hound-ci/{guest,provision}.sh
git diff --check
nix eval --json .#nixosConfigurations.hound.config.services.hound-ci
# Build only the eight units listed above, never system.build.toplevel.
```

The Python suite mocks gh/nft/QEMU and tests rule construction, the uid negative
check program, source pins, JIT usage and absence of forwarding/shared mounts.
It is not a substitute for the runtime acceptance checks above.

## Trusted image/cache generations (October 5, 2026)

`services.hound-ci.imageName` deliberately defaults to the **existing**
`base.qcow2`. Opening/merging this source change is not approval to switch the
pool. Build a separately named candidate first; select it only during a new,
explicitly approved narrow rollout. Never overwrite an image used by a worker.
The old image remains usable: the new bootstrap uses the normal runner-owned
`_work/_tool` directory when the cache ledger is absent.

### Seed contract

- `/opt/hostedtoolcache/node/24.21.0/x64` and `26.10.0/x64`, with their sibling
  `x64.complete` markers. Official Node release archives are exact-version and
  SHA256 pinned. Archive bytes, member count, expanded bytes, paths, links,
  special-file types and permissions are checked before extraction/publication.
  Both toolcache parents are writable by `runner` **inside its private VM**, so
  setup-node's ordinary unseeded-version download still works.
- The actual `run.sh --jitconfig` process receives `RUNNER_TOOL_CACHE`, home,
  Cargo/rustup homes, path, user and locale explicitly via `env -i`; a profile
  or environment-file marker alone is not the contract.
- Seven **public**, linux/amd64 Docker images are digest pinned in
  `feat/hound-ci/cache-pins.json`: Debian bookworm-slim, Alpine, Node24, dind,
  Ubuntu24.04, PostgreSQL17 and ClickHouse26.8. The builder independently verifies
  the immutable registry index, amd64 child manifest and compressed-byte count
  before pulling; Docker verifies content digests. Tags are installed only
  inside the golden builder's private Docker store.
- `ultimator-browser-test` and `ultimator-yas-test` are **complete images**, not
  partial APT download caches. Labels are
  `app.ultimator.ci.recipe-contract=1` and
  `app.ultimator.ci.recipe-sha256=<SHA256 of exact Dockerfile bytes>`.
  Contract1 recipes are context-free (no COPY/ADD, extra stage or external
  frontend). A job with a changed recipe/context must privately rebuild;
  existing fixture labels do not authorize a different recipe.
- Ultimator is private. The two tiny data-only recipe snapshots were
  independently extracted from freshly fetched upstream main
  `204a1d57af728b4d26f2155f864b8981f11c520c` and committed under
  `feat/hound-ci/fixture-recipes/`. Their hashes are checked again in the guest,
  before downloads. No GitHub credential, checkout, parent job artifact,
  `node_modules`, Cargo target, arbitrary npm/Cargo config, or job root disk is
  used as a seed. Refresh those snapshots and pins deliberately from trusted
  main, never from a PR branch.
- `/etc/hound-ci/cache-manifest.json` is bounded schema1/contract1 provenance:
  source commit, pins hash, exact Node/archive/binary hashes, actual native
  package versions, registry pins, image IDs/sizes and fixture IDs/hashes.
  A positive offline preflight compares real package/tool/Docker state before
  any JIT job runs. Missing or mismatched state fails rather than becoming zero.
- Docker, its socket and containerd are stopped before sealing. The controller
  requires successful provisioning **and** clean/seal markers, immutable root
  ownership, standalone qcow2 format (no backing or external data file), bounded
  qemu-img checks and successful final qcow integrity validation before rename.

### Bounds and refreshes

The trusted builder is now in the same 72GiB/24CPU-equivalent aggregate slice
as the four workers, with its own 6GiB host/4GiB guest/2CPU caps and a **32GiB
per-file ceiling**. That ceiling also bounds the growing standalone candidate
qcow2; exceeding it fails the build, not a storage-health hold. The sum of Docker-reported
unique-image Size values must stay within16GiB (this is an engine-reported
admission check, not an unpacked filesystem measurement/reservation). Source disk virtual
size stays at most120GiB, the dedicated dataset quota remains512GiB, and
admission requires256GiB CI space before a build. These are bounds, not a
reservation against concurrent jobs. No dataset/pool/global storage property,
firewall exception, kernel setting or host Docker/Cargo share is added.

An optional cache-only refresh uses `cache-only.sh` and **both** an explicit
`--source-image base-<generation>.qcow2` and `--source-sha256 <known hash>`.
Only root-owned0444 regular files with the safe golden basename are accepted;
checksum verification precedes the root image parser. The source must be a
previously qualified credential-free trusted golden image. Never rename/chmod a
job/canary/failed builder disk to qualify as a source. A warm trusted refresh
may reuse complete, hash-labelled fixtures; a divergent job clone is always
removed, never sealed/promoted.

### Qualification and rollout gate

Build only the eight unit derivations listed above. For qualification, clone
the candidate into a separate no-JIT VM under the identical QEMU uid/group,
cap0/NNP/seccomp/network policy. Exercise the actual pinned setup-node action
for24and26, normal fallback-parent writes, native helper fast path, matching
fixture reuse, and a deliberately changed YAS recipe's private rebuild. Remove
that deliberately divergent canary disk; retain bounded public evidence.

**No activation authority is implied.** After Pierre explicitly approves:
verify the candidate hash/source ledger and fresh main/source state, select its
imageName in a reviewed config, build only the CI units, drain/restart only the
CI-owned slots as agreed, install exact approved unit links/GC roots, then
qualify fresh JIT jobs. Preserve the old immutable base and rollback manifest.
Do not switch a whole host, touch indentbox, merge PRs, restart shared services,
change kernel/global storage, or promote job state as part of this cache work.

### First candidate failure retained

The first credential-free cache-v1 bake ran07:51:53–08:01:53UTC onOctober5.
The public Node/Docker downloads succeeded, then unauthenticated raw GitHub
recipe access returned404 because the repository is private. Provisioning
failed, no seal marker/final image was published, and four active workers kept
NRestarts0. The exact first private console/result remains under
`/var/lib/hound-ci/image-base-cache-v1/`; the corrected generation uses the
bounded independently verified snapshots described above, not builder auth.

## Cache-v2 qualification — October 5, 2026 (no rollout)

Pierre explicitly cleared **Hound-only qualification and non-main PR updates**
at09:31UTC. Indentbox operations, main merges, whole-host switches and selecting
this image for the running pool remain unauthorized.

### Actual bake and canary results

- Corrected credential-free bake ran08:11:32–08:19:42UTC. Cache seed work recorded
  **379.188s**; the complete cache-only upgrade took approximately8m10 including
  copying the independently hash-attested pristine original base, guest boot,
  preflight and sealing. This is not a fresh-Ubuntu full provisioning benchmark.
- Candidate `/var/lib/hound-ci/base-cache-v2.qcow2`, root:root0444, standalone
  qcow2/no backing/no external data file/no dirty/corrupt flag,120GiB virtual.
  SHA256 **daf2ab773887c98d9b8ac107a6cfcce9db450364d55fa7645ee46a873805296b**.
  Logical file **11,907,432,448B**, measured allocated **4,660,399,104B**.
- No-JIT acceptance clone ran09:35:22–09:36:45UTC,2vCPU/4GiB guest inside the
  existing aggregate slice. ActualQEMU UID979/GID974/allfivecapabilitysets0/NNP1
  and seccomp verified. Guest host/private-network connection-denial checks
  passed. No host mount/socket/forward/registration/API credential was added.
- Exact frozen parent helper bytes were verified against its source manifest,
  copied only into the **canary** seed, never the golden builder. The actual
  pinned `actions/setup-node@49933ea...` distribution script was checksum-verified.

| Acceptance operation | Measured elapsed | Interpretation |
| --- | ---: | --- |
| setup-node24 | **2.846s** | Found24.21.0 in `/opt/hostedtoolcache`; no Node distribution download |
| setup-node26 | **2.154s** | Found26.10.0 in toolcache; no Node distribution download |
| Native prerequisite helper | **0.743s** | All real packages installed; no APT update/install |
| All base + complete browser/YAS fixture reuse | **1.192s** | Both exact recipe labels matched; no pull/build |
| Deliberately changed YAS recipe | **12.770s** | Privately rebuilt; different imageID and changed WORKDIR; browser still reused |

The parent's prior actual job baseline was approximately172s for Docker fixture
preparation and18–21s for native APT updates. The warm fixture helper therefore
removed about171s of that preparation in this acceptance clone. The old19.6–30.5s
setup-node steps also included npm-cache restore: **do not compare those whole
steps directly with the isolated2.15–2.85s selection measurements**. npm cache
restore, service health checks, compiler/GHA caches and full GitHub job throughput
were not benchmarked here. A new unseeded version's toolcache directory and
completion marker were successfully created as runner, proving private fallback
parent writability.

The changed YAS recipe replaced original fixtureID
`sha256:9f88b3f647eaf335ef34919b7da7d133e778692553c87473d0dae14a9adfe41a`
with canary-only
`sha256:03e5f4bdb873affdccdd965e2942d1d3523e2c6f8bd898977640a722191132ba`.
The deliberately divergent private overlay **and seedISO were removed** after
successful shutdown; its console remains as bounded public evidence. No canary
or job filesystem was sealed/promoted.

### Ledger and bounds

`docs/hound-ci-cache-manifest-20261005.json` is the exact historical ledger read
inside that canary: schema1/contract1,2Nodes,7registry-pinned public images,
2complete fixtures,19actual native package versions. Native provenance includes
Ubuntu glibc2.39-0ubuntu8.9, Clang18, OpenSSL3.0.13-0ubuntu3.16 and WebKit2.52.6.
The Node/npm pairs are24.21.0/11.19.0 and26.10.0/11.19.1. Builder source hash
`117e48860c6d282add6e688a223e4f5a40e88a75e6fa56d3e2752bf619050d5c`
and pins hash
`deb2c6e9e9625aef1fd24c194d651ff375bf7d4344fc9b4660fd0713949ee192`
bind the baked guest payload. The later host-only paired-source-argument guard
fix does not change that guest payload or image hash.

Docker's reported unique-image Size sum is **1,377,133,573B**, below the16GiB
reported-size admission check. Docker29's OCI image-store reports are not an
unpacked filesystem measurement or space reservation; the actual hard growth
bound is the **32GiB per-file limit on the standalone qcow**, backed by the
existing512GiB CI dataset quota and256GiB pre-build admission gate. The builder
and canary are in the72GiB/24CPU-equivalent slice, with own6GiBhost/4GiBguest/2CPU
limits. The bake's measured memory peak was4,380,844,032B. No global storage,
filesystem, kernel, firewall exception or shared Docker/Cargo cache changed.

Source checks pass **28/28** cache/controller/security tests, Bash syntax,
ShellCheck, Nixfmt, diff checks, Nix evaluation and all8scoped unit builds. These
are not whole-platform NixOS CI or all-app-test-green claims. The parent retains
three observed Rust failures and interrupted app checks as separate evidence;
no assertions, buffers, deadlines or retries were tuned here.

### Publication and operational gate

The default `imageName` is still **base.qcow2**; all four original controller
PIDs and NRestarts0 were unchanged at09:33 immediately before acceptance. The
shared `/src/sys` checkout stayed unchanged. This candidate is qualified for
review, **not selected/deployed**. Keep the old image/attached units/GC roots
until Pierre approves a separate narrow rollout. No sys/app main merge,
indentbox operation or live pool restart/image switch was performed.

## Approved cache-v2 rollout and legacy drain

Pierre approved a narrow Hound rollout on October5 at11:56UTC: let all busy jobs
finish, then replace **only** the four CI controller/guest units, explicitly
selecting `base-cache-v2.qcow2`. No whole-host/profile switch, legacy runner,
indentbox, main merge, pool-size/resource/storage/kernel/firewall change.
`hosts/hound.nix` now records that explicit image choice for future rebuilds;
the reusable module's default remains `base.qcow2`.

### Why the old controller needs a one-shot gate

The deployed legacy supervisor loops after each single-job VM. Its SIGTERM
handler terminates QEMU; SIGSTOP also freezes its console-reader thread. Neither
is a safe busy-job drain. `feat/hound-ci/drain-old.py` is an **operator-only**
legacy migration helper, not an enabled service or guest payload:

1. Pin each exact old service MainPID/starttime/pidfd and its mount-namespace FD.
   Require four different namespace inodes, none equal to the host namespace;
   require the exact known old supervisor/guest/repo/slot argument shape.
2. Install only four temporary runtime `Restart=no` drop-ins and reload unit
   definitions, without stopping/signalling anything. Verify the loaded holds
   and pinned identities before mounting any gate.
3. In each pinned **private** mount namespace, make propagation recursively
   private, then bind the independently reviewed immutable `drain-gh-gate.py`
   read-only over the old public gh wrapper. Never write a Nix-store file or
   mount in the host/other controller namespaces.
4. Reject ONLY gh API POST requests to
   `repos/xmit-dev/ultimator/actions/runners/generate-jitconfig`, including the tested
   canonical body/input-inferred POST forms. The guarantee is deliberately
   limited to the immutable legacy supervisor's fixed argv contract, not every
   possible gh CLI placeholder/clustered-option/enterprise invocation. No raw arguments, stdin, environment, credentials,
   JIT or guest console data are copied/logged. Exact legacy DELETE cleanup
   delegates the original immutable non-shadowed gh ELF with preserved argv0,
   stdin and telemetry semantics. A root-only public receipt records just the
   pinned caller, exact runner ID/name, source/nonce, intent and return outcome
   (including the old controller's explicit HTTP404 cleanup semantics). All
   other calls exec the original ELF directly.
5. Current QEMU and its reader continue without pause. Any request already
   executing before the gate is adopted and drained; it is never cancelled.
   After each accepted job completes/VM exits/registration cleans up, the old
   next-JIT call receives the fixed drain refusal and the old supervisor exits.
   `Restart=no` prevents another old process or old-image job claim.
   The armer pins each slot's original systemd `InvocationID` and the kernel
   boot ID. It takes the same-boot CLOCK_MONOTONIC boundary **before** the
   first registration read and records that first read verbatim
   (`first_registration_read`, bound to the armer's source SHA). Only a root
   STOP strictly before the boundary **with the first read absent** is
   historical; a record present at the first read (an escaped old DELETE),
   any STOP after the boundary, or a wall-clock step never removes the need
   for a captured DELETE receipt, **unless** the same pinned process later
   STARTed another VM (see "Adoption and DELETE coverage" below).
6. `wait-drained.py` subscribes to trusted root controller/PID1 lifecycle,
   pidfds and subtree `cgroup.events` without timers. It replays this boot to
   bind genuine root-generated START/QEMU-verification/STOP in order and resets
   prior completion on each new VM. The exact pinned legacy source writes raw
   serial **only** to a private console file, never the host journal. Its
   guest-derived preflight/completion booleans remain **advisory**. The waiter
   produces a hardware-drained, **non-certificate** phase only. Manager
   terminal records count only with PID1 + `UNIT` + typed `MESSAGE_ID` **and**
   the exact pinned original `INVOCATION_ID` on the pinned `_BOOT_ID`;
   controller records need the original `_SYSTEMD_INVOCATION_ID`. Records of a
   later invocation (e.g. activation's tracked replacement) can neither satisfy
   nor revoke the original proof; a wrong boot or an unattributable record
   HOLDs. Absence, stale
   MainPID/cgroup evidence, an unclosed latest VM, uncertain pre-gate POST or
   missing local route-block receipt all fail closed.
7. A separate trusted operator obtains bounded ordinary Actions job metadata
   in finite, lifecycle-triggered requests with complete pagination (closed
   run-creation windows and a pinned gh configuration, see "Actions
   collection" below). Every
   adopted runner must bind by exact repository/runner ID/name to a real job,
   run and attempt with positive `completed` status, valid completion time and
   known terminal conclusion. Failed/cancelled jobs remain failed/cancelled,
   not 'all green'. No match, ambiguous identity, nonterminal job, truncated
   pages or a startup failure with no independently proven job stays **HOLD**.
   Completed job A must never certify a later accepted VM/job B. This step
   requires no runner-admin bypass or credentials in the continuous waiter.
8. `finish-drain.py` validates the source/nonce/manifest-bound receipts and
   freshly rechecks all four original exits, holds, identity-bound descendant
   emptiness and registration cleanup before issuing a final certificate.
   `activate-cache-v2.py` must independently validate that complete certificate
   and freshly revalidate the four slots again before replacement/release/start.
   A phase string or four summary booleans is never sufficient.
9. Only after that certificate and exact-source review, replace the four scoped
   controller links and add a separate new GC-root namespace. Preserve old
   unit content/links/enable links/GC roots/image and unchanged host profile.
   Check exact loaded executable/full argv/fragment/drop-ins for each slot,
   require existing dependencies already active and no conflicting jobs, then
   remove only the four owned holds — all four unlinks plus ONE
   `daemon-reload` as a single durable step (`holds-remove-reload`), because
   an unlinked-but-loaded drop-in sets `NeedDaemonReload=yes`, which the effect
   proof rejects — and start only their replacements. Do not
   repair/activate storage, firewall, image, slice or other services implicitly.
   Verify fresh QEMU cap0/NNP/seccomp, cache-v2 backing, guest offline cache/native
   preflight, private new overlays/JITs, registrations, cache hits and real results.
10. Sequential starts use the validator's `ACTIVATION_TRANSITION_API =
   "tracked-controller-identity-v1"`. Before each `systemctl --job-mode=fail
   start -- hound-ci-N.service`, activation fsyncs a per-slot start intent in
   `activation.json` (`slot_starts`): request, store unit source, full argv,
   candidate image path/SHA and the strict pre-start state (MainPID 0, the
   ORIGINAL InvocationID, empty/removed original cgroup, validation SHA). After
   the start it records the result: returncode 0, job `{start, fail, done}`,
   the NEW InvocationID, MainPID, starttime and cgroup. The validator moves a
   slot from strict-stopped to tracked-new only for such a complete record that
   equals activation's in-memory view and the live MainPID/starttime/
   InvocationID/kernel cgroup; the original certificate, old exit, old ordered
   lifecycle, DELETE receipts and Actions proof stay immutable and are
   revalidated at every phase. Unstarted slots must still carry their original
   InvocationID. Unknown, partial or foreign transitions HOLD: nothing is rolled
   back, stopped or killed. Holds may be released only in the start phases.
11. The host's PID1 (systemd 261.2, `baxgs…`) was qualified against upstream
   tag v261 for the effect proof: `feat/hound-ci/systemd-261.2-qualification.json`
   records that all eight effect-proof sources are byte-identical to v261 and
   untouched by every nixpkgs patch/postPatch, and why the one core patch
   (postponed D-Bus queue dispatch) and the v261→261.2 core changes do not alter
   START transaction semantics.
12. **Projection and its contract.** Active barriers keep their state, job,
   load, conditions and actions, their job-forming relations in full and other
   relations only towards closure units (`barrier-job-relations-v1`). Which
   relations form jobs (`JOB_RELATIONS`) is checked before every use by
   *running* the pinned `effect-proof.py`'s `closure()` on synthetic graphs, one
   edge per job type and relation; any difference HOLDs (`check_traversal_contract`).
13. **Device peers.** Follow sets of closure devices need every device with the
   same `SysFSPath`. Each proof reads the complete device index twice but binds
   only closure devices and the devices sharing a non-empty `SysFSPath` with
   one (`closure_device_peers`): a peer appearing or leaving, or a closure
   device leaving or moving, HOLDs; other devices (Docker veths) are not
   bound. A device that vanishes between `ListUnits` and its property read is
   left out only once the manager no longer lists it at that object.
14. **Resume.** `activate-cache-v2.py --resume` (same pins, a new held lease)
   continues a recorded `activation.json` only if every event completed, the
   events are exactly a prefix of the reviewed plan (root namespace, then per
   slot GC root and link, the held reload, the one holds-remove-reload step,
   four starts), the phase is the last completion, every completed start has
   its recorded result, and the manifest, certificate, images and validator are
   the same. It rechecks the partial state exactly as the next step of one run
   would (`resume-validated`), appends `resumes` (UTC, prior phase, lease), and
   performs only the remaining steps. An open intent, an unrecorded start
   result, or any state the journal does not describe HOLDs; nothing is ever
   undone or repeated.
15. **Per-slot start by hand.** `feat/hound-ci/anchor-proof.py` (read-only) is
   the reviewed form of the 10-06 manual completion's proof: with the cleared
   activation and effect sources (direct store paths, SHA-pinned), the lease's
   structure SHA and the four ORIGINAL invocations, it requires every slot on
   its new unit unheld, slots < N running with new invocations, slots ≥ N
   stopped with their original ones, and ONE `validate_dependencies()` pass
   certifying `hound-ci-N` as the sole START effect with exactly the lease's
   structure. Only after `ANCHOR_PROOF_OK` may
   `systemctl --job-mode=fail start -- hound-ci-N.service` run.

### Adoption and DELETE coverage (arm-overlap-v1)

- The finisher adopts a slot's root VM (Actions terminal proof + DELETE
  coverage) iff its root STOP monotonic is at/after **that slot's** gate
  observation-start boundary, or a pre/post gate registration names it, or it
  is the slot's last root VM. VMs that started after the 11:56 approval but
  stopped before arming ran and ended before any drain action touched their
  controller; they stay fully replayed, ordered, bounded and identity-checked
  history, but are not adopted. (Earlier revisions adopted every VM stopped
  after 11:56; at ~23 VMs/hour that multiplied match/HOLD risk and API calls.)
- **Same-process successor proof.** Every VM in a slot's history comes from
  ONE pinned (boot, InvocationID, PID, starttime) controller. The exact legacy
  `worker()` reaches its next root START only after the previous VM's
  `finally: cleanup_record()` returned normally (exact-id DELETE returned 0 or
  legacy-accepted HTTP 404, record unlinked); a DELETE that raised exits the
  process before any later START. So an adopted VM followed by a later START in
  the same history is DELETE-covered. The last VM of each slot still needs a
  positive gate receipt or the strict historical exemption; a receipt that
  exists but never returned (`delete-intent`) still HOLDs.
- HTTP 404 counts as a returned DELETE exactly as the legacy supervisor does
  (an already-removed runner). The gate maps a signal-killed gh to 128+N and
  never records it as success; it restores default SIGPIPE/SIGXFSZ before
  exec'ing the original gh.

### Actions collection (closed windows, pinned gh)

- Runs are listed only through `created=A..B` windows: 6-hour, disjoint and
  gapless, from 31 days (GitHub's 30-day re-run limit keeps the original
  `created_at`) before the earliest adopted VM START to `drained_utc` + 5 s.
  Every window is a closed past interval; the root capture refuses to start
  (creating nothing) until a minute after it closes. Each window is listed
  until two consecutive complete passes agree exactly (at most three): newly
  visible runs may appear (shifted duplicates are skipped by ID), but a run
  disappearing or a shrinking total HOLDs. Every listed run's `created_at` must
  lie in its window.
- **Window splitting.** A window whose `total_count` reaches 1000 (GitHub's
  filter cap, never a complete count) or that does not converge within three
  passes is split into two exact halves, listed depth-first; the paging proof
  records the split with the calls it spent (`workflow_runs_split`). Halves
  stop at 60 s (a smaller window HOLDs) and at most 1024 leaf windows. The
  validator recomputes the same recursive partition: every plan window must be
  listed or split, every leaf's converged total must be < 1000 and equal its
  run count, nothing outside the partition may appear, and split calls enter the
  exact request accounting. The collector also keeps every run ID a split
  window's passes listed: each must be listed again by exactly one leaf under
  that window (a closed window loses no runs), else HOLD.
- Completed runs last updated before earliest-START − 5 s are listed but not
  job-scanned: all their jobs completed before any adopted VM started, which the
  validator rejects anyway. Every other run (any non-`completed` status
  included) has all attempts' job pages read. The validator recomputes windows,
  window membership, scan decisions and exact request accounting.
- Measured 2026-10-05 19:31 UTC: 223 runs created in ~23.5 h and ~0.55 s per
  gh GET. Expected cost: ~125 windows × 2 passes plus the attempts of runs
  updated near the drain, a few hundred to ~1000 GETs, within the unchanged
  4096-call / 45 s-per-call / 30-minute bounds.
- **Trust boundary (P1-4).** The root-launched collector never reads the
  UID-1000-writable `~/.config/gh` (a host-scoped `http_unix_socket` or other
  override there could forge metadata). Root creates `/run/hound-ci-actions-gh`
  (root:2000001005, 0750) holding a fixed `config.yml` and a `hosts.yml` with
  only github.com's login and `oauth_token`, both root-owned 0440. Root reads
  that one token from pcarrier's own `hosts.yml` via a no-follow walk from `/`,
  requiring a single-link regular file owned by UID 1000 (so the copy discloses
  nothing new); it is never printed, logged, hashed into a receipt or exported.
  The collector runs as UID 1000 with the private GID 2000001005, which no
  account, group or subordinate-GID range holds (checked at capture), so other
  accounts (gid 100 is shared with `dauriac`) cannot read the copy. **This is
  not a boundary against a compromised UID 1000**: pcarrier is in the `docker`
  group (root-equivalent) and already owns the token, so a hostile UID-1000
  process could obtain it or interfere anyway. The pinned copy defends against
  other accounts and against casual or accidental config overrides in
  `~/.config/gh`, nothing more. HOME, GH_CONFIG_DIR
  and all XDG paths point at the pinned directory; the update notifier and
  prompts are off. The directory is removed after the capture and must not
  pre-exist. Token integrity does not affect authenticity (TLS to
  api.github.com plus response repository identity); keyring-only storage
  HOLDs. This is a deliberate exception to "no credential reads by root".
- **Cleanup on every path.** The copy is created under umask 077 with
  termination signals blocked until its ownership is recorded; SIGTERM, SIGINT,
  SIGHUP and SIGQUIT unwind through the capture's cleanup (signals stay blocked
  across fork+exec, so no unrecorded collector child exists, and during
  kill/reap/check/removal, so a second signal cannot interrupt it). The
  handler raises at most once and is disarmed by the cleanup's first
  statement; a signal it receives after that is redelivered to the original
  handler once cleanup is done, and the signal mask restored is the one read
  before the capture began. Errors and timeouts take the same path; parse errors never carry file contents. SIGKILL
  or power loss can leave the directory on tmpfs: the next capture or rehearsal
  then HOLDs with one line, `rm -r -- /run/hound-ci-actions-gh`, and never
  removes a directory it did not create.
- **Retrying a failed capture.** `actions-capture/` is one-shot. After a
  failed or interrupted capture, root `finish-drain.py
  --archive-failed-capture` renames it to `actions-capture-failed-N` (N ≤ 8,
  never edited or deleted, state directory fsynced) only if no
  `actions-terminal.json`/`.tmp` exists and no pinned gh copy remains; then
  `--capture` may run again. `--certify` reads only `actions-capture/`.
- **Pre-arm rehearsal.** Root `finish-drain.py --rehearse` (pinned Python
  `-I -B`, from the staged store source) runs the real pinned-config path with
  no manifest and writes no rollout state: it installs the pinned gh copy,
  launches the unchanged bootstrap as UID 1000/GID 2000001005, feeds it a
  `pinned-collector-rehearsal` request (horizon ending a minute ago, earliest
  START 8 h before it, the legacy VM lifetime, so as many windows as a real
  capture's worst case), and the child lists every window of the 31-day horizon
  (with splitting) and reads attempts and complete job pages for qualifying
  runs (no direct job GETs). Root checks the requests against 4096 calls and
  the time against the 30-minute capture timeout, requires the copy removed,
  and prints windows/leaves/splits/runs/requests/elapsed and the executing
  source path and SHA-256.

### Bounds and operating rules

- Replay caps: 2048 VMs per slot and 4096 in all (the all-slot cap binds
  first). Read-only counts at 19:14 UTC were 75/59/74/62 (270) for the current
  invocations, ~23 per hour since approval. The waiter rewrites
  `manifest.json` with full histories, so the gate, waiter, finisher and
  activation all read manifests up to 16 MiB (a full-cap manifest is < 2 MiB);
  the activation journal is bounded at 64 MiB.
- `drain-old.py`, `wait-drained.py` and `activate-cache-v2.py` refuse to run
  except under the pinned Nix Python with `-I -B`. Before arming, `drain-old`
  checks the reviewed old wrapper (SHA-pinned): its exported PATH reaches the
  gated gh directory before any other `gh`, and every loaded ExecStart is that
  wrapper.
- **Idle-runner precondition (pre-arm, read-only).** Before creating any
  state, `drain-old` reads each slot's registration R (positive id required)
  and makes ONE UID-1000 GET of
  `repos/xmit-dev/ultimator/actions/runners?per_page=100` through setpriv with
  pcarrier's ordinary gh config (a liveness-only signal: it authorizes nothing
  and certifies nothing, so the pinned copy is not needed). All four R must be
  listed with the same id and name, `online` and `busy`; R and each controller's
  PID/InvocationID are re-read after the GET. Otherwise it prints
  `HOUND_CI_DRAIN_NOT_READY <reason>` and exits 75 with nothing changed; the
  operator re-invokes later. Soundness: a runner listed busy exists, so its
  DELETE, the record unlink, the legacy 10 s sleep and only then the next record
  write and JIT POST follow, and every current VM is mid-job and ends with that
  job. Immediately before each slot's bind, the registration is re-read: R (with
  the bind less than 10 s later) means no new POST preceded the gate. Absent
  means none preceded *that read*; the legacy sleep may end between the read
  and the bind, and a record write plus POST there shows as a new name after
  the gate. A new name before the bind, a slow bind, or a new name after the
  gate is recorded as `idle_risk` in the manifest, warned
  (`HOUND_CI_DRAIN_IDLE_RISK`) and summarized on the ARMED line
  (`idle_risk=none` or `idle_risk=SLOT:reason,…;…`), never refused. The four
  controller pins (pidfd, namespace fd, argv, invocation, boot) are taken and
  checked before the state directory is created.
- **End path of an idle runner.** A JIT VM that never receives a job runs until
  the legacy 8 h VM lifetime: "VM lifetime exceeded" with no STOP line, the
  worker exits 1, and the waiter HOLDs on the unclosed VM. The same holds for a
  nonzero QEMU exit or a failed QEMU attestation. Reconciling such a slot needs
  separate authorization.
- **History pre-arm caps.** Before arming, `drain-old` counts this boot's root
  VM STARTs per pinned (InvocationID, PID) from the journal (bounded 64 MiB,
  64 KiB rows) and refuses unless each slot has ≤ 1024 and all four ≤ 2048, half
  the waiter's caps (test-pinned to the waiter's constants).
- A tracked NEW controller may atomically replace its registration file
  mid-read; activation reads that slot's record by inode content, untracked
  slots stay strict.
- Lifecycle clocks: journal and boundary use CLOCK_MONOTONIC while
  `/proc/PID/stat` starttime uses boot time. After a suspend, records may be
  filtered as predating the controller, which fails closed (HOLD). Hound
  showed no suspend this boot (BOOTTIME − MONOTONIC = 0 at 19:31 UTC).
- **NEVER run `nixos-rebuild switch` (or any profile activation) on hound
  while the drain or activation is in progress.** It would reload/restart the
  four units outside the reviewed transition, replace their invocations and
  strand the drain in HOLD.

The helper deliberately does **not** automatically unmount/restore/start on a
partial failure. Receipt-persistence failure before a delegated DELETE prevents
that API call; failure after its return leaves outcome UNKNOWN to the final
validator even if the API succeeded. It may change the old controller's exit
status and must never be reported as byte/result-preserving success. Missing or
partial cleanup proof requires explicit reconciliation, not a retry/rollback.
Its root0700 state and0600 fsynced phase manifest record per-slot drop-in
write-intent/written/loaded and gate intent/bound/read-only/probe stages (including partial
mutation). An early pre-hold failure must not be reported as a loaded hold. Transport EOF requires one bounded phase/PID
reconciliation before any continuation, never duplicate arming. A rollback
must be explicit and must not restart/reclaim old slots during handoff. Existing
job/controller failures remain honest; a post-job next-JIT refusal is intentional
drain, not a claim that the job itself failed or passed.

Before arming, host-free tests cover endpoint/method inference, exact cleanup
and other-repository delegation, byte-preserving argv/env behaviour, constant
no-data-leak refusal, four-private-namespace constraints, pinned PID exit/reuse,
namespace FD inheritance, ordering and fail-closed controls. A separate owned
`unshare --mount` smoke test onOctober5 at12:22:18UTC verified actual read-only
bind semantics, POST refusal75, original gh version delegation and unchanged
host gh bytes, **without touching any real CI namespace or registering a job**.
Independent review and normal source PR publication precede critical arming.

## Rollout record — October 6, 2026 (manual completion)

All times UTC. The drain ARMED at 23:34:35 on October 5, all four slots drained
by 23:59:49, and the final certificate was CERTIFIED at 00:06:57.

**Activation HOLD.** With the lease written at 04:02:27 (ack
`?at=1994` of session wdwxreogzwnzez2a), `activate-cache-v2.py` (draft PR #15's
bytes, store copy `c0bdwp66…-activate-cache-v2.py`, SHA `2678613a…`) HOLDed at
04:07:11: "Loaded complete Following/SysFS peer index changed during proof".
The index then bound every `.device`, and the Docker veth `vethabedac0` had
left at 04:07:07 (item 13 above now binds only closure devices and their
peers). By then it had made, each with a completed event: the root namespace
`gcroots/hound-ci/cache-v2-20261005` with the four new roots, and the unit
links of slots 1–3. `activation.json` ends at phase `gc-root-create-complete`
with 8 events.

**Manual completion** (parent decision 04:13; log
`/var/lib/hound-ci-rollout-records-20261006/manual-completion.log`, root 0600):

0. 04:18:02 read-only state check matched the journal: links 1–3 new, link 4
   old (`bp5vhd`); four holds loaded; all four failed/failed, MainPID 0, with
   their original invocations; no hound-ci jobs; old and new roots intact.
1. 04:18:59 slot 4's link, as the tool's `unit-link-replace` does: a symlink
   staged at `/etc/systemd/system.attached/hound-ci-4.service.cache-v2-new`,
   then `os.replace`d over `hound-ci-4.service`, pointing at
   `/nix/store/rkmx5h7g…-unit-hound-ci-4.service/hound-ci-4.service`. Verified
   04:20:26: all four links new, no staging artifact, old units still rooted,
   `NeedDaemonReload=no`.
2. 04:20:32–04:20:34, one step: for N in 1–4
   `unlink /run/systemd/system/hound-ci-N.service.d/90-cache-rollout-drain.conf`
   (content `[Service]\nRestart=no\n`) and `rmdir` of the then-empty `.d`
   directory, then ONE `systemctl daemon-reload` (exit 0).
3. 04:22:10 verified: each FragmentPath is the attached link to the new store
   unit; ExecStart equals the store unit's
   (`/nix/store/grszl3cv…-hound-ci/bin/hound-ci worker --slot N --repo
   xmit-dev/ultimator --guest /nix/store/sawmqyv0…-guest.sh --image
   base-cache-v2.qcow2`); Restart=always; no drop-ins; no jobs.
4. Per slot, in order: the anchor proof (`effect-anchor-proof.py`, SHA
   `8cbe6512…`; `feat/hound-ci/anchor-proof.py` is its reviewed form) printed
   `ANCHOR_PROOF_OK` with structure `f79cbf4e…` on its first attempt, then
   `sudo systemctl --job-mode=fail start -- hound-ci-N.service` (exit 0):

   | Slot | Started | MainPID / starttime | InvocationID | QEMU uid |
   |---|---|---|---|---|
   | 1 | 04:26:35 | 3885930 / 209371640 | `7541d747…` | 978 |
   | 2 | 04:34:16 | 3983407 / 209417818 | `269cf352…` | 977 |
   | 3 | 04:40:01 | 4044990 / 209452306 | `02a496c4…` | 976 |
   | 4 | 04:42:48 | 4073797 / 209469002 | `8ad21d8e…` | 975 |

   Each QEMU: all capabilities 0, NoNewPrivs 1, seccomp mode 2 (20 filters),
   overlay `/var/lib/hound-ci/slot-N/job.qcow2` backed by
   `/var/lib/hound-ci/base-cache-v2.qcow2` (re-hashed 04:32–04:33: `daf2ab77…`,
   inode 1544 unchanged). Slots 1 and 2's first VMs finished before
   inspection (their proof is VM #2); slots 3 and 4's is the first VM. Real
   jobs passed (an E2E run on hound-ci-2, 04:36–04:48). The legacy runner and
   the host profile were untouched; nothing was stopped or killed.

**Lease release.** 05:11:36, per `release_rule` (four new-PID runtime proofs):
`control-window.json` status held → released (SHA `b61118ab…`); the held copy
is kept beside the log.

**The journal does not record steps 1–4.** `activation.json` still ends at
`gc-root-create-complete` with 8 events. Never `--resume` or rerun it: the
live state no longer matches it (slot 4's link, the holds and the loaded units
differ), so a resume HOLDs at its first recheck. The manual-completion log is
the record of those steps.

## Reserved main slot — generation main-slot-20261006 (planned, not executed)

**Why.** GitHub hands queued jobs to JIT runners in no particular order, and
pull-request jobs win, so jobs of pushes to `main` (the only ones that write
the shared caches) wait behind every PR. xmit-dev/ultimator#316 makes push and
manual runs on `main` ask for `[self-hosted, Linux, X64, hound-ci-main]` and
everything else for `[self-hosted, Linux, X64, hound-ci]`. Slot 4 then serves
only `main`; slots 1–3 serve both. #316 merges only after the four new units
run (until then its main jobs would find no runner).

**Code.** `supervisor.py worker --labels LABEL...` (last argument) validates
the labels (non-empty, no duplicates, allowlist `self-hosted Linux X64
hound-ci hound-ci-main`, must hold `self-hosted`, `Linux`, `X64` and at least
one of `hound-ci`/`hound-ci-main`) before any work; without it the JIT request is
byte-identical to before. `services.hound-ci.reservedMainSlots` (0–3, below
`workers`) gives the last N slots the main-only labels; 0 leaves `ExecStart`
without `--labels`. `check.sh` evaluates hound's units with 1, 0 and 2
(`check_slot_labels.py`) and checks the rollout's pinned units against the
build.

**Units.** Only `ExecStart` differs from the loaded cache-v2 units
(`pahfqs…`/`0rvyj9…`/`gq43yb…`/`rkmx5h…`): the new wrapper
`gxbncrk0…-hound-ci` (supervisor `2s5sylgd…`), the same guest `sawmqyv0…` and
image `base-cache-v2.qcow2` (`daf2ab77…`, unchanged), plus `--labels`. New
units: `qaa4grx7…-unit-hound-ci-1.service`, `4wyv5r95…-2`, `lcxqsv1x…-3`,
`77h7vxpx…-4`. The image, firewall and storage units also change in the tree
but are not part of this rollout.

**Tooling.** The October 5–6 helpers are retargeted to this generation:
state `/var/lib/hound-ci/rollout-main-slot-20261006` (never the 20261005
state, whose `activation.json` must not resume), witness from 11:45 UTC
(Pierre's go-ahead), old controllers = the loaded cache-v2 ones (supervisor
`snp22ndc…`, wrapper `grszl3cv…`, guest `sawmqyv0…`, `--image
base-cache-v2.qcow2`), activation's new argv = old argv + `--labels`. New
`capture-rollback.py` (root, write-once) writes the schema-2 ledger
`/var/lib/hound-ci/rollout-main-slot-backup-20261006` that activation's
`Backup` reads, including the exact inventory of the retained
`gcroots/hound-ci/cache-v2-20261005` namespace, which activation now checks
too. It pins hound's current profile (`0yjgryij…`, switched 12:39 UTC
October 6, which added `wireguard-wg-ultimator`): if the profile moves again
before the capture, re-pin it in a reviewed commit. `test_generation.py`
keeps the helpers' constants consistent.

**Resume and aborted steps.** `--resume` after `holds-remove-reload`
rechecks under `resume-validated-released`, a hold-released phase of
`finish-drain.py` (`RELEASE_PHASES`); with the holds still loaded it stays
`resume-validated`. Each phase is refused by the real validator under the
other hold state. A step whose post-intent recheck HOLDs, before its
operation is dispatched, is recorded `aborted_utc`/`aborted_reason` (phase
`<step>-aborted-before-operation`) and a resume repeats it; an aborted
`start-anchor` leaves that slot's record at `start-intent`, which the
validator accepts only while the slot is strictly stopped under its original
invocation. A failure inside the operation is still an open intent (manual
reconcile). The journal's `program_sources` (activation and effect-proof
source and SHA) must match on resume.

**Scheduling, not isolation (P2-4).** The reserved slot is a scheduling
preference: `hound-ci-main` runners are ordinary JIT runners of the same
repository, and any workflow of `xmit-dev/ultimator` that asks for
`hound-ci-main` (including a PR that edits `ci.yml`) can be scheduled on
slot 4. Every job still gets a fresh disposable VM; nothing about secrets,
caches or the host boundary depends on the label.

**Next rollout's drain (P2-3).** Once #316 is live, slot 4 is busy only while
a main run is. `drain-old.py` arms only when all four runners are online and
busy, and the drain certifies each slot from a completed job (Actions
evidence). An idle slot 4 never becomes busy outside main runs, and a VM that
ends at its eight-hour lifetime without a job leaves no job evidence. The
following rollout therefore needs a reviewed idle-slot path (arm and certify
a slot whose last VM ended without a job) before it can drain slot 4; don't
force it by queueing work.

**Plan** (each step needs Pierre's OK; review of #15 and this change first):

1. `capture-rollback.py` → `ROLLBACK_CAPTURED`.
2. `drain-old.py` arm (all four online and busy, else `NOT_READY`, exit 75),
   `wait-drained.py`, `finish-drain.py` capture/certify.
3. A new held lease (effect structure recomputed), then
   `activate-cache-v2.py` (`--resume` after an interruption); by hand, the
   per-slot `anchor-proof.py` then `systemctl --job-mode=fail start`.
4. Merge #316.

Until #316 merges, slot 4 holds an idle `hound-ci-main` runner. Its VM reaches
the eight-hour lifetime, the worker raises, `cleanup_record` deletes the
runner registration, the controller exits 1 and `Restart=always` starts it
again after 10 s: one restart per eight hours, far below
`StartLimitBurst=4` per hour. Only `NRestarts` and the journal show it.

## Rollout record — October 6, 2026 (main slot)

All times UTC; cleared HEAD 1c716ff, Pierre's "Yes: roll out now".
Records: `/var/lib/hound-ci-rollout-records-20261006/main-slot-20261006/`
(root 0600 logs and `SHA256SUMS`).

- 16:13:29 root GC roots `/nix/var/nix/gcroots/hound-ci-rollout-sources-20261006`
  (the 9 cleared helper copies and 4 units, SHAs re-verified).
- 16:17:38 `ROLLBACK_CAPTURED` (ledger `e2f30528…`, profile `0yjgryij…`).
- 16:19:47 drain ARMED, `idle_risk=none`. All four hardware-drained by
  16:59:19. Actions capture 17:09:46, CERTIFIED 17:11:20 (terminal jobs:
  slot 1 cancelled, slot 2 failure, slots 3 and 4 cancelled; manifest
  `05b555eb…`, terminal certificate `ec21248e…`).
- 17:39:48 held lease `8a08e9f5…` (structure `f79cbf4e…`, manager 261.2
  `448f82f2…`). Activation 17:44:18: root namespace, four roots, four links,
  `reload-new-held`, `holds-remove-reload`, then slot 1 started 17:58:03
  (PID 2039286). **HOLD** 17:58:04 at slot 2's pre-intent recheck: the
  validator compared `/proc/PID/cgroup` to exactly `0::<unit cgroup>`, but
  hound mounts a cgroup-v1 `net_cls` hierarchy (Mullvad's
  `mullvad-exclusions`, waydroid's LXC) and every process also lists
  `1:net_cls:/`. Fixed afterwards in `unified_cgroup()` (one `0::` line, v1
  lines only at `/`), for later generations.
- Parent-approved fallback: per slot, `anchor-proof.py` `ANCHOR_PROOF_OK`
  then `systemctl --job-mode=fail start`: slot 2 18:15:39 (PID 2306247),
  slot 3 18:22:16 (2394302), slot 4 18:34:00 (2567651). A first slot-4
  run printed `ANCHOR_PROOF_OK` but was killed with its tool before any
  start; the proof was rerun.
- Runners 18:40: 603/607/601 `[self-hosted, Linux, X64, hound-ci,
  hound-ci-main]`, 605 `[self-hosted, Linux, X64, hound-ci-main]`.
- 21:23:23 lease released (`ba97423a…`). `activation.json` stays at
  `start-anchor-complete` (12 events) and must never be resumed.
