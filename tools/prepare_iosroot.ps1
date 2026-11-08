<#
.SYNOPSIS
  Build the 16 GiB HFS+ iOS root image for camellia from an iPhone X IPSW (WSL2).

.DESCRIPTION
  Pipeline (all steps idempotent, in order):
    1. extract the OS / SystemOS / AppOS .dmg files from the IPSW zip
       (Windows side, tools\extract_ipsw_dmg.py, resume-safe);
    2. check the distro has apfs-fuse + hfsprogs + an hfsplus-capable kernel
       (tools\wsl_setup_ubuntu.ps1 and tools\wsl_kernel_hfsplus.ps1);
    3. copy the images and third_party/ios-sweet/tools/mkiosroot.sh into the
       distro filesystem (the WSL disk is on D:);
    4. run mkiosroot.sh there as root (apfs-fuse mounts + rsync + fsck);
    5. copy the raw HFS+ image back to D: and print size + sha256.

  Nothing on the phone is touched here; flashing userdata is a separate,
  explicit step (see 15_N2b... §8) that needs the user's approval.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\prepare_iosroot.ps1
#>
[CmdletBinding()]
param(
    [string]$Ipsw = 'D:\ios-sweet-ipsw\iPhone10,3_16.7.16_20H392_Restore.ipsw',
    [string]$Work = 'D:\ios-sweet-ipsw\iosroot',
    [string]$DmgSource = 'C:\ios-sweet-ipsw\dmg',
    [string]$Distro = 'Ubuntu',
    [string]$Size = '16G',
    [string]$ImageName = 'iosroot-20H392.img',
    [switch]$SkipExtract,
    [switch]$SkipRun,
    [switch]$Force
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# Prefer the modern WSL package (Program Files) over System32's inbox wsl.exe,
# and drop PATH entries pointing at missing drives.
$wslDir = 'C:\Program Files\WSL'
$env:PATH = (($env:PATH -split ';') | Where-Object { $_ -and (Test-Path -LiteralPath $_) }) -join ';'
if (Test-Path (Join-Path $wslDir 'wsl.exe')) { $env:PATH = $wslDir + ';' + $env:PATH }

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Extract = Join-Path $PSScriptRoot 'extract_ipsw_dmg.py'
$MkRoot = Join-Path $RepoRoot 'third_party\ios-sweet\tools\mkiosroot.sh'
$Py = 'C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe'
if (-not (Test-Path $Py)) { $Py = (Get-Command python -ErrorAction SilentlyContinue).Source }
$WslWork = '/root/iosroot'
$ImageWsl = "$WslWork/$ImageName"

function Fail([string]$msg) { Write-Host "[X] $msg" -ForegroundColor Red; exit 1 }
function Info([string]$msg) { Write-Host "[*] $msg" }
function Ok([string]$msg) { Write-Host "[ok] $msg" -ForegroundColor Green }

function To-WslPath([string]$win) {
    $p = [System.IO.Path]::GetFullPath($win)
    $drive = $p.Substring(0, 1).ToLower()
    ($p.Substring(2) -replace '\\', '/') | ForEach-Object { "/mnt/$drive$_" }
}

function Invoke-Wsl([string[]]$Argv, [switch]$AllowFail) {
    & wsl.exe @Argv
    if (-not $AllowFail -and $LASTEXITCODE -ne 0) {
        Fail ("wsl " + ($Argv -join ' ') + " -> exit $LASTEXITCODE")
    }
}

New-Item -ItemType Directory -Force -Path $Work | Out-Null

# --- 1. extract ---------------------------------------------------------------
# The dmg sources are read by apfs-fuse.  Keep them on the NVMe SSD (C:) and
# write the HFS+ image into the WSL disk on the HDD (D:) - the HDD times out on
# mixed read/write (hv_storvsc 60 s timeouts killed the first attempt).
$dmgDir = if (Test-Path (Join-Path $DmgSource '*.dmg')) { $DmgSource } else { Join-Path $Work 'dmg' }
$os = $null; $sysos = $null; $appos = $null
if ($SkipExtract) {
    $os = Get-ChildItem (Join-Path $dmgDir '*.dmg') | Sort-Object Length -Descending | Select-Object -First 1
    $sysos = Get-ChildItem (Join-Path $dmgDir '*.dmg') | Sort-Object Length -Descending | Select-Object -Skip 1 -First 1
    $appos = Get-ChildItem (Join-Path $dmgDir '*.dmg') | Sort-Object Length | Select-Object -First 1
} else {
    if (-not (Test-Path $Ipsw)) { Fail "IPSW not found: $Ipsw" }
    Info "extracting dmg from $Ipsw"
    $lines = & $Py -X utf8 $Extract $Ipsw $dmgDir
    if ($LASTEXITCODE -ne 0) { Fail 'extract_ipsw_dmg.py failed' }
    foreach ($l in $lines) {
        if ($l -match '^OS=(.+)$')      { $os = Get-Item $Matches[1] }
        if ($l -match '^SYSOS=(.+)$')   { $sysos = Get-Item $Matches[1] }
        if ($l -match '^APPOS=(.+)$')   { $appos = Get-Item $Matches[1] }
    }
}
if (-not ($os -and $sysos -and $appos)) { Fail 'could not determine the three dmg files' }
Ok ("OS     {0} ({1:N2} GiB)" -f $os.Name, ($os.Length / 1GB))
Ok ("SYSOS  {0} ({1:N2} GiB)" -f $sysos.Name, ($sysos.Length / 1GB))
Ok ("APPOS  {0} ({1:N2} MiB)" -f $appos.Name, ($appos.Length / 1MB))

# --- 2. distro sanity ---------------------------------------------------------
$distros = ((& wsl.exe -l -q 2>&1 | Out-String) -replace "`0", '') -split "`r?`n" | ForEach-Object { $_.Trim() }
if ($distros -notcontains $Distro) { Fail "WSL distro $Distro missing: run tools\wsl_setup_ubuntu.ps1" }
# Exit codes only: wsl.exe output encoding differs between terminals.
$required = @(
    'command -v apfs-fuse',
    'command -v mkfs.hfsplus',
    'command -v fsck.hfsplus',
    'command -v rsync',
    'grep -q hfsplus /proc/filesystems'
)
$bad = @()
foreach ($cmd in $required) {
    & wsl.exe -d $Distro -u root -e bash -c $cmd 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { $bad += $cmd }
}
if ($bad.Count -gt 0) {
    Fail ("missing in $Distro : " + ($bad -join ' ; ') + ' -> tools\wsl_setup_ubuntu.ps1 / tools\wsl_kernel_hfsplus.ps1')
}
Ok 'distro has apfs-fuse, hfsprogs, rsync and hfsplus support'

# --- 3. stage inputs ----------------------------------------------------------
$hostDmg = ($dmgDir -ne (Join-Path $Work 'dmg'))
Invoke-Wsl @('-d', $Distro, '-u', 'root', '-e', 'bash', '-lc',
    "mkdir -p $WslWork && df -h / | tail -1")
if ($hostDmg) {
    # Read the dmg straight from the Windows SSD through 9p: no 7.4 GB copy into
    # the (HDD-backed) WSL disk, and reads/writes stay on different disks.
    Info "dmgs stay on the host: $dmgDir"
    $osArg = "'" + (To-WslPath $os.FullName) + "'"
    $sysosArg = "'" + (To-WslPath $sysos.FullName) + "'"
    $apposArg = "'" + (To-WslPath $appos.FullName) + "'"
} else {
    Info 'copying images into the distro ...'
    Invoke-Wsl @('-d', $Distro, '-u', 'root', '-e', 'bash', '-lc', "mkdir -p $WslWork/dmg")
    foreach ($f in @($os, $sysos, $appos)) {
        $dst = "$WslWork/dmg/$($f.Name)"
        $existing = (& wsl.exe -d $Distro -u root -e bash -lc "stat -c %s '$dst' 2>/dev/null || echo 0" | Out-String).Trim()
        if ($existing -eq "$($f.Length)") { Ok "$($f.Name) already in distro"; continue }
        Info "cp $($f.Name) -> $dst"
        Invoke-Wsl @('-d', $Distro, '-u', 'root', '-e', 'bash', '-lc',
            "cp -f '$(To-WslPath $f.FullName)' '$dst'")
    }
    $osArg = "'dmg/$($os.Name)'"
    $sysosArg = "'dmg/$($sysos.Name)'"
    $apposArg = "'dmg/$($appos.Name)'"
}
Invoke-Wsl @('-d', $Distro, '-u', 'root', '-e', 'bash', '-lc',
    "cp -f '$(To-WslPath $MkRoot)' $WslWork/mkiosroot.sh && sed -i 's/\r$//' $WslWork/mkiosroot.sh && chmod +x $WslWork/mkiosroot.sh")

if ($SkipRun) { Ok 'inputs staged; -SkipRun given'; exit 0 }

# --- 4. build ----------------------------------------------------------------
if ($Force) {
    Invoke-Wsl @('-d', $Distro, '-u', 'root', '-e', 'bash', '-lc', "rm -f '$ImageWsl'")
}
$exists = (& wsl.exe -d $Distro -u root -e bash -lc "test -e '$ImageWsl' && echo yes || echo no" | Out-String).Trim()
if ($exists -eq 'yes') { Fail "$ImageWsl already exists (mkiosroot refuses to overwrite); use -Force" }

Info "running mkiosroot.sh (this is the long step: apfs-fuse + rsync + fsck) ..."
$logWsl = "$WslWork/mkiosroot.log"
# pipefail so a failed rsync inside mkiosroot is not masked by tee; smaller
# dirty ratios so the HDD keeps up with the writeback of the 16 GiB image.
$inner = "cd $WslWork && sysctl -qw vm.dirty_ratio=10 vm.dirty_background_ratio=5 vm.dirty_expire_centisecs=500; " +
         "bash mkiosroot.sh $osArg $sysosArg $apposArg '$ImageWsl' '$Size' 2>&1 | tee '$logWsl'"
& wsl.exe -d $Distro -u root -e bash -o pipefail -c $inner
if ($LASTEXITCODE -ne 0) { Fail "mkiosroot.sh failed; log: $logWsl" }
$logTail = (& wsl.exe -d $Distro -u root -e bash -c "grep -c 'rsync error' $logWsl || true" | Out-String).Trim()
if ($logTail -and $logTail -ne '0') {
    Fail "rsync reported errors ($logTail lines) - image is incomplete; log: $logWsl"
}
$imgBytes = (& wsl.exe -d $Distro -u root -e bash -c "stat -c %b $ImageWsl" | Out-String).Trim()
Info "image allocated blocks: $imgBytes (of $(16 * 262144))"

# --- 5. fetch back -----------------------------------------------------------
Info 'copying the image back to Windows ...'
Invoke-Wsl @('-d', $Distro, '-u', 'root', '-e', 'bash', '-lc',
    "cp -f '$ImageWsl' '$(To-WslPath $Work)/$ImageName' && sync")
$outFile = Join-Path $Work $ImageName
if (-not (Test-Path $outFile)) { Fail "image not copied back: $outFile" }
$fi = Get-Item $outFile
$hash = (Get-FileHash $fi.FullName -Algorithm SHA256).Hash
Ok ("{0}  {1:N0} B  sha256 {2}" -f $fi.FullName, $fi.Length, $hash)
Write-Host ''
Ok 'Next (needs explicit user approval, wipes Android data in userdata):'
Write-Host "    fastboot -S 512M flash userdata `"$($fi.FullName)`"" -ForegroundColor Yellow
Write-Host '    boot with rd=ufsroot0 (see 15_N2b... §6)' -ForegroundColor Yellow
