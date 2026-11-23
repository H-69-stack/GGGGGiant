<#
  停止并移除巨管软件容器（不会删除数据卷与许可证）。
  用法：powershell -ExecutionPolicy Bypass -File deploy\windows\uninstall.ps1
#>
$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path "$PSScriptRoot\..\..").Path
Set-Location $RepoRoot

Write-Host "==> 停止并移除容器..." -ForegroundColor Cyan
docker compose down

Write-Host "已停止。数据库(backend/data, backend/database)与许可证(license/)保留未删除。" -ForegroundColor Green
Write-Host "如需彻底清理镜像：docker rmi giant-tube-app-backend:latest giant-tube-app-frontend:latest"
