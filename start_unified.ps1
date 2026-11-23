# 统一版捷安特（Giant-Unified）启动脚本
#
# 用法：
#   .\start_unified.ps1                 # 前台启动，5890 端口
#   .\start_unified.ps1 -Build          # 先重建前端 dist 再启动
#   .\start_unified.ps1 -Background     # 后台启动，日志写 logs\server.out.log
#   .\start_unified.ps1 -Port 5891      # 换端口
#   .\start_unified.ps1 -Pipeline       # 不起 Web，直接跑实体化流水线（等价 run_entity.py）
#
param(
    [int]$Port = 5890,
    [switch]$Build,
    [switch]$Background,
    [switch]$Open,
    [switch]$Pipeline,
    [string[]]$PipelineArgs = @('--dry-run'),
    # 许可证门禁：'0'=关（开发/试用，无 license.dat 时也能用），'1'=开（等同正式发行）
    [ValidateSet('0', '1')][string]$LicenseGate = '0'
)

$ErrorActionPreference = 'Stop'
# 控制台按 UTF-8 输出，避免中文在 cmd 里乱码（配套 start_unified.bat 的 chcp 65001）
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch { }
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$py = 'D:\python\envs\occ\Scripts\python.exe'
if (-not (Test-Path $py)) { throw "找不到主解释器：$py" }

# ---- 环境（与主程序 5880/5050/3000/18765 全错开）----
$env:GTAPP_LICENSE_GATE = $LicenseGate  # '0'=关闭许可证门禁（本机无 license.dat 时的默认）
if ($LicenseGate -eq '0') {
    Write-Host '⚠ 许可证门禁已关闭（GTAPP_LICENSE_GATE=0）：仅本机开发/试用；正式部署请用 -LicenseGate 1' -ForegroundColor Yellow
}
$env:APP_PORT           = "$Port"
$env:ENTITY_DEV_PORT    = '5060'
$env:PIPESOLID_BRIDGE_PORT = '18766'
$env:PYTHONUTF8         = '1'
$env:PYTHONIOENCODING   = 'utf-8'
$env:ENTITY_OUT         = (Join-Path $root 'entity_out')
$env:FREECAD_HOME       = 'D:\Freecad'
$env:GTAPP_OPEN_BROWSER = if ($Open) { '1' } else { '0' }

# 清掉上一轮残留的 PID 文件，避免按它误杀进程
$pidFile = Join-Path $root 'logs\server.pid'
if (Test-Path $pidFile) { Remove-Item $pidFile -Force -ErrorAction SilentlyContinue }

New-Item -ItemType Directory -Force -Path $env:ENTITY_OUT | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $root 'logs') | Out-Null

if ($Pipeline) {
    Push-Location (Join-Path $root 'backend')
    try { & $py 'run_entity.py' @PipelineArgs } finally { Pop-Location }
    exit $LASTEXITCODE
}

# ---- 可选：重建前端 ----
if ($Build) {
    Push-Location (Join-Path $root 'frontend')
    try {
        $env:TEMP = Join-Path $root '.tmp'
        $env:TMP = $env:TEMP
        New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
        Write-Host '[build] npm run build ...' -ForegroundColor Cyan
        & npm run build
        if ($LASTEXITCODE -ne 0) { throw "前端构建失败（退出码 $LASTEXITCODE）" }
    }
    finally { Pop-Location }
}

$url = "http://127.0.0.1:$Port"
Write-Host ''
Write-Host '  统一版捷安特 · Giant-Unified' -ForegroundColor Green
Write-Host "  端口        : $Port" -ForegroundColor Gray
Write-Host "  实体化输出  : $env:ENTITY_OUT" -ForegroundColor Gray
Write-Host "  许可证门禁  : 关闭（GTAPP_LICENSE_GATE=0，仅开发态）" -ForegroundColor Gray
Write-Host "  默认管理员  : admin / Admin@2027" -ForegroundColor Gray
Write-Host "  地址        : $url" -ForegroundColor Yellow
Write-Host ''

Push-Location (Join-Path $root 'backend')
try {
    if ($Background) {
        $out = Join-Path $root 'logs\server.out.log'
        $err = Join-Path $root 'logs\server.err.log'
        $p = Start-Process -FilePath $py -ArgumentList 'run_release.py' `
            -WorkingDirectory (Join-Path $root 'backend') `
            -RedirectStandardOutput $out -RedirectStandardError $err `
            -WindowStyle Hidden -PassThru
        $p.Id | Set-Content (Join-Path $root 'logs\server.pid')
        Write-Host "  已在后台启动，PID=$($p.Id)，日志：$out" -ForegroundColor Green
        if ($Open) { Start-Process $url }
        return
    }
    & $py 'run_release.py'
}
finally { Pop-Location }
