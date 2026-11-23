<#
  巨管软件 - Windows 交付包一键构建

  产物：
    dist\giant-tube-app-win\          离线 zip 包目录
    dist\giant-tube-app-win.zip       离线 zip（可选）
    dist\GiantTubeAppSetup_X.X.X.exe  Inno Setup 安装包（若已安装 ISCC）

  用法（仓库根目录，管理员 PowerShell）：
    powershell -ExecutionPolicy Bypass -File deploy\make_windows_setup.ps1
    powershell -ExecutionPolicy Bypass -File deploy\make_windows_setup.ps1 -Hardened
    powershell -ExecutionPolicy Bypass -File deploy\make_windows_setup.ps1 -SkipInno
    powershell -ExecutionPolicy Bypass -File deploy\make_windows_setup.ps1 -SkipZip

  前置：Docker Desktop 已启动；Python 3 可用（构建机器码工具）。
#>
param(
  [switch]$Hardened,
  [switch]$SkipInno,
  [switch]$SkipZip,
  [switch]$SkipTools
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path "$PSScriptRoot\..").Path
Set-Location $Root

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }

$AppVersion = (Get-Content "$Root\VERSION" -ErrorAction SilentlyContinue | Select-Object -First 1).Trim()
if (-not $AppVersion) { $AppVersion = "0.0.0" }

try { $GitCommit = (git rev-parse --short HEAD 2>$null).Trim() } catch { $GitCommit = "unknown" }

$PkgName = "giant-tube-app-win"
$Stage = Join-Path $Root "dist\$PkgName"
$BackendImage = "giant-tube-app-backend:latest"
$FrontendImage = "giant-tube-app-frontend:latest"

$env:DOCKER_PLATFORM = "linux/amd64"
$env:APP_VERSION = $AppVersion
$env:GIT_COMMIT = $GitCommit
$env:BUILD_CHANNEL = "release"

Write-Step "版本: $AppVersion  通道: release  commit: $GitCommit"

# ── 0) Docker ──
Write-Step "检查 Docker..."
docker info *> $null
if ($LASTEXITCODE -ne 0) {
  Write-Error "Docker 未运行。请先启动 Docker Desktop。"
}

# ── 1) 构建镜像 ──
Write-Step "构建 Docker 镜像（linux/amd64，首次较慢）..."
docker compose build
if ($LASTEXITCODE -ne 0) { Write-Error "docker compose build 失败" }

if ($Hardened) {
  Write-Step "加固后端（Nuitka 编译 core）..."
  docker build --platform linux/amd64 `
    --build-arg BASE_IMAGE=$BackendImage `
    -f docker/backend.hardened.Dockerfile `
    -t $BackendImage .
  if ($LASTEXITCODE -ne 0) { Write-Error "加固镜像构建失败" }
}

# ── 2) 机器码工具 exe ──
function Build-MachineCodeTool {
  Write-Step "构建 gt-machine-code.exe ..."
  python -m pip install pyinstaller -q 2>$null
  $distTools = Join-Path $Root "dist\tools"
  New-Item -ItemType Directory -Force -Path $distTools | Out-Null
  python -m PyInstaller --noconfirm --onefile --console `
    --name gt-machine-code `
    --distpath $distTools `
    --workpath (Join-Path $Root "build\pyi-machine-code") `
    --specpath (Join-Path $Root "build\pyi-machine-code") `
    --paths (Join-Path $Root "backend") `
    (Join-Path $Root "tools\machine_code\get_machine_code.py")
  if ($LASTEXITCODE -ne 0) { throw "PyInstaller 构建 gt-machine-code 失败" }
}

if (-not $SkipTools) {
  try {
    Build-MachineCodeTool
    Write-Host "   完成: dist\tools\gt-machine-code.exe" -ForegroundColor Green
  } catch {
    Write-Host "   ⚠️  机器码工具构建跳过: $_" -ForegroundColor Yellow
  }
}

# ── 3) 导出镜像为 gzip tar ──
function Export-DockerImageGzip([string]$Image, [string]$OutPath) {
  $tmp = [System.IO.Path]::Combine([System.IO.Path]::GetTempPath(), [Guid]::NewGuid().ToString() + ".tar")
  try {
    docker save -o $tmp $Image
    if ($LASTEXITCODE -ne 0) { throw "docker save $Image 失败" }
    $inStream = [System.IO.File]::OpenRead($tmp)
    $outStream = [System.IO.File]::Create($OutPath)
    $gzip = New-Object System.IO.Compression.GZipStream($outStream, [System.IO.Compression.CompressionMode]::Compress)
    $inStream.CopyTo($gzip)
    $gzip.Dispose(); $outStream.Dispose(); $inStream.Dispose()
  } finally {
    if (Test-Path $tmp) { Remove-Item $tmp -Force }
  }
}

Write-Step "准备打包目录: $Stage"
if (Test-Path $Stage) { Remove-Item $Stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path (Join-Path $Stage "images") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Stage "license") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Stage "backend") | Out-Null

Write-Step "导出后端镜像..."
Export-DockerImageGzip $BackendImage (Join-Path $Stage "images\backend.tar.gz")
Write-Step "导出前端镜像..."
Export-DockerImageGzip $FrontendImage (Join-Path $Stage "images\frontend.tar.gz")

$manifest = @(
  "built_at=$(Get-Date -Format 'yyyy-MM-ddTHH:mm:ssZ')"
  "platform=linux/amd64"
  "hardened=$([int]$Hardened)"
  "app_version=$AppVersion"
  "build_channel=release"
  "backend_image=$BackendImage"
  "frontend_image=$FrontendImage"
  "git_commit=$GitCommit"
)
$manifest | Set-Content (Join-Path $Stage "images\MANIFEST.txt") -Encoding UTF8

# ── 4) 复制部署文件 ──
Write-Step "复制部署脚本与配置..."
Copy-Item "$Root\deploy\windows\offline\docker-compose.yml" "$Stage\docker-compose.yml"
Copy-Item "$Root\deploy\windows\offline\install.ps1"        "$Stage\install.ps1"
Copy-Item "$Root\deploy\windows\offline\uninstall.ps1"      "$Stage\uninstall.ps1"
Copy-Item "$Root\deploy\windows\get_host_fingerprint.ps1"   "$Stage\get_host_fingerprint.ps1"
Copy-Item "$Root\deploy\windows\offline\部署.bat"            "$Stage\部署.bat"
Copy-Item "$Root\deploy\windows\offline\卸载.bat"            "$Stage\卸载.bat"
Copy-Item "$Root\deploy\windows\offline\deploy.bat"         "$Stage\deploy.bat"
Copy-Item "$Root\deploy\windows\offline\uninstall.bat"      "$Stage\uninstall.bat"
Copy-Item "$Root\.env.example"                              "$Stage\.env.example"

$siteLicense = "$Root\deploy\windows\offline\site_license.dat"
if (Test-Path $siteLicense) {
  Copy-Item $siteLicense "$Stage\license\license.dat"
  Write-Host "   已预置 site_license.dat" -ForegroundColor Green
} else {
  Write-Host "   未预置许可证（客户部署后需自行激活）" -ForegroundColor Yellow
}

# 机器码工具
$mcExe = Join-Path $Root "dist\tools\gt-machine-code.exe"
if (Test-Path $mcExe) {
  New-Item -ItemType Directory -Force -Path (Join-Path $Stage "tools") | Out-Null
  Copy-Item $mcExe (Join-Path $Stage "tools\gt-machine-code.exe")
  Write-Host "   已包含 tools\gt-machine-code.exe" -ForegroundColor Green
}

# ── 5) 数据骨架 ──
Write-Step "复制 backend\database ..."
$dbSrc = Join-Path $Root "backend\database"
$dbDst = Join-Path $Stage "backend\database"
New-Item -ItemType Directory -Force -Path $dbDst | Out-Null
Copy-Item "$dbSrc\*" $dbDst -Recurse -Force

Write-Step "创建 backend\data 目录骨架..."
$dataDirs = @(
  "backend\data", "backend\data\uploads", "backend\data\exports", "backend\data\cache",
  "backend\data\pp_saved_sources", "backend\data\rfm_sessions", "backend\data\rfm_shaping",
  "backend\data\rfm_belling", "backend\data\rfm_flattening", "backend\data\rfm_bending",
  "backend\data\tp_mold"
)
foreach ($rel in $dataDirs) {
  $d = Join-Path $Stage $rel
  New-Item -ItemType Directory -Force -Path $d | Out-Null
  New-Item -ItemType File -Force -Path (Join-Path $d ".keep") | Out-Null
}

# ── 6) 使用说明 ──
@'
巨管软件 - Windows 离线部署包
=================================

前置条件：
  目标机器已安装并启动 Docker Desktop（Windows 版，使用 Linux 容器）。

部署方式（任选其一）：
  A) 双击「部署.bat」或「deploy.bat」
  B) 若通过 GiantTubeAppSetup.exe 安装，安装程序会自动完成部署

部署步骤：
  1) 确认 Docker Desktop 已启动。
  2) 运行部署脚本，记录显示的机器码。
  3) 把机器码发送给软件供应商，获取 license.dat。
  4) 浏览器打开 http://127.0.0.1:5890 ，用 admin / Admin@2027 登录。
  5) 管理 → 部署设置 → 上传 license.dat。

常用操作：
  - 查看机器码：双击 tools\gt-machine-code.exe，或登录页底部复制。
  - 停止服务：双击「卸载.bat」或 uninstall.bat（数据保留）。
  - 换端口：powershell -ExecutionPolicy Bypass -File install.ps1 -Port 8080
  - 内网网段：管理员 → 部署设置 → 保存网络配置
  - 日志：docker compose logs -f backend
'@ | Set-Content (Join-Path $Stage "使用说明.txt") -Encoding UTF8

# ── 7) ZIP ──
if (-not $SkipZip) {
  Write-Step "压缩为 zip（较大，请稍候）..."
  $zipPath = Join-Path $Root "dist\$PkgName.zip"
  if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
  Compress-Archive -Path $Stage -DestinationPath $zipPath -CompressionLevel Optimal
  Write-Host "   完成: $zipPath" -ForegroundColor Green
}

# ── 8) Inno Setup ──
if (-not $SkipInno) {
  $isccCandidates = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
  )
  $iscc = $isccCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
  if ($iscc) {
    Write-Step "编译 Inno Setup 安装包..."
    & $iscc "/DAppVersion=$AppVersion" "/DOfflinePkgRoot=$Stage" "$Root\deploy\windows\giant_tube_app_docker.iss"
    if ($LASTEXITCODE -eq 0) {
      Write-Host "   完成: dist\GiantTubeAppSetup_$AppVersion.exe" -ForegroundColor Green
    } else {
      Write-Host "   ⚠️  ISCC 编译失败" -ForegroundColor Yellow
    }
  } else {
    Write-Host "   ⚠️  未找到 Inno Setup 6（ISCC.exe），跳过安装包编译。" -ForegroundColor Yellow
    Write-Host "      下载: https://jrsoftware.org/isinfo.php" -ForegroundColor Yellow
  }
}

Write-Host ""
Write-Host "✅ 全部完成。" -ForegroundColor Green
Write-Host "   离线包目录 : $Stage"
if (-not $SkipZip) { Write-Host "   离线 zip   : dist\$PkgName.zip" }
Write-Host "   发给客户   : zip 或 GiantTubeAppSetup_$AppVersion.exe（若已编译）"
