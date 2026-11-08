param([int]$TimeoutSeconds = 600)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$adb  = 'C:\Users\Administrator\Downloads\adb-fastboot\adb.exe'
$fb   = 'C:\Users\Administrator\Downloads\adb-fastboot\fastboot.exe'
$img  = Join-Path $root 'payload\out\magisk_patched.img'
$out  = Join-Path $root 'evidence\pmsg_read_20261025.txt'

if (-not (Test-Path -LiteralPath $img)) { throw "missing $img" }

Write-Host '等待 fastboot（长按电源 15 秒，再按住音量下 + 短按电源）...'
$sw = [Diagnostics.Stopwatch]::StartNew()
$dev = $null
while ($sw.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
    Start-Sleep -Seconds 2
    $dev = (& $fb devices 2>&1 | Out-String).Trim()
    if ($dev) { break }
}
if (-not $dev) { Write-Host '超时：没等到 fastboot'; exit 2 }
Write-Host ("fastboot: {0}" -f ($dev -replace "`r?`n", ' | '))

& $fb flash boot $img 2>&1 | ForEach-Object { Write-Host $_ }
Write-Host "flash exit=$LASTEXITCODE"
& $fb reboot 2>&1 | ForEach-Object { Write-Host $_ }

Write-Host '等 Android 上线...'
$sw2 = [Diagnostics.Stopwatch]::StartNew()
$ok = $false
while ($sw2.Elapsed.TotalSeconds -lt 180) {
    Start-Sleep -Seconds 5
    $bc = (& $adb shell "getprop sys.boot_completed" 2>&1 | Out-String).Trim()
    if ($bc -eq '1') { $ok = $true; break }
}
Write-Host ("boot_completed={0} ({1:N0}s)" -f $ok, $sw2.Elapsed.TotalSeconds)
if (-not $ok) { Write-Host 'Android 没上线'; exit 3 }

Write-Host '== pstore =='
& $adb shell "su -c 'ls -la /sys/fs/pstore/'" 2>&1 | ForEach-Object { Write-Host $_ }
Write-Host '== pmsg =='
$pmsg = & $adb shell "su -c 'cat /sys/fs/pstore/pmsg-ramoops-0'" 2>&1 | Out-String
$pmsg | Out-File -FilePath $out -Encoding utf8
Write-Host $pmsg
Write-Host "saved $out"
