"""
LMS web app — a handful of JSON endpoints plus one static page.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, crawler, db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("lms.main")

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="LMS — Local Media Search")

_crawl_lock = asyncio.Lock()
_crawl_running = False


@app.on_event("startup")
async def on_startup() -> None:
    await db.init_db()


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/search")
async def api_search(q: str = "") -> JSONResponse:
    results = await db.search(q)
    return JSONResponse({"query": q, "count": len(results), "results": results})


@app.get("/api/stats")
async def api_stats() -> JSONResponse:
    stats = await db.get_stats()
    return JSONResponse(stats)


@app.get("/api/status")
async def api_status() -> JSONResponse:
    status_rows = await db.get_status()
    return JSONResponse({"running": _crawl_running, "servers": status_rows})


@app.post("/api/reindex")
async def api_reindex() -> JSONResponse:
    global _crawl_running

    if _crawl_running:
        return JSONResponse({"started": False, "reason": "A crawl is already running."}, status_code=409)

    async def _run():
        global _crawl_running
        async with _crawl_lock:
            _crawl_running = True
            try:
                await crawler.crawl_all()
            finally:
                _crawl_running = False

    asyncio.create_task(_run())
    return JSONResponse({"started": True})


@app.post("/api/quit")
async def api_quit() -> JSONResponse:
    async def _shutdown():
        await asyncio.sleep(0.3)  # let the response flush first
        os.kill(os.getpid(), signal.SIGTERM)

    asyncio.create_task(_shutdown())
    return JSONResponse({"shutting_down": True})


# Static assets (app.js / style.css) mounted last so it doesn't shadow /api/*.
app.mount("/", StaticFiles(directory=STATIC_DIR), name="static")
