@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if exist ".env" (
  for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
    if not "%%a"=="" if not "%%a:~0,1%"=="#" set "%%a=%%b"
  )
)

if not defined APP_PORT set APP_PORT=5890
if not defined GTAPP_LICENSE_PATH set GTAPP_LICENSE_PATH=%~dp0license\license.dat
if not defined GTAPP_FRONTEND_DIST set GTAPP_FRONTEND_DIST=%~dp0frontend\dist
if not defined GTAPP_BIND_SERVER_MACHINE set GTAPP_BIND_SERVER_MACHINE=1
if not defined GTAPP_OPEN_BROWSER set GTAPP_OPEN_BROWSER=1

set PYTHONPATH=%~dp0backend
set PYTHONNOUSERSITE=1
set "PATH=%~dp0python;%~dp0python\Library\bin;%PATH%"

set "PYEXE=%~dp0python\python.exe"
if not exist "%PYEXE%" set "PYEXE=%~dp0python\pythonw.exe"
if not exist "%PYEXE%" set "PYEXE=%~dp0python\Scripts\python.exe"

if not exist "%PYEXE%" (
  echo [ERROR] 未找到 Python 运行环境：%~dp0python\python.exe
  echo 请重新安装本软件。
  pause
  exit /b 1
)

"%PYEXE%" "%~dp0backend\run_release.py"
if errorlevel 1 pause
endlocal
