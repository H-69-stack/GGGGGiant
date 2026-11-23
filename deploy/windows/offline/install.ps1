<#
  巨管软件 - Windows 离线一键部署（完整包内置镜像，无需联网、无需构建）

  适用：本脚本位于"完整离线包"根目录，同级应有：
    images\backend.tar.gz, images\frontend.tar.gz, docker-compose.yml,
    backend\data, backend\database, license\

  作用：
    1) 检查 Docker 是否就绪
    2) 准备 .env（端口、密钥）
    3) docker load 内置镜像（与开发环境完全一致）
    4) docker compose up -d 启动服务
    5) 打印访问地址

  用法（管理员 PowerShell，在离线包根目录）：
    powershell -ExecutionPolicy Bypass -File install.ps1
  或直接双击「部署.bat」。

  可选参数：
    -Port 5890     对外访问端口（默认 5890）
#>
param(
  [int]$Port = 5890
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
Set-Location $Root

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }

# 1) 检查 Docker（命令是否存在）
Write-Step "检查 Docker..."
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
  Write-Error "未检测到 Docker。请先安装 Docker Desktop 并启动后重试。"
  exit 1
}

# 等待 Docker 引擎就绪：Docker Desktop 刚启动时引擎可能还没起好（docker info 返回
# 500 / 连接失败）。这里最多等约 120 秒，避免“刚点开 Docker 就跑脚本”直接失败。
Write-Step "等待 Docker 引擎就绪..."
$engineReady = $false
for ($i = 0; $i -lt 60; $i++) {
  docker info *> $null
  if ($LASTEXITCODE -eq 0) { $engineReady = $true; break }
  if ($i -eq 0) { Write-Host "   Docker 引擎尚未就绪，正在等待（请确保 Docker Desktop 已打开）..." -ForegroundColor Yellow }
  Start-Sleep -Seconds 2
}
if (-not $engineReady) {
  Write-Error "Docker 引擎未就绪（请确认 Docker Desktop 已完全启动、状态为 Running 后重试）。"
  exit 1
}

docker compose version *> $null
if ($LASTEXITCODE -ne 0) {
  Write-Error "未检测到 Docker Compose（请更新 Docker Desktop 到较新版本）。"
  exit 1
}

# 2) 准备 .env
Write-Step "准备 .env..."
$envPath = Join-Path $Root ".env"
$lines = @()
if (Test-Path $envPath) { $lines = Get-Content $envPath }

function Set-EnvLine([string[]]$arr, [string]$key, [string]$val) {
  $found = $false
  $res = foreach ($l in $arr) {
    if ($l -match "^\s*$([regex]::Escape($key))=") { $found = $true; "$key=$val" } else { $l }
  }
  if (-not $found) { $res += "$key=$val" }
  # 必须用 @() 包裹：只有一行配置时 $res 会变成标量字符串，导致后续按字符
  # 拼接、.env 被写成一整行（APP_PORT=5890SECRET_KEY=... → compose 解析失败）。
  return @($res)
}

$lines = Set-EnvLine $lines "APP_PORT" "$Port"
if (-not ($lines -match "^SECRET_KEY=")) {
  $secret = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 32 | ForEach-Object {[char]$_})
  $lines = Set-EnvLine $lines "SECRET_KEY" $secret
}
$lines = Set-EnvLine $lines "DOCKER_PLATFORM" "linux/amd64"

# 客户交付：绑定宿主机硬件指纹（与 Python fingerprint.py 算法一致）
Write-Step "采集宿主机硬件指纹..."
. (Join-Path $PSScriptRoot "get_host_fingerprint.ps1")
$hostFp = Get-GtHostFingerprint
$hostCode = Format-GtMachineCode $hostFp
Write-Host "   机器码 : $hostCode" -ForegroundColor Green
$lines = Set-EnvLine $lines "GTAPP_HOST_FINGERPRINT" $hostFp
$lines = Set-EnvLine $lines "GTAPP_BIND_SERVER_MACHINE" "1"
$lines = Set-EnvLine $lines "GTAPP_IP_ALLOWLIST" "1"
# 用管道逐行写入，保证每行独立换行（-Value $lines 在某些情况下会挤成一行）。
$lines | Set-Content -Path $envPath -Encoding UTF8

# 3) 准备目录 + 加载内置镜像
New-Item -ItemType Directory -Force -Path (Join-Path $Root "license") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Root "backend\data") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Root "backend\database") | Out-Null

$backendTar  = Join-Path $Root "images\backend.tar.gz"
$frontendTar = Join-Path $Root "images\frontend.tar.gz"
if (-not (Test-Path $backendTar) -or -not (Test-Path $frontendTar)) {
  Write-Error "未找到内置镜像 images\backend.tar.gz / frontend.tar.gz，离线包不完整。"
  exit 1
}

Write-Step "加载内置镜像（与开发环境一模一样，无需联网/构建）..."
docker load -i $backendTar
if ($LASTEXITCODE -ne 0) { Write-Error "加载后端镜像失败"; exit 1 }
docker load -i $frontendTar
if ($LASTEXITCODE -ne 0) { Write-Error "加载前端镜像失败"; exit 1 }

$manifest = Join-Path $Root "images\MANIFEST.txt"
if (Test-Path $manifest) { Get-Content $manifest | ForEach-Object { Write-Host "   $_" -ForegroundColor Green } }

# 4) 启动
Write-Step "启动服务..."
docker compose up -d
if ($LASTEXITCODE -ne 0) { Write-Error "启动失败，请运行 docker compose logs 查看。"; exit 1 }

# 5) 结果
Write-Step "部署完成"
$ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.*'} | Select-Object -First 1).IPAddress
Write-Host ""
Write-Host "  本机访问 : http://127.0.0.1:$Port" -ForegroundColor Green
if ($ip) { Write-Host "  局域网访问 : http://$ip`:$Port" -ForegroundColor Green }
Write-Host "  机器码   : $hostCode （登录页底部也可查看，发给供应商申请许可证）" -ForegroundColor Yellow
Write-Host "  机器码工具: tools\gt-machine-code.exe（若已随包提供）"
Write-Host "  许可证   : 获取 license.dat 后，管理员登录 → 部署设置 → 上传安装"
Write-Host "  查看日志 : docker compose logs -f backend"
Write-Host ""
