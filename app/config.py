"""
Config loading for LMS.

Everything a user might want to tweak without touching code lives in
config.ini (network mode / port / crawler tuning) or servers.txt (the
server list). This module is intentionally tiny and re-reads the file
from disk on every access, so editing config.ini and restarting picks
up changes with no code changes required.
"""
from __future__ import annotations

import configparser
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT_DIR / "config.ini"
SERVERS_PATH = ROOT_DIR / "servers.txt"
DB_PATH = ROOT_DIR / "lms.sqlite3"

_DEFAULTS = {
    "server": {"host_mode": "localhost", "port": "8000"},
    "crawler": {
        "concurrency_per_server": "5",
        "request_timeout": "15",
        "max_depth": "15",
    },
}


def _load() -> configparser.ConfigParser:
    cp = configparser.ConfigParser()
    cp.read_dict(_DEFAULTS)
    if CONFIG_PATH.exists():
        cp.read(CONFIG_PATH, encoding="utf-8")
    return cp


def get_host_mode() -> str:
    """'localhost' or 'lan'."""
    mode = _load().get("server", "host_mode", fallback="localhost").strip().lower()
    return "lan" if mode in ("lan", "0.0.0.0", "network") else "localhost"


def get_bind_host() -> str:
    return "0.0.0.0" if get_host_mode() == "lan" else "127.0.0.1"


def get_port() -> int:
    try:
        return _load().getint("server", "port", fallback=8000)
    except ValueError:
        return 8000


def get_concurrency_per_server() -> int:
    return _load().getint("crawler", "concurrency_per_server", fallback=5)


def get_request_timeout() -> float:
    return _load().getfloat("crawler", "request_timeout", fallback=15.0)


def get_max_depth() -> int:
    return _load().getint("crawler", "max_depth", fallback=15)


def read_servers() -> list[dict]:
    """
    Parse servers.txt into an ordered list of
    {"order": int, "name": str, "url": str}.

    Accepts either:
        http://host/path/
        Friendly Name | http://host/path/
    Comments (#) and blank lines are ignored. File order == display order.
    """
    servers: list[dict] = []
    if not SERVERS_PATH.exists():
        return servers

    with SERVERS_PATH.open("r", encoding="utf-8") as f:
        order = 0
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "|" in line:
                name, url = line.split("|", 1)
                name, url = name.strip(), url.strip()
            else:
                url = line
                name = _derive_name(url)
            if not url:
                continue
            if not url.endswith("/"):
                url += "/"
            servers.append({"order": order, "name": name or url, "url": url})
            order += 1
    return servers


def _derive_name(url: str) -> str:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = parsed.netloc or parsed.path
    path = parsed.path.strip("/")
    if path:
        return f"{host} / {path}"
    return host
