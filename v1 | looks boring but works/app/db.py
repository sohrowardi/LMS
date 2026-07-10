"""
Storage layer for LMS.

- SQLite (via aiosqlite) holding one row per crawled file/folder in
  `items`, mirrored into an FTS5 virtual table `items_fts` for fast
  prefix full-text search.
- A small `crawl_status` table that the crawler updates live and the
  web UI polls for real-time progress.

Re-indexing a server replaces that server's rows (delete + insert),
never appends, so each crawl is a fresh snapshot per spec.
"""
from __future__ import annotations

import asyncio
import json
from typing import Iterable, Optional

import aiosqlite

from . import config

VIDEO_EXTS = {
    "mp4", "mkv", "avi", "mov", "wmv", "flv", "webm", "m4v", "rmvb", "3gp", "ts",
}
SUBTITLE_EXTS = {"srt", "sub", "ass", "idx"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    rowid       INTEGER PRIMARY KEY AUTOINCREMENT,
    server_name TEXT NOT NULL,
    server_order INTEGER NOT NULL,
    name        TEXT NOT NULL,
    url         TEXT NOT NULL UNIQUE,
    parent_url  TEXT,
    is_dir      INTEGER NOT NULL DEFAULT 0,
    ext         TEXT,
    depth       INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_items_server ON items(server_name);

CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
    name,
    content='items',
    content_rowid='rowid',
    tokenize='unicode61'
);

CREATE TABLE IF NOT EXISTS crawl_status (
    server_name  TEXT PRIMARY KEY,
    server_order INTEGER NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending', -- pending|running|done|error
    pages_visited INTEGER NOT NULL DEFAULT 0,
    files_found   INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    started_at    TEXT,
    finished_at   TEXT
);
"""

_write_lock = asyncio.Lock()  # serialize writers; SQLite is single-writer anyway


async def init_db() -> None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.executescript(_SCHEMA)
        await db.commit()


async def replace_server_items(server_name: str, rows: Iterable[tuple]) -> int:
    """
    rows: iterable of (name, url, parent_url, is_dir, ext, depth, server_order)
    Deletes the server's previous snapshot and inserts the new one
    atomically, keeping items_fts in sync (explicit rowid linkage
    since it's a content-linked FTS5 table).
    """
    rows = list(rows)
    async with _write_lock:
        async with aiosqlite.connect(config.DB_PATH) as db:
            await db.execute("PRAGMA foreign_keys=OFF;")
            # Drop old fts rows for this server, then old data rows.
            await db.execute(
                "DELETE FROM items_fts WHERE rowid IN "
                "(SELECT rowid FROM items WHERE server_name = ?)",
                (server_name,),
            )
            await db.execute("DELETE FROM items WHERE server_name = ?", (server_name,))

            for name, url, parent_url, is_dir, ext, depth, s_order in rows:
                cur = await db.execute(
                    "INSERT OR IGNORE INTO items "
                    "(server_name, server_order, name, url, parent_url, is_dir, ext, depth) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (server_name, s_order, name, url, parent_url, int(is_dir), ext, depth),
                )
                if cur.rowcount:
                    await db.execute(
                        "INSERT INTO items_fts(rowid, name) VALUES (?, ?)",
                        (cur.lastrowid, name),
                    )
            await db.commit()
    return len(rows)


def _sort_key(row: dict, video_exts=VIDEO_EXTS):
    if row["is_dir"]:
        type_rank = 0
    elif (row["ext"] or "").lower() in video_exts:
        type_rank = 1
    else:
        type_rank = 2
    return (row["server_order"], type_rank, (row["name"] or "").lower())


def _build_fts_query(raw: str) -> Optional[str]:
    """Turn free text into a safe FTS5 prefix query, e.g. 'aveng end' -> '"aveng"* "end"*'."""
    tokens = []
    for word in raw.strip().split():
        cleaned = "".join(ch for ch in word if ch.isalnum())
        if cleaned:
            tokens.append(f'"{cleaned}"*')
    if not tokens:
        return None
    return " ".join(tokens)


async def search(query: str, limit: int = 500) -> list[dict]:
    """Full text prefix search, with a LIKE fallback if FTS chokes on odd input."""
    query = (query or "").strip()
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows: list[aiosqlite.Row] = []

        if query:
            fts_query = _build_fts_query(query)
            if fts_query:
                try:
                    cur = await db.execute(
                        """
                        SELECT i.server_name, i.server_order, i.name, i.url,
                               i.is_dir, i.ext
                        FROM items_fts f
                        JOIN items i ON i.rowid = f.rowid
                        WHERE items_fts MATCH ?
                        LIMIT ?
                        """,
                        (fts_query, limit),
                    )
                    rows = await cur.fetchall()
                except aiosqlite.OperationalError:
                    rows = []

            if not rows:
                # Fallback: plain LIKE scan, never let a weird query hard-error.
                like = f"%{query}%"
                cur = await db.execute(
                    """
                    SELECT server_name, server_order, name, url, is_dir, ext
                    FROM items
                    WHERE name LIKE ? ESCAPE '\\'
                    LIMIT ?
                    """,
                    (like, limit),
                )
                rows = await cur.fetchall()
        else:
            cur = await db.execute(
                """
                SELECT server_name, server_order, name, url, is_dir, ext
                FROM items
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cur.fetchall()

        results = [dict(r) for r in rows]
        results.sort(key=_sort_key)
        return results


async def get_stats() -> dict:
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN is_dir = 0 THEN 1 ELSE 0 END) AS files,
                SUM(CASE WHEN is_dir = 1 THEN 1 ELSE 0 END) AS folders,
                COUNT(DISTINCT server_name) AS servers
            FROM items
            """
        )
        row = await cur.fetchone()
        return dict(row) if row else {"total": 0, "files": 0, "folders": 0, "servers": 0}


# ---------------------------------------------------------------- status ---

async def reset_status(servers: list[dict]) -> None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute("DELETE FROM crawl_status")
        await db.executemany(
            "INSERT INTO crawl_status (server_name, server_order, status) VALUES (?,?, 'pending')",
            [(s["name"], s["order"]) for s in servers],
        )
        await db.commit()


async def set_status(server_name: str, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(
            f"UPDATE crawl_status SET {cols} WHERE server_name = ?",
            (*fields.values(), server_name),
        )
        await db.commit()


async def bump_status(server_name: str, pages: int = 0, files: int = 0) -> None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(
            "UPDATE crawl_status SET pages_visited = pages_visited + ?, "
            "files_found = files_found + ? WHERE server_name = ?",
            (pages, files, server_name),
        )
        await db.commit()


async def get_status() -> list[dict]:
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM crawl_status ORDER BY server_order ASC")
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
