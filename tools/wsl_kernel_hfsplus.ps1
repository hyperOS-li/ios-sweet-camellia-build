<#
.SYNOPSIS
  Give WSL2 a kernel with CONFIG_HFSPLUS_FS, because Microsoft's kernel has it off.

.DESCRIPTION
  mkiosroot.sh populates a fresh HFS+ image with `mount -t hfsplus -o loop`,
  and WSL2's stock kernel is built with "# CONFIG_HFSPLUS_FS is not set"
  (checked against linux-msft-wsl-6.6.y/Microsoft/config-wsl).  Rather than
  patching the fs from user space, build the WSL2 kernel once with hfsplus
  enabled (everything else stays Microsoft's config) and point .wslconfig at
  the resulting bzImage.

  Build takes ~10-25 min on this machine; the kernel and its build tree live in
  the distro on D:, the bzImage is copied to D:\wsl\kernel-hfsplus.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\wsl_kernel_hfsplus.ps1
#>
[CmdletBinding()]
param(
    [string]$Distro = 'Ubuntu',
    [string]$KernelOut = 'D:\wsl\kernel-hfsplus',
    [string]$Branch = '',
    [string]$BuildDir = '/root/kbuild',
    [switch]$SkipBuild,
    [switch]$SkipInstall
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::Unicode

# Prefer the modern WSL package (Program Files) over System32's inbox wsl.exe,
# and drop PATH entries pointing at missing drives.
$wslDir = 'C:\Program Files\WSL'
$env:PATH = (($env:PATH -split ';') | Where-Object { $_ -and (Test-Path -LiteralPath $_) }) -join ';'
if (Test-Path (Join-Path $wslDir 'wsl.exe')) { $env:PATH = $wslDir + ';' + $env:PATH }

function Fail([string]$msg) { Write-Host "[X] $msg" -ForegroundColor Red; exit 1 }
function Info([string]$msg) { Write-Host "[*] $msg" }
function Ok([string]$msg) { Write-Host "[ok] $msg" -ForegroundColor Green }

function Invoke-Wsl([string[]]$Argv, [switch]$AllowFail) {
    & wsl.exe @Argv
    if (-not $AllowFail -and $LASTEXITCODE -ne 0) {
        Fail ("wsl " + ($Argv -join ' ') + " -> exit $LASTEXITCODE")
    }
}

$uname = (& wsl.exe -d $Distro -u root -e bash -lc 'uname -r' 2>&1 | Out-String).Trim()
if (-not $uname) { Fail "cannot run the $Distro distro; run tools\wsl_setup_ubuntu.ps1 first" }
Info "WSL kernel: $uname"

$ver = ($uname -split '-')[0]                     # 6.6.87.2
$mm = (($ver -split '\.')[0..1] -join '.')        # 6.6
if (-not $Branch) { $Branch = "linux-msft-wsl-$mm.y" }
Info "kernel source branch: $Branch"

if (-not $SkipBuild) {
    Info 'package deps ...'
    Invoke-Wsl @('-d', $Distro, '-u', 'root', '-e', 'bash', '-lc',
        'export DEBIAN_FRONTEND=noninteractive; apt-get update; apt-get install -y git build-essential flex bison libssl-dev libelf-dev bc dwarves')

    Info 'clone + configure + build (this is the long step) ...'
    $script = @"
set -e
export DEBIAN_FRONTEND=noninteractive
rm -rf $BuildDir
git clone --depth 1 -b $Branch https://github.com/microsoft/WSL2-Linux-Kernel $BuildDir
cd $BuildDir
cp Microsoft/config-wsl .config
scripts/config --enable HFSPLUS_FS --enable HFSPLUS_FS_POSIX_ACL --enable NLS_UTF8 --disable DEBUG_INFO_BTF
make olddefconfig
make -j"`$(nproc)" 2>&1 | tee build.log | tail -n 5
"@
    Invoke-Wsl @('-d', $Distro, '-u', 'root', '-e', 'bash', '-lc', $script)

    Info 'copying bzImage out ...'
    Invoke-Wsl @('-d', $Distro, '-u', 'root', '-e', 'bash', '-lc',
        "mkdir -p /mnt/d/wsl && cp $BuildDir/arch/x86/boot/bzImage /mnt/d/wsl/kernel-hfsplus && ls -l /mnt/d/wsl/kernel-hfsplus")
    Ok "kernel image -> $KernelOut"
}

if ($SkipInstall) { Ok 'install skipped'; exit 0 }

# --- install: .wslconfig ------------------------------------------------------
$wslconfig = Join-Path $env:USERPROFILE '.wslconfig'
$kernelWin = $KernelOut -replace '\\', '\\'
if (Test-Path $wslconfig) {
    $backup = $wslconfig + '.bak-' + (Get-Date -Format 'yyyyMMdd_HHmmss')
    Copy-Item $wslconfig $backup
    Info "existing .wslconfig backed up to $backup"
}
$content = @(
    '[wsl2]',
    "kernel=$kernelWin"
)
Set-Content -LiteralPath $wslconfig -Value $content -Encoding ASCII
Ok ".wslconfig -> $wslconfig (kernel=$KernelOut)"

Info 'restarting WSL ...'
& wsl.exe --shutdown
Start-Sleep -Seconds 3

$check = (& wsl.exe -d $Distro -u root -e bash -lc 'uname -r; grep -w hfsplus /proc/filesystems || echo NO_HFSPLUS; losetup -f 2>/dev/null || echo NO_LOOP; ls /dev/fuse 2>/dev/null || echo NO_FUSE' 2>&1 | Out-String).Trim()
Write-Host $check
if ($check -match 'NO_HFSPLUS') {
    Fail 'booted kernel still has no hfsplus: check build.log, or delete  %USERPROFILE%\.wslconfig  and  wsl --shutdown  to go back'
}
Ok 'hfsplus, loop and FUSE are available in WSL2'

Write-Host ''
Ok 'Next: tools\prepare_iosroot.ps1 -Extract  ->  -Run'
Write-Host '    (revert the kernel: remove %USERPROFILE%\.wslconfig and run: wsl --shutdown)' -ForegroundColor DarkGray
