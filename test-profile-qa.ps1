# Profile QA regression only (does not run the full test suite).
# Usage from repo root:  .\test-profile-qa.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendDir = Join-Path $Root "backend"

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
        if ((Test-PythonModule $p "pytest") -and (Test-PythonModule $p "flask")) {
            return (Resolve-Path $p).Path
        }
    }
    return $null
}

$Py = Get-ProjectPython
if (-not $Py) {
    Write-Host "[ERROR] No Python found that has both pytest and flask." -ForegroundColor Red
    Write-Host "Backend Python and Pytest Python must be the same env for this script."
    Write-Host "Minimal fix:  python -m pip install pytest"
    exit 1
}

Write-Host "Backend Python: $Py"
Write-Host "Pytest Python:  $Py"
Set-Location $BackendDir
& $Py -m pytest tests/test_profile_qa.py tests/test_profile_qa_api.py tests/test_profile_qa_real.py tests/test_pp_dev.py tests/test_length_key_section.py -q --tb=short
exit $LASTEXITCODE
