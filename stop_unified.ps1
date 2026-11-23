# 统一版捷安特（Giant-Unified）停止脚本
#
# 用法：
#   .\stop_unified.ps1          # 停止 5890 上的服务
#   .\stop_unified.ps1 -Port 5891
#
# 说明：python.exe 可能是 venv 的 shim（真实监听进程是它的子进程），
# 所以这里以「端口占用者」为主、logs\server.pid 为兜底。
param(
    [int]$Port = 5890
)

$ErrorActionPreference = 'SilentlyContinue'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$killed = New-Object System.Collections.Generic.List[int]

$owners = Get-NetTCPConnection -LocalPort $Port -State Listen |
    Select-Object -ExpandProperty OwningProcess -Unique
foreach ($procId in $owners) {
    if ($procId -and $procId -ne 0) {
        Write-Host "  停止监听进程 PID=$procId（端口 $Port）" -ForegroundColor Gray
        Stop-Process -Id $procId -Force
        $killed.Add([int]$procId)
    }
}

$pidFile = Join-Path $root 'logs\server.pid'
if (Test-Path $pidFile) {
    $text = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($text -and $text.Trim() -match '^\d+$') {
        $pidValue = [int]$text.Trim()
        if ($killed -notcontains $pidValue) {
            Write-Host "  停止 PID 文件记录的进程 PID=$pidValue" -ForegroundColor Gray
            Stop-Process -Id $pidValue -Force
            $killed.Add($pidValue)
        }
    }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}

Start-Sleep -Milliseconds 900
$left = Get-NetTCPConnection -LocalPort $Port -State Listen
if ($left) {
    Write-Host "⚠ 端口 $Port 仍被占用：$($left.OwningProcess -join ', ')" -ForegroundColor Yellow
    exit 1
}
Write-Host "已停止：端口 $Port 已释放" -ForegroundColor Green
exit 0
