#!/bin/bash
# start.sh
# One-click launcher for Media Search.
# - Starts the backend server in the background if it isn't already running.
# - Opens your default browser to the search page.
# Safe to double-click / run multiple times - it won't start duplicate servers.

cd "$(dirname "$0")"

PORT=8000
URL="http://localhost:$PORT"
PIDFILE="media-search.pid"

is_running() {
    if [ -f "$PIDFILE" ]; then
        PID="$(cat "$PIDFILE")"
        if kill -0 "$PID" 2>/dev/null; then
            return 0
        else
            rm -f "$PIDFILE"   # stale pidfile from a closed/killed server
        fi
    fi
    return 1
}

if is_running; then
    echo "Media Search is already running."
else
    echo "Starting Media Search server..."
    nohup uvicorn app:app --host 0.0.0.0 --port "$PORT" > server.log 2>&1 &
    echo $! > "$PIDFILE"
    # give it a moment to boot before opening the browser
    sleep 2
fi

# Open the default browser (works across common Linux desktops)
if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$URL" >/dev/null 2>&1 &
elif command -v gio >/dev/null 2>&1; then
    gio open "$URL" >/dev/null 2>&1 &
else
    echo "Open this URL in your browser: $URL"
fi
