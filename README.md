# Local Media Search Engine

A self-hosted, lightweight search engine that indexes 11 local BDIX / ISP
directory-listing media servers (Apache/Nginx/IIS autoindex) and lets you
search across all of them instantly from one page.

## Architecture

- **`database.py`** — SQLite schema (`files` table + `files_fts` FTS5 virtual
  table kept in sync via triggers) and all DB helper functions.
- **`crawler.py`** — Async recursive crawler (`httpx` + `BeautifulSoup`).
  Crawls all 11 servers concurrently, with per-server worker pools, depth
  limit (`MAX_DEPTH=25`), visited-URL loop protection, and graceful handling
  of timeouts / bad certs / dead servers.
- **`app.py`** — FastAPI backend + a single embedded HTML/Tailwind page.
  Search hits the local SQLite FTS5 index, so it returns in milliseconds —
  no live crawling happens during a search. Re-indexing runs in a background
  thread triggered by the "🔄 Re-index Now" button.

## 1. Install dependencies

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

`requirements.txt`:
```
fastapi
uvicorn[standard]
httpx
beautifulsoup4
```

## 2. Edit the server list (optional)

Open `crawler.py` and adjust `TARGET_SERVERS` if your local server IPs /
domains change. It's pre-filled with the 11 servers given in the brief.

## 3. Run the initial crawl (recommended before first use)

This populates the SQLite database (`media_index.db`) so search works
immediately on first page load:

```bash
python3 crawler.py
```

This will print per-server stats (pages visited, files found, errors) when
done. It's fully safe to re-run — each server's old data is cleared and
replaced on every crawl.

> You can skip this step and just click **"Re-index Now"** in the web UI
> instead — it runs the same crawler in a background thread.

## 4. Start the web app

```bash
uvicorn app:app --host 0.0.0.0 --port 8000
```

Then open **http://localhost:8000** (or `http://<your-lan-ip>:8000` from
another device on your network).

## How it works

1. Type a query (e.g. `Ace 2025`) — results stream in via SQLite FTS5
   prefix-matching across all indexed file/folder names, grouped by server.
2. Click any result to open the direct download/stream link on the
   originating server.
3. Toggle **"Files only"** to hide folder results.
4. Click **"🔄 Re-index Now"** any time to trigger a fresh background crawl;
   a banner shows while indexing is in progress, and the result list keeps
   working against the existing index the whole time.

## Notes & limitations

- HTTPS servers with self-signed/local certs are crawled with certificate
  verification disabled (`verify=False`) — intentional, since these are
  private LAN/ISP boxes, not public internet servers.
- The crawler only follows links that look like Apache/Nginx/IIS autoindex
  anchors (`<a href="...">`) ending in `/` for folders; it ignores
  "Parent Directory" links and Apache's `?C=N;O=D` sort-column links to
  avoid infinite loops.
- File sizes aren't scraped by default (autoindex size columns vary too much
  in format across servers); the schema has a `size_bytes` column ready if
  you want to extend `crawler.py` to parse them per-server.
- For very large servers, the first full crawl may take several minutes —
  this is expected and only needs to happen periodically (not per-search).

## One-click app (for non-technical users)

Once it's set up once (by you), anyone else on the machine can just
double-click an icon — no terminal, no commands.

**One-time setup:**

1. Make sure `pip install -r requirements.txt --break-system-packages` has
   already been run, and you've done at least one crawl
   (`python3 crawler.py`) so there's data to search.
2. Make `start.sh` executable (only needed once):
   ```bash
   chmod +x start.sh
   ```
3. Edit `media-search.desktop` and confirm the folder path in the `Exec=`
   line matches where you actually saved these files.
4. Copy the launcher onto the Desktop (or wherever you want the icon):
   ```bash
   cp media-search.desktop ~/Desktop/
   chmod +x ~/Desktop/media-search.desktop
   ```
5. You may need to right-click the new desktop icon and choose
   "Allow Launching" / "Trust this application" the first time
   (varies by desktop environment — GNOME/KDE both prompt for this once).

**From then on:** double-clicking the "Media Search" icon starts the
server in the background (if it isn't already running) and opens your
browser straight to the search page automatically. Double-clicking again
later just reopens the page — it won't start a second server.

To stop the background server completely:
```bash
kill $(cat media-search.pid)
```


