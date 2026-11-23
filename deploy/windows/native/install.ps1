<#
  巨管软件 - 原生安装后初始化（写 .env、创建目录）

  由 Inno Setup 安装完成后调用，也可手动运行。
#>
param(
  [int]$Port = 5890
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot

function Set-EnvLine([string[]]$arr, [string]$key, [string]$val) {
  $found = $false
  $res = foreach ($l in $arr) {
    if ($l -match "^\s*$([regex]::Escape($key))=") { $found = $true; "$key=$val" } else { $l }
  }
  if (-not $found) { $res += "$key=$val" }
  return @($res)
}

$envPath = Join-Path $Root ".env"
$lines = @()
if (Test-Path $envPath) { $lines = Get-Content $envPath }

$secret = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 32 | ForEach-Object {[char]$_})
$lines = Set-EnvLine $lines "APP_PORT" "$Port"
if (-not ($lines -match "^SECRET_KEY=")) { $lines = Set-EnvLine $lines "SECRET_KEY" $secret }
$lines = Set-EnvLine $lines "GTAPP_BIND_SERVER_MACHINE" "1"
$lines = Set-EnvLine $lines "GTAPP_LICENSE_PATH" (Join-Path $Root "license\license.dat")
$lines = Set-EnvLine $lines "GTAPP_FRONTEND_DIST" (Join-Path $Root "frontend\dist")
$lines = Set-EnvLine $lines "GTAPP_IP_ALLOWLIST" "1"
$lines = Set-EnvLine $lines "GTAPP_OPEN_BROWSER" "1"
$lines | Set-Content -Path $envPath -Encoding UTF8

@(
  "license",
  "entity_out",
  "freecad_user",
  "gnt管件\uploads",
  "backend\data", "backend\data\uploads", "backend\data\exports", "backend\data\cache",
  "backend\data\pp_saved_sources", "backend\data\rfm_sessions", "backend\data\logs",
  "backend\database", "backend\database\pp",
  "backend\database\normal-mold", "backend\database\tp-mold", "backend\database\taper-mold",
  "backend\database\user"
) | ForEach-Object {
  New-Item -ItemType Directory -Force -Path (Join-Path $Root $_) | Out-Null
}

# ── 关键修复：Program Files 下的目录默认对 Users 只读，SQLite 无法建库/写入。
#    这里对所有运行时可写目录（数据库、data、logs、license）显式授予 Modify 权限，
#    并用 (OI)(CI) 让子目录/文件继承，覆盖已存在文件（例如随包装出的 mold 模板 .db）。
#    /T = 递归；/C = 忽略个别文件错误；/Q = 静默。
$writableDirs = @(
  "license",
  "entity_out",
  "freecad_user",
  "gnt管件\uploads",
  "backend\data",
  "backend\logs",   # 兼容旧路径：如果曾经存在 logs 也一并放开
  "backend\database"
)
foreach ($rel in $writableDirs) {
  $target = Join-Path $Root $rel
  if (-not (Test-Path $target)) { continue }
  try {
    & icacls.exe "$target" /grant "*S-1-5-32-545:(OI)(CI)(M)" /T /C /Q | Out-Null
  } catch {
    Write-Host "  icacls 授权失败（可忽略，管理员运行时会用其它路径兜底）：$rel" -ForegroundColor Yellow
  }
}
# Users SID = S-1-5-32-545，避免中英文系统下"Users/用户"本地化名字差异导致 icacls 报错。

Write-Host "初始化完成。请从开始菜单启动软件。" -ForegroundColor Green
Write-Host "访问地址: http://127.0.0.1:$Port"
