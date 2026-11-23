<#
  Clean dist/ of stale / broken build artifacts.

  - Removes every *.BROKEN_KEEP* marker (those should not ship).
  - Removes truncated/corrupt GiantTubeAppSetup_*.zip duplicates (< 1 MB).
  - Keeps only the 3 newest setup exes; older ones are moved to
    dist\archive\ (or deleted with -RemoveOld) so dist/ stops growing
    without losing recoverable history.

  Usage: powershell -ExecutionPolicy Bypass -File deploy\clean_dist.ps1 [-RemoveOld]
#>
param([switch]$RemoveOld)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path "$PSScriptRoot\..").Path
$Dist = Join-Path $Root "dist"
if (-not (Test-Path $Dist)) { Write-Host "dist/ not found; nothing to clean"; exit 0 }

# 1) Always strip broken-keep markers and stray duplicate / truncated zips.
Get-ChildItem $Dist -Filter "*.BROKEN_KEEP*" -File -ErrorAction SilentlyContinue |
  ForEach-Object { Write-Host "  removing $($_.Name)"; Remove-Item $_.FullName -Force }

Get-ChildItem $Dist -Filter "GiantTubeAppSetup_*.zip" -File -ErrorAction SilentlyContinue |
  Where-Object { $_.Length -lt 1MB } |
  ForEach-Object { Write-Host "  removing truncated zip $($_.Name)"; Remove-Item $_.FullName -Force }

# 2) Keep only the 3 newest setup exes.
$exes = @(Get-ChildItem $Dist -Filter "GiantTubeAppSetup_*.exe" -File |
  Where-Object { $_.Name -notlike "*.BROKEN_KEEP*" } |
  Sort-Object LastWriteTime -Descending)
if ($exes.Count -gt 3) {
  $stale = $exes | Select-Object -Skip 3
  if ($RemoveOld) {
    $stale | ForEach-Object { Write-Host "  deleting old $($_.Name)"; Remove-Item $_.FullName -Force }
  } else {
    $archive = Join-Path $Dist "archive"
    New-Item -ItemType Directory -Force -Path $archive | Out-Null
    $stale | ForEach-Object { Write-Host "  archiving $($_.Name)"; Move-Item $_.FullName -Force -Destination $archive }
  }
}

Write-Host "dist/ cleaned." -ForegroundColor Green
