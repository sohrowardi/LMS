#!/usr/bin/env python3
"""
Run the LMS search site.

Reads search_config.txt for whether to bind to localhost-only or the
whole home network, starts the server, and opens your browser.
This script only ever starts the read-only Search App — it has no
way to trigger or affect scanning (that's run_indexer.py, separately).
"""

import re
import socket
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

CONFIG_FILE = Path(__file__).resolve().parent / "search_config.txt"


def load_config() -> dict:
    config = {"access": "local", "port": 8000}
    if CONFIG_FILE.exists():
        for line in CONFIG_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r"(\w+)\s*=\s*(.+)", line)
            if m:
                key, value = m.group(1), m.group(2).strip()
                config[key] = int(value) if key == "port" else value
    return config


def open_browser_when_ready(host: str, port: int):
    display_host = "127.0.0.1" if host == "0.0.0.0" else host
    url = f"http://{display_host}:{port}/"
    for _ in range(50):
        try:
            with socket.create_connection((display_host, port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.1)
    webbrowser.open(url)


def main():
    config = load_config()
    access = config.get("access", "local").lower()
    port = config.get("port", 8000)

    host = "0.0.0.0" if access == "network" else "127.0.0.1"

    print(f"Starting LMS search site — access: {access} — port: {port}")
    if access == "network":
        print("Reachable from any device on your home network.")
    else:
        print("Reachable only from this computer.")

    threading.Thread(target=open_browser_when_ready, args=(host, port), daemon=True).start()
    uvicorn.run("search_app.main:app", host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
