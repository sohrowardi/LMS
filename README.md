# LMS — Local Media Search

A self-hosted search engine for local media servers — the plain
"directory listing" pages many home media boxes and local ISPs expose
(folders of movies/shows with no real API, just clickable HTML links).

LMS crawls every server you configure once, indexes every file/folder
name into a local SQLite database, and gives you one instant search box
that finds matches across all of them — no live requests to the media
servers at search time.

## Quick start

**macOS / Linux**
```bash
./run.sh
```

**Windows**
```
run.bat
```

Either script creates a virtual environment on first run, installs
dependencies, and opens your browser straight to the search page.

(If you'd rather do it by hand: `pip install -r requirements.txt`
then `python launcher.py`.)

## Configure your servers

Edit `servers.txt` — one server per line, order = display order:

```
Dhaka Flix 7 | http://ftp7.dhakaflix.net/
http://10.10.10.10/media/
```

A name is optional (`Name | URL`); without one, LMS derives a name
from the hostname. Comments (`#`) and blank lines are ignored. Save
the file and hit **Re-index Now** in the UI — no restart needed.

## Network access

Edit `config.ini`:

```ini
[server]
host_mode = localhost   ; only this machine
; host_mode = lan       ; anyone on your LAN (phones, TVs, laptops)
port = 8000
```

## How it works

- `app/crawler.py` — async crawler, one independent task per server.
  Parses raw Apache/Nginx/IIS `<a href>` directory listings, follows
  every subfolder, ignores "Parent Directory" / sort-order links,
  tolerates self-signed HTTPS certs, and never lets one dead server
  stall the others.
- `app/db.py` — SQLite + FTS5 for instant prefix search ("aveng"
  matches "Avengers…"), with a plain `LIKE` fallback if a search
  string ever trips up the FTS query parser.
- `app/main.py` — FastAPI backend: `/api/search`, `/api/reindex`,
  `/api/status` (live per-server progress), `/api/stats`, `/api/quit`.
- `app/static/` — vanilla JS/HTML/CSS frontend. All filtering happens
  client-side against the already-fetched result set — zero extra
  network calls when you toggle a checkbox.

## Notes

- Re-indexing a server replaces that server's previously indexed data
  with a fresh snapshot; it never just appends.
- The database file `lms.sqlite3` is created next to this README on
  first run.
