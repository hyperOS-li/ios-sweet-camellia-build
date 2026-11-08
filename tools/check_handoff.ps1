# check_handoff.ps1 — 交班文档自检（只读，不碰手机）
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File .\tools\check_handoff.ps1
#
# 检查内容：
#   1. 关键文件是否存在
#   2. 关键镜像 SHA256 是否与文档里写的一致
#   3. 四个 .ps1 的语法是否能被 PowerShell 解析
#   4. 文档里是否还有过期表述（"默认不写分区""fastboot boot 可用"等）
#   5. 交班文档是否覆盖关键事实（decapsulate / 0x7DD10000 / ARMd / /psci / recover_stock / M2 / 哈希）
#   6. 跨文档一致性（M1 状态）
#
# 退出码：0 = 全过；1 = 有 FAIL

[CmdletBinding()]
param(
    [string]$ProjectRoot
)

$ErrorActionPreference = 'Stop'
if (-not $ProjectRoot) { $ProjectRoot = Split-Path -Parent $PSScriptRoot }
Set-Location -LiteralPath $ProjectRoot

$results = New-Object System.Collections.Generic.List[object]
function Add-Result([string]$Item, [bool]$Ok, [string]$Detail) {
    $results.Add([pscustomobject]@{ Item = $Item; OK = $Ok; Detail = $Detail })
}

function Read-Utf8([string]$p) {
    return [System.IO.File]::ReadAllText($p, [System.Text.Encoding]::UTF8)
}

# ---------- 1. 文件存在 ----------
$files = @(
    '00_项目章程.md', '01_设备取证.md', '02_M1_上屏计划.md', '03_实验记录.md',
    '04_M1_总结.md', '05_发布稿.md', '06_交班_HANDOFF.md',
    'tools\bootimg.py', 'tools\fdt.py', 'tools\disasm.py', 'tools\recover_stock.ps1',
    'payload\build.ps1', 'payload\main.c', 'payload\start.S', 'payload\linker.ld',
    'payload\out\payload_fb_off.img', 'payload\out\payload_off2.img',
    'evidence\runtime_fdt.bin', 'evidence\expdb.bin'
)
$missing = @()
foreach ($f in $files) { if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot $f))) { $missing += $f } }
Add-Result '关键文件存在' ($missing.Count -eq 0) $(if ($missing.Count) { '缺: ' + ($missing -join ', ') } else { "$($files.Count) 个文件都在" })

# ---------- 2. 哈希 ----------
$hashes = [ordered]@{
    '官方 boot.img' = @{
        Path = 'C:\rom_a11\camellia_images_V12.5.9.0.RKSCNXM_20220529.0000.00_11.0_cn\images\boot.img'
        Sha  = '24B22D1EF789BEAEC56B6A8EA9A22FDA998DBF28D27791B18A79B6088F0A485D'
    }
    'payload_fb_off.img' = @{ Path = 'payload\out\payload_fb_off.img'; Sha = '97397EE79EECBD9F04542AEF6D9B4DE0C828601A5FF3774A5432BFD7D53B60C3' }
    'payload_off2.img'   = @{ Path = 'payload\out\payload_off2.img';   Sha = '1484A9B74BA2ACC81BBB4E71DF69012B37673BFDC86314CCF3C0A82AA771CD42' }
    'runtime_fdt.bin'    = @{ Path = 'evidence\runtime_fdt.bin';       Sha = 'D8FBBEFC355466933AD1103BABB55CF4B711C56F4D582910197E3A89B588492C' }
    'expdb.bin'          = @{ Path = 'evidence\expdb.bin';             Sha = '556B763E8CDAF4BC9B51FA9E8A1A201C9F95D65283A86993B0CA7BAE24C1CDC7' }
}
foreach ($name in $hashes.Keys) {
    $item = $hashes[$name]
    $p = $item.Path
    if (-not [System.IO.Path]::IsPathRooted($p)) { $p = Join-Path $ProjectRoot $p }
    if (-not (Test-Path -LiteralPath $p)) { Add-Result "哈希 $name" $false '文件不存在'; continue }
    $actual = (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash
    Add-Result "哈希 $name" ($actual -eq $item.Sha) $(if ($actual -eq $item.Sha) { 'OK' } else { "期望 $($item.Sha.Substring(0,16))… 实际 $($actual.Substring(0,16))…" })
}

# ---------- 3. 脚本语法 ----------
$scripts = @('tools\recover_stock.ps1', 'payload\build.ps1', 'tools\pack_payload.ps1', 'tools\boot_test.ps1')
foreach ($s in $scripts) {
    $p = Join-Path $ProjectRoot $s
    if (-not (Test-Path -LiteralPath $p)) { Add-Result "语法 $s" $false '文件不存在'; continue }
    $errs = $null
    $null = [System.Management.Automation.PSParser]::Tokenize((Read-Utf8 $p), [ref]$errs)
    Add-Result "语法 $s" ($errs.Count -eq 0) $(if ($errs.Count -eq 0) { 'OK' } else { $errs[0].Message })
}

# ---------- 3b. 脚本 BOM（Windows PowerShell 5.1 读中文必需；没 BOM 会解析崩） ----------
$ps1s = @(Get-ChildItem -Path (Join-Path $ProjectRoot 'tools'), (Join-Path $ProjectRoot 'payload') -Filter '*.ps1' -File -ErrorAction SilentlyContinue)
$noBom = @()
foreach ($f in $ps1s) {
    $bytes = [System.IO.File]::ReadAllBytes($f.FullName)
    $has = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
    if (-not $has) { $noBom += $f.Name }
}
Add-Result '脚本 UTF-8 BOM' ($noBom.Count -eq 0) $(if ($noBom.Count) { '缺 BOM: ' + ($noBom -join ', ') } else { "$($ps1s.Count) 个 .ps1 都有 BOM" })

# ---------- 4. 过期表述 ----------
$docs = @('00_项目章程.md', '01_设备取证.md', '02_M1_上屏计划.md', '03_实验记录.md', '04_M1_总结.md', '06_交班_HANDOFF.md')
$forbidden = @(
    @{ P = '默认不写任何分区';        Why = '边界已改为只刷 boot' },
    @{ P = 'fastboot boot 可用';      Why = '实测不可用' },
    @{ P = 'fastboot boot 能用';      Why = '实测不可用' },
    @{ P = 'PSCI 关机成功';           Why = 'SMC 不返回' },
    @{ P = 'root 可用';               Why = '官方 boot 已覆盖 Magisk' },
    @{ P = 'ro.boot.selinux=permissive'; Why = '仅 13:16 的 Magisk 状态' }
)
$staleHits = @()
foreach ($d in $docs) {
    $p = Join-Path $ProjectRoot $d
    if (-not (Test-Path -LiteralPath $p)) { continue }
    $lines = (Read-Utf8 $p) -split "`r?`n"
    for ($i = 0; $i -lt $lines.Count; $i++) {
        $line = $lines[$i]
        foreach ($f in $forbidden) {
            if ($line.Contains($f.P)) {
                $allowed = $line -match '历史|已过期|⚠️|13:16|13:39|过期表述|扫 00'
                if (-not $allowed) { $staleHits += ("{0}:{1}  [{2}] {3}" -f $d, ($i + 1), $f.Why, $line.Trim()) }
            }
        }
    }
}
Add-Result '无过期表述' ($staleHits.Count -eq 0) $(if ($staleHits.Count) { "命中 $($staleHits.Count) 处：" + ($staleHits -join ' | ') } else { '未命中' })

# ---------- 5. 交班文档覆盖关键事实 ----------
$hand = Read-Utf8 (Join-Path $ProjectRoot '06_交班_HANDOFF.md')
$keywords = @('decapsulate', '0x7DD10000', 'ARMd', '/psci', 'recover_stock', 'M2', '24B22D1E', '97397EE7', '1484A9B7', 'D8FBBEFC')
$missingKw = $keywords | Where-Object { -not $hand.Contains($_) }
Add-Result '06 关键事实覆盖' ($missingKw.Count -eq 0) $(if ($missingKw.Count) { '缺: ' + ($missingKw -join ', ') } else { "$($keywords.Count) 个关键词都在" })
Add-Result '06 长度' ($hand.Length -gt 6000) ("$($hand.Length) 字符")

# ---------- 6. 跨文档一致性 ----------
$charter = Read-Utf8 (Join-Path $ProjectRoot '00_项目章程.md')
$summary = Read-Utf8 (Join-Path $ProjectRoot '04_M1_总结.md')
$ok1 = $charter.Contains('M1 已完成') -or $charter.Contains('M1 达成')
$ok2 = $summary.Contains('M1 达成') -or $summary.Contains('M1 完成')
$ok3 = $hand.Contains('M1')
Add-Result '跨文档：M1 状态一致' ($ok1 -and $ok2 -and $ok3) "00=$ok1 04=$ok2 06=$ok3"

# ---------- 输出 ----------
Write-Host ''
Write-Host '== 交班文档自检 ==' -ForegroundColor Cyan
foreach ($r in $results) {
    $mark = if ($r.OK) { '[PASS]' } else { '[FAIL]' }
    $color = if ($r.OK) { 'Green' } else { 'Red' }
    Write-Host ("{0} {1,-22} {2}" -f $mark, $r.Item, $r.Detail) -ForegroundColor $color
}
$failed = @($results | Where-Object { -not $_.OK })
Write-Host ''
if ($failed.Count -eq 0) { Write-Host '全部通过 ✅' -ForegroundColor Green; exit 0 }
Write-Host ("$($failed.Count) 项 FAIL，按上面 Detail 修文档后重跑。") -ForegroundColor Red
exit 1
