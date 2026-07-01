"""
crawler.py
Asynchronous recursive crawler for plain HTML "directory listing" servers
(Apache mod_autoindex, Nginx autoindex, IIS default listing).

Design notes
------------
- Each server is crawled independently and concurrently (asyncio.gather),
  wrapped in try/except so one dead/unreachable server never aborts the rest.
- Within a server, a bounded asyncio.Semaphore caps concurrent requests
  (be polite to small local boxes).
- `visited` is a set of normalized URLs -> prevents infinite loops from
  symlinks, "Parent Directory" links, or query-string sort links.
- `MAX_DEPTH` caps recursion depth as a second safety net.
- httpx.AsyncClient(verify=False) so self-signed / local HTTPS certs don't
  blow up the crawl. Per-request timeout + retry-free failure handling:
  a failed page just gets skipped and logged.
"""

import asyncio
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse, unquote

import httpx
from bs4 import BeautifulSoup

import database as db

# ----------------------------------------------------------------------
# Target servers - edit this list to add/remove sources
# ----------------------------------------------------------------------
TARGET_SERVERS = [
    {"name": "172.17.50.243", "url": "http://172.17.50.243/"},
    {"name": "DHAKA-FLIX-7", "url": "http://172.16.50.7/DHAKA-FLIX-7/"},
    {"name": "DHAKA-FLIX-8", "url": "http://172.16.50.8/DHAKA-FLIX-8/"},
    {"name": "DHAKA-FLIX-12", "url": "http://172.16.50.12/DHAKA-FLIX-12/"},
    {"name": "DHAKA-FLIX-14", "url": "http://172.16.50.14/DHAKA-FLIX-14/"},
    {"name": "15.1.1.10", "url": "http://15.1.1.10/"},
    {"name": "media.ctgfun.com", "url": "https://media.ctgfun.com/"},
    {"name": "dl.ctgfun.com", "url": "https://dl.ctgfun.com/"},
    {"name": "movie.ctgfun.com", "url": "https://movie.ctgfun.com/"},
    {"name": "data.ctgfun.com", "url": "https://data.ctgfun.com/"},
    {"name": "ftp.ctgfun.com", "url": "https://ftp.ctgfun.com/"},
]

MAX_DEPTH = 25                 # hard safety cap on recursion depth
MAX_CONCURRENT_PER_SERVER = 8  # politeness limit
REQUEST_TIMEOUT = 15.0         # seconds
BATCH_SIZE = 300               # rows buffered before a DB write

MEDIA_EXTENSIONS = {
    ".mkv", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v",
    ".srt", ".sub", ".ass", ".idx",
    ".zip", ".rar", ".7z",
    ".iso",
}

IGNORED_LINK_TEXT = {"parent directory", "../", "..", "name", "last modified", "size", "description"}


@dataclass
class CrawlStats:
    pages_visited: int = 0
    files_found: int = 0
    errors: int = 0


def normalize_url(url: str) -> str:
    """Strip fragments and sort/query params (?C=N;O=D etc.) used by autoindex pages."""
    parsed = urlparse(url)
    clean = parsed._replace(query="", fragment="")
    return clean.geturl()


def is_probably_sort_link(href: str) -> bool:
    # Apache "?C=N;O=D" style column-sort links re-point to the same dir
    return href.startswith("?")


def extract_links(base_url: str, html: str) -> list[dict]:
    """
    Parse an autoindex HTML page and return a list of
    {"url": absolute_url, "name": display_name, "is_dir": bool}
    """
    soup = BeautifulSoup(html, "html.parser")
    results = []
    seen = set()

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        text = a.get_text(strip=True)

        if not href or href.startswith("#"):
            continue
        if is_probably_sort_link(href):
            continue
        if href.lower() in ("/", "../", "..") or text.lower() in IGNORED_LINK_TEXT:
            continue
        if href.lower().startswith(("mailto:", "javascript:")):
            continue

        abs_url = normalize_url(urljoin(base_url, href))

        # Must stay within the same host to avoid wandering off-server
        if urlparse(abs_url).netloc != urlparse(base_url).netloc:
            continue
        # Must stay "below" the base path (no climbing to parent dirs)
        if not abs_url.startswith(normalize_url(base_url).rsplit("?", 1)[0]) and \
           not _is_descendant(base_url, abs_url):
            continue

        if abs_url in seen:
            continue
        seen.add(abs_url)

        is_dir = href.endswith("/")
        name = unquote(text or href.rstrip("/").rsplit("/", 1)[-1])
        results.append({"url": abs_url, "name": name, "is_dir": is_dir})

    return results


def _is_descendant(base_url: str, candidate: str) -> bool:
    """True if candidate path is at or below base_url's path on the same host."""
    b = urlparse(base_url)
    c = urlparse(candidate)
    if b.netloc != c.netloc:
        return False
    base_path = b.path if b.path.endswith("/") else b.path + "/"
    return c.path.startswith(base_path) or c.path == b.path


def file_ext(name: str) -> str:
    if "." in name:
        return "." + name.rsplit(".", 1)[-1].lower()
    return ""


async def fetch(client: httpx.AsyncClient, url: str) -> str | None:
    try:
        resp = await client.get(url, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            return None
        ctype = resp.headers.get("content-type", "")
        if "text/html" not in ctype and "text/plain" not in ctype and ctype != "":
            # Not a directory listing page (likely an actual file) -> skip parsing
            return None
        return resp.text
    except (httpx.TimeoutException, httpx.ConnectError, httpx.RequestError, httpx.HTTPStatusError):
        return None
    except Exception:
        return None


async def crawl_server(server: dict, stats: CrawlStats):
    """BFS/DFS-hybrid recursive crawl of a single server using a worker pool."""
    name = server["name"]
    base_url = normalize_url(server["url"])

    print(f"[{name}] starting crawl: {base_url}", flush=True)
    db.clear_server_data(name)
    log_id = db.start_crawl_log(name)

    visited: set[str] = set()
    queue: asyncio.Queue = asyncio.Queue()
    await queue.put((base_url, 0, None))  # (url, depth, parent_url)

    buffer: list[dict] = []
    sem = asyncio.Semaphore(MAX_CONCURRENT_PER_SERVER)

    limits = httpx.Limits(max_connections=MAX_CONCURRENT_PER_SERVER, max_keepalive_connections=MAX_CONCURRENT_PER_SERVER)

    async with httpx.AsyncClient(
        verify=False,            # local/self-signed HTTPS certs
        follow_redirects=True,
        limits=limits,
        headers={"User-Agent": "LocalMediaIndexer/1.0"},
    ) as client:

        async def flush():
            nonlocal buffer
            if buffer:
                db.insert_files_batch(buffer)
                buffer = []

        async def worker():
            while True:
                try:
                    url, depth, parent = await asyncio.wait_for(queue.get(), timeout=5)
                except asyncio.TimeoutError:
                    return  # nothing left to do for a while -> exit worker

                try:
                    if url in visited or depth > MAX_DEPTH:
                        continue
                    visited.add(url)

                    async with sem:
                        html = await fetch(client, url)

                    stats.pages_visited += 1
                    if stats.pages_visited % 20 == 0:
                        print(f"[{name}] ...{stats.pages_visited} pages, {stats.files_found} files so far", flush=True)

                    if html is None:
                        continue

                    links = extract_links(url, html)
                    for link in links:
                        if link["url"] in visited:
                            continue

                        ext = file_ext(link["name"]) if not link["is_dir"] else ""
                        buffer.append({
                            "server_name": name,
                            "server_base": base_url,
                            "name": link["name"],
                            "url": link["url"],
                            "parent_url": url,
                            "is_dir": 1 if link["is_dir"] else 0,
                            "ext": ext,
                            "depth": depth + 1,
                        })
                        if not link["is_dir"]:
                            stats.files_found += 1

                        if link["is_dir"]:
                            await queue.put((link["url"], depth + 1, url))

                        if len(buffer) >= BATCH_SIZE:
                            await flush()
                finally:
                    queue.task_done()

        workers = [asyncio.create_task(worker()) for _ in range(MAX_CONCURRENT_PER_SERVER)]
        await queue.join()
        for w in workers:
            w.cancel()
        await flush()

    db.finish_crawl_log(log_id, stats.pages_visited, stats.files_found, status="done")


async def crawl_server_safe(server: dict) -> tuple[str, CrawlStats, str | None]:
    stats = CrawlStats()
    try:
        await crawl_server(server, stats)
        return server["name"], stats, None
    except Exception as e:
        db.start_crawl_log(server["name"])  # ensure a row exists
        db.finish_crawl_log(
            db.start_crawl_log(server["name"]),
            stats.pages_visited, stats.files_found,
            status="error", error=str(e),
        )
        return server["name"], stats, str(e)


async def run_full_crawl(servers: list[dict] | None = None):
    """Crawl all target servers concurrently. Safe to call repeatedly."""
    db.init_db()
    servers = servers or TARGET_SERVERS
    started = time.time()
    print(f"Starting crawl of {len(servers)} servers...", flush=True)
    results = await asyncio.gather(*(crawl_server_safe(s) for s in servers))
    elapsed = time.time() - started

    print(f"\n=== Crawl finished in {elapsed:.1f}s ===")
    for name, stats, err in results:
        status = f"ERROR: {err}" if err else "OK"
        print(f"  {name:<20} pages={stats.pages_visited:<6} files={stats.files_found:<6} {status}")
    return results


if __name__ == "__main__":
    asyncio.run(run_full_crawl())
