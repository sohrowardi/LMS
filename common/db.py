"""
Shared, tiny module used by BOTH the indexer and the search app.

Important: this module is the ONLY place that knows how to open the
database. The search app calls get_connection(readonly=True), which
opens SQLite in read-only mode at the OS/driver level — not just "we
promise not to write." A bug or a malicious request in the search app
literally cannot issue a write; SQLite will raise an error first.
"""

import sqlite3
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "lms.db"
SERVERS_FILE = DATA_DIR / "servers.txt"
SCHEMA_FILE = DATA_DIR / "schema.sql"


def get_connection(readonly: bool = True) -> sqlite3.Connection:
    """
    Open the shared database.

    readonly=True  -> used by the search app. Opens via a `file:` URI
                       with mode=ro, so SQLite refuses any write attempt.
    readonly=False -> used ONLY by the indexer, to create/update data.
    """
    if readonly:
        uri = f"file:{DB_PATH}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create the database + tables if they don't exist yet. Indexer-only."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    with open(SCHEMA_FILE, "r") as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()


def parse_servers_file() -> list[dict]:
    """
    Read data/servers.txt and return a list of
    {"name": ..., "url": ..., "position": ...} in file order.

    Format per line:  Name | Base URL
    Blank lines and lines starting with # are ignored.
    """
    servers = []
    if not SERVERS_FILE.exists():
        return servers

    position = 0
    with open(SERVERS_FILE, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "|" not in line:
                raise ValueError(
                    f"servers.txt line is malformed (expected 'Name | URL'): {line!r}"
                )
            name, url = line.split("|", 1)
            name = name.strip()
            url = url.strip()
            if not name or not url:
                raise ValueError(f"servers.txt line missing name or url: {line!r}")
            servers.append({"name": name, "url": url, "position": position})
            position += 1

    return servers
