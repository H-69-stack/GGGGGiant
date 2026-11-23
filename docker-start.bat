@echo off
setlocal
cd /d "%~dp0"

where docker >nul 2>nul
if errorlevel 1 (
  echo 未检测到 Docker，请先安装 Docker Desktop。
  exit /b 1
)

docker compose version >nul 2>nul
if errorlevel 1 (
  echo 未检测到 Docker Compose。请更新 Docker Desktop 到最新版。
  exit /b 1
)

if "%APP_PORT%"=="" set APP_PORT=5890

set CMD=%1
if "%CMD%"=="" set CMD=start

if "%CMD%"=="start" (
  docker compose up -d --build
  echo 已启动：http://127.0.0.1:%APP_PORT%
  exit /b %errorlevel%
)

if "%CMD%"=="stop" (
  docker compose down
  exit /b %errorlevel%
)

if "%CMD%"=="restart" (
  docker compose down
  docker compose up -d --build
  echo 已重启：http://127.0.0.1:%APP_PORT%
  exit /b %errorlevel%
)

if "%CMD%"=="logs" (
  docker compose logs -f --tail=200
  exit /b %errorlevel%
)

if "%CMD%"=="status" (
  docker compose ps
  exit /b %errorlevel%
)

echo Usage: docker-start.bat {start^|stop^|restart^|logs^|status}
exit /b 1
