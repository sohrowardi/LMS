"""
The crawler.

Each configured server gets its own independent asyncio task. A dead,
slow, or self-signed server can only ever fail its own task — it never
takes down the crawl of the other servers, and every failure is caught,
logged into crawl_status, and swallowed.

Because we can't rely on an API, we parse raw HTML `<a href>` tags the
way a browser rendering an Apache/Nginx `autoindex` or IIS listing
would, and recurse into every folder link we find.
"""
from __future__ import annotations

import asyncio
import logging
import ssl
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse, parse_qs

import httpx
from bs4 import BeautifulSoup

from . import config, db

log = logging.getLogger("lms.crawler")

# Links that are noise in a directory listing, never real content.
_SKIP_TEXT = {"parent directory", "..", "name", "last modified", "size", "description"}
_SKIP_HREF_PREFIXES = ("?", "#", "mailto:", "javascript:")

_now = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")


def _is_sort_link(href: str) -> bool:
    """Apache emits column-sort links like '?C=N;O=D' — not content."""
    if not href.startswith("?"):
        return False
    qs = parse_qs(href.lstrip("?"))
    return "C" in qs or "O" in qs or href in ("?", "")


def _ext_of(name: str) -> str:
    dot = name.rfind(".")
    return name[dot + 1 :].lower() if dot > 0 else ""


class ServerCrawler:
    """Crawls one server's directory tree, isolated from all others."""

    def __init__(self, client: httpx.AsyncClient, name: str, root_url: str,
                 order: int, max_depth: int, concurrency: int):
        self.client = client
        self.name = name
        self.root_url = root_url
        self.order = order
        self.max_depth = max_depth
        self.sem = asyncio.Semaphore(concurrency)
        self.visited: set[str] = set()
        self.rows: list[tuple] = []  # (name, url, parent_url, is_dir, ext, depth, order)
        self.last_error: str | None = None

    async def run(self) -> None:
        await db.set_status(self.name, status="running", started_at=_now())
        try:
            await self._crawl_folder(self.root_url, depth=0)
            await db.replace_server_items(self.name, self.rows)
            await db.set_status(self.name, status="done", finished_at=_now())
        except Exception as exc:  # noqa: BLE001 - a bad server must never kill the run
            log.warning("Server %s failed: %s", self.name, exc)
            await db.set_status(
                self.name, status="error", error_message=str(exc)[:500], finished_at=_now()
            )

    async def _fetch(self, url: str) -> str | None:
        async with self.sem:
            try:
                resp = await self.client.get(url)
                resp.raise_for_status()
                ctype = resp.headers.get("content-type", "")
                if "text/html" not in ctype and "<html" not in resp.text[:200].lower():
                    return None
                return resp.text
            except (httpx.HTTPError, ssl.SSLError) as exc:
                log.info("Fetch failed for %s: %s", url, exc)
                self.last_error = str(exc)
                return None

    async def _crawl_folder(self, url: str, depth: int) -> None:
        if url in self.visited or depth > self.max_depth:
            return
        self.visited.add(url)

        html = await self._fetch(url)
        await db.bump_status(self.name, pages=1)
        if html is None:
            if depth == 0:
                # Can't even reach the server root — this is a real failure,
                # not just one dead subfolder link. Surface it as an error
                # rather than silently reporting an empty "done" crawl.
                raise ConnectionError(self.last_error or f"could not reach {url}")
            return

        soup = BeautifulSoup(html, "html.parser")
        child_dirs: list[str] = []

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            text = a.get_text(strip=True).lower()

            if not href or href.startswith(_SKIP_HREF_PREFIXES) or _is_sort_link(href):
                continue
            if text in _SKIP_TEXT or href.strip("/") in ("..", ""):
                continue

            absolute = urljoin(url, href)

            # Never escape the configured root — protects against ../ tricks
            # and absolute links pointing elsewhere on the same host/path.
            if not absolute.startswith(self.root_url):
                continue

            parsed = urlparse(absolute)
            is_dir = absolute.endswith("/")
            raw_name = href.rstrip("/").split("/")[-1] or parsed.path.rstrip("/").split("/")[-1]
            name = _unquote(raw_name)
            if not name:
                continue

            ext = "" if is_dir else _ext_of(name)
            self.rows.append((name, absolute, url, is_dir, ext, depth + 1, self.order))

            if is_dir and absolute not in self.visited:
                child_dirs.append(absolute)
            else:
                await db.bump_status(self.name, files=1)

        for child in child_dirs:
            await self._crawl_folder(child, depth + 1)


def _unquote(s: str) -> str:
    from urllib.parse import unquote

    return unquote(s)


async def crawl_all(progress_cb=None) -> None:
    """Kick off every configured server's crawl concurrently."""
    servers = config.read_servers()
    await db.reset_status(servers)

    if not servers:
        log.warning("No servers configured in servers.txt — nothing to crawl.")
        return

    timeout = httpx.Timeout(config.get_request_timeout())
    limits = httpx.Limits(max_connections=50, max_keepalive_connections=20)

    # verify=False: local media boxes commonly use self-signed certs.
    async with httpx.AsyncClient(
        timeout=timeout, limits=limits, verify=False, follow_redirects=True,
        headers={"User-Agent": "LMS/1.0 (Local Media Search crawler)"},
    ) as client:
        tasks = [
            ServerCrawler(
                client, s["name"], s["url"], s["order"],
                config.get_max_depth(), config.get_concurrency_per_server(),
            ).run()
            for s in servers
        ]
        # return_exceptions=True: one server's unexpected crash can never
        # cancel the others, even if it slips past ServerCrawler.run()'s
        # own try/except.
        await asyncio.gather(*tasks, return_exceptions=True)
