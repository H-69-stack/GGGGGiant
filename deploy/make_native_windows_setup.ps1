<#
  Native Windows installer build (no Docker).
  Usage: powershell -ExecutionPolicy Bypass -File deploy\make_native_windows_setup.ps1
#>
param(
  [switch]$SkipInno,
  [switch]$SkipZip,
  [switch]$SkipTools,
  [switch]$SkipFrontend,
  [switch]$SkipVenv,
  [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path "$PSScriptRoot\..").Path
Set-Location $Root

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }

function Invoke-SmokeTest {
  # Import every module the backend needs at runtime so a stale / missing
  # dependency is caught NOW, not on the customer's first launch. Called in
  # BOTH the fresh-venv path and the -SkipVenv reuse path — a reused portable
  # Python that predates a new import (e.g. cadquery-ocp) would otherwise
  # crash in the field with no clue back to the build.
  param([string]$Python)
  $smokePy = Join-Path $env:TEMP "gtapp_native_smoke.py"
  Set-Content -Path $smokePy -Encoding UTF8 -Value @(
    "import os, sys",
    "os.add_dll_directory(os.path.join(os.path.dirname(sys.executable), 'Library', 'bin'))",
    "import numpy; assert numpy.__version__=='1.26.4', numpy.__version__",
    "import scipy, trimesh, shapely, cadquery, nlopt",
    "from OCP.STEPControl import STEPControl_Reader",
    "from cryptography.hazmat.primitives.asymmetric import ed25519",
    "import xml.etree.ElementTree as _et; _et.fromstring('<a/>')",
    "import flask, flask_socketio",
    "print('smoke-test OK')"
  )
  $smokeOut = (& $Python $smokePy 2>&1 | Out-String)
  if ($smokeOut -notmatch 'smoke-test OK') {
    Write-Error "Smoke test failed (stale or missing deps): $smokeOut"
  }
  Write-Host "   $($smokeOut.Trim())" -ForegroundColor Green
}

function Get-IsccPath {
  $iscc = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "D:\Software\Inno Setup 6\ISCC.exe"
  ) | Where-Object { Test-Path $_ } | Select-Object -First 1
  if ($iscc) { return $iscc }

  Write-Step "Inno Setup 6 not found; downloading and installing..."
  $installer = Join-Path $env:TEMP "innosetup-latest.exe"
  curl.exe -L --retry 3 -o $installer "https://jrsoftware.org/download.php/is.exe" 2>$null
  if (-not (Test-Path $installer) -or (Get-Item $installer).Length -lt 1000000) {
    Write-Host "   Failed to download Inno Setup" -ForegroundColor Yellow
    return $null
  }
  Start-Process -FilePath $installer -ArgumentList "/VERYSILENT","/SUPPRESSMSGBOXES","/NORESTART","/SP-" -Wait
  return @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
  ) | Where-Object { Test-Path $_ } | Select-Object -First 1
}

$AppVersion = (Get-Content "$Root\VERSION" -ErrorAction SilentlyContinue | Select-Object -First 1).Trim()
if (-not $AppVersion) { $AppVersion = "0.0.0" }
try { $GitCommit = (git rev-parse --short HEAD 2>$null).Trim() } catch { $GitCommit = "unknown" }

# Capture the entire build log to dist/ so a failed 20-40 min run can be
# diagnosed from more than the last stdout line.
$LogPath = Join-Path $Root "dist\build_log_$AppVersion.txt"
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $LogPath) | Out-Null
Start-Transcript -Path $LogPath -Force | Out-Null

$PkgName = "giant-tube-app-native"
$Stage = Join-Path $Root "dist\$PkgName"

if (-not $PythonExe) {
  $PythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
}
if (-not $PythonExe -or -not (Test-Path $PythonExe)) {
  Write-Error "Python not found. Install Python 3.12 and add to PATH."
}

Write-Step "Version: $AppVersion  commit: $GitCommit"
Write-Step "Python: $PythonExe"

if (-not $SkipFrontend) {
  Write-Step "Building frontend (npm run build)..."
  Push-Location "$Root\frontend"
  if (-not (Test-Path "node_modules")) {
    npm ci
    if ($LASTEXITCODE -ne 0) { Pop-Location; Write-Error "npm ci failed" }
  }
  npm run build
  if ($LASTEXITCODE -ne 0) { Pop-Location; Write-Error "npm run build failed" }
  Pop-Location
}
if (-not (Test-Path "$Root\frontend\dist\index.html")) {
  Write-Error "frontend\dist\index.html not found"
}

Write-Step "Staging directory: $Stage"
if (-not $SkipVenv) {
  if (Test-Path $Stage) {
    # Move the previous stage aside BEFORE rebuilding. A failed rebuild then
    # leaves the last good stage recoverable at $Stage.old, and Move-Item
    # fails on locked files — surfacing "stage in use" instead of nuking a
    # stage some other process still has open.
    $StageOld = "$Stage.old"
    if (Test-Path $StageOld) { Remove-Item $StageOld -Recurse -Force -ErrorAction SilentlyContinue }
    Move-Item $Stage $StageOld -Force
  }
}
New-Item -ItemType Directory -Force -Path $Stage, "$Stage\backend", "$Stage\frontend\dist", "$Stage\license", "$Stage\tools" | Out-Null

# Portable Python layout: python.exe at TOP LEVEL (like python.org install)
# so it doesn't reference the developer's C:\PyProject via venv's pyvenv.cfg redirector.
$BasePythonDir = (Split-Path -Parent $PythonExe)
$PythonDir = Join-Path $Stage "python"
$PortablePython = Join-Path $PythonDir "python.exe"

if ($SkipVenv -and (Test-Path $PortablePython)) {
  Write-Step "Reusing existing portable Python at $PythonDir"
  # Verify the reused venv still has every dep the current code expects.
  # A stale portable Python (new imports added since the last build) would
  # otherwise crash on the customer's first launch with no link back here.
  Invoke-SmokeTest -Python $PortablePython
} else {
  Write-Step "Building portable Python from $BasePythonDir (this may take 20-40 min on first run)..."
  if (Test-Path $PythonDir) { Remove-Item $PythonDir -Recurse -Force }
  New-Item -ItemType Directory -Force -Path $PythonDir, "$PythonDir\Lib\site-packages", "$PythonDir\Library\bin" | Out-Null

  # 1) Top-level python.exe / pythonw.exe / all runtime DLLs (msvcp, vcruntime, python312.dll, ...)
  #    Skip conda-specific binaries that we do NOT want to ship.
  $topLevelSkip = @('Uninstall-Anaconda3.exe','_conda.exe','xlwings32-0.32.1.dll','xlwings64-0.32.1.dll')
  Get-ChildItem "$BasePythonDir\*" -Include *.exe,*.dll -File | Where-Object { $topLevelSkip -notcontains $_.Name } | ForEach-Object {
    Copy-Item $_.FullName $PythonDir -Force
  }

  # 2) DLLs\ (contains _ssl.pyd, _hashlib.pyd, _ctypes.pyd and other stdlib C extensions)
  if (Test-Path "$BasePythonDir\DLLs") {
    Copy-Item "$BasePythonDir\DLLs" $PythonDir -Recurse -Force
  }

  # 3) Stdlib (Lib\* minus site-packages)
  Get-ChildItem "$BasePythonDir\Lib" -Directory | Where-Object { $_.Name -ne 'site-packages' } | ForEach-Object {
    Copy-Item $_.FullName "$PythonDir\Lib\" -Recurse -Force
  }
  Get-ChildItem "$BasePythonDir\Lib" -File | ForEach-Object { Copy-Item $_.FullName "$PythonDir\Lib\" -Force }

  # 4) All native DLLs from conda's Library\bin\ (libffi for _ctypes, libexpat for pyexpat,
  #    OpenSSL for cryptography, MKL for numpy, ...). We copy them all rather than hand-picking
  #    to avoid missing runtime deps of stdlib .pyd files and packages.
  if (Test-Path "$BasePythonDir\Library\bin") {
    Copy-Item "$BasePythonDir\Library\bin\*.dll" "$PythonDir\Library\bin\" -Force -ErrorAction SilentlyContinue
  }

  # Bootstrap pip inside portable Python and install runtime deps.
  # PYTHONNOUSERSITE=1 prevents pip / imports from leaking APPDATA site-packages
  # (which on this dev machine has newer numpy that would shadow numpy==1.26.4).
  $env:PYTHONNOUSERSITE = "1"
  Write-Step "Bootstrapping pip in portable Python..."
  & $PortablePython -m ensurepip --upgrade
  if ($LASTEXITCODE -ne 0) { Write-Error "ensurepip failed" }
  & $PortablePython -m pip install --upgrade pip
  if ($LASTEXITCODE -ne 0) { Write-Error "pip upgrade failed" }

  Write-Step "Installing backend requirements into portable Python..."
  & $PortablePython -m pip install -r "$Root\backend\requirements.txt"
  if ($LASTEXITCODE -ne 0) { Write-Error "pip install requirements failed" }
  & $PortablePython -m pip install --no-deps nlopt==2.7.1 cadquery==2.5.2
  if ($LASTEXITCODE -ne 0) { Write-Error "pip install cadquery/nlopt failed" }

  Write-Step "Smoke test (importing critical modules)..."
  Invoke-SmokeTest -Python $PortablePython
}

Write-Step "Copying backend..."
# NOTE: /XF license.dat license.json — 交付给客户的安装包绝不能带任何许可证/激活数据，
# 否则客户装完后会显示为其它客户的授权，或跳过激活页。
# robocopy writes its summary to stderr; under $ErrorActionPreference=Stop that
# can abort the copy mid-way and leave the stage with NO backend source — a
# silently broken installer (the 2.0.1.2.0724 build shipped an empty backend
# this way). Run it under Continue, retry once, and VERIFY the .py count so a
# future silent failure aborts the build instead of shipping a broken exe.
$ErrorActionPreference = "Continue"
try {
  # Brief settle after the 16-min venv build so any lingering handle / FS
  # state from the smoke-test python child is released before robocopy.
  Start-Sleep -Seconds 2
  foreach ($attempt in 1..2) {
    robocopy "$Root\backend" "$Stage\backend" /E /XD data __pycache__ .pytest_cache dist build .tox .mypy_cache .git /XF *.pyc license.dat license.json /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
    $rcExit = $LASTEXITCODE
    $pyCount = @(Get-ChildItem "$Stage\backend" -Recurse -Filter *.py -File -ErrorAction SilentlyContinue).Count
    Write-Host "   robocopy attempt ${attempt}: exit=$rcExit staged .py=$pyCount" -ForegroundColor Cyan
    if ($rcExit -lt 8 -and $pyCount -ge 50) { break }
  }
} finally {
  $ErrorActionPreference = "Stop"
}
if ($rcExit -ge 8) { Write-Error "robocopy backend failed: exit $rcExit" }
if ($pyCount -lt 50) {
  Write-Error "robocopy backend copied only $pyCount .py files (expected 50+). Stage backend is empty — installer would ship with NO backend code. Aborting."
}

# 双保险：确保 stage 中不残留任何 license/激活数据（防止本地测试文件被打包）
Remove-Item "$Stage\backend\static\license.dat" -Force -ErrorAction SilentlyContinue
Remove-Item "$Stage\backend\static\license.json" -Force -ErrorAction SilentlyContinue
if (Test-Path "$Stage\license") { Remove-Item "$Stage\license\*" -Recurse -Force -ErrorAction SilentlyContinue }
New-Item -ItemType Directory -Force -Path "$Stage\license" | Out-Null
# 复制仓库中现成的说明文件（避免 PowerShell 处理中文字面量时的编码问题）
if (Test-Path "$Root\license\PUT_LICENSE_HERE.txt") {
  Copy-Item "$Root\license\PUT_LICENSE_HERE.txt" "$Stage\license\PUT_LICENSE_HERE.txt" -Force
}

$buildInfoLines = @(
  'BUILD_CHANNEL = "release"',
  "VERSION = `"$AppVersion`"",
  "GIT_COMMIT = `"$GitCommit`"",
  '',
  'def is_release():',
  '    return str(BUILD_CHANNEL).strip().lower() == "release"',
  '',
  'def get_version():',
  '    return VERSION',
  '',
  'def summary():',
  "    return f'{get_version()} ({BUILD_CHANNEL}, commit {GIT_COMMIT})'"
)
$buildInfoPath = "$Stage\backend\core\common\build_info.py"
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $buildInfoPath) | Out-Null
[System.IO.File]::WriteAllText($buildInfoPath, ($buildInfoLines -join "`r`n") + "`r`n", [System.Text.UTF8Encoding]::new($false))

Write-Step "Copying frontend/dist..."
Copy-Item "$Root\frontend\dist\*" "$Stage\frontend\dist\" -Recurse -Force

# NOTE: backend/database 已经由前面的 robocopy /E 一并复制到 $Stage\backend\database。
#       之前这里再 Copy-Item -Recurse 会把源 database\ 复制成 $Stage\backend\database\database\
#       造成嵌套重复。这一整段删除即可。

$dataDirs = @(
  "uploads", "exports", "cache", "pp_saved_sources",
  "rfm_sessions", "rfm_shaping", "rfm_belling", "rfm_flattening", "rfm_bending", "tp_mold"
)
New-Item -ItemType Directory -Force -Path "$Stage\backend\data" | Out-Null
foreach ($d in $dataDirs) {
  New-Item -ItemType Directory -Force -Path "$Stage\backend\data\$d" | Out-Null
  New-Item -ItemType File -Force -Path "$Stage\backend\data\$d\.keep" | Out-Null
}

Write-Step "Copying launch scripts..."
Copy-Item "$Root\deploy\windows\native\*" $Stage -Force
Copy-Item "$Root\deploy\windows\get_host_fingerprint.ps1" "$Stage\get_host_fingerprint.ps1" -Force

Write-Step "App icon + GiantTubeApp.exe launcher..."
& $PythonExe "$Root\tools\build\generate_app_icon.py"
$iconDir = Join-Path $Stage "assets"
New-Item -ItemType Directory -Force -Path $iconDir | Out-Null
Copy-Item "$Root\deploy\windows\assets\app_icon.ico" (Join-Path $iconDir "app_icon.ico") -Force
Copy-Item "$Root\deploy\windows\assets\app_icon.png" (Join-Path $iconDir "app_icon.png") -Force

# Build launcher .exe using the portable Python we just prepared, so PyInstaller resolves
# imports against the exact runtime we ship. PYTHONNOUSERSITE=1 prevents pip/PyInstaller from
# leaking APPDATA site-packages into the portable env.
$env:PYTHONNOUSERSITE = "1"
# 用临时抑制 ErrorAction 的方式跑 pip：pip 的诊断 WARNING 会走 stderr，
# 而 PowerShell 严格模式会把 native stderr 视作错误。这里显式忽略退出码之外的所有噪音。
# 直接显式 Stop 恢复（不保存 $prevEA）—— 之前 $prevEA 在某些上下文为 $null，
# 赋值回 EAP 会抛 ArgumentTransformationMetadataException（见 build_log）。
$ErrorActionPreference = "Continue"
try {
  $pipLog = & $PortablePython -m pip install --upgrade --target "$PythonDir\Lib\site-packages" pyinstaller 2>&1 | Out-String
  $pipExit = $LASTEXITCODE
} finally {
  $ErrorActionPreference = "Stop"
}
if ($pipExit -ne 0 -and -not (Test-Path "$PythonDir\Lib\site-packages\PyInstaller")) {
  Write-Host $pipLog
  Write-Error "Failed to install PyInstaller into portable Python (exit=$pipExit)"
}
& $PortablePython -m PyInstaller --noconfirm --onefile --noconsole `
  --name GiantTubeApp `
  --icon "$Root\deploy\windows\assets\app_icon.ico" `
  --distpath $Stage `
  --workpath "$Root\build\pyi-giant-tube-app" `
  --specpath "$Root\build\pyi-giant-tube-app" `
  "$Root\tools\launcher\giant_tube_launcher.py"
if (-not (Test-Path (Join-Path $Stage "GiantTubeApp.exe"))) {
  Write-Error "GiantTubeApp.exe build failed"
}

# Purge PyInstaller and other build-only packages from shipped site-packages to save space.
$purgePackages = @(
  'PyInstaller','pyinstaller','pyinstaller_hooks_contrib','altgraph','pefile','pywin32_ctypes'
)
foreach ($pkg in $purgePackages) {
  Get-ChildItem "$PythonDir\Lib\site-packages" -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -eq $pkg -or $_.Name -like "$pkg-*.dist-info" } |
    ForEach-Object { Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }
}
Remove-Item "$PythonDir\Scripts\pyi-*" -Force -ErrorAction SilentlyContinue
Remove-Item "$PythonDir\Scripts\pyinstaller*" -Force -ErrorAction SilentlyContinue

if (-not $SkipTools) {
  Write-Step "Building gt-machine-code.exe (default icon)..."
  try {
    & $PythonExe -m pip install pyinstaller -q 2>$null
    & $PythonExe -m PyInstaller --noconfirm --onefile --console `
      --name gt-machine-code `
      --distpath "$Root\dist\tools" `
      --workpath "$Root\build\pyi-machine-code" `
      --specpath "$Root\build\pyi-machine-code" `
      --paths "$Root\backend" `
      "$Root\tools\machine_code\get_machine_code.py"
    if (Test-Path "$Root\dist\tools\gt-machine-code.exe") {
      Copy-Item "$Root\dist\tools\gt-machine-code.exe" "$Stage\tools\gt-machine-code.exe"
    }
  } catch {
    Write-Host "   Skipped gt-machine-code.exe: $_" -ForegroundColor Yellow
  }
}

$readmeLines = @(
  "Bicycle Tube App - Native Windows Package (no Docker)",
  "",
  "After install:",
  "  1) Start from Start Menu",
  "  2) Browser opens http://127.0.0.1:5890",
  "  3) Send machine code to vendor for license.dat",
  "  4) Admin login admin / Admin@2027 -> Deployment settings -> upload license",
  "",
  "LAN: http://<server-ip>:5890"
)
$readmeLines | Set-Content "$Stage\README.txt" -Encoding UTF8

if (-not $SkipZip) {
  Write-Step "Creating zip (large, please wait)..."
  $zipPath = Join-Path $Root "dist\$PkgName.zip"
  if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
  Compress-Archive -Path $Stage -DestinationPath $zipPath -CompressionLevel Optimal
  Write-Host "   $zipPath" -ForegroundColor Green
}

if (-not $SkipInno) {
  $iscc = Get-IsccPath
  if (-not (Test-Path (Join-Path $Stage "GiantTubeApp.exe"))) {
    Write-Error "Stage incomplete (missing GiantTubeApp.exe). Run without -SkipVenv or rebuild stage: $Stage"
  }
  if ($iscc) {
    Write-Step "Compiling Inno Setup installer..."
    & $iscc "/DAppVersion=$AppVersion" "/DNativePkgRoot=$Stage" "$Root\deploy\windows\giant_tube_app.iss"
    if ($LASTEXITCODE -eq 0) {
      Write-Host "   dist\GiantTubeAppSetup_$AppVersion.exe" -ForegroundColor Green
    } else {
      Write-Host "   ISCC failed with exit code $LASTEXITCODE" -ForegroundColor Yellow
    }
  } else {
    Write-Host "   Inno Setup 6 not available, skipped .exe installer" -ForegroundColor Yellow
  }
}

Write-Host ""
Write-Host "DONE. Output:" -ForegroundColor Green
Write-Host "  Stage: $Stage"
Write-Host "  Zip:   dist\$PkgName.zip"
Write-Host "  Setup: dist\GiantTubeAppSetup_$AppVersion.exe (if ISCC ran)"

# Manifest: map this build's exe -> git commit + contents, so testers can
# tell which .exe corresponds to which code without installing it first.
$manifest = Join-Path $Root "dist\manifest_$AppVersion.txt"
$exePath = Join-Path $Root "dist\GiantTubeAppSetup_$AppVersion.exe"
$mLines = @(
  "GiantTubeApp build manifest",
  "==========================",
  "Version:     $AppVersion",
  "Git commit:  $GitCommit",
  "Built:       $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
)
if (Test-Path $exePath) {
  $sz = (Get-Item $exePath).Length
  $mLines += "Setup exe:   dist\GiantTubeAppSetup_$AppVersion.exe"
  $mLines += "Setup size:  $([math]::Round($sz/1MB,1)) MB"
}
if (Test-Path $Stage) {
  $stageFiles = @(Get-ChildItem $Stage -Recurse -File -ErrorAction SilentlyContinue)
  $stageBytes = ($stageFiles | Measure-Object Length -Sum).Sum
  $mLines += "Stage:       $Stage"
  $mLines += "Stage files: $($stageFiles.Count)"
  $mLines += "Stage size:  $([math]::Round(($stageBytes/1MB),1)) MB"
}
$mLines | Set-Content $manifest -Encoding UTF8
Write-Host "  Manifest: dist\manifest_$AppVersion.txt" -ForegroundColor Green

# Successful build — safe to drop the previous stage we moved aside at the
# top. (On failure this line is never reached, so $Stage.old survives for
# recovery — that is the "build new, delete old only on success" guarantee.)
$StageOld = "$Stage.old"
if (Test-Path $StageOld) { Remove-Item $StageOld -Recurse -Force -ErrorAction SilentlyContinue }

Stop-Transcript | Out-Null
