#!/usr/bin/env bash
# One-click launcher for macOS/Linux.
set -e
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "Setting up virtual environment (first run only)..."
  python3 -m venv .venv
  ./.venv/bin/pip install --quiet -r requirements.txt
fi

./.venv/bin/python launcher.py