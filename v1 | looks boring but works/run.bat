@echo off
REM One-click launcher for Windows.
cd /d "%~dp0"

if not exist ".venv" (
    echo Setting up virtual environment ^(first run only^)...
    python -m venv .venv
    .venv\Scripts\pip install --quiet -r requirements.txt
)

.venv\Scripts\python launcher.py
