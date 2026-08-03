#!/bin/bash

set -e
cd "$(dirname "$0")"

APP_PYTHON=".app-venv/bin/python"

if [ ! -x "$APP_PYTHON" ]; then
  PYTHON_COMMAND=""
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
      PYTHON_COMMAND="$candidate"
      break
    fi
  done

  if [ -z "$PYTHON_COMMAND" ]; then
    echo "[ERROR] Python 3.11 or newer is required."
    echo "Install Python from https://www.python.org/downloads/ and run install.command again."
    exit 1
  fi

  "$PYTHON_COMMAND" -m venv .app-venv
fi

echo "[1/4] Installing Python packages..."
"$APP_PYTHON" -m pip install --upgrade pip
"$APP_PYTHON" -m pip install -r requirements.txt

echo "[2/4] Checking browser for long-image export..."
if [ ! -x "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ] && \
   [ ! -x "$HOME/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ] && \
   [ ! -x "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge" ] && \
   [ ! -x "$HOME/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge" ]; then
  "$APP_PYTHON" -m playwright install chromium
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "[ERROR] Node.js 18 or newer is required to build the React interface."
  echo "Install Node.js from https://nodejs.org/ and run install.command again."
  exit 1
fi

NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || true)"
if [ -z "$NODE_MAJOR" ] || [ "$NODE_MAJOR" -lt 18 ]; then
  echo "[ERROR] Node.js 18 or newer is required to build the React interface."
  exit 1
fi

echo "[3/4] Installing frontend packages..."
npm install --prefix frontend

echo "[4/4] Building frontend..."
npm run build --prefix frontend

if [ ! -f .env ]; then
  cp .env.example .env
fi

echo "Installation complete. Edit .env, then double-click start.command."
