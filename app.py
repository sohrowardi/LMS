"""
app.py
FastAPI backend serving:
  - GET  /                 -> single-page search UI (Tailwind via CDN)
  - GET  /api/search?q=    -> JSON search results (SQLite FTS5)
  - POST /api/reindex      -> kicks off the crawler in the background
  - GET  /api/status       -> indexing progress / last crawl status per server
  - GET  /api/stats        -> total indexed counts

Run with:  uvicorn app:app --host 0.0.0.0 --port 8000
"""

import asyncio
import threading

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

import database as db
from crawler import run_full_crawl, TARGET_SERVERS

app = FastAPI(title="Local Media Search Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

db.init_db()

_indexing_lock = threading.Lock()
_indexing_in_progress = False


def _background_crawl():
    global _indexing_in_progress
    try:
        asyncio.run(run_full_crawl())
    finally:
        _indexing_in_progress = False


@app.post("/api/reindex")
def reindex():
    global _indexing_in_progress
    with _indexing_lock:
        if _indexing_in_progress:
            return JSONResponse({"status": "already_running"}, status_code=409)
        _indexing_in_progress = True
        t = threading.Thread(target=_background_crawl, daemon=True)
        t.start()
    return {"status": "started", "servers": [s["name"] for s in TARGET_SERVERS]}


@app.get("/api/status")
def status():
    rows = db.get_latest_crawl_status()
    return {
        "indexing_in_progress": _indexing_in_progress,
        "servers": [dict(r) for r in rows],
    }


@app.get("/api/stats")
def stats():
    return db.get_stats()


@app.post("/api/shutdown")
def shutdown():
    """Cleanly stops the server - called by the 'Quit' button in the UI."""
    import os
    import signal

    def _stop():
        import time
        time.sleep(0.5)  # give the response time to reach the browser first
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=_stop, daemon=True).start()
    return {"status": "shutting_down"}


@app.get("/api/search")
def search(q: str = "", only_files: bool = False, limit: int = 200):
    if not q.strip():
        return {"query": q, "count": 0, "results": []}
    rows = db.search_files(q, limit=limit, only_files=only_files)
    results = [dict(r) for r in rows]
    return {"query": q, "count": len(results), "results": results}


# ----------------------------------------------------------------------
# Frontend (single-file HTML + Tailwind CDN + vanilla JS)
# ----------------------------------------------------------------------
INDEX_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Local Media Search</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>
  body { background: #0b0f17; }
  ::-webkit-scrollbar { width: 8px; }
  ::-webkit-scrollbar-thumb { background: #2d3748; border-radius: 4px; }
  .fade-in { animation: fadeIn .25s ease-in; }
  @keyframes fadeIn { from {opacity:0; transform: translateY(4px);} to {opacity:1; transform: translateY(0);} }
</style>
</head>
<body class="min-h-screen text-slate-200 font-sans">

  <div class="max-w-4xl mx-auto px-4 py-10">

    <div class="text-center mb-8">
      <h1 class="text-4xl font-bold bg-gradient-to-r from-indigo-400 to-cyan-300 bg-clip-text text-transparent">
        🎬 Local Media Search
      </h1>
      <p class="text-slate-400 mt-2 text-sm">Search across all 11 BDIX / local media servers instantly</p>
    </div>

    <div class="flex gap-2 mb-2">
      <input id="searchInput" type="text" placeholder="Search movies, anime, TV shows..."
        class="flex-1 bg-slate-900 border border-slate-700 rounded-xl px-4 py-3 text-lg
               focus:outline-none focus:ring-2 focus:ring-indigo-500 placeholder-slate-500" />
      <button id="reindexBtn"
        class="bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded-xl px-4 py-3 text-sm whitespace-nowrap">
        🔄 Re-index Now
      </button>
      <button id="quitBtn"
        class="bg-red-900/40 hover:bg-red-900/70 border border-red-800/60 text-red-300 rounded-xl px-4 py-3 text-sm whitespace-nowrap">
        ⏻ Quit
      </button>
    </div>

    <div class="flex items-center justify-between text-xs text-slate-500 mb-6 px-1">
      <span id="statsLine">Loading stats…</span>
      <label class="flex items-center gap-1 cursor-pointer">
        <input type="checkbox" id="onlyFiles" class="accent-indigo-500" />
        Files only (hide folders)
      </label>
    </div>

    <div id="indexBanner" class="hidden mb-4 text-sm bg-amber-900/30 border border-amber-700/50 text-amber-300 rounded-lg px-4 py-2">
      ⏳ Indexing in progress... results may be incomplete until it finishes.
    </div>

    <div id="resultsCount" class="text-sm text-slate-500 mb-3"></div>
    <div id="results" class="space-y-2"></div>
  </div>

<script>
const searchInput = document.getElementById('searchInput');
const resultsDiv = document.getElementById('results');
const resultsCount = document.getElementById('resultsCount');
const statsLine = document.getElementById('statsLine');
const reindexBtn = document.getElementById('reindexBtn');
const onlyFiles = document.getElementById('onlyFiles');
const indexBanner = document.getElementById('indexBanner');

let debounceTimer = null;

const extIcon = (ext, isDir) => {
  if (isDir) return '📁';
  const e = (ext || '').toLowerCase();
  if (['.mkv','.mp4','.avi','.mov','.wmv','.flv','.webm','.m4v'].includes(e)) return '🎞️';
  if (['.srt','.sub','.ass','.idx'].includes(e)) return '💬';
  if (['.zip','.rar','.7z','.iso'].includes(e)) return '📦';
  return '📄';
};

function renderResults(data) {
  resultsDiv.innerHTML = '';
  resultsCount.textContent = data.count
    ? `${data.count} result${data.count === 1 ? '' : 's'} for "${data.query}"`
    : (data.query ? `No results for "${data.query}"` : '');

  // group by server for readability
  const groups = {};
  for (const r of data.results) {
    (groups[r.server_name] ||= []).push(r);
  }

  for (const [server, items] of Object.entries(groups)) {
    const header = document.createElement('div');
    header.className = 'text-xs uppercase tracking-wide text-indigo-400/80 mt-4 mb-1 px-1';
    header.textContent = `${server} (${items.length})`;
    resultsDiv.appendChild(header);

    for (const item of items) {
      const row = document.createElement('a');
      row.href = item.url;
      row.target = '_blank';
      row.rel = 'noopener noreferrer';
      row.className = 'fade-in flex items-center gap-3 bg-slate-900 hover:bg-slate-800 border border-slate-800 rounded-lg px-4 py-2.5 transition';
      row.innerHTML = `
        <span class="text-lg">${extIcon(item.ext, item.is_dir)}</span>
        <span class="flex-1 truncate text-sm">${item.name}</span>
        <span class="text-xs text-slate-500 truncate max-w-[200px]">${item.url}</span>
      `;
      resultsDiv.appendChild(row);
    }
  }
}

async function doSearch() {
  const q = searchInput.value.trim();
  if (!q) { resultsDiv.innerHTML = ''; resultsCount.textContent = ''; return; }
  const res = await fetch(`/api/search?q=${encodeURIComponent(q)}&only_files=${onlyFiles.checked}`);
  const data = await res.json();
  renderResults(data);
}

searchInput.addEventListener('input', () => {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(doSearch, 250);
});
onlyFiles.addEventListener('change', doSearch);

reindexBtn.addEventListener('click', async () => {
  reindexBtn.disabled = true;
  reindexBtn.textContent = '⏳ Starting...';
  await fetch('/api/reindex', { method: 'POST' });
  pollStatus();
});

const quitBtn = document.getElementById('quitBtn');
quitBtn.addEventListener('click', async () => {
  if (!confirm('Quit Media Search? You can reopen it anytime from the icon.')) return;
  quitBtn.disabled = true;
  quitBtn.textContent = '⏻ Closing...';
  try {
    await fetch('/api/shutdown', { method: 'POST' });
  } catch (e) {
    // server cuts the connection mid-response, that's expected
  }
  document.body.innerHTML = `
    <div class="min-h-screen flex items-center justify-center text-center px-6">
      <div>
        <div class="text-5xl mb-4">👋</div>
        <h1 class="text-2xl font-semibold text-slate-200 mb-2">Media Search is closed</h1>
        <p class="text-slate-500 text-sm">You can close this tab now. Click the app icon anytime to reopen it.</p>
      </div>
    </div>`;
});

async function loadStats() {
  const res = await fetch('/api/stats');
  const s = await res.json();
  statsLine.textContent = `${s.total_entries.toLocaleString()} items indexed (${s.total_files.toLocaleString()} files) across ${Object.keys(s.per_server).length} servers`;
}

async function pollStatus() {
  const res = await fetch('/api/status');
  const s = await res.json();
  if (s.indexing_in_progress) {
    indexBanner.classList.remove('hidden');
    reindexBtn.textContent = '⏳ Indexing...';
    reindexBtn.disabled = true;
    setTimeout(pollStatus, 3000);
  } else {
    indexBanner.classList.add('hidden');
    reindexBtn.textContent = '🔄 Re-index Now';
    reindexBtn.disabled = false;
    loadStats();
  }
}

loadStats();
pollStatus();
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(INDEX_HTML)
