# Run the XNU kernel in QEMU virt, dump the framebuffer, then quit.
param(
    [string]$Loader  = 'payload\out\loader_qemu.bin',
    [string]$Payload = 'payload\out\qemu_payload.bin',
    [int]$Seconds = 15
)
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$out  = Join-Path $root 'payload\out'
$qemu = 'C:\Program Files\qemu\qemu-system-aarch64.exe'
if (-not (Test-Path -LiteralPath $qemu)) { throw "qemu not found: $qemu" }

$loader  = Join-Path $root $Loader
$payload = Join-Path $root $Payload
$log     = Join-Path $out 'qemu.log'
$fb      = Join-Path $env:TEMP 'qemu_fb.bin'
$fbq     = ($fb -replace '\\', '/')
Remove-Item -LiteralPath $log, $fb -ErrorAction SilentlyContinue

$qemuArgs = @(
    '-M', 'virt,acpi=off', '-cpu', 'cortex-a76', '-m', '1024', '-smp', '1',
    '-kernel', $loader, '-initrd', $payload,
    '-display', 'none', '-serial', 'none',
    '-monitor', 'tcp:127.0.0.1:55555,server,nowait',
    '-d', 'int,guest_errors', '-D', $log
)
Write-Host ("qemu: {0} {1}" -f $qemu, ($qemuArgs -join ' '))
$p = Start-Process -FilePath $qemu -ArgumentList $qemuArgs -PassThru -WindowStyle Hidden
Start-Sleep -Seconds $Seconds

try {
    $client = New-Object System.Net.Sockets.TcpClient('127.0.0.1', 55555)
    $stream = $client.GetStream()
    $writer = New-Object System.IO.StreamWriter($stream)
    $writer.AutoFlush = $true
    $writer.WriteLine('info registers')
    Start-Sleep -Milliseconds 500
    $writer.WriteLine("pmemsave 0x50000000 0x1df0000 $fbq")
    Start-Sleep -Seconds 3
    $writer.WriteLine('quit')
    Start-Sleep -Seconds 2
    $client.Close()
} catch {
    Write-Host ("monitor connect failed: {0}" -f $_.Exception.Message)
}

if (-not $p.HasExited) { $p.WaitForExit(10000) | Out-Null }
if (-not $p.HasExited) { $p.Kill() }

if (Test-Path -LiteralPath $fb) {
    $png = Join-Path $out 'qemu_fb.png'
    $py = 'C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe'
    & $py (Join-Path $root 'tools\fb_to_png.py') $fb $png 1080 2400 4352
} else {
    Write-Host 'no fb dump'
}
Write-Host '== qemu.log tail =='
Get-Content -LiteralPath $log -Tail 40 -ErrorAction SilentlyContinue
