#!/usr/bin/env python3
"""
Double-click / run this to start LMS.

- Reads config.ini for host_mode (localhost vs lan) and port.
- If LMS is already running (a matching lock file + a live process),
  it just opens the browser instead of starting a second instance.
- Opens the default browser straight to the search page.
"""
from __future__ import annotations

import configparser
import socket
import sys
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402

LOCK_FILE = ROOT / ".lms.lock"


def _pid_is_alive(pid: int) -> bool:
    try:
        import os

        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError, PermissionError):
        return False
    except Exception:
        return False


def _already_running() -> bool:
    if not LOCK_FILE.exists():
        return False
    try:
        pid = int(LOCK_FILE.read_text().strip())
    except ValueError:
        return False
    return _pid_is_alive(pid)


def _lan_ip() -> str:
    """Best-effort guess at this machine's LAN IP for the "open browser to" URL."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def _display_url() -> str:
    port = config.get_port()
    if config.get_host_mode() == "lan":
        return f"http://{_lan_ip()}:{port}/"
    return f"http://127.0.0.1:{port}/"


def _prompt_host_mode() -> str:
    """Ask user to choose between localhost and lan mode."""
    current_mode = config.get_host_mode()
    print(f"\nCurrent mode: {current_mode}")
    print("Choose network mode:")
    print("  1. localhost (only accessible from this machine)")
    print("  2. lan      (accessible from any device on your network)")
    
    while True:
        choice = input("\nEnter 1 or 2 (or press Enter for current mode): ").strip()
        
        if not choice:
            return current_mode
        if choice == "1":
            return "localhost"
        if choice == "2":
            return "lan"
        print("Invalid choice. Please enter 1 or 2.")


def _update_config_mode(new_mode: str) -> None:
    """Update config.ini with the selected host_mode."""
    cp = configparser.ConfigParser()
    if Path(ROOT / "config.ini").exists():
        cp.read(ROOT / "config.ini", encoding="utf-8")
    
    if not cp.has_section("server"):
        cp.add_section("server")
    
    cp.set("server", "host_mode", new_mode)
    
    with open(ROOT / "config.ini", "w", encoding="utf-8") as f:
        cp.write(f)


def main() -> None:
    import os

    # Ask user for network mode preference
    selected_mode = _prompt_host_mode()
    current_mode = config.get_host_mode()
    
    if selected_mode != current_mode:
        _update_config_mode(selected_mode)
        print(f"✓ Updated config to {selected_mode} mode")
    
    url = _display_url()

    if _already_running():
        print(f"LMS is already running — opening {url}")
        webbrowser.open(url)
        return

    LOCK_FILE.write_text(str(os.getpid()))
    print(f"Starting LMS ({config.get_host_mode()} mode) on {url}")

    import uvicorn

    try:
        # Open the browser shortly after the server has had time to bind.
        import threading

        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

        uvicorn.run(
            "app.main:app",
            host=config.get_bind_host(),
            port=config.get_port(),
            log_level="info",
        )
    finally:
        if LOCK_FILE.exists():
            LOCK_FILE.unlink()


if __name__ == "__main__":
    main()
