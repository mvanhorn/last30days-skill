#!/usr/bin/env python3
"""Keyless Threads search through a real headless browser (Playwright).

Opens the public ``threads.com/search`` page logged out and reads the posts
the page itself renders. Threads server-renders the first page of search
results as JSON inside ``<script type="application/json" data-sjs>`` tags;
any GraphQL search response the page fetches afterwards is read the same way.

This file is a standalone helper: it imports nothing from ``lib`` so the
skill can run it under any interpreter that has Playwright, including
``uv run --with playwright``. It prints one JSON object on stdout:

    {"posts": [...raw post dicts...], "pages": [...], "error": "..."?}

Exit code 0 means the browser ran; ``posts`` may still be empty.
"""

from __future__ import annotations

import sys

# Run by path, Python puts this file's directory (lib/) first on sys.path,
# where lib/http.py would shadow the stdlib ``http`` package Playwright and
# urllib import. Drop that entry before any other import.
if __name__ == "__main__":
    import os as _os

    _here = _os.path.realpath(_os.path.dirname(_os.path.abspath(__file__)))
    sys.path[:] = [p for p in sys.path
                   if _os.path.realpath(_os.path.abspath(p or ".")) != _here]

import argparse
import json
import re
import time
from typing import Any, Dict, Iterator, List
from urllib.parse import quote

SEARCH_URL = "https://www.threads.com/search?q={q}"
# Logged out, ``serp_type=default`` is the fresh mix (mostly the last days)
# and a bare ``?q=`` is the all-time top list. ``filter=recent`` redirects to
# ``serp_type=default`` and ``serp_type=recent`` is ignored, so neither is used.
MODE_SUFFIX = {"recent": "&serp_type=default", "top": ""}
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_SJS_RE = re.compile(
    r'<script type="application/json"[^>]*data-sjs[^>]*>(.*?)</script>', re.S
)
_LOGIN_HINTS = ("log in", "login", "sign up")

# Fields worth sending back; the raw post object is ~60 keys and mostly media.
_KEEP_KEYS = (
    "pk", "id", "code", "taken_at", "caption", "like_count", "user",
    "text_post_app_info", "permalink", "canonical_url", "media_type",
)


def _iter_posts(node: Any) -> Iterator[Dict[str, Any]]:
    """Yield every Threads post object inside a decoded JSON tree."""
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            if "taken_at" in cur and "user" in cur and ("code" in cur or "pk" in cur):
                yield cur
                continue
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)


def _slim(post: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: post.get(k) for k in _KEEP_KEYS if k in post}
    user = post.get("user") or {}
    if isinstance(user, dict):
        out["user"] = {
            "username": user.get("username"),
            "full_name": user.get("full_name"),
            "is_verified": user.get("is_verified"),
        }
    info = post.get("text_post_app_info") or {}
    if isinstance(info, dict):
        out["text_post_app_info"] = {
            k: info.get(k)
            for k in ("direct_reply_count", "repost_count", "quote_count", "reshare_count")
        }
    return out


def posts_from_html(html: str) -> List[Dict[str, Any]]:
    """Extract search-result posts from server-rendered Threads HTML."""
    posts: List[Dict[str, Any]] = []
    for blob in _SJS_RE.findall(html):
        if "taken_at" not in blob or "searchResults" not in blob:
            continue
        try:
            data = json.loads(blob)
        except ValueError:
            continue
        posts.extend(_iter_posts(data))
    return posts


def posts_from_json_text(text: str) -> List[Dict[str, Any]]:
    """Extract posts from a GraphQL response body (may be NDJSON)."""
    if "taken_at" not in text:
        return []
    posts: List[Dict[str, Any]] = []
    for chunk in text.splitlines() or [text]:
        chunk = chunk.strip()
        if chunk.startswith("for (;;);"):
            chunk = chunk[len("for (;;);"):]
        if not chunk.startswith("{"):
            continue
        try:
            posts.extend(_iter_posts(json.loads(chunk)))
        except ValueError:
            continue
    return posts


def _launch(p, headless: bool):
    """Bundled Chromium first, then an installed Chrome as a fallback."""
    errors = []
    for kwargs in ({}, {"channel": "chrome"}):
        try:
            return p.chromium.launch(headless=headless, **kwargs)
        except Exception as exc:  # noqa: BLE001 - report every attempt
            first = (str(exc).splitlines() or [""])[0]
            errors.append(f"{kwargs.get('channel', 'chromium')}: {first}")
    raise RuntimeError(
        "no browser could start ("
        + "; ".join(errors)
        + "). Run: python -m playwright install chromium"
    )


def search(queries: List[str], modes: List[str], timeout_s: float, scrolls: int, headless: bool) -> Dict[str, Any]:
    """Run each mode; within a mode try ``queries`` in order until one hits.

    Threads answers "No results." to long multi-word queries, so the caller
    passes the query plus shorter fallbacks.
    """
    from playwright.sync_api import sync_playwright

    deadline = time.monotonic() + timeout_s
    seen: Dict[str, Dict[str, Any]] = {}
    pages: List[Dict[str, Any]] = []
    login_wall = False

    def add(found: List[Dict[str, Any]], mode: str) -> int:
        added = 0
        for post in found:
            key = str(post.get("pk") or post.get("code") or "")
            if key and key not in seen:
                slim = _slim(post)
                slim["_mode"] = mode
                seen[key] = slim
                added += 1
        return added

    with sync_playwright() as p:
        browser = _launch(p, headless)
        try:
            ctx = browser.new_context(locale="en-US", user_agent=USER_AGENT)
            page = ctx.new_page()
            current = {"mode": modes[0] if modes else "recent"}

            def on_response(resp):
                if "/graphql" not in resp.url:
                    return
                try:
                    body = resp.text()
                except Exception:  # noqa: BLE001 - body may be gone
                    return
                add(posts_from_json_text(body), current["mode"])

            page.on("response", on_response)
            for mode in modes:
                current["mode"] = mode
                for query in queries:
                    remaining_ms = (deadline - time.monotonic()) * 1000
                    if remaining_ms < 3000:
                        break
                    url = SEARCH_URL.format(q=quote(query)) + MODE_SUFFIX.get(mode, "")
                    before = len(seen)
                    found: List[Dict[str, Any]] = []
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=min(remaining_ms, 30000))
                        for _ in range(scrolls):
                            if time.monotonic() > deadline - 2:
                                break
                            page.mouse.wheel(0, 5000)
                            page.wait_for_timeout(1200)
                        html = page.content()
                        found = posts_from_html(html)
                        add(found, mode)
                        if not found and "searchResults" not in html:
                            wait = max(500, min(2000, (deadline - time.monotonic()) * 1000))
                            body = (page.inner_text("body", timeout=wait) or "").lower()[:2000]
                            if any(h in body for h in _LOGIN_HINTS):
                                login_wall = True
                    except Exception as exc:  # noqa: BLE001 - keep other modes
                        first = (str(exc).splitlines() or [""])[0]
                        pages.append({"mode": mode, "query": query, "error": first})
                        continue
                    got = len(seen) - before
                    pages.append({"mode": mode, "query": query, "url": page.url, "new_posts": got})
                    # A query that rendered posts answered this mode, even when
                    # every post was already seen; shorter fallbacks would only
                    # broaden it.
                    if found or got:
                        break
        finally:
            browser.close()

    out: Dict[str, Any] = {"posts": list(seen.values()), "pages": pages}
    if not seen and login_wall:
        out["error"] = "login_wall: Threads showed a login page instead of results"
    elif not seen and pages and all("error" in pg for pg in pages):
        out["error"] = "; ".join(pg["error"] for pg in pages)
    return out


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("query", nargs="+", help="query, then shorter fallbacks")
    ap.add_argument("--modes", default="recent,top", help="comma list of recent,top")
    ap.add_argument("--timeout", type=float, default=60.0, help="total seconds")
    ap.add_argument("--stop-epoch", type=float, default=None,
                    help="absolute wall-clock time (time.time()) to stop by")
    ap.add_argument("--scrolls", type=int, default=1)
    ap.add_argument("--headed", action="store_true", help="show the browser (debug)")
    args = ap.parse_args(argv)

    modes = [m.strip() for m in args.modes.split(",") if m.strip() in MODE_SUFFIX]
    budget = args.timeout
    if args.stop_epoch is not None:
        # Startup (uv resolving Playwright, imports) already spent part of
        # the caller's window; stop by its absolute end, with room to print.
        budget = min(budget, args.stop_epoch - time.time() - 2)
    try:
        result = search(args.query, modes or ["recent"], max(1.0, budget), max(0, args.scrolls), not args.headed)
    except ImportError:
        result = {"posts": [], "error": "playwright_missing: pip install playwright && python -m playwright install chromium"}
    except Exception as exc:  # noqa: BLE001 - always answer with JSON
        first = (str(exc).splitlines() or [""])[0]
        result = {"posts": [], "error": f"{type(exc).__name__}: {first}"}
    # ASCII-only JSON: one "\n"-terminated line whatever the posts contain
    # (U+2028, emoji) and whatever the stdout encoding.
    json.dump(result, sys.stdout, ensure_ascii=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
