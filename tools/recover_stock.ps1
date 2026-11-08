# recover_stock.ps1 — 一键恢复官方 boot（M1 测试后专用）
#
# 用法：
#   .\recover_stock.ps1                 # 轮询 fastboot，核哈希，刷回，重启，等 MIUI
#   .\recover_stock.ps1 -TimeoutSeconds 180
#
# 前提：手机已经进 fastboot（长按电源 15 秒 → 按住音量下 + 短按电源）。
# 只写 boot_a，不碰其它分区。

[CmdletBinding()]
param(
    [string]$FastbootPath,
    [string]$StockBoot = 'C:\rom_a11\camellia_images_V12.5.9.0.RKSCNXM_20220529.0000.00_11.0_cn\images\boot.img',
    [string]$ExpectedSha256 = '24B22D1EF789BEAEC56B6A8EA9A22FDA998DBF28D27791B18A79B6088F0A485D',
    [int]$TimeoutSeconds = 120
)

$ErrorActionPreference = 'Continue'

function Find-Tool([string]$name, [string]$hint) {
    if ($hint) { if (Test-Path -LiteralPath $hint) { return (Resolve-Path -LiteralPath $hint).Path } else { throw "$name 不存在: $hint" } }
    $c = Get-Command $name -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    $p = Join-Path 'C:\Users\Administrator\Downloads\adb-fastboot' $name
    if (Test-Path -LiteralPath $p) { return $p }
    throw "找不到 $name"
}

$fastboot = Find-Tool 'fastboot.exe' $FastbootPath
$adb = Find-Tool 'adb.exe' $null

if (-not (Test-Path -LiteralPath $StockBoot)) { throw "官方 boot 不存在: $StockBoot" }
$sha = (Get-FileHash -LiteralPath $StockBoot -Algorithm SHA256).Hash
if ($sha -ne $ExpectedSha256) {
    Write-Host ("[X] 官方 boot 哈希不符！`n  期望 {0}`n  实际 {1}" -f $ExpectedSha256, $sha) -ForegroundColor Red
    throw '拒绝刷入哈希不符的镜像'
}
Write-Host ("官方 boot 校验通过: {0}" -f $sha)

Write-Host '等待 fastboot（长按电源 15 秒，再按住音量下 + 短按电源）...'
$sw = [Diagnostics.Stopwatch]::StartNew()
$dev = $null
while ($sw.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
    $dev = (& $fastboot devices 2>&1 | Out-String).Trim()
    if ($dev) { break }
    Start-Sleep -Seconds 2
}
if (-not $dev) { Write-Host '[X] 等不到 fastboot，先按组合键进 bootloader 再跑一次。' -ForegroundColor Red; exit 3 }
Write-Host ("fastboot: {0}" -f ($dev -replace "`r?`n", ' | '))

$out = (& $fastboot flash boot $StockBoot 2>&1 | Out-String)
($out -split "`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ -match 'Sending|Writing|Finished|FAILED' }) -join "`n"
if ($LASTEXITCODE -ne 0) { Write-Host '[X] 刷写失败，手机仍在 fastboot，可重跑。' -ForegroundColor Red; exit 4 }

& $fastboot reboot 2>&1 | Out-Null
Write-Host '已重启，等 MIUI 上线...'
$sw2 = [Diagnostics.Stopwatch]::StartNew()
while ($sw2.Elapsed.TotalSeconds -lt 150) {
    Start-Sleep -Seconds 3
    if (((& $adb devices 2>&1 | Out-String).Trim()) -match 'device$') { break }
}
Start-Sleep -Seconds 8
$boot = ((& $adb shell getprop sys.boot_completed 2>&1 | Out-String).Trim())
$inc = ((& $adb shell getprop ro.build.version.incremental 2>&1 | Out-String).Trim())
Write-Host ("boot_completed={0}  incremental={1}  用时 {2:N0}s" -f $boot, $inc, $sw2.Elapsed.TotalSeconds)
if ($boot -eq '1') { Write-Host '恢复完成 ✅' -ForegroundColor Green; exit 0 }
Write-Host 'adb 上线了但 boot_completed != 1，再等 30 秒看看。' -ForegroundColor Yellow
exit 1
