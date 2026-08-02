@echo off
setlocal
cd /d "%~dp0"

set "APP_PYTHON=.app-venv\Scripts\python.exe"
if exist "%APP_PYTHON%" goto install_dependencies

where python >nul 2>nul
if not errorlevel 1 (
  python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
  if not errorlevel 1 (
    python -m venv .app-venv
    goto install_dependencies
  )
)

where py >nul 2>nul
if not errorlevel 1 (
  py -3.11 -m venv .app-venv >nul 2>nul
  if not errorlevel 1 goto install_dependencies
)

echo [ERROR] Python 3.11 or newer is required.
echo Install Python from https://www.python.org/downloads/ and run install.bat again.
exit /b 1

:install_dependencies
echo [1/4] Installing Python packages...
"%APP_PYTHON%" -m pip install --upgrade pip
if errorlevel 1 exit /b 1
"%APP_PYTHON%" -m pip install -r requirements.txt
if errorlevel 1 exit /b 1

echo [2/4] Checking browser for long-image export...
if exist "C:\Program Files\Google\Chrome\Application\chrome.exe" goto browser_ready
if exist "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" goto browser_ready
if exist "C:\Program Files\Microsoft\Edge\Application\msedge.exe" goto browser_ready
if exist "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" goto browser_ready
"%APP_PYTHON%" -m playwright install chromium
if errorlevel 1 exit /b 1
:browser_ready

where npm >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Node.js 18 or newer is required to build the React interface.
  exit /b 1
)

echo [3/4] Installing frontend packages...
call npm install --prefix frontend
if errorlevel 1 exit /b 1

echo [4/4] Building frontend...
call npm run build --prefix frontend
if errorlevel 1 exit /b 1

if not exist .env copy .env.example .env >nul
echo Installation complete. Edit .env, then run start.bat.
