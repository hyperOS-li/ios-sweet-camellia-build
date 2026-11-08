<#
.SYNOPSIS
  Install Ubuntu under WSL2, move its disk to D: and install the APFS/HFS tools.

.DESCRIPTION
  The camellia iOS-root pipeline needs a Linux userspace (apfs-fuse + hfsprogs +
  rsync).  This machine has C: too small for the WSL disk plus the 7 GB of IPSW
  images plus a 16 GB HFS+ image, so the distro is exported/imported to D:.

  Prerequisites: WSL + VirtualMachinePlatform features enabled and the machine
  rebooted once (tools/wsl_features.ps1 did the enable part on 2026-10-30).

  Ubuntu's apfs-fuse package is used when available; otherwise apfs-fuse is
  built from source in /opt/apfs-fuse (needs git/cmake/fuse3).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\wsl_setup_ubuntu.ps1
#>
[CmdletBinding()]
param(
    [string]$Distro = 'Ubuntu',
    [string]$DistroLocation = 'D:\wsl\Ubuntu',
    [string]$ExportTarball = 'D:\wsl\ubuntu-base.tar',
    [switch]$SkipDeps
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::Unicode

# This box has the modern WSL package under Program Files but System32's inbox
# wsl.exe wins the PATH lookup (no --version/--location).  Prefer the modern
# one, and drop PATH entries pointing at missing drives (the "E:\UnLockCatch"
# leftover makes every wsl call print a translate warning).
$wslDir = 'C:\Program Files\WSL'
$env:PATH = (($env:PATH -split ';') | Where-Object { $_ -and (Test-Path -LiteralPath $_) }) -join ';'
if (Test-Path (Join-Path $wslDir 'wsl.exe')) { $env:PATH = $wslDir + ';' + $env:PATH }

function Fail([string]$msg) { Write-Host "[X] $msg" -ForegroundColor Red; exit 1 }
function Info([string]$msg) { Write-Host "[*] $msg" }
function Ok([string]$msg) { Write-Host "[ok] $msg" -ForegroundColor Green }

function Get-WslDistros {
    $raw = (& wsl.exe -l -q 2>&1 | Out-String) -replace "`0", ''
    @($raw -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}

function Invoke-Wsl([string[]]$Argv) {
    & wsl.exe @Argv
    if ($LASTEXITCODE -ne 0) { Fail ("wsl " + ($Argv -join ' ') + " -> exit $LASTEXITCODE") }
}

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { Fail 'run this from an elevated shell (WSL feature checks need it)' }

# --- 1. features ---------------------------------------------------------------
$wslState = (Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux).State
$vmpState = (Get-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform).State
Info "features: WSL=$wslState VMP=$vmpState"
if ($wslState -ne 'Enabled' -or $vmpState -ne 'Enabled') {
    Fail 'WSL features are not active yet: reboot Windows, then run this script again'
}

# --- 2. distro ---------------------------------------------------------------
$distros = Get-WslDistros
if ($distros -contains $Distro) {
    Ok "distro $Distro already registered"
} else {
    # Store (wsl --install -d) and GitHub release downloads are blocked on this
    # network, but Canonical's cloud-images host is reachable: import their
    # Ubuntu 24.04 WSL rootfs tarball instead and keep the disk on D:.
    $rootfs = 'D:\wsl\ubuntu-noble-wsl-amd64.rootfs.tar.gz'
    $rootfsUrl = 'https://cloud-images.ubuntu.com/wsl/releases/24.04/current/ubuntu-noble-wsl-amd64-wsl.rootfs.tar.gz'
    if (-not (Test-Path $rootfs)) {
        Info "downloading Ubuntu 24.04 WSL rootfs (340 MiB) ..."
        & curl.exe -L --fail --ssl-no-revoke --retry 5 --retry-delay 2 -C - -o $rootfs $rootfsUrl
        if ($LASTEXITCODE -ne 0) { Fail 'Ubuntu WSL rootfs download failed' }
        Ok ("rootfs sha256 " + (Get-FileHash $rootfs -Algorithm SHA256).Hash)
    }
    New-Item -ItemType Directory -Force -Path $DistroLocation | Out-Null
    Info "importing $Distro into $DistroLocation ..."
    Invoke-Wsl @('--import', $Distro, $DistroLocation, $rootfs, '--version', '2')
    $distros = Get-WslDistros
    if ($distros -notcontains $Distro) { Fail "distro $Distro still missing after import" }
    Ok "distro $Distro imported to $DistroLocation"
}

# --- 3. move the disk to D: ---------------------------------------------------
$vhdx = Join-Path $DistroLocation 'ext4.vhdx'
if (Test-Path $vhdx) {
    Ok "distro disk already at $DistroLocation"
} else {
    New-Item -ItemType Directory -Force -Path (Split-Path $DistroLocation) | Out-Null
    Info "exporting $Distro to $ExportTarball ..."
    Invoke-Wsl @('--export', $Distro, $ExportTarball)
    Info "unregistering $Distro ..."
    Invoke-Wsl @('--unregister', $Distro)
    New-Item -ItemType Directory -Force -Path $DistroLocation | Out-Null
    Info "importing to $DistroLocation ..."
    Invoke-Wsl @('--import', $Distro, $DistroLocation, $ExportTarball, '--version', '2')
    Remove-Item $ExportTarball -Force -ErrorAction SilentlyContinue
    Ok "distro disk moved to $DistroLocation"
}

# --- 4. sanity + tools --------------------------------------------------------
Invoke-Wsl @('-d', $Distro, '-u', 'root', '-e', 'bash', '-lc', 'uname -a; df -h / | tail -1')
if ($SkipDeps) { Ok 'deps skipped'; exit 0 }

Info 'installing packages (apt) ...'
& wsl.exe -d $Distro -u root -e bash -lc @'
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y hfsprogs rsync pkg-config git cmake build-essential
apt-get install -y apfs-fuse || echo "APFS_FUSE_PKG_MISSING"
'@
if ($LASTEXITCODE -ne 0) { Fail 'apt-get install failed (network?)' }

# apfs-fuse from apt?  otherwise build it (Ubuntu before 24.04 has no package).
$hasApfs = (& wsl.exe -d $Distro -u root -e bash -lc 'command -v apfs-fuse || true' | Out-String).Trim()
if (-not $hasApfs) {
    Info 'apfs-fuse not packaged; building from source (/opt/apfs-fuse) ...'
    & wsl.exe -d $Distro -u root -e bash -lc @'
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get install -y libfuse3-dev libbz2-dev zlib1g-dev libicu-dev libssl-dev
rm -rf /opt/apfs-fuse
git clone --depth 1 --recursive https://github.com/sgan81/apfs-fuse.git /opt/apfs-fuse
cd /opt/apfs-fuse
git submodule update --init --recursive
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j"$(nproc)"
cmake --install build
command -v apfs-fuse
'@
    if ($LASTEXITCODE -ne 0) { Fail 'apfs-fuse build failed' }
    $hasApfs = (& wsl.exe -d $Distro -u root -e bash -lc 'command -v apfs-fuse || true' | Out-String).Trim()
    if (-not $hasApfs) { Fail 'apfs-fuse still missing' }
}
Ok "apfs-fuse: $hasApfs"

$tools = (& wsl.exe -d $Distro -u root -e bash -lc 'command -v mkfs.hfsplus fsck.hfsplus rsync apfs-fuse | tr "\n" " "' | Out-String).Trim()
Ok "tools: $tools"

Write-Host ''
Ok 'WSL base is ready. Next: tools\wsl_kernel_hfsplus.ps1 (WSL2 kernel has no hfsplus), then tools\prepare_iosroot.ps1'
