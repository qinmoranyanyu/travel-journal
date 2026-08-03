#!/bin/bash

set -e
cd "$(dirname "$0")"
unset SSLKEYLOGFILE

if [ ! -x ".app-venv/bin/python" ]; then
  ./install.command
fi

.app-venv/bin/python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 &
API_PID=$!

cleanup() {
  kill "$API_PID" 2>/dev/null || true
  wait "$API_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

npm run dev --prefix frontend
