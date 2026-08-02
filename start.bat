@echo off
setlocal
cd /d "%~dp0"
set "SSLKEYLOGFILE="

if not exist ".app-venv\Scripts\python.exe" (
  call install.bat
  if errorlevel 1 exit /b 1
)

if not exist "frontend\dist\index.html" (
  call npm run build --prefix frontend
  if errorlevel 1 exit /b 1
)

start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process 'http://127.0.0.1:8000'"
echo Travel Journal is running at http://127.0.0.1:8000
echo Press Ctrl+C to stop.
".app-venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
