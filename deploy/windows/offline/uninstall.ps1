<#
  巨管软件 - 停止容器（离线包 / Inno 安装目录根目录）

  用法：powershell -ExecutionPolicy Bypass -File uninstall.ps1
#>
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
Set-Location $Root

Write-Host "==> 停止并移除容器..." -ForegroundColor Cyan
docker compose down 2>$null
if ($LASTEXITCODE -ne 0) {
  Write-Host "   容器可能未运行，已跳过。" -ForegroundColor Yellow
}

Write-Host "已停止。以下目录保留未删除：" -ForegroundColor Green
Write-Host "  - backend\data、backend\database（业务数据）"
Write-Host "  - license\（许可证）"
Write-Host ""
Write-Host "如需删除镜像：docker rmi giant-tube-app-backend:latest giant-tube-app-frontend:latest"
