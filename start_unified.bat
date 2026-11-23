@echo off
setlocal
cd /d "%~dp0"

rem Giant-Unified launcher: forwards all arguments to start_unified.ps1.
rem ASCII only on purpose (cmd codepage safety). Use CRLF line endings.

rem Console to UTF-8 so Chinese output from PowerShell/Python is not garbled.
chcp 65001 >nul 2>nul
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

set "PS=powershell"
where pwsh >nul 2>nul
if not errorlevel 1 set "PS=pwsh"

"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_unified.ps1" %*
if errorlevel 1 pause
