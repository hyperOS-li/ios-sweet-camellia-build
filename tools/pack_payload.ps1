# pack_payload.ps1 — 把 payload.bin 包成可 `fastboot boot` 的 boot.img
#
# 用法：
#   .\pack_payload.ps1 -Mode reboot
#   .\pack_payload.ps1 -Mode fb
#
# 原理：以官方 boot.img 为模板（page_size=2048 / kernel_addr=0x40080000 等全部照抄），
# 只把 kernel 换成我们的载荷、ramdisk 换成最小 cpio、dtb 置 0。

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Mode,
    [string]$ProjectRoot,
    [string]$Template,
    [string]$PayloadBin,
    [string]$Out
)

$ErrorActionPreference = 'Stop'
if (-not $ProjectRoot) { $ProjectRoot = Split-Path -Parent $PSScriptRoot }
if (-not $Template) { $Template = 'C:\rom_a11\camellia_images_V12.5.9.0.RKSCNXM_20220529.0000.00_11.0_cn\images\boot.img' }
if (-not $PayloadBin) { $PayloadBin = Join-Path $ProjectRoot ("payload\out\payload_{0}.bin" -f $Mode) }
if (-not $Out) { $Out = Join-Path $ProjectRoot ("payload\out\payload_{0}.img" -f $Mode) }

$py = 'C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe'
if (-not (Test-Path $py)) {
    $c = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $c) { throw '找不到 python.exe' }
    $py = $c.Source
}
$bootimg = Join-Path $ProjectRoot 'tools\bootimg.py'
$mkram = Join-Path $ProjectRoot 'tools\mkramdisk.py'
$ramdisk = Join-Path $ProjectRoot 'payload\out\ramdisk.cpio.gz'

foreach ($p in @($Template, $PayloadBin, $bootimg, $mkram)) {
    if (-not (Test-Path -LiteralPath $p)) { throw "缺少文件: $p" }
}

$env:PYTHONIOENCODING = 'utf-8'
[Console]::OutputEncoding = [Text.Encoding]::UTF8

Write-Host '== 生成最小 ramdisk =='
& $py $mkram $ramdisk
if ($LASTEXITCODE -ne 0) { throw 'mkramdisk 失败' }

Write-Host '== 打包 boot.img =='
& $py $bootimg pack --template $Template --kernel $PayloadBin --ramdisk $ramdisk -o $Out
if ($LASTEXITCODE -ne 0) { throw 'bootimg pack 失败' }

Write-Host '== 回读校验 =='
& $py $bootimg info $Out
& $py $bootimg verify $Out --kernel $PayloadBin
if ($LASTEXITCODE -ne 0) { throw 'verify 失败' }

$h = (Get-FileHash $Out -Algorithm SHA256).Hash
"`n镜像: {0}" -f $Out
"大小: {0} 字节" -f (Get-Item $Out).Length
"SHA256: {0}" -f $h
"`n上机: fastboot boot `"$Out`""
