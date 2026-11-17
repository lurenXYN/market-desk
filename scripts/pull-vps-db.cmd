@echo off
REM Daily pull of the VPS desk.db snapshot (see .github/workflows/vps-db-snapshot.yml).
REM Scheduled by Windows Task Scheduler; keeps the newest 30 local snapshots.
setlocal
set "REPO=D:\Source\Repos\market-desk"
set "PATH=C:\Program Files\GitHub CLI;%PATH%"
cd /d "%REPO%" || exit /b 1
if not exist "data\vps-db" mkdir "data\vps-db"
".venv\Scripts\python.exe" "scripts\pull_vps_db.py" --keep-days 30 >> "data\vps-db\pull.log" 2>&1
exit /b %ERRORLEVEL%
