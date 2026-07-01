# LMS - Local Media Search

A self-hosted, lightweight search engine that indexes **11 local BDIX / ISP directory-listing media servers** (Apache, Nginx, and IIS autoindex) and lets you search across all of them instantly from a single, clean web interface.

---

# Architecture

## `database.py`

Handles the SQLite database layer.

- Creates the `files` table.
- Creates and maintains the `files_fts` FTS5 virtual table.
- Keeps both tables synchronized using SQLite triggers.
- Provides all database helper functions.
- Includes safe fallback handling for complex search queries.

---

## `crawler.py`

An asynchronous recursive crawler built with **httpx** and **BeautifulSoup**.

Features include:

- Crawls all 11 servers concurrently.
- Per-server worker pools.
- Maximum crawl depth of **25**.
- Visited URL tracking to prevent infinite loops.
- Graceful handling of:
  - Timeouts
  - Dead servers
  - Invalid/self-signed certificates

---

## `app.py`

A FastAPI backend that serves both the API and the embedded frontend.

Features include:

- Single-page web interface (Tailwind CSS via CDN).
- Direct SQLite FTS5 searching.
- Millisecond-level search results.
- No live crawling during searches.

---

# API Endpoints

## `GET /`

Serves the interactive search interface.

---

## `GET /api/search?q=&only_files=`

Searches the SQLite FTS5 index using prefix matching.

Returns fast results grouped by originating server.

---

## `POST /api/reindex`

Starts a background indexing job.

- Thread-safe
- Prevents duplicate crawler instances

---

## `GET /api/status`

Returns live indexing status including:

- Whether indexing is currently running
- Per-server crawl health/history

---

## `GET /api/stats`

Returns database statistics including:

- Total indexed entries
- Per-server breakdown

---

## `POST /api/shutdown`

Gracefully shuts down the FastAPI application after a short delay.

Useful for desktop deployments where users launch the application from an icon.

---

# Installation

## 1. Install dependencies

Create a virtual environment and install the required packages.

```bash
python3 -m venv venv
source venv/bin/activate
pip install fastapi uvicorn[standard] httpx beautifulsoup4
```

---

## 2. Configure target servers (optional)

Open `crawler.py` and edit the `TARGET_SERVERS` list if your local IP addresses or domains change.

By default it is already configured for the 11 target servers.

---

## 3. Run the initial crawl (recommended)

Populate the SQLite database before launching the web application.

```bash
python3 crawler.py
```

This will:

- Crawl every configured server
- Print crawl statistics
- Replace old entries with fresh ones

Running the crawler multiple times is completely safe.

If you skip this step, you can instead press **Re-index Now** from the web interface after launching the application.

---

## 4. Start the web application

```bash
uvicorn app:app --host 0.0.0.0 --port 8000
```

Open:

- http://localhost:8000

or

- http://YOUR-IP:8000

from another device on your local network.

---

# How It Works

1. Enter a search query (for example, `Ace 2025`).

   SQLite FTS5 performs instant prefix matching across every indexed file and folder.

2. Results are grouped by their originating server.

3. Click any result to open its direct download or streaming URL.

4. Enable **Files only** to instantly hide folders.

5. Click **Re-index Now** at any time to refresh the database.

   Searches remain fully available while indexing runs in the background.

6. Click **Quit** to gracefully stop the backend server.

---

# Notes & Limitations

- HTTPS servers using self-signed or local certificates are crawled with certificate verification disabled (`verify=False`).

  This is intentional because these servers exist only on trusted private LAN/ISP networks.

- Only directory links that resemble Apache/Nginx/IIS autoindex folders are followed.

  The crawler automatically ignores:

  - Parent Directory links
  - Apache sorting links (`?C=N;O=D`)
  - Other cyclic navigation paths

- File sizes are not currently indexed because autoindex implementations expose size information differently.

  The database already includes a `size_bytes` field for future expansion.

- Initial indexing of very large servers may take several minutes.

  This happens only during crawling and does not affect search performance afterward.

---

# One-Click Desktop Launcher

Once everything has been configured, non-technical users can launch the application simply by double-clicking a desktop icon.

## One-time setup

### 1. Complete the initial installation

Ensure that:

- All Python packages are installed.
- At least one crawl has been completed.

```bash
python3 crawler.py
```

---

### 2. Make the startup script executable

```bash
chmod +x start.sh
```

---

### 3. Verify the launcher

Open `media-search.desktop` and confirm that the `Exec=` path points to the correct project directory.

---

### 4. Copy the launcher to the Desktop

```bash
cp media-search.desktop ~/Desktop/
chmod +x ~/Desktop/media-search.desktop
```

---

### 5. Trust the launcher

The first time you open it, your desktop environment may ask you to:

- Allow Launching
- Trust this Application

(GNOME and KDE typically prompt once.)

---

# Daily Usage

After setup:

- Double-click **Media Search**.
- The application starts automatically (if not already running).
- Your browser opens directly to the search page.
- Opening the launcher again simply reopens the webpage without creating duplicate server instances.

---

# Stopping the Server

You can stop the application by:

- Clicking **Quit** inside the web interface

or

```bash
kill $(cat media-search.pid)
```