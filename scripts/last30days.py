#!/usr/bin/env python3
"""
last30days - Research a topic from the last 30 days on Reddit + X + YouTube + Web.

Usage:
    python3 last30days.py <topic> [options]

Options:
    --mock              Use fixtures instead of real API calls
    --emit=MODE         Output mode: compact|json|md|context|path (default: compact)
    --sources=MODE      Source selection: auto|reddit|x|both (default: auto)
    --quick             Faster research with fewer sources (8-12 each)
    --deep              Comprehensive research with more sources (50-70 Reddit, 40-60 X)
    --debug             Enable verbose debug logging
    --store             Persist findings to SQLite database
    --diagnose          Show source availability diagnostics and exit
"""

import argparse
import atexit
import json
import os
import signal
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

# Add lib to path
SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

# ---------------------------------------------------------------------------
# Global timeout & child process management
# ---------------------------------------------------------------------------
_child_pids: set = set()
_child_pids_lock = threading.Lock()

TIMEOUT_PROFILES = {
    "quick":   {"global": 90,  "future": 30, "reddit_future": 60,  "youtube_future": 60,  "http": 15, "enrich_per": 8,  "enrich_total": 30, "enrich_max_items": 10},
    "default": {"global": 180, "future": 60, "reddit_future": 90,  "youtube_future": 90,  "http": 30, "enrich_per": 15, "enrich_total": 45, "enrich_max_items": 15},
    "deep":    {"global": 300, "future": 90, "reddit_future": 120, "youtube_future": 120, "http": 30, "enrich_per": 15, "enrich_total": 60, "enrich_max_items": 25},
}


def register_child_pid(pid: int):
    """Track a child process for cleanup."""
    with _child_pids_lock:
        _child_pids.add(pid)


def unregister_child_pid(pid: int):
    """Remove a child process from tracking."""
    with _child_pids_lock:
        _child_pids.discard(pid)


def _cleanup_children():
    """Kill all tracked child processes."""
    with _child_pids_lock:
        pids = list(_child_pids)
    for pid in pids:
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass


atexit.register(_cleanup_children)


def _install_global_timeout(timeout_seconds: int):
    """Install a global timeout watchdog.

    Uses SIGALRM on Unix, threading.Timer as fallback.
    """
    if hasattr(signal, 'SIGALRM'):
        def _handler(signum, frame):
            sys.stderr.write(f"\n[TIMEOUT] Global timeout ({timeout_seconds}s) exceeded. Cleaning up.\n")
            sys.stderr.flush()
            _cleanup_children()
            sys.exit(1)
        signal.signal(signal.SIGALRM, _handler)
        signal.alarm(timeout_seconds)
    else:
        # Windows fallback
        def _watchdog():
            sys.stderr.write(f"\n[TIMEOUT] Global timeout ({timeout_seconds}s) exceeded. Cleaning up.\n")
            sys.stderr.flush()
            _cleanup_children()
            os._exit(1)
        timer = threading.Timer(timeout_seconds, _watchdog)
        timer.daemon = True
        timer.start()

from lib import (
    bird_x,
    dates,
    dedupe,
    entity_extract,
    env,
    hacker_news,
    http,
    models,
    normalize,
    openai_reddit,
    reddit_enrich,
    render,
    schema,
    score,
    ui,
    websearch,
    xai_x,
    youtube_yt,
)


def load_fixture(name: str) -> dict:
    """Load a fixture file."""
    fixture_path = SCRIPT_DIR.parent / "fixtures" / name
    if fixture_path.exists():
        with open(fixture_path) as f:
            return json.load(f)
    return {}


def _search_reddit(
    topic: str,
    config: dict,
    selected_models: dict,
    from_date: str,
    to_date: str,
    depth: str,
    mock: bool,
) -> tuple:
    """Search Reddit via OpenAI (runs in thread).

    Returns:
        Tuple of (reddit_items, raw_openai, error)
    """
    raw_openai = None
    reddit_error = None

    if mock:
        raw_openai = load_fixture("openai_sample.json")
    else:
        try:
            raw_openai = openai_reddit.search_reddit(
                config["OPENAI_API_KEY"],
                selected_models["openai"],
                topic,
                from_date,
                to_date,
                depth=depth,
            )
        except http.HTTPError as e:
            raw_openai = {"error": str(e)}
            reddit_error = f"API error: {e}"
        except Exception as e:
            raw_openai = {"error": str(e)}
            reddit_error = f"{type(e).__name__}: {e}"

    # Parse response
    reddit_items = openai_reddit.parse_reddit_response(raw_openai or {})

    # Quick retry with simpler query if few results
    if len(reddit_items) < 5 and not mock and not reddit_error:
        core = openai_reddit._extract_core_subject(topic)
        if core.lower() != topic.lower():
            try:
                retry_raw = openai_reddit.search_reddit(
                    config["OPENAI_API_KEY"],
                    selected_models["openai"],
                    core,
                    from_date, to_date,
                    depth=depth,
                )
                retry_items = openai_reddit.parse_reddit_response(retry_raw)
                # Add items not already found (by URL)
                existing_urls = {item.get("url") for item in reddit_items}
                for item in retry_items:
                    if item.get("url") not in existing_urls:
                        reddit_items.append(item)
            except Exception:
                pass

    # Subreddit-targeted fallback if still < 3 results
    if len(reddit_items) < 3 and not mock and not reddit_error:
        sub_query = openai_reddit._build_subreddit_query(topic)
        try:
            sub_raw = openai_reddit.search_reddit(
                config["OPENAI_API_KEY"],
                selected_models["openai"],
                sub_query,
                from_date, to_date,
                depth=depth,
            )
            sub_items = openai_reddit.parse_reddit_response(sub_raw)
            existing_urls = {item.get("url") for item in reddit_items}
            for item in sub_items:
                if item.get("url") not in existing_urls:
                    reddit_items.append(item)
        except Exception:
            pass

    return reddit_items, raw_openai, reddit_error


def _search_x(
    topic: str,
    config: dict,
    selected_models: dict,
    from_date: str,
    to_date: str,
    depth: str,
    mock: bool,
    x_source: str = "xai",
) -> tuple:
    """Search X via Bird CLI or xAI (runs in thread).

    Args:
        x_source: 'bird' or 'xai' - which backend to use

    Returns:
        Tuple of (x_items, raw_response, error)
    """
    raw_response = None
    x_error = None

    if mock:
        raw_response = load_fixture("xai_sample.json")
        x_items = xai_x.parse_x_response(raw_response or {})
        return x_items, raw_response, x_error

    # Use Bird if specified
    if x_source == "bird":
        try:
            raw_response = bird_x.search_x(
                topic,
                from_date,
                to_date,
                depth=depth,
            )
        except Exception as e:
            raw_response = {"error": str(e)}
            x_error = f"{type(e).__name__}: {e}"

        x_items = bird_x.parse_bird_response(raw_response or {})

        # Check for error in response (Bird returns list on success, dict on error)
        if raw_response and isinstance(raw_response, dict) and raw_response.get("error") and not x_error:
            x_error = raw_response["error"]

        return x_items, raw_response, x_error

    # Use xAI (original behavior)
    try:
        raw_response = xai_x.search_x(
            config["XAI_API_KEY"],
            selected_models["xai"],
            topic,
            from_date,
            to_date,
            depth=depth,
        )
    except http.HTTPError as e:
        raw_response = {"error": str(e)}
        x_error = f"API error: {e}"
    except Exception as e:
        raw_response = {"error": str(e)}
        x_error = f"{type(e).__name__}: {e}"

    x_items = xai_x.parse_x_response(raw_response or {})

    return x_items, raw_response, x_error


def _search_youtube(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str,
) -> tuple:
    """Search YouTube via yt-dlp (runs in thread).

    Returns:
        Tuple of (youtube_items, youtube_error)
    """
    youtube_error = None

    try:
        response = youtube_yt.search_and_transcribe(
            topic, from_date, to_date, depth=depth,
        )
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"

    youtube_items = youtube_yt.parse_youtube_response(response)

    if response.get("error"):
        youtube_error = response["error"]

    return youtube_items, youtube_error


def _search_hn(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str,
) -> tuple:
    """Search Hacker News via Algolia API (runs in thread).

    Returns:
        Tuple of (hn_items, hn_error)
    """
    hn_error = None

    try:
        raw_response = hacker_news.search_hacker_news(
            topic, from_date, to_date, depth=depth,
        )
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"

    hn_items = hacker_news.parse_hn_response(raw_response)

    if raw_response.get("error") and not hn_error:
        hn_error = raw_response["error"]

    return hn_items, hn_error


def _search_web(
    topic: str,
    config: dict,
    from_date: str,
    to_date: str,
    depth: str,
) -> tuple:
    """Search the web via native API backend (runs in thread).

    Uses the best available backend: Parallel AI > Brave > OpenRouter.

    Returns:
        Tuple of (web_items, web_error)
        web_items are raw dicts ready for websearch.normalize_websearch_items()
    """
    from lib import brave_search, parallel_search, openrouter_search

    backend = env.get_web_search_source(config)
    if not backend:
        return [], "No web search API keys configured"

    web_error = None
    raw_results = []

    try:
        if backend == "parallel":
            raw_results = parallel_search.search_web(
                topic, from_date, to_date, config["PARALLEL_API_KEY"], depth=depth,
            )
        elif backend == "brave":
            raw_results = brave_search.search_web(
                topic, from_date, to_date, config["BRAVE_API_KEY"], depth=depth,
            )
        elif backend == "openrouter":
            raw_results = openrouter_search.search_web(
                topic, from_date, to_date, config["OPENROUTER_API_KEY"], depth=depth,
            )
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"

    # Add IDs and date_confidence for websearch.normalize_websearch_items()
    for i, item in enumerate(raw_results):
        item.setdefault("id", f"W{i+1}")
        if item.get("date") and not item.get("date_confidence"):
            item["date_confidence"] = "med"
        elif not item.get("date"):
            item["date_confidence"] = "low"
        item.setdefault("why_relevant", "")

    return raw_results, web_error


def _run_supplemental(
    topic: str,
    reddit_items: list,
    x_items: list,
    from_date: str,
    to_date: str,
    depth: str,
    x_source: str,
    progress: ui.ProgressDisplay = None,
    skip_reddit: bool = False,
) -> tuple:
    """Run Phase 2 supplemental searches based on entities from Phase 1.

    Extracts handles/subreddits from initial results, then runs targeted
    searches to find additional content the broad search missed.

    Args:
        topic: Original search topic
        reddit_items: Phase 1 Reddit items (raw dicts)
        x_items: Phase 1 X items (raw dicts)
        from_date: Start date
        to_date: End date
        depth: Research depth
        x_source: 'bird' or 'xai'
        progress: Optional progress display
        skip_reddit: If True, skip Reddit supplemental (e.g. rate-limited)

    Returns:
        Tuple of (supplemental_reddit, supplemental_x)
    """
    # Depth-dependent caps
    if depth == "default":
        max_handles = 3
        max_subs = 3
        count_per = 3
    else:  # deep
        max_handles = 5
        max_subs = 5
        count_per = 5

    # Extract entities from Phase 1 results
    entities = entity_extract.extract_entities(
        reddit_items, x_items,
        max_handles=max_handles,
        max_subreddits=max_subs,
    )

    has_handles = entities["x_handles"] and x_source == "bird"
    has_subs = entities["reddit_subreddits"] and not skip_reddit

    if not has_handles and not has_subs:
        return [], []

    parts = []
    if has_handles:
        parts.append(f"@{', @'.join(entities['x_handles'][:3])}")
    if has_subs:
        parts.append(f"r/{', r/'.join(entities['reddit_subreddits'][:3])}")
    sys.stderr.write(f"[Phase 2] Drilling into {' + '.join(parts)}\n")
    sys.stderr.flush()

    supplemental_reddit = []
    supplemental_x = []

    # Collect existing URLs to avoid adding duplicates before dedupe
    existing_urls = set()
    for item in reddit_items:
        existing_urls.add(item.get("url", ""))
    for item in x_items:
        existing_urls.add(item.get("url", ""))

    # Run supplemental searches in parallel
    reddit_future = None
    x_future = None

    with ThreadPoolExecutor(max_workers=2) as executor:
        if has_subs:
            reddit_future = executor.submit(
                openai_reddit.search_subreddits,
                entities["reddit_subreddits"],
                topic,
                from_date,
                to_date,
                count_per,
            )

        if has_handles:
            x_future = executor.submit(
                bird_x.search_handles,
                entities["x_handles"],
                topic,
                from_date,
                count_per,
            )

        if reddit_future:
            try:
                raw_reddit = reddit_future.result(timeout=30)
                # Filter out URLs already found in Phase 1
                supplemental_reddit = [
                    item for item in raw_reddit
                    if item.get("url", "") not in existing_urls
                ]
            except TimeoutError:
                sys.stderr.write("[Phase 2] Supplemental Reddit timed out (30s)\n")
            except Exception as e:
                sys.stderr.write(f"[Phase 2] Supplemental Reddit error: {e}\n")

        if x_future:
            try:
                raw_x = x_future.result(timeout=30)
                supplemental_x = [
                    item for item in raw_x
                    if item.get("url", "") not in existing_urls
                ]
            except TimeoutError:
                sys.stderr.write("[Phase 2] Supplemental X timed out (30s)\n")
            except Exception as e:
                sys.stderr.write(f"[Phase 2] Supplemental X error: {e}\n")

    if supplemental_reddit or supplemental_x:
        sys.stderr.write(
            f"[Phase 2] +{len(supplemental_reddit)} Reddit, +{len(supplemental_x)} X\n"
        )
        sys.stderr.flush()

    return supplemental_reddit, supplemental_x


def run_research(
    topic: str,
    sources: str,
    config: dict,
    selected_models: dict,
    from_date: str,
    to_date: str,
    depth: str = "default",
    mock: bool = False,
    progress: ui.ProgressDisplay = None,
    x_source: str = "xai",
    run_youtube: bool = False,
    timeouts: dict = None,
) -> tuple:
    """Run the research pipeline.

    Returns:
        Tuple of (reddit_items, x_items, youtube_items, web_items, hn_items, web_needed,
                  raw_openai, raw_xai, raw_reddit_enriched,
                  reddit_error, x_error, youtube_error, web_error, hn_error)

    Note: web_needed is True when web search should be performed by the assistant
    (i.e., no native web search API keys are configured). When native web search
    runs, web_items will be populated and web_needed will be False.
    """
    if timeouts is None:
        timeouts = TIMEOUT_PROFILES[depth]
    future_timeout = timeouts["future"]

    reddit_items = []
    x_items = []
    youtube_items = []
    web_items = []
    hn_items = []
    raw_openai = None
    raw_xai = None
    raw_reddit_enriched = []
    reddit_error = None
    x_error = None
    youtube_error = None
    web_error = None
    hn_error = None

    # Determine web search mode
    do_web = sources in ("all", "web", "reddit-web", "x-web")
    web_backend = env.get_web_search_source(config) if do_web else None
    web_needed = do_web and not web_backend

    # Web-only mode
    if sources == "web":
        if web_backend:
            # Native web search available — run it
            sys.stderr.write(f"[web] Searching via {web_backend}\n")
            sys.stderr.flush()
            try:
                web_items, web_error = _search_web(topic, config, from_date, to_date, depth)
                if web_error and progress:
                    progress.show_error(f"Web error: {web_error}")
            except Exception as e:
                web_error = f"{type(e).__name__}: {e}"
                if progress:
                    progress.show_error(f"Web error: {e}")
            sys.stderr.write(f"[web] {len(web_items)} results\n")
            sys.stderr.flush()
        else:
            # No native backend — assistant handles WebSearch
            if progress:
                progress.start_web_only()
                progress.end_web_only()
        # Still run YouTube in web-only mode if yt-dlp is available
        if run_youtube:
            if progress:
                progress.start_youtube()
            try:
                youtube_items, youtube_error = _search_youtube(topic, from_date, to_date, depth)
                if youtube_error and progress:
                    progress.show_error(f"YouTube error: {youtube_error}")
            except Exception as e:
                youtube_error = f"{type(e).__name__}: {e}"
                if progress:
                    progress.show_error(f"YouTube error: {e}")
            if progress:
                progress.end_youtube(len(youtube_items))
        return reddit_items, x_items, youtube_items, web_items, hn_items, web_needed, raw_openai, raw_xai, raw_reddit_enriched, reddit_error, x_error, youtube_error, web_error, hn_error

    # Determine which searches to run
    do_reddit = sources in ("both", "reddit", "all", "reddit-web")
    do_x = sources in ("both", "x", "all", "x-web")

    # Run Reddit, X, YouTube, HN, and Web searches in parallel
    reddit_future = None
    x_future = None
    youtube_future = None
    hn_future = None
    web_future = None
    max_workers = 2 + (1 if run_youtube else 0) + (1 if web_backend else 0)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit searches
        if do_reddit:
            if progress:
                progress.start_reddit()
            reddit_future = executor.submit(
                _search_reddit, topic, config, selected_models,
                from_date, to_date, depth, mock
            )

        if do_x:
            if progress:
                progress.start_x()
            x_future = executor.submit(
                _search_x, topic, config, selected_models,
                from_date, to_date, depth, mock, x_source
            )

        if run_youtube:
            if progress:
                progress.start_youtube()
            youtube_future = executor.submit(
                _search_youtube, topic, from_date, to_date, depth
            )

        # HN is always searched (no API key needed)
        hn_future = executor.submit(
            _search_hn, topic, from_date, to_date, depth
        )

        if web_backend:
            sys.stderr.write(f"[web] Searching via {web_backend}\n")
            sys.stderr.flush()
            web_future = executor.submit(
                _search_web, topic, config, from_date, to_date, depth
            )

        # Collect results (with timeouts to prevent indefinite blocking)
        if reddit_future:
            reddit_timeout = timeouts.get("reddit_future", future_timeout)
            try:
                reddit_items, raw_openai, reddit_error = reddit_future.result(timeout=reddit_timeout)
                if reddit_error and progress:
                    progress.show_error(f"Reddit error: {reddit_error}")
            except TimeoutError:
                reddit_error = f"Reddit search timed out after {reddit_timeout}s"
                if progress:
                    progress.show_error(reddit_error)
            except Exception as e:
                reddit_error = f"{type(e).__name__}: {e}"
                if progress:
                    progress.show_error(f"Reddit error: {e}")
            if progress:
                progress.end_reddit(len(reddit_items))

        if x_future:
            try:
                x_items, raw_xai, x_error = x_future.result(timeout=future_timeout)
                if x_error and progress:
                    progress.show_error(f"X error: {x_error}")
            except TimeoutError:
                x_error = f"X search timed out after {future_timeout}s"
                if progress:
                    progress.show_error(x_error)
            except Exception as e:
                x_error = f"{type(e).__name__}: {e}"
                if progress:
                    progress.show_error(f"X error: {e}")
            if progress:
                progress.end_x(len(x_items))

        if youtube_future:
            yt_timeout = timeouts.get("youtube_future", future_timeout)
            try:
                youtube_items, youtube_error = youtube_future.result(timeout=yt_timeout)
                if youtube_error and progress:
                    progress.show_error(f"YouTube error: {youtube_error}")
            except TimeoutError:
                youtube_error = f"YouTube search timed out after {yt_timeout}s"
                if progress:
                    progress.show_error(youtube_error)
            except Exception as e:
                youtube_error = f"{type(e).__name__}: {e}"
                if progress:
                    progress.show_error(f"YouTube error: {e}")
            if progress:
                progress.end_youtube(len(youtube_items))

        if web_future:
            try:
                web_items, web_error = web_future.result(timeout=future_timeout)
                if web_error and progress:
                    progress.show_error(f"Web error: {web_error}")
            except TimeoutError:
                web_error = f"Web search timed out after {future_timeout}s"
                if progress:
                    progress.show_error(web_error)
            except Exception as e:
                web_error = f"{type(e).__name__}: {e}"
                if progress:
                    progress.show_error(f"Web error: {e}")
            sys.stderr.write(f"[web] {len(web_items)} results\n")
            sys.stderr.flush()

        # Collect HN results
        if hn_future:
            try:
                hn_items, hn_error = hn_future.result(timeout=future_timeout)
                if hn_error and progress:
                    progress.show_error(f"HN error: {hn_error}")
            except TimeoutError:
                hn_error = f"HN search timed out after {future_timeout}s"
                if progress:
                    progress.show_error(hn_error)
            except Exception as e:
                hn_error = f"{type(e).__name__}: {e}"
                if progress:
                    progress.show_error(f"HN error: {e}")

    # Enrich Reddit items with real data (parallel, capped)
    enrich_max = timeouts["enrich_max_items"]
    enrich_total_timeout = timeouts["enrich_total"]
    items_to_enrich = reddit_items[:enrich_max]
    rate_limited = False  # Set True if Reddit returns 429 during enrichment

    if items_to_enrich:
        if progress:
            progress.start_reddit_enrich(1, len(items_to_enrich))

        if mock:
            # Sequential mock enrichment (fast, no need for parallelism)
            for i, item in enumerate(items_to_enrich):
                if progress and i > 0:
                    progress.update_reddit_enrich(i + 1, len(items_to_enrich))
                try:
                    mock_thread = load_fixture("reddit_thread_sample.json")
                    reddit_items[i] = reddit_enrich.enrich_reddit_item(item, mock_thread)
                except Exception as e:
                    if progress:
                        progress.show_error(f"Enrich failed for {item.get('url', 'unknown')}: {e}")
                raw_reddit_enriched.append(reddit_items[i])
        else:
            # Parallel enrichment with bounded concurrency and total timeout
            # Uses short HTTP timeout (10s) and 1 retry to fail fast on 429
            completed_count = 0
            rate_limited = False
            with ThreadPoolExecutor(max_workers=5) as enrich_pool:
                futures = {
                    enrich_pool.submit(reddit_enrich.enrich_reddit_item, item): i
                    for i, item in enumerate(items_to_enrich)
                }
                try:
                    for future in as_completed(futures, timeout=enrich_total_timeout):
                        idx = futures[future]
                        completed_count += 1
                        if progress:
                            progress.update_reddit_enrich(completed_count, len(items_to_enrich))
                        try:
                            reddit_items[idx] = future.result(timeout=timeouts["enrich_per"])
                        except reddit_enrich.RedditRateLimitError:
                            rate_limited = True
                            if progress:
                                progress.show_error(
                                    "Reddit rate-limited (429) — skipping remaining enrichment"
                                )
                            # Cancel remaining futures and bail
                            for f in futures:
                                f.cancel()
                            break
                        except Exception as e:
                            if progress:
                                progress.show_error(
                                    f"Enrich failed for {items_to_enrich[idx].get('url', 'unknown')}: {e}"
                                )
                        raw_reddit_enriched.append(reddit_items[idx])
                except TimeoutError:
                    if progress:
                        progress.show_error(
                            f"Enrichment timed out after {enrich_total_timeout}s "
                            f"({completed_count}/{len(items_to_enrich)} done)"
                        )
                    # Keep unenriched items as-is
                    for idx in futures.values():
                        if reddit_items[idx] not in raw_reddit_enriched:
                            raw_reddit_enriched.append(reddit_items[idx])

        if progress:
            progress.end_reddit_enrich()

    # Phase 2: Supplemental search based on entities from Phase 1
    # Skip on --quick (speed matters), mock mode, or if Reddit is rate-limiting
    if depth != "quick" and not mock and (reddit_items or x_items):
        sup_reddit, sup_x = _run_supplemental(
            topic, reddit_items, x_items,
            from_date, to_date, depth, x_source, progress,
            skip_reddit=rate_limited,
        )
        if sup_reddit:
            reddit_items.extend(sup_reddit)
        if sup_x:
            x_items.extend(sup_x)

    return reddit_items, x_items, youtube_items, web_items, hn_items, web_needed, raw_openai, raw_xai, raw_reddit_enriched, reddit_error, x_error, youtube_error, web_error, hn_error


def main():
    # Fix Unicode output on Windows (cp1252 can't encode emoji)
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Research a topic from the last N days on Reddit + X"
    )
    parser.add_argument("topic", nargs="?", help="Topic to research")
    parser.add_argument("--mock", action="store_true", help="Use fixtures")
    parser.add_argument(
        "--emit",
        choices=["compact", "json", "md", "context", "path"],
        default="compact",
        help="Output mode",
    )
    parser.add_argument(
        "--sources",
        choices=["auto", "reddit", "x", "both"],
        default="auto",
        help="Source selection",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Faster research with fewer sources (8-12 each)",
    )
    parser.add_argument(
        "--deep",
        action="store_true",
        help="Comprehensive research with more sources (50-70 Reddit, 40-60 X)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose debug logging",
    )
    parser.add_argument(
        "--include-web",
        action="store_true",
        help="Include general web search alongside Reddit/X (lower weighted)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        choices=range(1, 31),
        metavar="N",
        help="Number of days to look back (1-30, default: 30)",
    )
    parser.add_argument(
        "--store",
        action="store_true",
        help="Persist findings to SQLite database (~/.local/share/last30days/research.db)",
    )
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="Show source availability diagnostics and exit",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        metavar="SECS",
        help="Global timeout in seconds (default: 180, quick: 90, deep: 300)",
    )

    args = parser.parse_args()

    # Enable debug logging if requested
    if args.debug:
        os.environ["LAST30DAYS_DEBUG"] = "1"
        # Re-import http to pick up debug flag
        from lib import http as http_module
        http_module.DEBUG = True

    # Determine depth
    if args.quick and args.deep:
        print("Error: Cannot use both --quick and --deep", file=sys.stderr)
        sys.exit(1)
    elif args.quick:
        depth = "quick"
    elif args.deep:
        depth = "deep"
    else:
        depth = "default"

    # Install global timeout watchdog
    timeouts = TIMEOUT_PROFILES[depth]
    global_timeout = args.timeout or timeouts["global"]
    _install_global_timeout(global_timeout)

    # Load config
    config = env.get_config()

    # Auto-detect Bird (no prompts - just use it if available)
    x_source_status = env.get_x_source_status(config)
    x_source = x_source_status["source"]  # 'bird', 'xai', or None

    # Auto-detect yt-dlp for YouTube search
    has_ytdlp = env.is_ytdlp_available()

    # --diagnose: show source availability and exit
    if args.diagnose:
        web_source = env.get_web_search_source(config)
        diag = {
            "openai": bool(config.get("OPENAI_API_KEY")),
            "xai": bool(config.get("XAI_API_KEY")),
            "x_source": x_source_status["source"],
            "bird_installed": x_source_status["bird_installed"],
            "bird_authenticated": x_source_status["bird_authenticated"],
            "bird_username": x_source_status.get("bird_username"),
            "youtube": has_ytdlp,
            "web_search_backend": web_source,
            "parallel_ai": bool(config.get("PARALLEL_API_KEY")),
            "brave": bool(config.get("BRAVE_API_KEY")),
            "openrouter": bool(config.get("OPENROUTER_API_KEY")),
        }
        print(json.dumps(diag, indent=2))
        sys.exit(0)

    # Validate topic (--diagnose doesn't need one)
    if not args.topic:
        print("Error: Please provide a topic to research.", file=sys.stderr)
        print("Usage: python3 last30days.py <topic> [options]", file=sys.stderr)
        sys.exit(1)

    # Initialize progress display with topic
    progress = ui.ProgressDisplay(args.topic, show_banner=True)

    # Show diagnostic banner when sources are missing
    web_source = env.get_web_search_source(config)
    diag = {
        "openai": bool(config.get("OPENAI_API_KEY")),
        "xai": bool(config.get("XAI_API_KEY")),
        "x_source": x_source_status["source"],
        "bird_installed": x_source_status["bird_installed"],
        "bird_authenticated": x_source_status["bird_authenticated"],
        "bird_username": x_source_status.get("bird_username"),
        "youtube": has_ytdlp,
        "web_search_backend": web_source,
    }
    ui.show_diagnostic_banner(diag)

    # Check available sources (accounting for Bird auto-detection)
    available = env.get_available_sources(config)

    # Override available if Bird is ready
    if x_source == 'bird':
        if available == 'reddit':
            available = 'both'  # Now have both Reddit + X (via Bird)
        elif available == 'web':
            available = 'x'  # Now have X via Bird

    # Mock mode can work without keys
    if args.mock:
        if args.sources == "auto":
            sources = "both"
        else:
            sources = args.sources
    else:
        # Validate requested sources against available
        sources, error = env.validate_sources(args.sources, available, args.include_web)
        if error:
            # If it's a warning about WebSearch fallback, print but continue
            if "WebSearch fallback" in error:
                print(f"Note: {error}", file=sys.stderr)
            else:
                print(f"Error: {error}", file=sys.stderr)
                sys.exit(1)

    # Get date range
    from_date, to_date = dates.get_date_range(args.days)

    # Check what keys are missing for promo messaging
    missing_keys = env.get_missing_keys(config)

    # Show NUX / promo for missing keys BEFORE research
    if missing_keys != 'none':
        progress.show_promo(missing_keys, diag=diag)

    # Select models
    if args.mock:
        # Use mock models
        mock_openai_models = load_fixture("models_openai_sample.json").get("data", [])
        mock_xai_models = load_fixture("models_xai_sample.json").get("data", [])
        selected_models = models.get_models(
            {
                "OPENAI_API_KEY": "mock",
                "XAI_API_KEY": "mock",
                **config,
            },
            mock_openai_models,
            mock_xai_models,
        )
    else:
        selected_models = models.get_models(config)

    # Determine mode string
    if sources == "all":
        mode = "all"  # reddit + x + web
    elif sources == "both":
        mode = "both"  # reddit + x
    elif sources == "reddit":
        mode = "reddit-only"
    elif sources == "reddit-web":
        mode = "reddit-web"
    elif sources == "x":
        mode = "x-only"
    elif sources == "x-web":
        mode = "x-web"
    elif sources == "web":
        mode = "web-only"
    else:
        mode = sources

    # Run research
    reddit_items, x_items, youtube_items, web_items, hn_items, web_needed, raw_openai, raw_xai, raw_reddit_enriched, reddit_error, x_error, youtube_error, web_error, hn_error = run_research(
        args.topic,
        sources,
        config,
        selected_models,
        from_date,
        to_date,
        depth,
        args.mock,
        progress,
        x_source=x_source or "xai",
        run_youtube=has_ytdlp,
        timeouts=timeouts,
    )

    # Processing phase
    progress.start_processing()

    # Normalize items
    normalized_reddit = normalize.normalize_reddit_items(reddit_items, from_date, to_date)
    normalized_x = normalize.normalize_x_items(x_items, from_date, to_date)
    normalized_youtube = normalize.normalize_youtube_items(youtube_items, from_date, to_date) if youtube_items else []
    normalized_web = websearch.normalize_websearch_items(web_items, from_date, to_date) if web_items else []
    normalized_hn = normalize.normalize_hn_items(hn_items, from_date, to_date) if hn_items else []

    # Hard date filter: exclude items with verified dates outside the range
    # This is the safety net - even if prompts let old content through, this filters it
    filtered_reddit = normalize.filter_by_date_range(normalized_reddit, from_date, to_date)
    filtered_x = normalize.filter_by_date_range(normalized_x, from_date, to_date)
    # YouTube: skip hard date filter — youtube_yt.py already applies a soft filter
    # that prefers recent videos but keeps older ones for evergreen topics.
    # YouTube content has a longer shelf life than tweets/posts.
    filtered_youtube = normalized_youtube
    filtered_web = normalize.filter_by_date_range(normalized_web, from_date, to_date) if normalized_web else []
    filtered_hn = normalize.filter_by_date_range(normalized_hn, from_date, to_date) if normalized_hn else []

    # Score items
    scored_reddit = score.score_reddit_items(filtered_reddit)
    scored_x = score.score_x_items(filtered_x)
    scored_youtube = score.score_youtube_items(filtered_youtube) if filtered_youtube else []
    scored_web = score.score_websearch_items(filtered_web) if filtered_web else []
    scored_hn = score.score_hn_items(filtered_hn) if filtered_hn else []

    # Sort items
    sorted_reddit = score.sort_items(scored_reddit)
    sorted_x = score.sort_items(scored_x)
    sorted_youtube = score.sort_items(scored_youtube) if scored_youtube else []
    sorted_web = score.sort_items(scored_web) if scored_web else []
    sorted_hn = score.sort_items(scored_hn) if scored_hn else []

    # Dedupe items
    deduped_reddit = dedupe.dedupe_reddit(sorted_reddit)
    deduped_x = dedupe.dedupe_x(sorted_x)
    deduped_youtube = dedupe.dedupe_youtube(sorted_youtube) if sorted_youtube else []
    deduped_web = websearch.dedupe_websearch(sorted_web) if sorted_web else []
    deduped_hn = dedupe.dedupe_hn(sorted_hn) if sorted_hn else []

    # Minimum result guarantee: if all Reddit results were filtered out but
    # we had raw results, keep top 3 by relevance regardless of score
    if not deduped_reddit and normalized_reddit:
        print("[REDDIT WARNING] All results scored below threshold, keeping top 3 by relevance", file=sys.stderr)
        by_relevance = sorted(normalized_reddit, key=lambda item: item.relevance, reverse=True)
        deduped_reddit = by_relevance[:3]

    progress.end_processing()

    # Create report
    report = schema.create_report(
        args.topic,
        from_date,
        to_date,
        mode,
        selected_models.get("openai"),
        selected_models.get("xai"),
    )
    report.reddit = deduped_reddit
    report.x = deduped_x
    report.youtube = deduped_youtube
    report.web = deduped_web
    report.hn = deduped_hn
    report.reddit_error = reddit_error
    report.x_error = x_error
    report.youtube_error = youtube_error
    report.web_error = web_error
    report.hn_error = hn_error

    # Generate context snippet
    report.context_snippet_md = render.render_context_snippet(report)

    # Write outputs
    render.write_outputs(report, raw_openai, raw_xai, raw_reddit_enriched)

    # Show completion
    if sources == "web":
        progress.show_web_only_complete()
    else:
        progress.show_complete(len(deduped_reddit), len(deduped_x), len(deduped_youtube))

    # Build source info for status footer
    source_info = {}
    if not bool(config.get("OPENAI_API_KEY")):
        source_info["reddit_skip_reason"] = "No OPENAI_API_KEY (add to ~/.config/last30days/.env)"
    if not x_source:
        if x_source_status["bird_installed"]:
            source_info["x_skip_reason"] = "Bird installed but not authenticated — log into x.com in browser"
        else:
            source_info["x_skip_reason"] = "No Bird CLI or XAI_API_KEY (Node.js 22+ needed for Bird)"
    if not has_ytdlp:
        source_info["youtube_skip_reason"] = "yt-dlp not installed — fix: brew install yt-dlp"
    if not web_source:
        source_info["web_skip_reason"] = "assistant will use WebSearch (add BRAVE_API_KEY for native search)"

    # Output result
    output_result(report, args.emit, web_needed, args.topic, from_date, to_date, missing_keys, args.days, source_info)

    # Persist findings to SQLite if requested
    if args.store:
        import store as store_mod
        store_mod.init_db()
        topic_row = store_mod.add_topic(args.topic)
        topic_id = topic_row["id"]
        run_id = store_mod.record_run(topic_id, source_mode=mode, status="completed")

        findings = []
        for item in deduped_reddit:
            findings.append({
                "source": "reddit",
                "url": item.url,
                "title": item.title,
                "author": item.subreddit,
                "content": item.title,
                "engagement_score": item.engagement.score if item.engagement else 0,
                "relevance_score": item.relevance,
            })
        for item in deduped_x:
            findings.append({
                "source": "x",
                "url": item.url,
                "title": item.text[:100],
                "author": item.author_handle,
                "content": item.text,
                "engagement_score": item.engagement.likes if item.engagement else 0,
                "relevance_score": item.relevance,
            })
        for item in deduped_youtube:
            findings.append({
                "source": "youtube",
                "url": item.url,
                "title": item.title,
                "author": item.channel_name,
                "content": item.transcript_snippet[:500] if item.transcript_snippet else item.title,
                "engagement_score": item.engagement.views if item.engagement and item.engagement.views else 0,
                "relevance_score": item.relevance,
            })
        for item in deduped_web:
            findings.append({
                "source": "web",
                "url": item.url,
                "title": item.title,
                "author": item.source_domain,
                "content": item.snippet,
                "engagement_score": 0,
                "relevance_score": item.relevance,
            })

        counts = store_mod.store_findings(run_id, topic_id, findings)
        store_mod.update_run(
            run_id,
            status="completed",
            findings_new=counts["new"],
            findings_updated=counts["updated"],
        )
        sys.stderr.write(
            f"[store] Saved {counts['new']} new, {counts['updated']} updated findings\n"
        )
        sys.stderr.flush()


def output_result(
    report: schema.Report,
    emit_mode: str,
    web_needed: bool = False,
    topic: str = "",
    from_date: str = "",
    to_date: str = "",
    missing_keys: str = "none",
    days: int = 30,
    source_info: dict = None,
):
    """Output the result based on emit mode."""
    if emit_mode == "compact":
        print(render.render_compact(report, missing_keys=missing_keys))
        # Append source status footer
        print(render.render_source_status(report, source_info))
    elif emit_mode == "json":
        print(json.dumps(report.to_dict(), indent=2))
    elif emit_mode == "md":
        print(render.render_full_report(report))
    elif emit_mode == "context":
        print(report.context_snippet_md)
    elif emit_mode == "path":
        print(render.get_context_path())

    # Output WebSearch instructions if needed
    if web_needed:
        print("\n" + "="*60)
        print("### WEBSEARCH REQUIRED ###")
        print("="*60)
        print(f"Topic: {topic}")
        print(f"Date range: {from_date} to {to_date}")
        print("")
        print("Assistant: Use your web search tool to find 8-15 relevant web pages.")
        print("EXCLUDE: reddit.com, x.com, twitter.com (already covered above)")
        print(f"INCLUDE: blogs, docs, news, tutorials from the last {days} days")
        print("")
        print("After searching, synthesize WebSearch results WITH the Reddit/X")
        print("results above. WebSearch items should rank LOWER than comparable")
        print("Reddit/X items (they lack engagement metrics).")
        print("="*60)


if __name__ == "__main__":
    main()
