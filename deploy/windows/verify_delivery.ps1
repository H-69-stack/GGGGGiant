<#
  Giant Tube App - Windows delivery smoke tests

  Usage (repo root):
    powershell -ExecutionPolicy Bypass -File deploy\windows\verify_delivery.ps1
    powershell -ExecutionPolicy Bypass -File deploy\windows\verify_delivery.ps1 -SkipDocker
#>
param(
  [switch]$SkipDocker
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path "$PSScriptRoot\..\..").Path
Set-Location $Root

$failed = 0
function Assert-Ok($cond, $msg) {
  if ($cond) { Write-Host "  [OK] $msg" -ForegroundColor Green }
  else { Write-Host "  [FAIL] $msg" -ForegroundColor Red; $script:failed++ }
}

Write-Host "==> [1/4] Fingerprint: PowerShell vs Python" -ForegroundColor Cyan
. (Join-Path $PSScriptRoot "get_host_fingerprint.ps1")
$psFp = Get-GtHostFingerprint
$psCode = Format-GtMachineCode $psFp

$pyScript = Join-Path $env:TEMP "gtapp_verify_fp.py"
Set-Content -Path $pyScript -Encoding UTF8 -Value @(
  'import sys',
  'sys.path.insert(0, sys.argv[1])',
  'from core.common.licensing.fingerprint import get_machine_fingerprint, format_machine_code',
  'fp = get_machine_fingerprint()',
  'print(fp)',
  'print(format_machine_code(fp))'
)

$pyOut = python $pyScript (Join-Path $Root "backend") 2>&1
if ($LASTEXITCODE -ne 0) {
  Assert-Ok $false "Python fingerprint failed: $pyOut"
}
else {
  $lines = @($pyOut | ForEach-Object { "$_".Trim() } | Where-Object { $_ })
  $pyFp = $lines[0]
  $pyCode = $lines[1]
  Assert-Ok ($psFp -eq $pyFp) "Full fingerprint match"
  Assert-Ok ($psCode -eq $pyCode) "Machine code match: $psCode"
  Assert-Ok ($psCode -match '^[0-9A-F]{5}(-[0-9A-F]{5}){3}$') "Machine code format"
}

Write-Host "==> [2/4] License issue and bind" -ForegroundColor Cyan
$laDir = Join-Path $Root "tools\license_authority"
$keyPath = Join-Path $laDir "keys\private_key.pem"
if (-not (Test-Path $keyPath)) {
  Write-Host "  [SKIP] No private key" -ForegroundColor Yellow
}
else {
  $issued = Join-Path $laDir "issued\verify_delivery_test.dat"
  New-Item -ItemType Directory -Force -Path (Join-Path $laDir "issued") | Out-Null
  $expires = (Get-Date).AddYears(1).ToString("yyyy-MM-dd")

  python "$laDir\issue_license.py" --customer "delivery-test" --machine-code $psCode --roles ALL --expires $expires --out $issued 2>&1 | Out-Null
  Assert-Ok ($LASTEXITCODE -eq 0) "Issue license.dat"

  $verifyScript = Join-Path $env:TEMP "gtapp_verify_lic.py"
  Set-Content -Path $verifyScript -Encoding UTF8 -Value @(
    'import os, sys',
    "os.environ['GTAPP_LICENSE_PATH'] = sys.argv[1]",
    "os.environ['GTAPP_HOST_FINGERPRINT'] = sys.argv[2]",
    "os.environ['GTAPP_BIND_SERVER_MACHINE'] = '1'",
    'sys.path.insert(0, sys.argv[3])',
    'from core.common.license import get_license_manager',
    's = get_license_manager().get_status(force_reload=True)',
    "print('valid=' + str(s.valid))"
  )

  $verifyOut = python $verifyScript $issued $psFp (Join-Path $Root "backend") 2>&1
  Assert-Ok ($verifyOut -match 'valid=True') "LicenseManager verify+bind"
}

Write-Host "==> [3/4] Offline package structure" -ForegroundColor Cyan
$stage = Join-Path $Root "dist\giant-tube-app-win"
$required = @(
  "images\backend.tar.gz",
  "images\frontend.tar.gz",
  "docker-compose.yml",
  "install.ps1",
  "uninstall.ps1",
  "get_host_fingerprint.ps1",
  "deploy.bat"
)
if (Test-Path $stage) {
  foreach ($rel in $required) {
    Assert-Ok (Test-Path (Join-Path $stage $rel)) "dist\giant-tube-app-win\$rel"
  }
  if (Test-Path (Join-Path $stage "tools\gt-machine-code.exe")) {
    Write-Host "  [OK] gt-machine-code.exe included" -ForegroundColor Green
  }
  else {
    Write-Host "  [WARN] gt-machine-code.exe missing" -ForegroundColor Yellow
  }
}
else {
  Write-Host "  [SKIP] dist\giant-tube-app-win not found (run make_windows_setup.ps1)" -ForegroundColor Yellow
}

Write-Host "==> [4/4] Docker API probe" -ForegroundColor Cyan
if ($SkipDocker) {
  Write-Host "  [SKIP] --SkipDocker" -ForegroundColor Yellow
}
else {
  docker info *> $null
  if ($LASTEXITCODE -ne 0) {
    Write-Host "  [SKIP] Docker not running" -ForegroundColor Yellow
  }
  else {
    $port = 5890
    try {
      $mc = Invoke-RestMethod "http://127.0.0.1:$port/api/auth/machine-code" -TimeoutSec 5
      Assert-Ok ($mc.success -and $mc.machine_code) "GET /api/auth/machine-code"
      $lic = Invoke-RestMethod "http://127.0.0.1:$port/api/auth/license-status" -TimeoutSec 5
      Assert-Ok ($null -ne $lic.license) "GET /api/auth/license-status"
    }
    catch {
      Write-Host "  [SKIP] Service not running on port $port" -ForegroundColor Yellow
    }
  }
}

Write-Host ""
if ($failed -eq 0) {
  Write-Host "PASS: all delivery checks passed" -ForegroundColor Green
  exit 0
}
else {
  Write-Host "FAIL: $failed check(s) failed" -ForegroundColor Red
  exit 1
}
