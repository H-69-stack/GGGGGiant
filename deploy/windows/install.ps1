<#
  巨管软件 - Windows 一键部署脚本（中心服务器模式）

  作用：
    1) 检查 Docker 是否就绪。
    2) 准备 .env（端口、密钥、网段白名单）。
    3) 构建并以后台服务方式启动容器（开机自启由 Docker + restart 策略保证）。
    4) 打印访问地址。

  用法（管理员 PowerShell，在仓库根目录）：
    powershell -ExecutionPolicy Bypass -File deploy\windows\install.ps1

  可选参数：
    -Port 5890               对外访问端口
    -AllowedCidrs "192.168.10.0/24"   收紧到客户子网（默认放行整个局域网）
    -Hardened                使用加固镜像（Nuitka 编译 core）
#>
param(
  [int]$Port = 5890,
  [string]$AllowedCidrs = "",
  [switch]$Hardened
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path "$PSScriptRoot\..\..").Path
Set-Location $RepoRoot

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }

# 1) 检查 Docker
Write-Step "检查 Docker..."
try { docker version | Out-Null } catch {
  Write-Error "未检测到 Docker。请先安装 Docker Desktop / Docker Engine 并启动后重试。"
  exit 1
}

# 2) 准备 .env（托管/离线部署默认不绑定服务器硬件指纹）
Write-Step "准备 .env..."
$envPath = Join-Path $RepoRoot ".env"
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

$secret = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 32 | ForEach-Object {[char]$_})
$lines = Set-EnvLine $lines "APP_PORT" "$Port"
if (-not ($lines -match "^SECRET_KEY=")) { $lines = Set-EnvLine $lines "SECRET_KEY" $secret }
$lines = Set-EnvLine $lines "DOCKER_PLATFORM" "linux/amd64"

# 客户本地交付：绑定宿主机硬件指纹
Write-Step "采集宿主机硬件指纹..."
. (Join-Path $PSScriptRoot "get_host_fingerprint.ps1")
$hostFp = Get-GtHostFingerprint
$hostCode = Format-GtMachineCode $hostFp
Write-Host "   机器码 : $hostCode" -ForegroundColor Green
$lines = Set-EnvLine $lines "GTAPP_HOST_FINGERPRINT" $hostFp
$lines = Set-EnvLine $lines "GTAPP_BIND_SERVER_MACHINE" "1"
if ($AllowedCidrs) { $lines = Set-EnvLine $lines "GTAPP_ALLOWED_CIDRS" $AllowedCidrs }
$lines = Set-EnvLine $lines "GTAPP_IP_ALLOWLIST" "1"
# 用管道逐行写入，保证每行独立换行（-Value $lines 在某些情况下会挤成一行）。
$lines | Set-Content -Path $envPath -Encoding UTF8

# 4) 准备 license 目录
New-Item -ItemType Directory -Force -Path (Join-Path $RepoRoot "license") | Out-Null

# 5) 优先加载随包的离线镜像（与开发环境一模一样），否则现场构建
$imagesDir   = Join-Path $RepoRoot "deploy\images"
$backendTar  = Join-Path $imagesDir "backend.tar.gz"
$frontendTar = Join-Path $imagesDir "frontend.tar.gz"
$loaded = $false

if ((Test-Path $backendTar) -and (Test-Path $frontendTar)) {
  Write-Step "加载离线镜像（无需现场构建，确保与开发环境一致）..."
  # docker load -i 可直接读取 gzip 压缩的 tar；切勿用 PowerShell 管道传二进制
  docker load -i $backendTar
  if ($LASTEXITCODE -ne 0) { Write-Error "加载后端镜像失败"; exit 1 }
  docker load -i $frontendTar
  if ($LASTEXITCODE -ne 0) { Write-Error "加载前端镜像失败"; exit 1 }
  $manifest = Join-Path $imagesDir "MANIFEST.txt"
  if (Test-Path $manifest) { Get-Content $manifest | ForEach-Object { Write-Host "   $_" -ForegroundColor Green } }
  $loaded = $true
}

if (-not $loaded) {
  Write-Step "构建镜像并启动服务（首次较慢，请耐心等待）..."
  if ($Hardened) {
    Write-Host "   使用加固镜像（Nuitka）" -ForegroundColor Yellow
    docker build -f docker/backend.hardened.Dockerfile -t giant-tube-app-backend:latest .
  }
  docker compose build
}

Write-Step "启动服务..."
docker compose up -d

# 6) 打印结果
Write-Step "部署完成"
$ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.*'} | Select-Object -First 1).IPAddress
Write-Host ""
Write-Host "  访问地址 : http://$ip`:$Port  （局域网内浏览器打开）" -ForegroundColor Green
Write-Host "  机器码   : $hostCode （登录页底部也可查看）" -ForegroundColor Yellow
Write-Host "  许可证   : 获取 license.dat 后，管理员登录 → 部署设置 → 上传安装"
Write-Host ""
