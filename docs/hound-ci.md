# Hound's disposable Ubuntu CI pool

`feat/hound-ci.nix` is imported **only by hound**. It does not change the legacy
`github-runner-hound.service`, crab/crab-win, indentbox, or the deployment runner.

## Intended operation

- Four repository-bound slots for `xmit-dev/ultimator`, labels
  `[self-hosted, Linux, X64, hound-ci]`.
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
   `repos/xmit-dev/ultimator/actions/runners/generate-jitconfig`, including gh's
   body/input-inferred POST form. No arguments, stdin, environment, credentials,
   JIT or guest console data are read/logged. Other calls, including exact old
   registration DELETE cleanup, exec the original immutable non-shadowed gh ELF
   with the original wrapper's argv0/telemetry semantics and untouched stdin.
5. Current QEMU and its reader continue without pause. Any request already
   executing before the gate is adopted and drained; it is never cancelled.
   After each accepted job completes/VM exits/registration cleans up, the old
   next-JIT call receives the fixed drain refusal and the old supervisor exits.
   `Restart=no` prevents another old process or old-image job claim.
6. Await completion-driven trusted journal/cgroup exit evidence: original PID
   exit and cgroup empty, positive completed-job/QEMU lifecycle, registration
   recovery state understood. API busy=false or QEMU disappearance alone is
   **not** sufficient. Do not poll; retain the original job's finite lifetime.
7. Only when ALL4 are safely drained, install the four checked cache-v2 unit
   links and their GC roots, preserve old unit/enable/GC-root/image rollback,
   then remove only the four owned runtime restart holds and start replacements.
   New service namespaces have no old route gate; LoadCredential remains host
   only. Verify fresh QEMU cap0/NNP/seccomp, guest offline cache/native preflight,
   private new overlays/JITs, GitHub registration, cache hits and real results.

The helper deliberately does **not** automatically unmount/restore/start on a
partial failure. Its root0700 state and0600 phase manifest record exactly which
identities/gates were armed. Transport EOF requires one bounded phase/PID
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
