<#
.SYNOPSIS
  DESTRUCTIVE: write the HFS+ iOS root image into userdata and boot it.

.DESCRIPTION
  This is the one step that wipes Android user data.  It refuses to run unless
  -YesWipeUserdata is given explicitly, and it only ever writes two partitions:

      userdata   the HFS+ iOS root image (16 GiB, sparse-split by fastboot -S)
      boot_a     payload/out/n2/boot_iosroot.img  (rd=ufsroot0 ufslun=2 ufsrw=1 rootrw=1)

  GPT is not touched.  Recovery: reflash the official ROM
  (C:\rom_a11\...\flash_all_except_data_storage.bat) and `fastboot format userdata`,
  or recover_stock.ps1 for boot only.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\flash_iosroot.ps1 -YesWipeUserdata
#>
[CmdletBinding()]
param(
    [string]$Image = 'D:\ios-sweet-ipsw\iosroot\iosroot-20H392.img',
    [string]$BootImage = 'payload\out\n2\boot_iosroot.img',
    [string]$FastbootPath = 'C:\Users\Administrator\Downloads\adb-fastboot\fastboot.exe',
    [string]$EvidenceDir = 'evidence',
    [switch]$YesWipeUserdata,
    [switch]$SkipHash,
    [int]$TimeoutSeconds = 240
)
$ErrorActionPreference = 'Stop'

function Fail([string]$msg) { Write-Host "[X] $msg" -ForegroundColor Red; exit 1 }
function Info([string]$msg) { Write-Host "[*] $msg" }
function Ok([string]$msg) { Write-Host "[ok] $msg" -ForegroundColor Green }

$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

if (-not $YesWipeUserdata) {
    Write-Host @'
This would DESTROY the Android data in userdata:

    fastboot -S 512M flash userdata <iosroot.img>
    fastboot flash boot payload\out\n2\boot_iosroot.img

GPT is not modified; the official ROM can be reflashed later.
Re-run with -YesWipeUserdata once the user has approved this.
'@ -ForegroundColor Yellow
    exit 2
}

if (-not (Test-Path $Image)) { Fail "image not found: $Image" }
if (-not (Test-Path $BootImage)) { Fail "boot image not found: $BootImage" }
if (-not (Test-Path $FastbootPath)) { Fail "fastboot not found: $FastbootPath" }
$fb = (Resolve-Path $FastbootPath).Path

$fi = Get-Item $Image
Info ("userdata image: {0}  {1:N0} B ({2:N2} GiB)" -f $fi.FullName, $fi.Length, ($fi.Length / 1GB))
if (-not $SkipHash) {
    Info 'hashing the image (this takes a while for 16 GiB) ...'
    $h = (Get-FileHash $fi.FullName -Algorithm SHA256).Hash
    Ok "sha256 $h"
} else {
    $h = 'skipped'
}
$bh = (Get-FileHash (Resolve-Path $BootImage).Path -Algorithm SHA256).Hash
Info "boot image: $BootImage sha256 $bh"

Info 'waiting for fastboot (power + volume-down) ...'
$sw = [Diagnostics.Stopwatch]::StartNew()
$dev = $null
while ($sw.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
    $dev = ((& $fb devices 2>&1 | Out-String).Trim())
    if ($dev) { break }
    Start-Sleep -Seconds 2
}
if (-not $dev) { Fail 'no fastboot device' }
Ok ("fastboot: " + ($dev -replace "`r?`n", ' | '))

New-Item -ItemType Directory -Force -Path $EvidenceDir | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$log = Join-Path $EvidenceDir "flash_iosroot_$stamp.txt"
$lines = New-Object System.Collections.Generic.List[string]
function Say([string]$s) { $lines.Add($s); Write-Host $s }

Say ("# flash_iosroot {0}" -f (Get-Date -Format s))
Say ("# userdata image {0} sha256 {1}" -f $fi.FullName, $h)
Say ("# boot image {0} sha256 {1}" -f $BootImage, $bh)

Say '== fastboot -S 512M flash userdata (sparse split) =='
$out = (& $fb -S 512M flash userdata $fi.FullName 2>&1 | Out-String)
Say $out.Trim()
if ($LASTEXITCODE -ne 0) {
    Set-Content -LiteralPath $log -Value ($lines -join "`n") -Encoding UTF8
    Fail "flashing userdata failed; log: $log"
}
Ok 'userdata written'

Say '== fastboot flash boot boot_iosroot.img =='
$out2 = (& $fb flash boot $BootImage 2>&1 | Out-String)
Say $out2.Trim()
if ($LASTEXITCODE -ne 0) {
    Set-Content -LiteralPath $log -Value ($lines -join "`n") -Encoding UTF8
    Fail "flashing boot failed; log: $log"
}
Ok 'boot_a written'

Say '== fastboot reboot =='
(& $fb reboot 2>&1 | Out-String).Trim() | ForEach-Object { Say $_ }
Set-Content -LiteralPath $log -Value ($lines -join "`n") -Encoding UTF8

Write-Host @'
[*] rebooting into the iOS root.  Expected on screen within 60-90 s:

      SWEET UFS: "userdata" on LUN 2: first block 0x67a000, 95440 MiB, read-write
      hfs: mounted iOSRoot on device b(1, 0)
      load_init_program: attempting to load /usr/appleinternal/sbin/launchd.development
      load_init_program: attempting to load /sbin/launchd
      ...launchd output / panic with the real reason...

    If it panics, photograph the screen.  Recovery:
      powershell -ExecutionPolicy Bypass -File tools\recover_stock.ps1   (boot only)
      C:\rom_a11\...\flash_all_except_data_storage.bat                    (full ROM)
'@ -ForegroundColor Cyan
