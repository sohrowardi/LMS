"""
database.py
SQLite schema + helper functions for the Local Media Search Engine.

Uses SQLite FTS5 (full text search) for fast, fuzzy-ish keyword search
over potentially hundreds of thousands of indexed file/folder names.
"""

import sqlite3
import time
import threading
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).parent / "media_index.db"

# A single re-entrant lock guards writes since aiosqlite isn't used here;
# the crawler runs in a background thread and writes in batches.
_write_lock = threading.Lock()


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


@contextmanager
def get_cursor(commit: bool = False):
    conn = get_connection()
    try:
        cur = conn.cursor()
        yield cur
        if commit:
            conn.commit()
    finally:
        conn.close()


def init_db():
    """Create tables/indices/FTS virtual table if they don't already exist."""
    with get_cursor(commit=True) as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS files (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                server_name TEXT NOT NULL,
                server_base TEXT NOT NULL,
                name        TEXT NOT NULL,
                url         TEXT NOT NULL UNIQUE,
                parent_url  TEXT,
                is_dir      INTEGER NOT NULL DEFAULT 0,
                ext         TEXT,
                size_bytes  INTEGER,
                depth       INTEGER DEFAULT 0,
                indexed_at  REAL
            );
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_files_name ON files(name);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_files_server ON files(server_name);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_files_isdir ON files(is_dir);")

        # Full text search index (porter stemming + unicode61 tokenizer)
        cur.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(
                name,
                content='files',
                content_rowid='id',
                tokenize='unicode61 remove_diacritics 2'
            );
            """
        )

        # Triggers to keep FTS table in sync automatically
        cur.execute(
            """
            CREATE TRIGGER IF NOT EXISTS files_ai AFTER INSERT ON files BEGIN
                INSERT INTO files_fts(rowid, name) VALUES (new.id, new.name);
            END;
            """
        )
        cur.execute(
            """
            CREATE TRIGGER IF NOT EXISTS files_ad AFTER DELETE ON files BEGIN
                INSERT INTO files_fts(files_fts, rowid, name) VALUES('delete', old.id, old.name);
            END;
            """
        )
        cur.execute(
            """
            CREATE TRIGGER IF NOT EXISTS files_au AFTER UPDATE ON files BEGIN
                INSERT INTO files_fts(files_fts, rowid, name) VALUES('delete', old.id, old.name);
                INSERT INTO files_fts(rowid, name) VALUES (new.id, new.name);
            END;
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS crawl_log (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                server_name  TEXT NOT NULL,
                status       TEXT NOT NULL,   -- pending|running|done|error
                pages_visited INTEGER DEFAULT 0,
                files_found  INTEGER DEFAULT 0,
                started_at   REAL,
                finished_at  REAL,
                last_error   TEXT
            );
            """
        )


def clear_server_data(server_name: str):
    """Wipe previously indexed rows for one server before a fresh re-crawl."""
    with _write_lock, get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM files WHERE server_name = ?", (server_name,))


def insert_files_batch(rows: list[dict]):
    """
    rows: list of dicts with keys:
      server_name, server_base, name, url, parent_url, is_dir, ext, depth
    Uses INSERT OR IGNORE on the unique `url` column to dedupe automatically.
    """
    if not rows:
        return
    with _write_lock, get_cursor(commit=True) as cur:
        cur.executemany(
            """
            INSERT OR IGNORE INTO files
                (server_name, server_base, name, url, parent_url, is_dir, ext, depth, indexed_at)
            VALUES (:server_name, :server_base, :name, :url, :parent_url, :is_dir, :ext, :depth, :indexed_at)
            """,
            [{**r, "indexed_at": time.time()} for r in rows],
        )


def search_files(query: str, limit: int = 200, only_files: bool = False) -> list[sqlite3.Row]:
    """
    Full text search via FTS5. Falls back to a LIKE search if the FTS query
    syntax fails (e.g. user typed special characters like '+' or '"').
    """
    query = query.strip()
    if not query:
        return []

    fts_query = " ".join(f'"{tok}"*' for tok in query.split())  # prefix match per token

    sql = """
        SELECT f.id, f.server_name, f.name, f.url, f.is_dir, f.ext, f.depth
        FROM files_fts
        JOIN files f ON f.id = files_fts.rowid
        WHERE files_fts MATCH ?
        {filter}
        ORDER BY f.is_dir ASC, length(f.name) ASC
        LIMIT ?
    """.format(filter="AND f.is_dir = 0" if only_files else "")

    try:
        with get_cursor() as cur:
            cur.execute(sql, (fts_query, limit))
            return cur.fetchall()
    except sqlite3.OperationalError:
        # Fallback: plain LIKE search (slower, but always works)
        like = f"%{query}%"
        sql2 = """
            SELECT id, server_name, name, url, is_dir, ext, depth
            FROM files
            WHERE name LIKE ?
            {filter}
            ORDER BY is_dir ASC, length(name) ASC
            LIMIT ?
        """.format(filter="AND is_dir = 0" if only_files else "")
        with get_cursor() as cur:
            cur.execute(sql2, (like, limit))
            return cur.fetchall()


def get_stats() -> dict:
    with get_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS c FROM files")
        total = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) AS c FROM files WHERE is_dir = 0")
        total_files = cur.fetchone()["c"]
        cur.execute(
            "SELECT server_name, COUNT(*) AS c FROM files GROUP BY server_name ORDER BY server_name"
        )
        per_server = {r["server_name"]: r["c"] for r in cur.fetchall()}
    return {"total_entries": total, "total_files": total_files, "per_server": per_server}


def start_crawl_log(server_name: str) -> int:
    with _write_lock, get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO crawl_log (server_name, status, started_at) VALUES (?, 'running', ?)",
            (server_name, time.time()),
        )
        return cur.lastrowid


def finish_crawl_log(log_id: int, pages: int, files: int, status: str = "done", error: str = None):
    with _write_lock, get_cursor(commit=True) as cur:
        cur.execute(
            """
            UPDATE crawl_log
            SET status = ?, pages_visited = ?, files_found = ?, finished_at = ?, last_error = ?
            WHERE id = ?
            """,
            (status, pages, files, time.time(), error, log_id),
        )


def get_latest_crawl_status() -> list[sqlite3.Row]:
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT cl.* FROM crawl_log cl
            INNER JOIN (
                SELECT server_name, MAX(id) AS max_id FROM crawl_log GROUP BY server_name
            ) latest ON cl.server_name = latest.server_name AND cl.id = latest.max_id
            ORDER BY cl.server_name
            """
        )
        return cur.fetchall()


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {DB_PATH}")
