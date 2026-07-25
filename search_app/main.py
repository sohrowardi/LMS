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

_ROW_COLUMNS = """
    e.id, e.server_id, e.name, e.full_path, e.parent_path, e.url, e.kind, e.size_bytes,
    s.name AS server_name, s.position AS server_position
"""


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
            f"""
            SELECT {_ROW_COLUMNS}
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
            f"""
            SELECT {_ROW_COLUMNS}
            FROM entries e
            JOIN servers s ON s.id = e.server_id
            WHERE e.name LIKE ? ESCAPE '\\'
            """,
            (like_term,),
        ).fetchall()


def _folder_descendants(
    conn: sqlite3.Connection, server_id: int, folder_full_path: str
) -> list[sqlite3.Row]:
    """
    Everything nested inside a matched folder, any depth — so that a
    query matching only the folder's name still shows what's inside it.
    Uses a plain prefix comparison (not LIKE) so folder names containing
    '%' or '_' can't produce bogus matches.
    """
    return conn.execute(
        f"""
        SELECT {_ROW_COLUMNS}
        FROM entries e
        JOIN servers s ON s.id = e.server_id
        WHERE e.server_id = ?
          AND substr(e.full_path, 1, ?) = ?
          AND e.full_path != ?
        """,
        (server_id, len(folder_full_path), folder_full_path, folder_full_path),
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
        matched = _run_search(conn, q) if q.strip() else []

        # A folder matching the query doesn't mean its contents matched
        # too — pull those in explicitly so the folder isn't shown empty.
        combined = {r["id"]: r for r in matched}
        for r in matched:
            if r["kind"] == "folder":
                for child in _folder_descendants(conn, r["server_id"], r["full_path"]):
                    combined.setdefault(child["id"], child)

        rows = [r for r in combined.values() if _passes_filter(r, filter_tokens)]
        rows.sort(
            key=lambda r: (
                r["server_position"],
                r["parent_path"],
                KIND_RANK.get(r["kind"], 99),
                r["name"].lower(),
            )
        )

        # Two-level grouping: server -> folder (parent_path). Order of
        # groups follows the sort above, so insertion order is correct.
        servers: dict[str, dict] = {}
        for r in rows:
            server_group = servers.setdefault(
                r["server_name"], {"server": r["server_name"], "folders": {}}
            )
            folder_group = server_group["folders"].setdefault(
                r["parent_path"], {"path": r["parent_path"], "results": []}
            )
            folder_group["results"].append(
                {
                    "name": r["name"],
                    "full_path": r["full_path"],
                    "parent_path": r["parent_path"],
                    "url": r["url"],
                    "kind": r["kind"],
                    "size_bytes": r["size_bytes"],
                }
            )

        groups = [
            {"server": s["server"], "folders": list(s["folders"].values())}
            for s in servers.values()
        ]
        return JSONResponse({"groups": groups})
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
