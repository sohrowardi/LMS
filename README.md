# LMS — Local Media Search

Indexes plain HTTP directory-listing pages on your local media servers
and gives you one instant search box across all of them. Two fully
separate programs sharing one database:

- **Indexer** (`run_indexer.py`) — the only thing that scans servers
  and writes to the database. You run it yourself, by hand or on a
  schedule.
- **Search site** (`run_search.py`) — an always-running, read-only
  website. It can never trigger, see, or influence a scan.

## Setup

```bash
pip install -r requirements.txt
```

(Or with `--break-system-packages` if your system Python needs it.)

## 1. List your servers

Edit `data/servers.txt` — one server per line:

```
Server One | http://10.10.10.11/movies/
Server Two | http://10.10.10.12/tv-shows/
```

The order here is the order servers appear in search results. Edit
this file any time; changes take effect the next time you run a scan.

## 2. Run the indexer

```bash
python3 run_indexer.py --full              # scan every server
python3 run_indexer.py --new               # scan only newly-added servers
python3 run_indexer.py --retry             # rescan only servers that errored last time
python3 run_indexer.py --only "Server One" # rescan specific server(s), comma-separated
python3 run_indexer.py --summary           # print the last scan's results, no scanning
```

If you run it with no flags, it will ask you to confirm before doing a
full rescan — it never silently wipes and rescans everything.

Live progress prints to the terminal as it scans. Each server is
scanned independently, so one slow/broken server never blocks the
others.

## 3. Run the search site

```bash
python3 run_search.py
```

This reads `search_config.txt` to decide whether the site is reachable
only from this computer (`access = local`) or from any device on your
home network (`access = network`) — edit that file and restart to
change it. It opens your browser automatically once the server is up.

## Where things live

- `data/lms.db` — the shared database (created automatically on first
  indexer run)
- `data/servers.txt` — your editable server list
- `search_config.txt` — the search site's network-access toggle

Back up the whole `data/` folder if you want to preserve your index.

## Notes

- No accounts/logins — home network use only.
- The search site never plays or streams anything; it links you
  straight to the file on the source server.
- Self-signed certs on your servers are tolerated automatically.
