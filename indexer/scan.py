"""
Orchestrates a scan run: figures out which servers to touch for the
chosen mode, crawls them concurrently (independent of each other),
and commits results to the database — replacing only the servers that
were actually scanned, one transaction per server.
"""

import asyncio
import sqlite3
from datetime import datetime, timezone

from common.db import get_connection, parse_servers_file
from .crawler import crawl_server

SERVER_CONCURRENCY = 4  # how many servers to crawl at once


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sync_servers_table(conn: sqlite3.Connection) -> None:
    """
    Upsert servers.txt into the servers table:
      - existing servers (matched by name): url/position updated
      - new servers: inserted with status 'never'
      - servers removed from the file: left untouched in the DB (per
        user's choice — old data stays until manually cleaned up)
    """
    file_servers = parse_servers_file()
    for s in file_servers:
        existing = conn.execute(
            "SELECT id FROM servers WHERE name = ?", (s["name"],)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE servers SET url = ?, position = ? WHERE id = ?",
                (s["url"], s["position"], existing["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO servers (name, url, position, last_scan_status) "
                "VALUES (?, ?, ?, 'never')",
                (s["name"], s["url"], s["position"]),
            )
    conn.commit()


def _select_targets(conn: sqlite3.Connection, mode: str, only_names=None):
    rows = conn.execute("SELECT * FROM servers ORDER BY position").fetchall()
    if mode == "full":
        return list(rows)
    if mode == "new":
        return [r for r in rows if r["last_scan_status"] == "never"]
    if mode == "retry":
        return [r for r in rows if r["last_scan_status"] == "error"]
    if mode == "only":
        wanted = set(only_names or [])
        missing = wanted - {r["name"] for r in rows}
        if missing:
            raise ValueError(f"No such server(s) in servers.txt: {', '.join(missing)}")
        return [r for r in rows if r["name"] in wanted]
    raise ValueError(f"Unknown scan mode: {mode}")


def _replace_server_entries(conn: sqlite3.Connection, server_id: int, entries: list[dict]):
    """Delete + insert for one server, inside a single transaction."""
    conn.execute("BEGIN")
    try:
        conn.execute("DELETE FROM entries WHERE server_id = ?", (server_id,))
        conn.executemany(
            "INSERT INTO entries (server_id, parent_path, name, full_path, url, kind, size_bytes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    server_id,
                    e["parent_path"],
                    e["name"],
                    e["full_path"],
                    e["url"],
                    e["kind"],
                    e["size_bytes"],
                )
                for e in entries
            ],
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


async def run_scan(mode: str, only_names=None) -> int:
    """Run a scan. Returns the scan_run id. Prints live progress to stdout."""
    conn = get_connection(readonly=False)
    sync_servers_table(conn)

    targets = _select_targets(conn, mode, only_names)
    if not targets:
        print(f"No servers match mode '{mode}' — nothing to do.")
        conn.close()
        return -1

    run_id = conn.execute(
        "INSERT INTO scan_runs (mode, started_at) VALUES (?, ?)", (mode, _now())
    ).lastrowid
    conn.commit()

    print(f"Starting scan (mode={mode}) — {len(targets)} server(s): "
          f"{', '.join(t['name'] for t in targets)}")

    sem = asyncio.Semaphore(SERVER_CONCURRENCY)

    async def scan_one(server_row):
        async with sem:
            name, url, sid = server_row["name"], server_row["url"], server_row["id"]
            started = _now()

            def progress(msg):
                print(f"[{name}] {msg}")

            print(f"[{name}] starting...")
            try:
                entries, stats = await crawl_server(name, url, on_progress=progress)
                status = "error" if stats.errors and not entries else (
                    "partial" if stats.errors else "ok"
                )
                _replace_server_entries(conn, sid, entries)
                conn.execute(
                    "UPDATE servers SET last_scan_status = ?, last_scan_time = ?, "
                    "last_scan_mode = ? WHERE id = ?",
                    (status, _now(), mode, sid),
                )
                conn.execute(
                    "INSERT INTO scan_log (run_id, server_id, server_name, status, "
                    "files_found, folders_found, error_text, started_at, finished_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        run_id, sid, name, status, stats.files, stats.folders,
                        f"{stats.errors} request error(s)" if stats.errors else None,
                        started, _now(),
                    ),
                )
                conn.commit()
                print(f"[{name}] done: {stats.folders} folders, {stats.files} files, "
                      f"{stats.errors} errors ({status})")
            except Exception as exc:
                conn.execute(
                    "UPDATE servers SET last_scan_status = 'error', last_scan_time = ?, "
                    "last_scan_mode = ? WHERE id = ?",
                    (_now(), mode, sid),
                )
                conn.execute(
                    "INSERT INTO scan_log (run_id, server_id, server_name, status, "
                    "files_found, folders_found, error_text, started_at, finished_at) "
                    "VALUES (?, ?, ?, 'error', 0, 0, ?, ?, ?)",
                    (run_id, sid, name, str(exc), started, _now()),
                )
                conn.commit()
                print(f"[{name}] FAILED: {exc}")

    await asyncio.gather(*(scan_one(row) for row in targets))

    conn.execute("UPDATE scan_runs SET finished_at = ? WHERE id = ?", (_now(), run_id))
    conn.commit()
    conn.close()
    print(f"Scan complete (run id {run_id}).")
    return run_id


def print_summary(run_id: int | None = None) -> None:
    """Print the last scan's results, or a specific run_id, without scanning anything."""
    conn = get_connection(readonly=True)
    if run_id is None:
        row = conn.execute(
            "SELECT id FROM scan_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            print("No scans have been run yet.")
            return
        run_id = row["id"]

    run = conn.execute("SELECT * FROM scan_runs WHERE id = ?", (run_id,)).fetchone()
    if not run:
        print(f"No scan run with id {run_id}.")
        return

    print(f"Scan run #{run['id']} — mode={run['mode']} — "
          f"started {run['started_at']} — finished {run['finished_at'] or '(in progress)'}")
    print("-" * 70)
    logs = conn.execute(
        "SELECT * FROM scan_log WHERE run_id = ? ORDER BY server_name", (run_id,)
    ).fetchall()
    for log in logs:
        print(f"  {log['server_name']:<20} {log['status']:<8} "
              f"{log['folders_found']:>5} folders  {log['files_found']:>6} files"
              + (f"  — {log['error_text']}" if log["error_text"] else ""))
    conn.close()
