"""
The Search App. Read-only, by construction: every DB access here goes
through common.db.get_connection(readonly=True), which opens SQLite
in mode=ro. This process cannot write to the database even if the
code had a bug — SQLite itself refuses the write at the driver level.

There is intentionally no route, button, or code path anywhere in this
app that can start, monitor, or influence a scan.
"""

import re
import sqlite3
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from common.db import get_connection
from indexer.classify import VIDEO_EXTENSIONS, SUBTITLE_EXTENSIONS

app = FastAPI(title="LMS Search")

# kind display/sort order per the brief: folders, then video, then everything else
KIND_RANK = {"folder": 0, "video": 1, "subtitle": 2, "other": 3}

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _build_fts_query(raw_query: str) -> str | None:
    """Turn free text into a prefix-matching FTS5 query, e.g.
    'aveng end' -> '"aveng"* AND "end"*'. Returns None if nothing usable."""
    tokens = _TOKEN_RE.findall(raw_query)
    if not tokens:
        return None
    # quote each token so punctuation-like characters can't break FTS syntax
    return " AND ".join(f'"{t}"*' for t in tokens)


def _run_search(conn: sqlite3.Connection, raw_query: str) -> list[sqlite3.Row]:
    fts_query = _build_fts_query(raw_query)
    if fts_query is None:
        return []

    try:
        rows = conn.execute(
            """
            SELECT e.id, e.server_id, e.name, e.full_path, e.url, e.kind, e.size_bytes,
                   s.name AS server_name, s.position AS server_position
            FROM entries_fts f
            JOIN entries e ON e.id = f.rowid
            JOIN servers s ON s.id = e.server_id
            WHERE entries_fts MATCH ?
            """,
            (fts_query,),
        ).fetchall()
        return rows
    except sqlite3.OperationalError:
        # FTS5 choked on something (unusual characters etc.) — fall back
        # to a plain LIKE search rather than showing an error.
        like_term = f"%{raw_query.strip()}%"
        return conn.execute(
            """
            SELECT e.id, e.server_id, e.name, e.full_path, e.url, e.kind, e.size_bytes,
                   s.name AS server_name, s.position AS server_position
            FROM entries e
            JOIN servers s ON s.id = e.server_id
            WHERE e.name LIKE ? ESCAPE '\\'
            """,
            (like_term,),
        ).fetchall()


def _extension(name: str) -> str:
    idx = name.rfind(".")
    return name[idx:].lower() if idx != -1 else ""


def _passes_filter(row: sqlite3.Row, filter_tokens: set[str]) -> bool:
    """filter_tokens empty => show everything (the default, unfiltered state)."""
    if not filter_tokens:
        return True
    kind = row["kind"]
    if kind in ("folder", "other"):
        return kind in filter_tokens
    # video / subtitle: either the bare kind (=> "all extensions" / select-all)
    # or a specific "kind:.ext" token is enough to match
    if kind in filter_tokens:
        return True
    ext = _extension(row["name"])
    return f"{kind}:{ext}" in filter_tokens


@app.get("/api/search")
def search(
    q: str = Query("", description="Search text"),
    filter: str = Query("", description="Comma-separated filter tokens"),
):
    filter_tokens = {t for t in filter.split(",") if t}

    conn = get_connection(readonly=True)
    try:
        rows = _run_search(conn, q) if q.strip() else []
        rows = [r for r in rows if _passes_filter(r, filter_tokens)]
        rows.sort(
            key=lambda r: (
                r["server_position"],
                KIND_RANK.get(r["kind"], 99),
                r["name"].lower(),
            )
        )

        grouped: dict[str, dict] = {}
        for r in rows:
            grouped.setdefault(
                r["server_name"], {"server": r["server_name"], "results": []}
            )["results"].append(
                {
                    "name": r["name"],
                    "full_path": r["full_path"],
                    "url": r["url"],
                    "kind": r["kind"],
                    "size_bytes": r["size_bytes"],
                }
            )
        # preserve server display order even though dict insertion order
        # already follows it (rows are sorted by server_position first)
        return JSONResponse({"groups": list(grouped.values())})
    finally:
        conn.close()


@app.get("/api/stats")
def stats():
    conn = get_connection(readonly=True)
    try:
        servers = conn.execute("SELECT COUNT(*) c FROM servers").fetchone()["c"]
        folders = conn.execute(
            "SELECT COUNT(*) c FROM entries WHERE kind = 'folder'"
        ).fetchone()["c"]
        files = conn.execute(
            "SELECT COUNT(*) c FROM entries WHERE kind != 'folder'"
        ).fetchone()["c"]
        return {"servers": servers, "folders": folders, "files": files}
    finally:
        conn.close()


@app.get("/api/filetypes")
def filetypes():
    """Lets the frontend build the video/subtitle sub-checkboxes without
    hardcoding the extension lists twice."""
    return {
        "video": sorted(VIDEO_EXTENSIONS),
        "subtitle": sorted(SUBTITLE_EXTENSIONS),
    }


static_dir = Path(__file__).resolve().parent / "static"
app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
