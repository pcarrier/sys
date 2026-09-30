# A GitHub Actions runner on a Windows VM

crab (macOS, ARM64) and hound (Linux, X64) run yas-run's self-hosted runners
from Nix: `feat/github-runner-darwin.nix` and `feat/github-runner.nix`. Windows
isn't Nix-managed, so its runner is set up by hand, once, as described here.
It serves the same organisation, and the same jobs as
`.github/workflows/_build-windows.yml` in yas-run/yas: it builds the web UI
(wasm-pack, pnpm, bun), then `cargo build --release -p yas-cli`, then the
workspace's tests, and zips `yas.exe` with 7-Zip.

A job gets this runner with `runs-on: [self-hosted, Windows, X64]`, or with
`runs-on: [self-hosted, <vm-name>]` for this machine alone.

## 1. The VM

Put it on **hound**. It's x86_64 with KVM, so the VM builds the
`windows_x86_64` artifacts that yas ships. A Windows VM on crab would be ARM64
and would build something else.

| | |
|---|---|
| OS | **Windows Server 2025** (Desktop Experience). It's what `windows-2025` runs, and it needs no TPM. The evaluation edition runs 180 days (`slmgr /dlv`); after that, rearm it or license it. Windows 11 Pro works too, but needs the TPM below. |
| CPU | 8–12 vCPUs, `host-passthrough` |
| Memory | 16–24 GiB. Linking `yas-cli` and running the tests in parallel wants 16. |
| Disk | 200 GiB VirtIO, qcow2 on `tank`. A cold Rust target dir plus `node_modules` comes to about 30 GiB. |
| Firmware | UEFI (OVMF). Secure Boot is optional. |
| Network | libvirt's NAT (`default` network). The runner only dials out to GitHub. |

hound doesn't run libvirt yet. It would be one more module, e.g.
`feat/libvirt.nix` added to `hosts/hound.nix`:

```nix
{ pkgs, ... }:
{
  virtualisation.libvirtd = {
    enable = true;
    qemu.swtpm.enable = true; # Windows 11 wants a TPM 2.0; Server 2025 doesn't
  };
  programs.virt-manager.enable = true;
  users.users.pcarrier.extraGroups = [ "libvirtd" ];
  environment.systemPackages = [ pkgs.virtio-win ]; # driver ISO, see below
}
```

(OVMF comes with QEMU. The old `qemu.ovmf` options are gone.)

Then, in virt-manager (`qemu:///system`):

1. **New VM** → the Windows Server 2025 ISO → set the memory and CPUs from
   the table → a 200 GiB disk → tick **Customize configuration before
   install**.
2. Overview → Firmware: **UEFI**. CPUs → Configuration: **host-passthrough**.
3. Change the disk's bus to **VirtIO** and the NIC's model to **virtio**.
   Add a second CD-ROM holding the `virtio-win.iso` from
   `$(nix build --print-out-paths nixpkgs#virtio-win)/share/virtio-win/`.
4. For Windows 11, also add a TPM: **Add Hardware → TPM**, emulated, v2.0.
5. Install. When setup finds no disk, click **Load driver** and pick
   `E:\viostor\2k25\amd64` (the virtio-win CD).
6. Once Windows is up, run `virtio-win-guest-tools.exe` from the same CD. It
   installs the network, balloon and QEMU guest agent drivers.
7. Have the VM start with hound:
   `virsh --connect qemu:///system autostart <vm-name>`.

To reach the VM for admin, enable Remote Desktop and install Tailscale in it,
so it shows up on the tailnet like the other machines.

## 2. Windows itself

Run these in an elevated PowerShell.

```powershell
# The account the runner service runs as. Jobs run as this user. Not an admin.
$pw = Read-Host -AsSecureString "Password for gha"
New-LocalUser gha -Password $pw -PasswordNeverExpires -AccountNeverExpires `
  -Description "GitHub Actions runner"

# Paths past 260 characters (node_modules, Cargo's registry)
Set-ItemProperty HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem LongPathsEnabled 1

# A server doesn't sleep, and Windows Update doesn't reboot it mid-job
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
```

Pick a maintenance window under Windows Update → Advanced options → Active
hours. A reboot during a job fails that job, and the service comes back up
on its own afterwards.

### Tools the Windows jobs don't install themselves

The actions in `_build-windows.yml` download Rust (`dtolnay/rust-toolchain`),
wasm-pack, pnpm, Node and Bun into the runner's tool cache on each run. Four
things have to already be on the machine, as they are on GitHub's image:

```powershell
winget install --scope machine -e --id Git.Git              # bash, for `shell: bash` steps
winget install --scope machine -e --id 7zip.7zip            # the "Package zips" step
winget install --scope machine -e --id Microsoft.PowerShell # pwsh, the default `run:` shell
winget install -e --id Microsoft.VisualStudio.2022.BuildTools --override `
  "--quiet --wait --norestart --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
                                                           # MSVC and the Windows SDK, which rustc's msvc target links with
```

Then put Git's `bin` and 7-Zip on the **system** PATH, ahead of
`C:\Windows\System32`. If `System32\bash.exe` (WSL) came first, `shell: bash`
steps would run in WSL.

```powershell
$p = [Environment]::GetEnvironmentVariable("Path", "Machine")
[Environment]::SetEnvironmentVariable("Path",
  "C:\Program Files\Git\bin;C:\Program Files\7-Zip;$p", "Machine")
git config --system core.longpaths true
```

`dtolnay/rust-toolchain` needs `rustup`. If the first job reports it missing,
install it as `gha`: `runas /user:gha "winget install -e --id Rustlang.Rustup"`.
It installs per user, into `C:\Users\gha\.cargo`.

### Faster builds

Defender scans every file Cargo and pnpm write, which makes Rust builds on
Windows much slower. Put the runner's work directory on a **Dev Drive**
(Settings → System → Storage → Disks & volumes → Create dev drive, e.g. 120 GiB
as `D:`). A Dev Drive is ReFS, and Defender scans it in performance mode. If
you skip the Dev Drive, exclude the directories instead:

```powershell
Add-MpPreference -ExclusionPath C:\actions-runner, D:\_work, C:\Users\gha\.cargo, C:\Users\gha\.rustup
```

## 3. The runner

On a machine with the org-admin `gh` login, get a registration token. It is
good for an hour:

```sh
gh api -X POST orgs/yas-run/actions/runners/registration-token --jq .token
```

(Or: github.com/organizations/yas-run/settings/actions/runners → **New
runner** → Windows, where the page shows the token.)

In the VM, in an elevated PowerShell, get the latest release from
<https://github.com/actions/runner/releases> (2.337.0 as of this writing),
check its SHA-256 against the release notes, and register it as a service:

```powershell
$v = "2.337.0"
mkdir C:\actions-runner; cd C:\actions-runner
Invoke-WebRequest -OutFile runner.zip `
  "https://github.com/actions/runner/releases/download/v$v/actions-runner-win-x64-$v.zip"
(Get-FileHash runner.zip -Algorithm SHA256).Hash   # compare with the release notes
Expand-Archive runner.zip -DestinationPath .; rm runner.zip

.\config.cmd --unattended `
  --url https://github.com/yas-run `
  --token <TOKEN> `
  --name <vm-name> `
  --labels <vm-name> `
  --work D:\_work `
  --replace `
  --runasservice `
  --windowslogonaccount .\gha `
  --windowslogonpassword <gha's password>
```

`config.cmd` grants `gha` "Log on as a service". It installs a service named
`actions.runner.yas-run.<vm-name>` that starts at boot:
`Get-Service actions.runner.*`. Its logs are in `C:\actions-runner\_diag`.

Unlike the Nix runners, which are pinned (`--disableupdate`) and move with
nixpkgs, this one updates itself when GitHub releases a new version. Leave it
that way. GitHub stops giving jobs to runners that fall too far behind.

Check that it's online:

```sh
gh api orgs/yas-run/actions/runners --jq '.runners[] | "\(.name) \(.os) \(.status) \([.labels[].name]|join(","))"'
```

A job can then use `runs-on: [self-hosted, Windows, X64]` in place of
`windows-2025`.

## 4. Safety

yas-run/yas is public, and this runner keeps its state between jobs. The org's
Default runner group allows public repositories, so any of the org's
workflows can land here. Two things keep strangers' code off the VM:

- The repository asks for approval before running workflows from **any**
  outside contributor (Settings → Actions → General → "Require approval for
  all outside collaborators"). Read a fork's workflow changes before
  approving its run.
- It's a VM: nothing of hound's is inside it. Once it's set up, take a
  snapshot (`virsh snapshot-create-as <vm-name> clean`), and revert to it if
  anything looks off.

## Removing it

```powershell
# token from: gh api -X POST orgs/yas-run/actions/runners/remove-token --jq .token
cd C:\actions-runner
.\config.cmd remove --token <REMOVE_TOKEN>
```

This also uninstalls the service. Alternatively, delete the runner in the
org's Runners page and then delete the VM.
