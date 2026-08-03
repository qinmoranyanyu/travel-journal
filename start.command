#!/bin/bash

set -e
cd "$(dirname "$0")"
unset SSLKEYLOGFILE

if [ ! -x ".app-venv/bin/python" ]; then
  ./install.command
fi

if [ ! -f "frontend/dist/index.html" ]; then
  npm run build --prefix frontend
fi

(sleep 2 && open "http://127.0.0.1:8000") &
echo "Travel Journal is running at http://127.0.0.1:8000"
echo "Press Ctrl+C to stop."
exec .app-venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
