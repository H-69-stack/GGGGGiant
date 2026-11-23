@echo off
rem Start the default 1 mm measurement instance on port 5890.
rem Settings come from .env (APP_PORT=5890, GTAPP_LICENSE_GATE=0, GTAPP_OPEN_BROWSER=0).
cd /d D:\AAAGNT-1\Giant-Unified
"D:\python\envs\occ\Scripts\python.exe" backend\run_release.py >> "D:\AAAGNT-1\.tmp\launch_1mm.log" 2>&1
echo exit=%ERRORLEVEL% >> "D:\AAAGNT-1\.tmp\launch_1mm.log"
