"""
Crawls a single server's directory-listing pages and returns every
file/folder found. Designed to be run once per server, independently —
the caller (scan.py) runs several of these concurrently across servers,
so one bad server never blocks the others.
"""

import asyncio
import time
from urllib.parse import urljoin, urlparse, unquote

import httpx
from bs4 import BeautifulSoup

from .classify import classify

MAX_RETRIES = 4
BASE_BACKOFF_SECONDS = 1.5
FOLDER_CONCURRENCY = 8
REQUEST_TIMEOUT = 20.0

# Skip these when parsing a directory-listing page
_SKIP_HREF_PREFIXES = ("?", "#", "mailto:")
_PARENT_DIR_MARKERS = ("../", "..")


def _normalize_base(url: str) -> str:
    return url if url.endswith("/") else url + "/"


def _extract_links(html: str, page_url: str) -> list[tuple[str, str, bool]]:
    """Return [(name, absolute_url, is_folder), ...] for a listing page,
    skipping parent-directory links and sort/query links."""
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(_SKIP_HREF_PREFIXES):
            continue
        if href in _PARENT_DIR_MARKERS or href.startswith("../"):
            continue

        absolute = urljoin(page_url, href)
        is_folder = href.endswith("/")
        raw_name = href[:-1] if is_folder else href
        raw_name = raw_name.rsplit("/", 1)[-1]
        name = unquote(raw_name).strip()
        if not name:
            continue
        results.append((name, absolute, is_folder))
    return results


class CrawlStats:
    def __init__(self):
        self.files = 0
        self.folders = 0
        self.errors = 0


async def _fetch_with_retry(
    client: httpx.AsyncClient, url: str, stats: CrawlStats, on_progress
) -> str | None:
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = await client.get(url)
            if resp.status_code in (429, 503):
                raise httpx.HTTPStatusError(
                    "throttled", request=resp.request, response=resp
                )
            resp.raise_for_status()
            return resp.text
        except (httpx.HTTPError, httpx.HTTPStatusError) as exc:
            if attempt == MAX_RETRIES:
                stats.errors += 1
                if on_progress:
                    on_progress(f"  ! giving up on {url} ({exc.__class__.__name__})")
                return None
            wait = BASE_BACKOFF_SECONDS * (2**attempt)
            if on_progress:
                on_progress(f"  ~ {url} slow/blocked, backing off {wait:.1f}s")
            await asyncio.sleep(wait)
    return None


async def crawl_server(
    server_name: str,
    base_url: str,
    on_progress=None,
) -> tuple[list[dict], CrawlStats]:
    """
    Crawl one server starting at base_url. Returns (entries, stats).
    entries: list of {parent_path, name, full_path, url, kind, size_bytes}
    Never follows a link outside base_url (containment check).
    """
    base_url = _normalize_base(base_url)
    base_prefix = base_url  # containment: every followed link must start with this

    entries: list[dict] = []
    stats = CrawlStats()
    queue: asyncio.Queue = asyncio.Queue()
    queue.put_nowait((base_url, ""))
    visited: set[str] = set()

    limits = httpx.Limits(max_connections=FOLDER_CONCURRENCY + 2)
    async with httpx.AsyncClient(
        verify=False,  # tolerate self-signed / unusual certs on local servers
        timeout=REQUEST_TIMEOUT,
        limits=limits,
        follow_redirects=True,
    ) as client:

        async def worker():
            while True:
                url, rel_path = await queue.get()
                try:
                    if url in visited:
                        continue
                    visited.add(url)

                    html = await _fetch_with_retry(client, url, stats, on_progress)
                    if html is None:
                        continue

                    for name, link_url, is_folder in _extract_links(html, url):
                        if not link_url.startswith(base_prefix):
                            continue  # never wander outside the server's given path
                        if link_url in visited:
                            continue

                        full_path = rel_path + name + ("/" if is_folder else "")
                        kind = classify(name, is_folder)
                        entries.append(
                            {
                                "parent_path": rel_path,
                                "name": name,
                                "full_path": full_path,
                                "url": link_url,
                                "kind": kind,
                                "size_bytes": None,
                            }
                        )
                        if is_folder:
                            stats.folders += 1
                            queue.put_nowait((link_url, full_path))
                        else:
                            stats.files += 1

                    if on_progress and (stats.files + stats.folders) % 25 == 0:
                        on_progress(
                            f"  {server_name}: {stats.folders} folders, "
                            f"{stats.files} files so far..."
                        )
                finally:
                    queue.task_done()

        workers = [asyncio.create_task(worker()) for _ in range(FOLDER_CONCURRENCY)]
        await queue.join()
        for w in workers:
            w.cancel()
        await asyncio.gather(*workers, return_exceptions=True)

    return entries, stats
