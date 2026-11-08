param(
    [string]$Out = (Join-Path $PSScriptRoot '..\payload\out\n2\flash_progress.json'),
    [int64]$UserdataTotal = 17179869184,
    [int64]$BootTotal = 16805888
)
$ErrorActionPreference = 'SilentlyContinue'
$start = Get-Date
$lastRead = 0
$lastAt = Get-Date

while ($true) {
    $p = Get-CimInstance Win32_Process -Filter "Name='fastboot.exe'" |
        Sort-Object CreationDate -Descending | Select-Object -First 1
    $now = Get-Date
    if ($p) {
        $cmd = [string]$p.CommandLine
        $phase = 'fastboot'
        $total = $UserdataTotal
        if ($cmd -match 'userdata') { $phase = 'userdata'; $total = $UserdataTotal }
        elseif ($cmd -match '\sboot\s') { $phase = 'boot'; $total = $BootTotal }
        $read = [int64]$p.ReadTransferCount
        $dt = ($now - $lastAt).TotalSeconds
        $speed = 0.0
        if ($dt -gt 0.2) { $speed = ($read - $lastRead) / $dt; $lastRead = $read; $lastAt = $now }
        $obj = [ordered]@{
            running    = $true
            pid        = [int]$p.ProcessId
            phase      = $phase
            read       = $read
            total      = $total
            speed      = [math]::Round($speed, 0)
            elapsedSec = [math]::Round(((Get-Date) - $start).TotalSeconds, 0)
            updated    = $now.ToString('HH:mm:ss')
        }
    } else {
        $obj = [ordered]@{
            running    = $false
            pid        = $null
            phase      = 'idle'
            read       = 0
            total      = $UserdataTotal
            speed      = 0
            elapsedSec = [math]::Round(((Get-Date) - $start).TotalSeconds, 0)
            updated    = $now.ToString('HH:mm:ss')
        }
    }
    $obj | ConvertTo-Json -Compress | Set-Content -LiteralPath $Out -Encoding UTF8
    Start-Sleep -Milliseconds 800
}
