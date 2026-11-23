#Requires -Version 5.1
<#
.SYNOPSIS
    Giant-Unified S10：实体化流水线端到端脚本（单测 + 冒烟/全量 + 汇总校验）。

.DESCRIPTION
    ① 跑 backend\tests\entity 的标准库单测（discover，slow 组用 ENTITY_SKIP_SLOW=1 跳过）；
    ② 跑流水线（默认冒烟：24TK_1324-31 的 成型_外径形腔 × {c1, pipe}；-Full 跑 6 件×5 类×默认路线 = 30 格）；
    ③ 校验 <Out>\_report\summary.json 存在且 counts.error == 0，打印每格状态/体积/偏差/面数/valid；
    ④ 退出码非 0 表示失败，失败原因写入 logs\e2e_<时间戳>.log。

.EXAMPLE
    .\run_unified_e2e.ps1
    .\run_unified_e2e.ps1 -Out .tmp\s10\e2e_smoke
    .\run_unified_e2e.ps1 -Full -Force
    .\run_unified_e2e.ps1 -Parts 24TK_1324-1,24TK_1324-31 -Models 成型_外径形腔 -Routes c1
#>
[CmdletBinding()]
param(
    [string]$Out = 'entity_out',
    [string[]]$Parts = @(),
    [string[]]$Models = @(),
    [string[]]$Routes = @(),
    [switch]$Full,
    [switch]$Force,
    [switch]$SkipTests
)

$ErrorActionPreference = 'Continue'

# --- 控制台/管道统一 UTF-8，避免中文在 PS 5.1 下乱码 ---------------------------------
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
try { [Console]::InputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$OutputEncoding = [System.Text.Encoding]::UTF8

# --- 定位仓库根 ---------------------------------------------------------------------
if ($PSScriptRoot) {
    $Repo = $PSScriptRoot
} else {
    $Repo = Split-Path -Parent $MyInvocation.MyCommand.Path
}
Set-Location -LiteralPath $Repo

# --- 时间戳与日志 -------------------------------------------------------------------
$Stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$LogDir = Join-Path $Repo 'logs'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$script:LogFile = Join-Path $LogDir ("e2e_$Stamp.log")
$script:Failures = New-Object System.Collections.Generic.List[string]
$script:StartedAt = Get-Date

function Write-E2ELog {
    param(
        [Parameter(Mandatory = $true)][string]$Message,
        [ValidateSet('INFO', 'WARN', 'ERROR', 'OK')][string]$Level = 'INFO'
    )
    $line = '[{0}] [{1}] {2}' -f (Get-Date -Format 'HH:mm:ss'), $Level, $Message
    Write-Host $line
    Add-Content -LiteralPath $script:LogFile -Value $line -Encoding UTF8
}

function Add-Failure {
    param([Parameter(Mandatory = $true)][string]$Message)
    $script:Failures.Add($Message) | Out-Null
    Write-E2ELog "失败：$Message" 'ERROR'
}

function Get-DisplayWidth {
    # CJK 宽字符按 2 列算，保证表头/单元格在 Windows 控制台里对齐（同 report.py 的 text_width）
    param([string]$Text)
    $width = 0
    foreach ($ch in $Text.ToCharArray()) {
        $code = [int]$ch
        if (($code -ge 0x1100 -and $code -le 0x115F) -or
            ($code -ge 0x2E80 -and $code -le 0xA4CF) -or
            ($code -ge 0xAC00 -and $code -le 0xD7A3) -or
            ($code -ge 0xF900 -and $code -le 0xFAFF) -or
            ($code -ge 0xFE30 -and $code -le 0xFE4F) -or
            ($code -ge 0xFF00 -and $code -le 0xFF60) -or
            ($code -ge 0xFFE0 -and $code -le 0xFFE6)) {
            $width += 2
        } else {
            $width += 1
        }
    }
    return $width
}

function Pad-Display {
    param([string]$Text, [int]$Width)
    $pad = $Width - (Get-DisplayWidth $Text)
    if ($pad -lt 0) { $pad = 0 }
    return $Text + (' ' * $pad)
}

function Expand-List {
    param([string[]]$Values)
    $result = New-Object System.Collections.Generic.List[string]
    foreach ($value in $Values) {
        if ($null -eq $value) { continue }
        foreach ($piece in ([string]$value -split ',')) {
            $item = $piece.Trim()
            if ($item) { $result.Add($item) | Out-Null }
        }
    }
    return @($result)
}

# --- Python 解释器 ------------------------------------------------------------------
$PythonExe = $env:ENTITY_PYTHON
if (-not $PythonExe) { $PythonExe = 'D:\python\envs\occ\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $PythonExe)) {
    $found = Get-Command python -ErrorAction SilentlyContinue
    if ($found) {
        $PythonExe = $found.Source
    } else {
        Write-Host '找不到 Python：请设置 ENTITY_PYTHON 或把 python 放进 PATH' -ForegroundColor Red
        exit 2
    }
}

# --- 线程数与编码（机器上还有别的重活在跑，别抢核） -----------------------------------
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:OMP_NUM_THREADS = '2'
$env:MKL_NUM_THREADS = '2'
$env:OPENBLAS_NUM_THREADS = '2'

# --- 规范化输出目录 -----------------------------------------------------------------
if (-not [System.IO.Path]::IsPathRooted($Out)) { $Out = Join-Path $Repo $Out }
$Out = [System.IO.Path]::GetFullPath($Out)
New-Item -ItemType Directory -Force -Path $Out | Out-Null

$RunParts = Expand-List $Parts
$RunModels = Expand-List $Models
$RunRoutes = Expand-List $Routes
if (-not $Full) {
    if ($RunParts.Count -eq 0) { $RunParts = @('24TK_1324-31') }
    if ($RunModels.Count -eq 0) { $RunModels = @('成型_外径形腔') }
    if ($RunRoutes.Count -eq 0) { $RunRoutes = @('c1', 'pipe') }
}

Write-E2ELog '======================================================================'
Write-E2ELog 'Giant-Unified 实体化流水线 · 端到端 (S10)'
Write-E2ELog ("仓库     ：{0}" -f $Repo)
Write-E2ELog ("Python   ：{0}" -f $PythonExe)
Write-E2ELog ("输出根   ：{0}" -f $Out)
Write-E2ELog ("模式     ：{0}" -f $(if ($Full) { '全量（-Full）' } else { '冒烟' }))
Write-E2ELog ("件号     ：{0}" -f $(if ($RunParts.Count) { $RunParts -join ', ' } else { '(全部 6 件)' }))
Write-E2ELog ("模型     ：{0}" -f $(if ($RunModels.Count) { $RunModels -join ', ' } else { '(全部 5 类)' }))
Write-E2ELog ("路线     ：{0}" -f $(if ($RunRoutes.Count) { $RunRoutes -join ', ' } else { '(按模型默认：外径形腔→c1，扩口/压扁/压弯→pipe)' }))
Write-E2ELog ("Force    ：{0} / 跳过单测：{1}" -f [bool]$Force, [bool]$SkipTests)
Write-E2ELog ("日志     ：{0}" -f $script:LogFile)
Write-E2ELog '======================================================================'

# --- 测量缓存播种：自测目录不共享 entity_out\_cache 时硬链接过去（避免冷跑 5–6 min）----
$SeedCache = Join-Path $Repo 'entity_out\_cache'
$TargetCache = Join-Path $Out '_cache'
if ((Test-Path -LiteralPath $SeedCache) -and ($TargetCache -ne $SeedCache)) {
    New-Item -ItemType Directory -Force -Path $TargetCache | Out-Null
    foreach ($part in $RunParts) {
        $source = Join-Path $SeedCache ("{0}_measured.json" -f $part)
        if (-not (Test-Path -LiteralPath $source)) { continue }
        $dest = Join-Path $TargetCache (Split-Path -Leaf $source)
        if (Test-Path -LiteralPath $dest) { continue }
        try {
            New-Item -ItemType HardLink -Path $dest -Target $source -ErrorAction Stop | Out-Null
            Write-E2ELog ("缓存播种（硬链接）：{0}" -f (Split-Path -Leaf $source))
        } catch {
            Copy-Item -LiteralPath $source -Destination $dest
            Write-E2ELog ("缓存播种（复制）：{0}" -f (Split-Path -Leaf $source)) 'WARN'
        }
    }
} elseif ($TargetCache -eq $SeedCache) {
    Write-E2ELog '输出根就是 entity_out，直接复用已有测量缓存'
}

# --- ① 单测 -------------------------------------------------------------------------
if ($SkipTests) {
    Write-E2ELog '① 单测：已用 -SkipTests 跳过' 'WARN'
} else {
    Write-E2ELog '① 单测：python -m unittest discover -s backend\tests\entity -t backend -v'
    $testLog = Join-Path $LogDir ("unit_{0}.log" -f $Stamp)
    $testStart = Get-Date
    & $PythonExe -m unittest discover -s backend\tests\entity -t backend -v 2>&1 | ForEach-Object {
        # PS 5.1 会把 Python 的空 stderr 行包成 RemoteException 记录，[string] 后会变成
        # "System.Management.Automation.RemoteException"；这些空行直接丢掉。
        $line = [string]$_
        if ($line -eq 'System.Management.Automation.RemoteException') { return }
        Write-Host $line
        Add-Content -LiteralPath $testLog -Value $line -Encoding UTF8
    }
    $testExit = $LASTEXITCODE
    $testSeconds = ((Get-Date) - $testStart).TotalSeconds
    if ($testExit -ne 0) {
        Add-Failure ("单测失败（exit={0}，{1:N1} s），详见 {2}" -f $testExit, $testSeconds, $testLog)
    } else {
        Write-E2ELog ("单测通过（{0:N1} s），日志 {1}" -f $testSeconds, $testLog) 'OK'
    }
}

# --- ② 流水线 -----------------------------------------------------------------------
$cliArgs = @('--src', 'gnt管件', '--out', $Out)
if ($RunParts.Count -gt 0) { $cliArgs += @('--parts', ($RunParts -join ',')) }
if ($RunModels.Count -gt 0) { $cliArgs += @('--models', ($RunModels -join ',')) }
if ($RunRoutes.Count -gt 0) { $cliArgs += @('--routes', ($RunRoutes -join ',')) }
if ($Force) { $cliArgs += '--force' }

$Entry = Join-Path $Repo 'backend\run_entity.py'
$pipelineLog = Join-Path $LogDir ("pipeline_{0}.log" -f $Stamp)
Write-E2ELog ("② 流水线：{0} {1} {2}" -f $PythonExe, $Entry, ($cliArgs -join ' '))
$runStart = Get-Date
Set-Content -LiteralPath $pipelineLog -Value ("`$ {0} {1} {2}" -f $PythonExe, $Entry, ($cliArgs -join ' ')) -Encoding UTF8
& $PythonExe $Entry @cliArgs 2>&1 | ForEach-Object {
    $line = [string]$_
    if ($line -eq 'System.Management.Automation.RemoteException') { return }
    Write-Host $line
    Add-Content -LiteralPath $pipelineLog -Value $line -Encoding UTF8
}
$runExit = $LASTEXITCODE
$runSeconds = ((Get-Date) - $runStart).TotalSeconds
Write-E2ELog ("流水线结束：exit={0}，耗时 {1:N1} s，运行日志 {2}" -f $runExit, $runSeconds, $pipelineLog)
if ($runExit -ne 0) {
    Add-Failure ("流水线退出码 {0}（见 {1}；summary.json 若有错误格会列在下面）" -f $runExit, $pipelineLog)
}

# --- ③ 汇总校验 ---------------------------------------------------------------------
$summaryPath = Join-Path $Out '_report\summary.json'
if (-not (Test-Path -LiteralPath $summaryPath)) {
    Add-Failure ("缺少汇总文件：{0}" -f $summaryPath)
} else {
    $summary = $null
    try {
        $summary = Get-Content -LiteralPath $summaryPath -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        Add-Failure ("汇总文件无法解析为 JSON：{0}：{1}" -f $summaryPath, $_.Exception.Message)
    }
    if ($null -ne $summary) {
        Write-E2ELog ("汇总：{0}" -f $summaryPath) 'OK'
        $counts = $summary.counts
        Write-E2ELog ("counts：total={0} ok={1} cached={2} skipped={3} error={4} other={5}" -f `
                $counts.total, $counts.ok, $counts.cached, $counts.skipped, $counts.error, $counts.other)
        if ([int]$counts.error -ne 0) {
            Add-Failure ("counts.error = {0}（应为 0）" -f $counts.error)
        }
        if ($null -ne $summary.conformance) {
            Write-E2ELog ("conformance：faces≠4={0} solids≠1={1} free_edges≠0={2} parting≠2={3} invalid={4}" -f `
                    $summary.conformance.faces_ne_4, $summary.conformance.solids_ne_1, `
                    $summary.conformance.free_edges_ne_0, $summary.conformance.parting_ne_2, `
                    $summary.conformance.invalid)
        }

        $cells = @($summary.cells)
        Write-E2ELog ("③ 每格结果（{0} 格）" -f $cells.Count)

        # summary.json 的 cells 已含 solids，表格按实际字段出列；bbox/方向/耗时见件级 report.json。
        $headers = @('件号', '模型', '路线', '状态', '体积mm3', '偏差pct', '面数', '实体', '自由边', '分型边', 'valid')
        $table = New-Object System.Collections.Generic.List[object]
        $table.Add($headers)
        foreach ($cell in $cells) {
            $volume = '-'
            if ($null -ne $cell.volume_mm3) { $volume = '{0:N3}' -f [double]$cell.volume_mm3 }
            $deviation = '-'
            if ($null -ne $cell.volume_error_pct) { $deviation = '{0:N4}' -f [double]$cell.volume_error_pct }
            $table.Add(@(
                    [string]$cell.part,
                    [string]$cell.key,
                    [string]$cell.route,
                    [string]$cell.status,
                    $volume,
                    $deviation,
                    $(if ($null -ne $cell.faces) { [string]$cell.faces } else { '-' }),
                    $(if ($null -ne $cell.solids) { [string]$cell.solids } else { '-' }),
                    $(if ($null -ne $cell.free_edges) { [string]$cell.free_edges } else { '-' }),
                    $(if ($null -ne $cell.parting_edges) { [string]$cell.parting_edges } else { '-' }),
                    $(if ($null -ne $cell.valid) { [string]$cell.valid } else { '-' })
                ))
        }
        $widths = New-Object int[] $headers.Count
        foreach ($row in $table) {
            for ($i = 0; $i -lt $headers.Count; $i++) {
                $w = Get-DisplayWidth ([string]$row[$i])
                if ($w -gt $widths[$i]) { $widths[$i] = $w }
            }
        }
        foreach ($row in $table) {
            $padded = @()
            for ($i = 0; $i -lt $headers.Count; $i++) {
                $padded += (Pad-Display ([string]$row[$i]) $widths[$i])
            }
            $line = ($padded -join '  ').TrimEnd()
            Write-Host $line
            Add-Content -LiteralPath $script:LogFile -Value $line -Encoding UTF8
        }
        if ($cells.Count -gt 0) {
            Write-E2ELog '注：cells 现含实体数（solids）；更细的 bbox/方向/耗时见各件 report.json 与 STEP 复检' 'INFO'
        }

        # 每格规范值：faces=4 / solids=1 / free_edges=0 / parting_edges=2 / valid=True。
        # cells 里没有 solids，实体数从件级 report.json 的 results[] 取（同一份产出）。
        $partReportCache = @{}
        foreach ($cell in $cells) {
            if ($cell.status -eq 'error') {
                $reason = ''
                foreach ($item in @($summary.errors)) {
                    if ($item.part -eq $cell.part -and $item.key -eq $cell.key -and $item.route -eq $cell.route) {
                        $reason = [string]$item.error
                        break
                    }
                }
                Add-Failure ("格 error：{0}/{1}/{2}：{3}" -f $cell.part, $cell.key, $cell.route, $reason)
            } elseif ($cell.status -eq 'stopped') {
                Add-Failure ("格被中止：{0}/{1}/{2}" -f $cell.part, $cell.key, $cell.route)
            } elseif ($cell.status -eq 'skipped') {
                Write-E2ELog ("格 skipped：{0}/{1}/{2}" -f $cell.part, $cell.key, $cell.route) 'WARN'
            } elseif ($cell.status -eq 'cached') {
                Write-E2ELog ("格 cached（断点续跑，几何已复检）：{0}/{1}/{2}" -f $cell.part, $cell.key, $cell.route) 'WARN'
            }
            if ($cell.status -eq 'ok' -or $cell.status -eq 'cached') {
                $label = "{0}/{1}/{2}" -f $cell.part, $cell.key, $cell.route
                foreach ($field in @('faces', 'free_edges', 'parting_edges')) {
                    $wanted = switch ($field) { 'faces' { 4 } 'free_edges' { 0 } 'parting_edges' { 2 } }
                    $actual = $cell.$field
                    if ($null -eq $actual -or [int]$actual -ne [int]$wanted) {
                        Add-Failure ("格不合规 {0}：{1} = {2}（应为 {3}）" -f $label, $field,
                            $(if ($null -eq $actual) { 'null' } else { $actual }), $wanted)
                    }
                }
                if ([string]$cell.valid -ne 'True') {
                    Add-Failure ("格不合规 {0}：OCC valid = {1}（应为 True）" -f $label,
                        $(if ($null -eq $cell.valid) { 'null' } else { $cell.valid }))
                }
                if (-not $partReportCache.ContainsKey($cell.part)) {
                    $partReportPath = Join-Path $Out (Join-Path $cell.part 'report.json')
                    $partReportData = $null
                    if (Test-Path -LiteralPath $partReportPath) {
                        try {
                            $partReportData = Get-Content -LiteralPath $partReportPath -Raw -Encoding UTF8 |
                                ConvertFrom-Json
                        } catch {
                            $partReportData = $null
                        }
                    }
                    $partReportCache[$cell.part] = $partReportData
                }
                $solids = $null
                $foundSolids = $false
                foreach ($item in @($partReportCache[$cell.part].results)) {
                    if ($null -ne $item -and $item.key -eq $cell.key -and $item.route -eq $cell.route) {
                        $solids = $item.solids
                        $foundSolids = $true
                        break
                    }
                }
                if (-not $foundSolids -or $null -eq $solids) {
                    Add-Failure ("格不合规 {0}：件级 report.json 里没有该格的 solids 记录" -f $label)
                } elseif ([int]$solids -ne 1) {
                    Add-Failure ("格不合规 {0}：solids = {1}（应为 1）" -f $label, $solids)
                }
            }
        }
        if ((-not $Full) -and ($cells.Count -lt 2)) {
            Add-Failure ("冒烟应产生 2 格（c1/pipe），实际 {0} 格" -f $cells.Count)
        }
        if ($Full -and ($cells.Count -lt 30)) {
            Add-Failure ("全量应产生 30 格（6 件 × 5 类 × 默认路线），实际 {0} 格" -f $cells.Count)
        }
        if ($summary.over_threshold -and @($summary.over_threshold).Count -gt 0) {
            Write-E2ELog ("体积偏差超过 1% 的格：{0}" -f (@($summary.over_threshold).Count)) 'WARN'
        }
    }
}

# --- ④ 收尾 -------------------------------------------------------------------------
$totalSeconds = ((Get-Date) - $script:StartedAt).TotalSeconds
Write-E2ELog '======================================================================'
if ($script:Failures.Count -gt 0) {
    Write-E2ELog ("结果：失败（{0} 项），总耗时 {1:N1} s" -f $script:Failures.Count, $totalSeconds) 'ERROR'
    foreach ($reason in $script:Failures) { Write-E2ELog ("  - {0}" -f $reason) 'ERROR' }
    Write-E2ELog ("失败日志：{0}" -f $script:LogFile) 'ERROR'
    exit 1
}
Write-E2ELog ("结果：全部通过，总耗时 {0:N1} s" -f $totalSeconds) 'OK'
Write-E2ELog ("日志：{0}" -f $script:LogFile)
exit 0
