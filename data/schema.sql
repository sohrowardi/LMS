-- LMS database schema
-- Only the Indexer ever writes to these tables. The Search App opens
-- this file read-only and only ever runs SELECT queries.

PRAGMA journal_mode = WAL;   -- lets the search app read while the indexer writes
PRAGMA foreign_keys = ON;

-- One row per server from servers.txt
CREATE TABLE IF NOT EXISTS servers (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT NOT NULL UNIQUE,
    url               TEXT NOT NULL,
    position          INTEGER NOT NULL,        -- display order, from servers.txt order
    last_scan_status  TEXT NOT NULL DEFAULT 'never',   -- never | ok | error
    last_scan_time    TEXT,                    -- ISO8601 timestamp
    last_scan_mode    TEXT                     -- full | new | retry | single
);

-- One row per file or folder found on a server
CREATE TABLE IF NOT EXISTS entries (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id    INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    parent_path  TEXT NOT NULL DEFAULT '',      -- folder path this entry lives in
    name         TEXT NOT NULL,                 -- file/folder name, as displayed
    full_path    TEXT NOT NULL,                 -- full path within the server
    url          TEXT NOT NULL,                 -- direct link to the file/folder
    kind         TEXT NOT NULL CHECK (kind IN ('folder','video','subtitle','other')),
    size_bytes   INTEGER                        -- NULL for folders / unknown sizes
);

CREATE INDEX IF NOT EXISTS idx_entries_server ON entries(server_id);
CREATE INDEX IF NOT EXISTS idx_entries_parent ON entries(server_id, parent_path);

-- Full-text search index over entry names, kept in sync with `entries`
-- automatically via triggers below (external-content FTS5 table).
CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
    name,
    content = 'entries',
    content_rowid = 'id',
    tokenize = 'unicode61'
);

CREATE TRIGGER IF NOT EXISTS entries_ai AFTER INSERT ON entries BEGIN
    INSERT INTO entries_fts(rowid, name) VALUES (new.id, new.name);
END;

CREATE TRIGGER IF NOT EXISTS entries_ad AFTER DELETE ON entries BEGIN
    INSERT INTO entries_fts(entries_fts, rowid, name) VALUES ('delete', old.id, old.name);
END;

CREATE TRIGGER IF NOT EXISTS entries_au AFTER UPDATE ON entries BEGIN
    INSERT INTO entries_fts(entries_fts, rowid, name) VALUES ('delete', old.id, old.name);
    INSERT INTO entries_fts(rowid, name) VALUES (new.id, new.name);
END;

-- One row per scan run (a single "indexer.py --full" invocation, etc.)
CREATE TABLE IF NOT EXISTS scan_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    mode         TEXT NOT NULL,                 -- full | new | retry | single | summary
    started_at   TEXT NOT NULL,
    finished_at  TEXT
);

-- One row per server touched within a scan run
CREATE TABLE IF NOT EXISTS scan_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        INTEGER NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
    server_id     INTEGER REFERENCES servers(id) ON DELETE SET NULL,
    server_name   TEXT NOT NULL,                -- kept even if server later removed
    status        TEXT NOT NULL,                -- ok | error | partial
    files_found   INTEGER NOT NULL DEFAULT 0,
    folders_found INTEGER NOT NULL DEFAULT 0,
    error_text    TEXT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT
);
