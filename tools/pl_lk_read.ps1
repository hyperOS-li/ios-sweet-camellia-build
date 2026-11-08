param([int]$TimeoutSeconds = 600)

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$adb  = 'C:\Users\Administrator\Downloads\adb-fastboot\adb.exe'
$fb   = 'C:\Users\Administrator\Downloads\adb-fastboot\fastboot.exe'
$img  = Join-Path $env:TEMP 'magisk_patched.img'
$out  = Join-Path $root 'evidence\pl_lk_20261025.txt'

if (-not (Test-Path -LiteralPath $img)) { throw "missing $img" }

Write-Host 'Waiting for fastboot (hold power 15s, then vol-down + power)...'
$sw = [Diagnostics.Stopwatch]::StartNew()
$dev = $null
while ($sw.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
    Start-Sleep -Seconds 2
    $dev = (cmd /c "`"$fb`" devices 2>&1" | Out-String).Trim()
    if ($dev -match 'fastboot') { break }
}
if ($dev -notmatch 'fastboot') { Write-Host 'timeout: no fastboot'; exit 2 }
Write-Host ("fastboot: {0}" -f ($dev -replace "`r?`n", ' | '))

$bat = Join-Path $env:TEMP 'pl_lk_flash.bat'
$flog = Join-Path $env:TEMP 'pl_lk_flash.log'
$ferr = Join-Path $env:TEMP 'pl_lk_flash.err'
Remove-Item -LiteralPath $flog, $ferr -ErrorAction SilentlyContinue
@"
@echo off
"$fb" flash boot_a "$img" 2>&1
exit /b %ERRORLEVEL%
"@ | Set-Content -LiteralPath $bat -Encoding ASCII

$p = Start-Process -FilePath 'cmd.exe' -ArgumentList "/c `"$bat`"" -NoNewWindow -RedirectStandardOutput $flog -RedirectStandardError $ferr -PassThru
$done = $p.WaitForExit(120000)
if (-not $done) { $p.Kill(); Write-Host 'flash timeout'; exit 3 }
Get-Content -LiteralPath $flog -ErrorAction SilentlyContinue
$all = (Get-Content -LiteralPath $flog -Raw -ErrorAction SilentlyContinue) + (Get-Content -LiteralPath $ferr -Raw -ErrorAction SilentlyContinue)
if ($all -notmatch "Writing 'boot_a'.*OKAY") { Write-Host 'write not confirmed'; exit 4 }

Write-Host 'Rebooting...'
cmd /c "`"$fb`" reboot 2>&1" | Out-Null
$sw2 = [Diagnostics.Stopwatch]::StartNew()
$ok = $false
while ($sw2.Elapsed.TotalSeconds -lt 180) {
    Start-Sleep -Seconds 5
    $bc = (& $adb shell "getprop sys.boot_completed" 2>&1 | Out-String).Trim()
    if ($bc -eq '1') { $ok = $true; break }
}
Write-Host ("boot_completed={0} ({1:N0}s)" -f $ok, $sw2.Elapsed.TotalSeconds)
if (-not $ok) { Write-Host 'android not up'; exit 5 }

Write-Host '== su =='
& $adb shell "su -c id" 2>&1
Write-Host '== reading /proc/pl_lk =='
& $adb shell "su -c 'cat /proc/pl_lk'" 2>&1 | Out-File -FilePath $out -Encoding utf8
Get-Content -LiteralPath $out -TotalCount 40
Write-Host '== SHIM lines =='
Select-String -LiteralPath $out -Pattern 'SHIM' -ErrorAction SilentlyContinue | ForEach-Object { $_.Line }
Write-Host "saved $out"
