@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "GiantTubeApp.exe" (
  start "" "GiantTubeApp.exe"
) else (
  call start.bat
)
