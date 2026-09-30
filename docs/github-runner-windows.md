# A GitHub Actions runner on a Windows VM

crab (macOS, ARM64) and hound (Linux, X64) run xmit-dev's self-hosted runners
from Nix: `feat/github-runner-darwin.nix` and `feat/github-runner.nix`. Windows
isn't Nix-managed, so its runner is set up by hand, once, as described here.
It registers with the same organisation. The Windows job xmit-dev runs is
the Windows leg of `.github/workflows/browser.yml` in xmit-dev/ultimator. It
installs Rust 1.98.1 with `rustup`, then runs `cargo test -p ultimator-browser`
against the Chrome installed in Program Files.

## crab-win, the one in use

xmit-dev's Windows runner today is **crab-win**: the Windows 11 VM (ARM64) in
Parallels on crab. It has 4 vCPUs and 6 GiB, and its 256 GiB disk expands into
crab's own. `browser.yml` sends it the Windows leg with
`runs-on: [self-hosted, Windows, ARM64]`.

- The runner is in `C:\actions-runner`. It runs as the service
  `actions.runner.xmit-dev.crab-win`, as `NETWORK SERVICE`, and jobs run as
  that account too.
- [`github-runner-windows-setup.ps1`](github-runner-windows-setup.ps1)
  installed what the job needs, the ARM64 builds of each:
  - MSVC's ARM64 build tools and the Windows SDK (Build Tools 2022);
  - Git for Windows, whose `bin` goes ahead of `System32` on the system PATH;
  - Chrome's enterprise MSI;
  - rustup for every account, in `C:\rust`, which `NETWORK SERVICE` may
    write, since the workflow installs its own toolchain;
  - long paths and Defender exclusions.

  It then restarts the service, so the runner sees the new PATH. It runs
  elevated and skips what's installed, so run it again for a new Git (change
  its URL and SHA-256).
- Admin goes through crab: `prlctl exec 'Windows 11' <command>` runs a command
  in the guest as SYSTEM. Some things to know about it:
  - give it `</dev/null`, or it swallows the rest of a script on stdin;
  - it collapses `\\` to `\`, so write UNC paths as `C:\Mac\Home\…`;
  - a long command line fails with "Unable to open new session".
  - The guest sees only crab's `~/Desktop`, `~/Documents` and `~/Downloads`,
    as `C:\Mac\Home\…`.

  So the script went in through `~/Downloads`, and ran as a one-off SYSTEM
  scheduled task, which keeps going when the ssh session ends. In bash on
  crab (its login shell is fish), from a checkout of this repository:

  ```bash
  cp docs/github-runner-windows-setup.ps1 ~/Downloads/gha-setup.ps1
  vm='Windows 11'
  prlctl exec "$vm" powershell -NoProfile -Command "New-Item -ItemType Directory -Force C:\ProgramData\gha-setup | Out-Null; Copy-Item C:\Mac\Home\Downloads\gha-setup.ps1 C:\ProgramData\gha-setup\setup.ps1; Set-Content -Encoding ascii C:\ProgramData\gha-setup\run.cmd '@powershell -NoProfile -ExecutionPolicy Bypass -File C:\ProgramData\gha-setup\setup.ps1'" </dev/null
  rm ~/Downloads/gha-setup.ps1
  prlctl exec "$vm" schtasks /create /f /tn gha-setup /ru SYSTEM /rl HIGHEST /sc once /st 23:59 /tr 'C:\ProgramData\gha-setup\run.cmd' </dev/null
  prlctl exec "$vm" schtasks /run /tn gha-setup </dev/null
  prlctl exec "$vm" cmd /c type 'C:\ProgramData\gha-setup\setup.log' </dev/null   # ends with DONE
  prlctl exec "$vm" schtasks /delete /tn gha-setup /f </dev/null   # else it runs again at 23:59
  ```

- The rest of this document describes building another one from scratch, an
  x86_64 VM on hound, as GitHub's `windows-2025` is.

## Another one, on hound

A job gets such a runner with `runs-on: [self-hosted, Windows, X64]`, or with
`runs-on: [self-hosted, <vm-name>]` for that machine alone.

## 1. The VM

Put it on **hound**: it's x86_64 with KVM, so the VM builds and tests the same
x86_64 Windows that GitHub's `windows-2025` runs, where crab-win is ARM64.

| | |
|---|---|
| OS | **Windows Server 2025** (Desktop Experience). It's what `windows-2025` runs, and it needs no TPM. The evaluation edition runs 180 days (`slmgr /dlv`); after that, rearm it or license it. Windows 11 Pro works too, but needs the TPM below. |
| CPU | 8–12 vCPUs, `host-passthrough` |
| Memory | 16–24 GiB. Rust links and Chrome both want room. |
| Disk | 200 GiB VirtIO, qcow2 on `tank`. Cargo's target directories grow fast. |
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

GitHub's image already has these, and `browser.yml` counts on them:

```powershell
winget install --scope machine -e --id Git.Git              # bash, for `shell: bash` steps
winget install --scope machine -e --id Google.Chrome        # the browser the test drives, in Program Files
winget install --scope machine -e --id Microsoft.PowerShell # pwsh, the default `run:` shell
winget install -e --id Microsoft.VisualStudio.2022.BuildTools --override `
  "--quiet --wait --norestart --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
                                                           # MSVC and the Windows SDK, which rustc's msvc target links with
```

Then put Git's `bin` on the **system** PATH, ahead of `C:\Windows\System32`.
If `System32\bash.exe` (WSL) came first, `shell: bash` steps would run in WSL.

```powershell
$p = [Environment]::GetEnvironmentVariable("Path", "Machine")
[Environment]::SetEnvironmentVariable("Path", "C:\Program Files\Git\bin;$p", "Machine")
git config --system core.longpaths true
```

The workflow calls `rustup` itself, so rustup has to be installed for the
account the jobs run as. It installs per user, into `C:\Users\gha\.cargo`, and
puts that on gha's PATH:

```powershell
runas /user:gha "winget install -e --id Rustlang.Rustup"
```

(Add 7-Zip, Node and so on when a job wants them. `actions/setup-node` and the
like download into the runner's tool cache on their own.)

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
gh api -X POST orgs/xmit-dev/actions/runners/registration-token --jq .token
```

(Or: github.com/organizations/xmit-dev/settings/actions/runners → **New
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
  --url https://github.com/xmit-dev `
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
`actions.runner.xmit-dev.<vm-name>` that starts at boot:
`Get-Service actions.runner.*`. Its logs are in `C:\actions-runner\_diag`.

Unlike the Nix runners, which are pinned (`--disableupdate`) and move with
nixpkgs, this one updates itself when GitHub releases a new version. Leave it
that way. GitHub stops giving jobs to runners that fall too far behind.

Check that it's online:

```sh
gh api orgs/xmit-dev/actions/runners --jq '.runners[] | "\(.name) \(.os) \(.status) \([.labels[].name]|join(","))"'
```

A job can then use `runs-on: [self-hosted, Windows, X64]` in place of
`windows-2025`.

## 4. Safety

The runner keeps its state between jobs, and whatever a job leaves behind is
there for the next one. What reaches it:

- The org's Default runner group keeps to **private** repositories, which is
  GitHub's default. So only xmit-dev's private repositories (xmit-dev/ultimator
  among them) can send jobs here, and forks of its public ones can't. Keep it
  that way. If a public repository needs it, give it its own runner group, and
  require approval for all outside contributors' workflow runs.
- It's a VM: nothing of hound's is inside it. Once it's set up, take a
  snapshot (`virsh snapshot-create-as <vm-name> clean`), and revert to it if
  anything looks off.

## Removing it

```powershell
# token from: gh api -X POST orgs/xmit-dev/actions/runners/remove-token --jq .token
cd C:\actions-runner
.\config.cmd remove --token <REMOVE_TOKEN>
```

This also uninstalls the service. Alternatively, delete the runner in the
org's Runners page and then delete the VM.
