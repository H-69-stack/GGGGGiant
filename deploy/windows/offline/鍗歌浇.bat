@echo off
cd /d "%~dp0"
docker compose down
echo Done. Data and license are preserved.
pause
