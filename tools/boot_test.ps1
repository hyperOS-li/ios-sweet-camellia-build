# boot_test.ps1 — Gate 1 / M1 上机测试：只读 getvar，或 `fastboot boot` 一个镜像
#
# 用法：
#   .\boot_test.ps1 -Action getvar                          # 只读，看 LK 支持什么
#   .\boot_test.ps1 -Action boot -Image <path>              # 从内存启动（不写盘）
#   .\boot_test.ps1 -Action stock                           # 用官方 boot.img 做对照
#
# 输出：控制台 + evidence\getvar_<时间>.txt / evidence\boot_<时间>.txt

[CmdletBinding()]
param(
    [ValidateSet('getvar', 'boot', 'stock')]
    [string]$Action = 'getvar',
    [string]$Image,
    [int]$ObserveSeconds = 150,
    [string]$ProjectRoot,
    [string]$FastbootPath
)

$ErrorActionPreference = 'Continue'
if (-not $ProjectRoot) { $ProjectRoot = Split-Path -Parent $PSScriptRoot }
$evidence = Join-Path $ProjectRoot 'evidence'
New-Item -ItemType Directory -Force -Path $evidence | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'

function Find-Tool([string]$name, [string]$hint) {
    if ($hint) { if (Test-Path -LiteralPath $hint) { return (Resolve-Path -LiteralPath $hint).Path } else { throw "$name 不存在: $hint" } }
    $c = Get-Command $name -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    $p = Join-Path 'C:\Users\Administrator\Downloads\adb-fastboot' $name
    if (Test-Path -LiteralPath $p) { return $p }
    throw "找不到 $name（可用参数指定）"
}

$fastboot = Find-Tool 'fastboot.exe' $FastbootPath
$adb = Find-Tool 'adb.exe' $null
Write-Host ("fastboot: {0}" -f $fastboot)

function Show-Devices {
    $fb = (& $fastboot devices 2>&1 | Out-String).Trim()
    $ad = (& $adb devices 2>&1 | Out-String).Trim()
    "fastboot: " + $(if ($fb) { $fb -replace "`r?`n", ' | ' } else { '(none)' })
    "adb:      " + $(if ($ad) { $ad -replace "`r?`n", ' | ' } else { '(none)' })
}

Write-Host '== 当前设备 =='
Show-Devices | Write-Host

$fbNow = (& $fastboot devices 2>&1 | Out-String).Trim()
if (-not $fbNow) {
    Write-Host "`n[!] fastboot 里没有设备（不会调用 fastboot，避免卡在 < waiting for any device >）。" -ForegroundColor Yellow
    Write-Host '    先让手机进 bootloader：adb reboot bootloader，或关机后 音量下+电源，等 15 秒。'
    exit 3
}

if ($Action -eq 'getvar') {
    $out = Join-Path $evidence ("getvar_{0}.txt" -f $stamp)
    Write-Host "`n== fastboot getvar all（只读）=="
    $text = (& $fastboot getvar all 2>&1 | Out-String)
    Set-Content -LiteralPath $out -Value $text -Encoding UTF8
    $text | Write-Host
    Write-Host "`n已保存: $out"
    exit 0
}

# boot / stock
if ($Action -eq 'stock') {
    $Image = 'C:\rom_a11\camellia_images_V12.5.9.0.RKSCNXM_20220529.0000.00_11.0_cn\images\boot.img'
}
if (-not $Image) { throw '需要 -Image' }
if (-not (Test-Path -LiteralPath $Image)) { throw "镜像不存在: $Image" }

$img = Get-Item -LiteralPath $Image
$hash = (Get-FileHash -LiteralPath $Image -Algorithm SHA256).Hash
Write-Host "`n== 镜像 =="
Write-Host ("  {0}" -f $img.FullName)
Write-Host ("  {0} 字节  sha256={1}" -f $img.Length, $hash)

$log = Join-Path $evidence ("boot_{0}.txt" -f $stamp)
$lines = New-Object System.Collections.Generic.List[string]
function Log([string]$s) { $lines.Add($s); Write-Host $s }

Log ("# boot test {0}  image={1}" -f (Get-Date -Format s), $img.FullName)
Log ("# sha256={0}" -f $hash)

$fbOut = (& $fastboot devices 2>&1 | Out-String).Trim()
if (-not $fbOut) {
    Log '[X] fastboot 里没有设备：先 `adb reboot bootloader`，等 15 秒。'
    Set-Content -LiteralPath $log -Value ($lines -join "`n") -Encoding UTF8
    exit 3
}

Log '== 发送并启动（不写盘）=='
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$bootOut = (& $fastboot boot $img.FullName 2>&1 | Out-String)
Log $bootOut.Trim()
Log ("[t={0:N1}s] fastboot boot 返回 exit={1}" -f $sw.Elapsed.TotalSeconds, $LASTEXITCODE)

if ($LASTEXITCODE -ne 0) {
    Log '[!] fastboot boot 失败：把上面原文记下来（unknown command = LK 不支持内存启动）'
    Set-Content -LiteralPath $log -Value ($lines -join "`n") -Encoding UTF8
    Write-Host "`n日志: $log"
    exit 4
}

Log '== 观察启动过程（每 2 秒采样）=='
$seenAdb = $false
$seenFastboot = $false
while ($sw.Elapsed.TotalSeconds -lt $ObserveSeconds) {
    Start-Sleep -Seconds 2
    $ad = ((& $adb devices 2>&1) -match 'device\s*$') -ne $null
    $fb = ((& $fastboot devices 2>&1) -match 'fastboot\s*$') -ne $null
    if ($fb -and -not $seenFastboot) { $seenFastboot = $true; Log ("[t={0:N1}s] 出现在 fastboot" -f $sw.Elapsed.TotalSeconds) }
    if ($ad -and -not $seenAdb) { $seenAdb = $true; Log ("[t={0:N1}s] adb 上线（系统起来了）" -f $sw.Elapsed.TotalSeconds) }
    if ($seenAdb) { break }
}
if (-not $seenAdb) {
    Log ("[t={0:N1}s] {1} 秒内没等到 adb 上线" -f $sw.Elapsed.TotalSeconds, $ObserveSeconds)
}

Log '== 结束状态 =='
Log (Show-Devices | Out-String).Trim()
Set-Content -LiteralPath $log -Value ($lines -join "`n") -Encoding UTF8
Write-Host "`n日志: $log"
