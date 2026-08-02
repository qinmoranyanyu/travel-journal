@echo off
setlocal
cd /d "%~dp0"
set "SSLKEYLOGFILE="

if not exist ".app-venv\Scripts\python.exe" (
  call install.bat
  if errorlevel 1 exit /b 1
)

start "Travel Journal API" cmd /k ".app-venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000"
call npm run dev --prefix frontend
