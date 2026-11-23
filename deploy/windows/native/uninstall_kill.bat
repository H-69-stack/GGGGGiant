@echo off
REM 卸载专用 —— 由 Inno Setup 的 [UninstallRun] 静默调用。
REM 必须保证：不询问、不 pause、5 秒内退出，且能把安装目录下所有占用文件的进程杀光。

setlocal
cd /d "%~dp0"

REM 1) 先按 pid 文件优雅关一次
if exist "giant_tube.pid" (
  for /f "usebackq delims=" %%p in ("giant_tube.pid") do (
    taskkill /F /PID %%p >nul 2>&1
  )
  del /f /q giant_tube.pid >nul 2>&1
)

REM 2) 兜底：杀掉安装目录下的 launcher 与 Python，防止文件被占用导致卸载卡住
taskkill /F /IM GiantTubeApp.exe >nul 2>&1

REM 用 wmic 精准匹配从本安装目录启动的 python(w).exe（用 %~dp0 转成过滤字符串）
set "INST=%~dp0"
for /f "tokens=2 delims==" %%i in ('wmic process where "ExecutablePath like '%INST:\=\\%%%%'" get ProcessId /format:value 2^>nul ^| find "="') do (
  taskkill /F /PID %%i >nul 2>&1
)

exit /b 0
