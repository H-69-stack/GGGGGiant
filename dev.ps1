# Giant Tube local development launcher (Flask 5060 + Vite 3001，与旧程序 5050/3000 错开).
# Usage from repo root:  .\dev.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendDir = Join-Path $Root "backend"
$FrontendDir = Join-Path $Root "frontend"
$BackendPort = 5060
$FrontendPort = 3001

function Write-Err($msg) { Write-Host "[ERROR] $msg" -ForegroundColor Red }
function Write-Info($msg) { Write-Host "[INFO]  $msg" -ForegroundColor Cyan }

function Test-TcpPort([int]$Port) {
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $iar = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(200, $false)
        if ($ok -and $client.Connected) { $client.Close(); return $true }
        $client.Close()
        return $false
    } catch {
        return $false
    }
}

function Get-ListenPid([int]$Port) {
    try {
        $c = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($c) { return $c.OwningProcess }
    } catch {}
    return $null
}

function Test-PythonModule([string]$Py, [string]$Mod) {
    & $Py -c "import $Mod" 2>$null
    return ($LASTEXITCODE -eq 0)
}

function Get-ProjectPython {
    $candidates = @()
    if ($env:GTAPP_PYTHON) { $candidates += $env:GTAPP_PYTHON }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $candidates += $cmd.Source }
    if ($env:CONDA_PREFIX) { $candidates += (Join-Path $env:CONDA_PREFIX "python.exe") }
    $candidates += @(
        "D:\python\envs\occ\python.exe",
        "D:\Anaconda\python.exe"
    )
    foreach ($p in $candidates) {
        if (-not $p -or -not (Test-Path $p)) { continue }
        if (Test-PythonModule $p "flask") { return (Resolve-Path $p).Path }
    }
    return $null
}

Write-Host "========================================"
Write-Host "Giant Tube Development Environment"
Write-Host "========================================"

$Py = Get-ProjectPython
if (-not $Py) {
    Write-Err "Python not found, or none of the candidates can import flask."
    Write-Host "Set GTAPP_PYTHON to a working interpreter, e.g.:"
    Write-Host "  `$env:GTAPP_PYTHON = 'D:\python\envs\occ\python.exe'"
    exit 1
}

$pyVer = & $Py -c "import sys; print(sys.version.split()[0])"
Write-Info "Backend Python: $Py ($pyVer)"

$pytestPy = "missing"
if (Test-PythonModule $Py "pytest") {
    $pytestPy = $Py
} else {
    Write-Host "[WARN] This Python has no pytest. .\test-profile-qa.ps1 will fail until pytest is installed in this env."
}
Write-Info "Pytest Python: $pytestPy"

Write-Host "FastAPI/Uvicorn: not used. Backend entry is Flask backend\run_dev.py (create_app + socketio)."
if (-not (Test-PythonModule $Py "flask")) {
    Write-Err "Backend dependency missing: flask"
    exit 1
}

$node = Get-Command node -ErrorAction SilentlyContinue
$npm = Get-Command npm -ErrorAction SilentlyContinue
if (-not $node) { Write-Err "Node.js not found (node)."; exit 1 }
if (-not $npm) { Write-Err "npm not found."; exit 1 }
Write-Info "Node: $((& node -v))   npm: $((& npm -v))"

if (-not (Test-Path (Join-Path $FrontendDir "node_modules"))) {
    Write-Err "frontend\node_modules missing. Run:  cd frontend; npm install"
    exit 1
}

if (Test-TcpPort $BackendPort) {
    $pidListen = Get-ListenPid $BackendPort
    Write-Err "Backend port $BackendPort is already in use."
    if ($pidListen) { Write-Host "Possible existing backend: PID $pidListen" }
    Write-Host "Please stop the existing process or reuse it. This script will not kill it."
    exit 1
}
if (Test-TcpPort $FrontendPort) {
    $pidListen = Get-ListenPid $FrontendPort
    Write-Err "Frontend port $FrontendPort is already in use."
    if ($pidListen) { Write-Host "Possible existing frontend: PID $pidListen" }
    Write-Host "Please stop the existing process or reuse it. This script will not kill it."
    exit 1
}

if (-not (Test-PythonModule $Py "OCP")) {
    Write-Host "[WARN] cadquery-ocp (import OCP) missing in this Python. STP reading will fail until it is installed."
}

Write-Info "Starting backend (reload ON) ..."
$backendCmd = @"
Set-Location '$BackendDir'
Write-Host 'Backend Python: $Py'
& '$Py' run_dev.py
if (`$LASTEXITCODE -ne 0) { Write-Host 'backend exited' -ForegroundColor Red; pause }
"@
$backend = Start-Process -FilePath "powershell" -ArgumentList @("-NoExit", "-Command", $backendCmd) -PassThru -WindowStyle Normal

Write-Info "Starting frontend (Vite HMR ON) ..."
$frontendCmd = @"
Set-Location '$FrontendDir'
npm run dev
if (`$LASTEXITCODE -ne 0) { Write-Host 'frontend exited' -ForegroundColor Red; pause }
"@
$frontend = Start-Process -FilePath "powershell" -ArgumentList @("-NoExit", "-Command", $frontendCmd) -PassThru -WindowStyle Normal

$ready = $false
for ($i = 0; $i -lt 45; $i++) {
    if ($backend.HasExited) {
        Write-Err "backend failed to start. See the backend window."
        exit 1
    }
    try {
        Invoke-WebRequest -Uri "http://127.0.0.1:$BackendPort/api/version" -UseBasicParsing -TimeoutSec 2 | Out-Null
        $ready = $true
        break
    } catch {
        Start-Sleep -Seconds 1
    }
}
if (-not $ready) {
    Write-Err "backend did not become ready on port $BackendPort."
    exit 1
}

Write-Host ""
Write-Host "========================================"
Write-Host "Giant Tube Development Environment"
Write-Host ""
Write-Host "Backend:"
Write-Host "http://127.0.0.1:$BackendPort"
Write-Host ""
Write-Host "Frontend:"
Write-Host "http://localhost:$FrontendPort"
Write-Host ""
Write-Host "Backend Python:"
Write-Host $Py
Write-Host "Pytest Python:"
Write-Host $pytestPy
Write-Host ""
Write-Host "Backend reload: ON"
Write-Host "Frontend HMR: ON"
Write-Host ""
Write-Host "Press Ctrl+C in THIS window to stop the two DEV processes we started."
Write-Host "========================================"

try {
    while ($true) {
        if ($backend.HasExited) { Write-Err "backend window exited."; break }
        if ($frontend.HasExited) { Write-Err "frontend window exited."; break }
        Start-Sleep -Seconds 2
    }
} finally {
    foreach ($proc in @($backend, $frontend)) {
        if ($proc -and -not $proc.HasExited) {
            Stop-Process -Id $proc.Id -ErrorAction SilentlyContinue
        }
    }
}
