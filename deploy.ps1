$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectDir

Write-Host "========================================"
Write-Host " Giant Tube App Docker deploy - port 5890"
Write-Host "========================================"
Write-Host ""

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "[ERROR] 未检测到 Docker。请先安装并启动 Docker Desktop。" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

docker compose version *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] 未检测到 Docker Compose。请更新 Docker Desktop 到最新版。" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

if (-not $env:APP_PORT) { $env:APP_PORT = "5890" }

Write-Host "[1/1] Building and starting Docker containers..."
docker compose up -d --build
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Docker 启动失败。请运行 docker compose logs 查看日志。" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host ""
Write-Host "========================================"
Write-Host " Done"
Write-Host " Local:  http://127.0.0.1:$env:APP_PORT"
Write-Host "========================================"
Read-Host "Press Enter to exit"
