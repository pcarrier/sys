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

The companion workflow PR **xmit-dev/ultimator#231 is not merged**. Main routing
still requires Pierre's explicit merge decision. Its graph has ten expanded
core jobs (including the aggregate gate created after needs finish), plus the
connector; queued jobs continue as the four slots free. Full test-suite success
is distinct from infrastructure readiness.

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
