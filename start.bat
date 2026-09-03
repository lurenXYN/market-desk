@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto :install

where py >nul 2>&1
if %errorlevel%==0 (
  echo Creating venv with py launcher...
  py -3 -m venv .venv
  goto :install
)

if exist "%LocalAppData%\Programs\Python\Python312\python.exe" (
  echo Creating venv with Python 3.12...
  "%LocalAppData%\Programs\Python\Python312\python.exe" -m venv .venv
  goto :install
)

if exist "%LocalAppData%\Programs\Python\Python311\python.exe" (
  echo Creating venv with Python 3.11...
  "%LocalAppData%\Programs\Python\Python311\python.exe" -m venv .venv
  goto :install
)

where python >nul 2>&1
if %errorlevel%==0 (
  echo Creating venv with python...
  python -m venv .venv
  goto :install
)

echo Python 3 not found. Install from https://www.python.org/downloads/
pause
exit /b 1

:install
if not exist ".venv\Scripts\python.exe" (
  echo Failed to create .venv
  pause
  exit /b 1
)

echo Installing dependencies...
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if errorlevel 1 (
  echo pip install failed
  pause
  exit /b 1
)

echo Freeing port 8765 if occupied...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8765" ^| findstr "LISTENING"') do (
  echo Stopping old PID %%p
  taskkill /PID %%p /F >nul 2>&1
)
timeout /t 1 /nobreak >nul

echo Starting http://127.0.0.1:8765/
".venv\Scripts\python.exe" -m market_desk
pause