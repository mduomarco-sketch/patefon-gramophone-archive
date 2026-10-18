@echo off
rem Russian Records dashboard launcher - double-click to start.
rem Serves http://127.0.0.1:8000 (this computer only). Close this window to stop it.
title Russian Records Dashboard
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

if not exist ".venv\Scripts\python.exe" (
  echo [error] The Python environment .venv was not found in:
  echo         %~dp0
  echo Create it once with:
  echo   python -m venv .venv
  echo   .venv\Scripts\python -m pip install -r requirements.txt
  echo.
  pause
  exit /b 1
)

netstat -ano | findstr /R /C:"127.0.0.1:8000 .*LISTENING" >nul
if not errorlevel 1 (
  echo The dashboard already appears to be running - opening it in your browser.
  start "" "http://127.0.0.1:8000"
  timeout /t 3 >nul
  exit /b 0
)

rem Open the browser a moment after the server starts.
start "" /b cmd /c "timeout /t 3 /nobreak >nul & start "" http://127.0.0.1:8000"
".venv\Scripts\python.exe" dashboard.py --port 8000
echo.
echo The dashboard has stopped.
pause
