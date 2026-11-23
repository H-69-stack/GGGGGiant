@echo off
chcp 65001 >nul
cd /d "%~dp0"

if exist "giant_tube.pid" (
  set /p PID=<giant_tube.pid
  taskkill /F /PID %PID% >nul 2>&1
  del /f /q giant_tube.pid >nul 2>&1
  echo 已停止服务 (PID %PID%)
) else (
  echo 未找到运行中的服务 (giant_tube.pid)
)

REM 只有在传入 "wait" 参数时才停下来等用户看输出；卸载和开始菜单双击都直接退出，
REM 避免旧版本因为 pause 卡住 UninstallRun (runhidden + waituntilterminated) 造成卸载假死。
if /I "%1"=="wait" pause
