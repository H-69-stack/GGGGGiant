@echo off
rem Start the 3 mm measurement instance on port 5891 (detached via WMI).
setlocal
set APP_PORT=5891
set GTAPP_STATION_MM=3
set GTAPP_STATION_GRID=1
set GTAPP_LICENSE_GATE=0
set GTAPP_OPEN_BROWSER=0
set ENTITY_DEV_PORT=5061
set PIPESOLID_BRIDGE_PORT=18767
set ENTITY_OUT=D:\AAAGNT-1\Giant-Unified\entity_out
set FREECAD_HOME=D:\Freecad
cd /d D:\AAAGNT-1\Giant-Unified
"D:\python\envs\occ\Scripts\python.exe" backend\run_release.py >> "D:\AAAGNT-1\.tmp\launch_3mm.log" 2>&1
echo exit=%ERRORLEVEL% >> "D:\AAAGNT-1\.tmp\launch_3mm.log"
