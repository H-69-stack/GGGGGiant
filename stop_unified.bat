@echo off
setlocal
cd /d "%~dp0"
rem Giant-Unified stop helper. ASCII only; CRLF line endings required.

chcp 65001 >nul 2>nul

set "PS=powershell"
where pwsh >nul 2>nul
if not errorlevel 1 set "PS=pwsh"

"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_unified.ps1" %*
if errorlevel 1 pause
