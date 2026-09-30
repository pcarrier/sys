# crab-win: what xmit-dev/ultimator's browser.yml needs on a Windows runner, as GitHub's windows-2025
# image has it. Windows 11 on ARM64 (Parallels on crab); the runner service runs as NETWORK SERVICE.
# Run elevated (SYSTEM or an admin). Idempotent: installed pieces are skipped.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$dir = 'C:\ProgramData\gha-setup'
New-Item -ItemType Directory -Force $dir | Out-Null
Start-Transcript -Append (Join-Path $dir 'setup.log')

function Fetch($url, $name, $sha256) {
  $path = Join-Path $dir $name
  if (-not (Test-Path $path)) { Invoke-WebRequest -UseBasicParsing -OutFile $path $url }
  if ($sha256) {
    $got = (Get-FileHash $path -Algorithm SHA256).Hash.ToLower()
    if ($got -ne $sha256) { Remove-Item $path; throw "${name}: SHA-256 $got, not $sha256" }
  } else {
    $sig = Get-AuthenticodeSignature $path
    if ($sig.Status -ne 'Valid') { Remove-Item $path; throw "${name}: signature $($sig.Status)" }
    Write-Host "${name}: signed by $($sig.SignerCertificate.Subject)"
  }
  $path
}
function Run($file, [string[]]$arguments) {
  # Windows PowerShell's Start-Process joins arguments with spaces, quoting none.
  $quoted = $arguments | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }
  $p = Start-Process -Wait -PassThru -FilePath $file -ArgumentList $quoted
  if ($p.ExitCode -notin 0, 3010) { throw "$file exited $($p.ExitCode)" }
}

# Paths past 260 characters (Cargo's registry, node_modules).
Set-ItemProperty HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem LongPathsEnabled 1

# MSVC and the Windows SDK, which rustc's aarch64-pc-windows-msvc target links with, and clang-cl, which
# aws-lc-sys (rustls's crypto) compiles with on ARM64 Windows. First, so that rustup-init finds them and
# doesn't offer to install Visual Studio Community itself.
$buildTools = 'C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools'
$components = @('--add', 'Microsoft.VisualStudio.Workload.VCTools',
  '--add', 'Microsoft.VisualStudio.Component.VC.Tools.ARM64',
  '--add', 'Microsoft.VisualStudio.Component.VC.Llvm.Clang',
  '--add', 'Microsoft.VisualStudio.Component.VC.Llvm.ClangToolset',
  '--includeRecommended')
if (-not (Test-Path "$buildTools\VC\Tools\MSVC")) {
  $vs = Fetch 'https://aka.ms/vs/17/release/vs_BuildTools.exe' 'vs_BuildTools.exe'
  Run $vs (@('--quiet', '--wait', '--norestart', '--nocache') + $components)
} elseif (-not (Test-Path "$buildTools\VC\Tools\Llvm\ARM64\bin\clang-cl.exe")) {
  $vs = Fetch 'https://aka.ms/vs/17/release/vs_BuildTools.exe' 'vs_BuildTools.exe'
  Run $vs (@('modify', '--installPath', $buildTools, '--quiet', '--wait', '--norestart', '--nocache') + $components)
}

# Git for Windows: git for actions/checkout, bash for `shell: bash` steps.
if (-not (Test-Path 'C:\Program Files\Git\bin\bash.exe')) {
  $git = Fetch 'https://github.com/git-for-windows/git/releases/download/v2.56.0.windows.1/Git-2.56.0-arm64.exe' `
    'Git-2.56.0-arm64.exe' 'c130c04301d06995ef08f1cbd895342844d1fbb3312f5d32cb27cc05b4b394dc'
  Run $git @('/VERYSILENT', '/NORESTART', '/NOCANCEL', '/SP-', '/SUPPRESSMSGBOXES')
}
& 'C:\Program Files\Git\cmd\git.exe' config --system core.longpaths true

# Chrome, in Program Files, where the browser tool looks for it.
if (-not (Test-Path 'C:\Program Files\Google\Chrome\Application\chrome.exe')) {
  $chrome = Fetch 'https://dl.google.com/dl/chrome/install/googlechromestandaloneenterprise_arm64.msi' 'chrome-arm64.msi'
  Run 'msiexec.exe' @('/i', $chrome, '/qn', '/norestart')
}

# rustup for every account, NETWORK SERVICE (the jobs) included: the workflow installs its own
# toolchain with it, so C:\rust is the jobs' to write.
[Environment]::SetEnvironmentVariable('RUSTUP_HOME', 'C:\rust\rustup', 'Machine')
[Environment]::SetEnvironmentVariable('CARGO_HOME', 'C:\rust\cargo', 'Machine')
$env:RUSTUP_HOME = 'C:\rust\rustup'; $env:CARGO_HOME = 'C:\rust\cargo'
if (-not (Test-Path 'C:\rust\cargo\bin\rustup.exe')) {
  # Not Authenticode-signed: checked against the SHA-256 static.rust-lang.org gives beside it.
  $rustup = Fetch 'https://static.rust-lang.org/rustup/archive/1.29.1/aarch64-pc-windows-msvc/rustup-init.exe' `
    'rustup-init-1.29.1.exe' '01aa49cf9574a8bd0ae52005d7de2590e8f27181ded6748236e702c92aef826d'
  Run $rustup @('-y', '--default-toolchain', 'none', '--profile', 'minimal', '--no-modify-path')
}
& icacls.exe C:\rust /grant '*S-1-5-20:(OI)(CI)M' /T /Q | Out-Null # NETWORK SERVICE

# Git's bin ahead of System32, whose bash.exe would be WSL's, and Cargo's bin.
$path = [Environment]::GetEnvironmentVariable('Path', 'Machine')
$parts = $path -split ';' | Where-Object { $_ -and $_ -notin 'C:\Program Files\Git\bin', 'C:\rust\cargo\bin' }
[Environment]::SetEnvironmentVariable('Path', (@('C:\Program Files\Git\bin', 'C:\rust\cargo\bin') + $parts) -join ';', 'Machine')

# Defender scans every file Cargo writes: not the runner's nor Rust's.
Add-MpPreference -ExclusionPath 'C:\actions-runner', 'C:\rust'

# The runner reads the environment at start.
Restart-Service 'actions.runner.xmit-dev.crab-win'
Write-Host 'DONE'
Stop-Transcript
