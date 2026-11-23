<#
  在 Windows 上加载随安装包附带的离线镜像（由 deploy/export_images.sh 导出）。

  若 deploy\images\backend.tar.gz 与 frontend.tar.gz 存在，则 docker load 之。
  docker load -i 可直接读取 gzip 压缩的 tar，无需先解压、也无需经 PowerShell 管道
  传二进制（PowerShell 管道会破坏二进制流）。

  用法：
    & deploy\windows\load_images.ps1
#>
$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path "$PSScriptRoot\..\..").Path
$ImagesDir = Join-Path $RepoRoot "deploy\images"

$backendTar  = Join-Path $ImagesDir "backend.tar.gz"
$frontendTar = Join-Path $ImagesDir "frontend.tar.gz"

if (-not (Test-Path $backendTar) -or -not (Test-Path $frontendTar)) {
  Write-Host "未发现离线镜像文件 (deploy\images\*.tar.gz)。" -ForegroundColor Yellow
  exit 2
}

Write-Host "==> 加载后端镜像: $backendTar" -ForegroundColor Cyan
docker load -i $backendTar
if ($LASTEXITCODE -ne 0) { throw "docker load 后端镜像失败" }

Write-Host "==> 加载前端镜像: $frontendTar" -ForegroundColor Cyan
docker load -i $frontendTar
if ($LASTEXITCODE -ne 0) { throw "docker load 前端镜像失败" }

$manifest = Join-Path $ImagesDir "MANIFEST.txt"
if (Test-Path $manifest) {
  Write-Host "镜像构建信息：" -ForegroundColor Green
  Get-Content $manifest | ForEach-Object { Write-Host "  $_" }
}
Write-Host "==> 离线镜像加载完成。" -ForegroundColor Green
