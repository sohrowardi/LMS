#!/usr/bin/env python3
"""
Run the LMS search site.

Prompts at startup for localhost-only vs LAN access (defaulting to
whatever search_config.txt currently says), starts the server, and
opens your browser. This script only ever starts the read-only Search
App — it has no way to trigger or affect scanning (that's
run_indexer.py, separately).
"""

import re
import socket
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

CONFIG_FILE = Path(__file__).resolve().parent / "search_config.txt"

# internal config value <-> prompt label
_MODE_LABELS = {"local": "localhost", "network": "lan"}


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


def save_access_mode(access: str) -> None:
    """Persist the chosen mode back to search_config.txt so next time's
    default reflects what was actually run last."""
    if not CONFIG_FILE.exists():
        return
    lines = CONFIG_FILE.read_text().splitlines()
    out = []
    replaced = False
    for line in lines:
        if re.match(r"\s*access\s*=", line):
            out.append(f"access = {access}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"access = {access}")
    CONFIG_FILE.write_text("\n".join(out) + "\n")


def prompt_for_mode(current_access: str) -> str:
    """Interactive terminal prompt. Returns 'local' or 'network'."""
    current_label = _MODE_LABELS.get(current_access, "lan")
    print(f"Current mode: {current_label}")
    print("Choose network mode:")
    print("  1. localhost (only accessible from this machine)")
    print("  2. lan       (accessible from any device on your network)")
    choice = input("Enter 1 or 2 (or press Enter for current mode): ").strip()

    if choice == "1":
        return "local"
    if choice == "2":
        return "network"
    if choice == "":
        return current_access
    print(f"Didn't recognize '{choice}' — keeping current mode ({current_label}).")
    return current_access


def get_local_ip() -> str:
    """Best-effort detection of this machine's LAN-facing IP address.
    Doesn't actually send any data — just asks the OS which interface
    would be used to reach an external address."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def open_browser_when_ready(display_host: str, port: int):
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
    port = config.get("port", 8000)

    access = prompt_for_mode(config.get("access", "local"))
    save_access_mode(access)

    host = "0.0.0.0" if access == "network" else "127.0.0.1"

    print()
    if access == "network":
        local_ip = get_local_ip()
        print(f"Starting LMS search site — LAN mode — port {port}")
        print(f"Accessible from any device on your network at: http://{local_ip}:{port}/")
        display_host = local_ip
    else:
        print(f"Starting LMS search site — localhost mode — port {port}")
        print("Accessible only from this computer.")
        display_host = "127.0.0.1"

    threading.Thread(
        target=open_browser_when_ready, args=(display_host, port), daemon=True
    ).start()
    uvicorn.run("search_app.main:app", host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
