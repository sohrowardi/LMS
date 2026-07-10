#!/usr/bin/env bash
# One-click launcher for macOS/Linux.
set -e
cd "$(dirname "$0")"

# Try to install packages if they're missing
python3 launcher.py
