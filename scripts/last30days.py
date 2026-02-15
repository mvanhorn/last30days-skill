#!/usr/bin/env python3
"""
last30days - Research a topic from the last 30 days on Reddit + X.

Usage:
    python3 last30days.py <topic> [options]

Options:
    --mock              Use fixtures instead of real API calls
    --emit=MODE         Output mode: compact|json|md|context|path (default: compact)
    --sources=MODE      Source selection: auto|reddit|x|both (default: auto)
    --search=SOURCES    Comma-separated list of sources to search (e.g. reddit,hn,yt)
                        Valid sources: reddit, x, web, hn, yt, ph
                        Overrides --sources and --include-web when specified
    --quick             Faster research with fewer sources (8-12 each)
    --deep              Comprehensive research with more sources (50-70 Reddit, 40-60 X)
    --debug             Enable verbose debug logging
"""

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

# Add lib to path
SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from lib import (
    bird_x,
    dates,
    dedupe,
    entity_extract,
    env,
    hackernews,
    http,
    models,
    normalize,
    openai_reddit,
    producthunt,
    reddit_enrich,
    render,
    schema,
    score,
    ui,
    websearch,
    xai_x,
    youtube,
)


# Valid source names for --search flag
VALID_SEARCH_SOURCES = {"reddit", "x", "web", "hn", "yt", "ph"}


def parse_search_flag(search_str: str) -> set:
    """Parse and validate the --search flag value.

    Args:
        search_str: Comma-separated source names (e.g. "reddit,hn,yt")

    Returns:
        Set of validated source names

    Raises:
        SystemExit: If invalid sources are specified
    """
    sources = set()
    for s in search_str.split(","):
        s = s.strip().lower()
        if not s:
            continue
        if s not in VALID_SEARCH_SOURCES:
            print(
                f"Error: Unknown search source '{s}'. "
                f"Valid sources: {', '.join(sorted(VALID_SEARCH_SOURCES))}",
                file=sys.stderr,
            )
            sys.exit(1)
        sources.add(s)

    if not sources:
        print("Error: --search requires at least one source.", file=sys.stderr)
        sys.exit(1)

    return sources


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


def _search_hn(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str,
    mock: bool,
) -> tuple:
    """Search Hacker News via Algolia (runs in thread).

    Returns:
        Tuple of (hn_items, raw_hn, error)
    """
    raw_hn = None
    hn_error = None

    if mock:
        raw_hn = load_fixture("hackernews_sample.json")
    else:
        try:
            raw_hn = hackernews.search_hn(
                topic,
                from_date,
                to_date,
                depth=depth,
            )
        except Exception as e:
            raw_hn = {"error": str(e)}
            hn_error = f"{type(e).__name__}: {e}"

    # Parse response
    hn_items = hackernews.parse_hn_response(raw_hn or {})

    # Supplemental: also search by date for recent stories
    if not mock and not hn_error and depth != "quick":
        try:
            date_raw = hackernews.search_hn_by_date(
                topic, from_date, to_date, depth=depth,
            )
            date_items = hackernews.parse_hn_response(date_raw)
            # Add items not already found (by HN URL)
            existing_urls = {item.get("hn_url") for item in hn_items}
            for item in date_items:
                if item.get("hn_url") not in existing_urls:
                    hn_items.append(item)
        except Exception:
            pass

    return hn_items, raw_hn, hn_error


def _search_yt(
    topic: str,
    config: dict,
    from_date: str,
    to_date: str,
    depth: str,
    mock: bool,
) -> tuple:
    """Search YouTube via Data API v3 (runs in thread).

    Returns:
        Tuple of (yt_items, raw_yt, error)
    """
    raw_yt = None
    yt_error = None

    if mock:
        raw_yt = load_fixture("youtube_sample.json")
    else:
        api_key = config.get("YOUTUBE_API_KEY")
        if not api_key:
            return [], None, "YOUTUBE_API_KEY not configured"
        try:
            raw_yt = youtube.search_youtube(
                api_key,
                topic,
                from_date,
                to_date,
                depth=depth,
            )
        except http.HTTPError as e:
            raw_yt = {"error": str(e)}
            yt_error = f"API error: {e}"
        except Exception as e:
            raw_yt = {"error": str(e)}
            yt_error = f"{type(e).__name__}: {e}"

    # Parse response
    yt_items = youtube.parse_youtube_response(raw_yt or {})

    return yt_items, raw_yt, yt_error


def _search_ph(
    topic: str,
    config: dict,
    from_date: str,
    to_date: str,
    depth: str,
    mock: bool,
    ph_slugs: list = None,
) -> tuple:
    """Search Product Hunt via API v2 (runs in thread).

    Args:
        ph_slugs: Pre-selected topic slugs. If None, skips PH search
                  (caller should pass slugs via --ph-slugs).

    Returns:
        Tuple of (ph_items, raw_ph, error)
    """
    raw_ph = None
    ph_error = None

    if mock:
        raw_ph = load_fixture("producthunt_sample.json")
    else:
        access_token = config.get("PH_ACCESS_TOKEN")
        if not access_token:
            return [], None, "PH_ACCESS_TOKEN not configured"
        slugs = ph_slugs or []
        if not slugs:
            return [], None, "No --ph-slugs provided"
        try:
            raw_ph = producthunt.search_producthunt(
                access_token,
                slugs,
                from_date,
                to_date,
                depth=depth,
            )
        except http.HTTPError as e:
            raw_ph = {"error": str(e)}
            ph_error = f"API error: {e}"
        except Exception as e:
            raw_ph = {"error": str(e)}
            ph_error = f"{type(e).__name__}: {e}"

    # Parse response
    ph_items = producthunt.parse_ph_response(raw_ph or {})

    return ph_items, raw_ph, ph_error


def _run_supplemental(
    topic: str,
    reddit_items: list,
    x_items: list,
    from_date: str,
    to_date: str,
    depth: str,
    x_source: str,
    progress: ui.ProgressDisplay = None,
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
    has_subs = entities["reddit_subreddits"]

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
                raw_reddit = reddit_future.result()
                # Filter out URLs already found in Phase 1
                supplemental_reddit = [
                    item for item in raw_reddit
                    if item.get("url", "") not in existing_urls
                ]
            except Exception as e:
                sys.stderr.write(f"[Phase 2] Supplemental Reddit error: {e}\n")

        if x_future:
            try:
                raw_x = x_future.result()
                supplemental_x = [
                    item for item in raw_x
                    if item.get("url", "") not in existing_urls
                ]
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
    search_sources: set = None,
    ph_slugs: list = None,
) -> tuple:
    """Run the research pipeline.

    Args:
        search_sources: If provided, overrides source selection. Set of source
            names like {"reddit", "x", "hn", "yt", "ph", "web"}.

    Returns:
        Tuple of (reddit_items, x_items, hn_items, yt_items, ph_items, web_needed, raw_openai, raw_xai, raw_reddit_enriched, raw_hn, raw_yt, raw_ph, reddit_error, x_error, hn_error, yt_error, ph_error)

    Note: web_needed is True when WebSearch should be performed by Claude.
    The script outputs a marker and Claude handles WebSearch in its session.
    """
    reddit_items = []
    x_items = []
    hn_items = []
    yt_items = []
    ph_items = []
    raw_openai = None
    raw_xai = None
    raw_hn = None
    raw_yt = None
    raw_ph = None
    raw_reddit_enriched = []
    reddit_error = None
    x_error = None
    hn_error = None
    yt_error = None
    ph_error = None

    if search_sources is not None:
        # --search flag overrides all source selection logic
        run_reddit = "reddit" in search_sources
        run_x = "x" in search_sources
        run_hn = "hn" in search_sources
        run_yt = "yt" in search_sources
        run_ph = "ph" in search_sources
        web_needed = "web" in search_sources

        # Web-only via --search (no API sources selected)
        if search_sources == {"web"}:
            if progress:
                progress.start_web_only()
                progress.end_web_only()
            return reddit_items, x_items, hn_items, yt_items, ph_items, True, raw_openai, raw_xai, raw_reddit_enriched, raw_hn, raw_yt, raw_ph, reddit_error, x_error, hn_error, yt_error, ph_error
    else:
        # Original source selection logic
        # Check if WebSearch is needed (always needed in web-only mode)
        web_needed = sources in ("all", "web", "reddit-web", "x-web")

        # HN is always available (free, no auth)
        run_hn = True

        # YouTube runs alongside other sources when API key is available
        run_yt = bool(config.get("YOUTUBE_API_KEY"))

        # Product Hunt runs alongside other sources when token is available
        run_ph = bool(config.get("PH_ACCESS_TOKEN"))

        # Web-only mode: no API calls needed, Claude handles everything
        if sources == "web":
            if progress:
                progress.start_web_only()
                progress.end_web_only()
            return reddit_items, x_items, hn_items, yt_items, ph_items, True, raw_openai, raw_xai, raw_reddit_enriched, raw_hn, raw_yt, raw_ph, reddit_error, x_error, hn_error, yt_error, ph_error

        # Determine which searches to run
        run_reddit = sources in ("both", "reddit", "all", "reddit-web")
        run_x = sources in ("both", "x", "all", "x-web")

    # Run Reddit, X, HN, YouTube, and Product Hunt searches in parallel
    reddit_future = None
    x_future = None
    hn_future = None
    yt_future = None
    ph_future = None

    with ThreadPoolExecutor(max_workers=5) as executor:
        # Submit all searches
        if run_reddit:
            if progress:
                progress.start_reddit()
            reddit_future = executor.submit(
                _search_reddit, topic, config, selected_models,
                from_date, to_date, depth, mock
            )

        if run_x:
            if progress:
                progress.start_x()
            x_future = executor.submit(
                _search_x, topic, config, selected_models,
                from_date, to_date, depth, mock, x_source
            )

        if run_hn:
            if progress:
                progress.start_hn()
            hn_future = executor.submit(
                _search_hn, topic,
                from_date, to_date, depth, mock
            )

        if run_yt:
            if progress:
                progress.start_yt()
            yt_future = executor.submit(
                _search_yt, topic, config,
                from_date, to_date, depth, mock
            )

        if run_ph:
            if progress:
                progress.start_ph()
            ph_future = executor.submit(
                _search_ph, topic, config,
                from_date, to_date, depth, mock, ph_slugs
            )

        # Collect results
        if reddit_future:
            try:
                reddit_items, raw_openai, reddit_error = reddit_future.result()
                if reddit_error and progress:
                    progress.show_error(f"Reddit error: {reddit_error}")
            except Exception as e:
                reddit_error = f"{type(e).__name__}: {e}"
                if progress:
                    progress.show_error(f"Reddit error: {e}")
            if progress:
                progress.end_reddit(len(reddit_items))

        if x_future:
            try:
                x_items, raw_xai, x_error = x_future.result()
                if x_error and progress:
                    progress.show_error(f"X error: {x_error}")
            except Exception as e:
                x_error = f"{type(e).__name__}: {e}"
                if progress:
                    progress.show_error(f"X error: {e}")
            if progress:
                progress.end_x(len(x_items))

        if hn_future:
            try:
                hn_items, raw_hn, hn_error = hn_future.result()
                if hn_error and progress:
                    progress.show_error(f"HN error: {hn_error}")
            except Exception as e:
                hn_error = f"{type(e).__name__}: {e}"
                if progress:
                    progress.show_error(f"HN error: {e}")
            if progress:
                progress.end_hn(len(hn_items))

        if yt_future:
            try:
                yt_items, raw_yt, yt_error = yt_future.result()
                if yt_error and progress:
                    progress.show_error(f"YouTube error: {yt_error}")
            except Exception as e:
                yt_error = f"{type(e).__name__}: {e}"
                if progress:
                    progress.show_error(f"YouTube error: {e}")
            if progress:
                progress.end_yt(len(yt_items))

        if ph_future:
            try:
                ph_items, raw_ph, ph_error = ph_future.result()
                if ph_error and progress:
                    progress.show_error(f"Product Hunt error: {ph_error}")
            except Exception as e:
                ph_error = f"{type(e).__name__}: {e}"
                if progress:
                    progress.show_error(f"Product Hunt error: {e}")
            if progress:
                progress.end_ph(len(ph_items))

    # Enrich Reddit items with real data (sequential, but with error handling per-item)
    if reddit_items:
        if progress:
            progress.start_reddit_enrich(1, len(reddit_items))

        for i, item in enumerate(reddit_items):
            if progress and i > 0:
                progress.update_reddit_enrich(i + 1, len(reddit_items))

            try:
                if mock:
                    mock_thread = load_fixture("reddit_thread_sample.json")
                    reddit_items[i] = reddit_enrich.enrich_reddit_item(item, mock_thread)
                else:
                    reddit_items[i] = reddit_enrich.enrich_reddit_item(item)
            except Exception as e:
                # Log but don't crash - keep the unenriched item
                if progress:
                    progress.show_error(f"Enrich failed for {item.get('url', 'unknown')}: {e}")

            raw_reddit_enriched.append(reddit_items[i])

        if progress:
            progress.end_reddit_enrich()

    # Phase 2: Supplemental search based on entities from Phase 1
    # Skip on --quick (speed matters) and mock mode
    if depth != "quick" and not mock and (reddit_items or x_items):
        sup_reddit, sup_x = _run_supplemental(
            topic, reddit_items, x_items,
            from_date, to_date, depth, x_source, progress,
        )
        if sup_reddit:
            reddit_items.extend(sup_reddit)
        if sup_x:
            x_items.extend(sup_x)

    return reddit_items, x_items, hn_items, yt_items, ph_items, web_needed, raw_openai, raw_xai, raw_reddit_enriched, raw_hn, raw_yt, raw_ph, reddit_error, x_error, hn_error, yt_error, ph_error


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
        "--search",
        type=str,
        default=None,
        help="Comma-separated list of sources to search (e.g. reddit,hn,yt). "
             "Valid: reddit, x, web, hn, yt, ph. Overrides --sources and --include-web.",
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
        "--ph-slugs",
        type=str,
        default=None,
        help="Comma-separated Product Hunt topic slugs to search (e.g. artificial-intelligence,video)",
    )
    parser.add_argument(
        "--list-ph-topics",
        action="store_true",
        help="List available Product Hunt topic slugs and exit",
    )

    args = parser.parse_args()

    # Handle --list-ph-topics early exit
    if args.list_ph_topics:
        config = env.get_config()
        access_token = config.get("PH_ACCESS_TOKEN")
        if not access_token:
            print("Error: PH_ACCESS_TOKEN not configured", file=sys.stderr)
            sys.exit(1)
        print(producthunt.list_topic_slugs(access_token))
        sys.exit(0)

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

    # Parse --ph-slugs
    ph_slugs = None
    if args.ph_slugs:
        ph_slugs = [s.strip() for s in args.ph_slugs.split(",") if s.strip()]

    # Validate topic first (matches original NUX)
    if not args.topic:
        print("Error: Please provide a topic to research.", file=sys.stderr)
        print("Usage: python3 last30days.py <topic> [options]", file=sys.stderr)
        sys.exit(1)

    # Parse --search flag (overrides --sources and --include-web)
    search_sources = None
    if args.search is not None:
        search_sources = parse_search_flag(args.search)

    # Load config
    config = env.get_config()

    # Auto-detect Bird (no prompts - just use it if available)
    x_source_status = env.get_x_source_status(config)
    x_source = x_source_status["source"]  # 'bird', 'xai', or None

    # Initialize progress display with topic
    progress = ui.ProgressDisplay(args.topic, show_banner=True)

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

    # Show promo for missing keys BEFORE research
    if missing_keys != 'none':
        progress.show_promo(missing_keys)

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
    if search_sources is not None:
        mode = "+".join(sorted(search_sources))
    elif sources == "all":
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
    reddit_items, x_items, hn_items, yt_items, ph_items, web_needed, raw_openai, raw_xai, raw_reddit_enriched, raw_hn, raw_yt, raw_ph, reddit_error, x_error, hn_error, yt_error, ph_error = run_research(
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
        search_sources=search_sources,
        ph_slugs=ph_slugs,
    )

    # Processing phase
    progress.start_processing()

    # Normalize items
    normalized_reddit = normalize.normalize_reddit_items(reddit_items, from_date, to_date)
    normalized_x = normalize.normalize_x_items(x_items, from_date, to_date)
    normalized_hn = normalize.normalize_hn_items(hn_items, from_date, to_date)
    normalized_yt = normalize.normalize_yt_items(yt_items, from_date, to_date)
    normalized_ph = normalize.normalize_ph_items(ph_items, from_date, to_date)

    # Hard date filter: exclude items with verified dates outside the range
    # This is the safety net - even if prompts let old content through, this filters it
    filtered_reddit = normalize.filter_by_date_range(normalized_reddit, from_date, to_date)
    filtered_x = normalize.filter_by_date_range(normalized_x, from_date, to_date)
    filtered_hn = normalize.filter_by_date_range(normalized_hn, from_date, to_date)
    filtered_yt = normalize.filter_by_date_range(normalized_yt, from_date, to_date)
    filtered_ph = normalize.filter_by_date_range(normalized_ph, from_date, to_date)

    # Score items
    scored_reddit = score.score_reddit_items(filtered_reddit)
    scored_x = score.score_x_items(filtered_x)
    scored_hn = score.score_hn_items(filtered_hn)
    scored_yt = score.score_yt_items(filtered_yt)
    scored_ph = score.score_ph_items(filtered_ph)

    # Sort items
    sorted_reddit = score.sort_items(scored_reddit)
    sorted_x = score.sort_items(scored_x)
    sorted_hn = score.sort_items(scored_hn)
    sorted_yt = score.sort_items(scored_yt)
    sorted_ph = score.sort_items(scored_ph)

    # Dedupe items
    deduped_reddit = dedupe.dedupe_reddit(sorted_reddit)
    deduped_x = dedupe.dedupe_x(sorted_x)
    deduped_hn = dedupe.dedupe_hn(sorted_hn)
    deduped_yt = dedupe.dedupe_yt(sorted_yt)
    deduped_ph = dedupe.dedupe_ph(sorted_ph)

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
    report.hn = deduped_hn
    report.yt = deduped_yt
    report.ph = deduped_ph
    report.reddit_error = reddit_error
    report.x_error = x_error
    report.hn_error = hn_error
    report.yt_error = yt_error
    report.ph_error = ph_error

    # Generate context snippet
    report.context_snippet_md = render.render_context_snippet(report)

    # Write outputs
    render.write_outputs(report, raw_openai, raw_xai, raw_reddit_enriched, raw_hn, raw_yt, raw_ph)

    # Show completion
    if sources == "web":
        progress.show_web_only_complete()
    else:
        progress.show_complete(len(deduped_reddit), len(deduped_x), hn_count=len(deduped_hn), yt_count=len(deduped_yt), ph_count=len(deduped_ph))

    # Output result
    output_result(report, args.emit, web_needed, args.topic, from_date, to_date, missing_keys, args.days)


def output_result(
    report: schema.Report,
    emit_mode: str,
    web_needed: bool = False,
    topic: str = "",
    from_date: str = "",
    to_date: str = "",
    missing_keys: str = "none",
    days: int = 30,
):
    """Output the result based on emit mode."""
    if emit_mode == "compact":
        print(render.render_compact(report, missing_keys=missing_keys))
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
        print("Claude: Use your WebSearch tool to find 8-15 relevant web pages.")
        print("EXCLUDE: reddit.com, x.com, twitter.com (already covered above)")
        print(f"INCLUDE: blogs, docs, news, tutorials from the last {days} days")
        print("")
        print("After searching, synthesize WebSearch results WITH the Reddit/X")
        print("results above. WebSearch items should rank LOWER than comparable")
        print("Reddit/X items (they lack engagement metrics).")
        print("="*60)


if __name__ == "__main__":
    main()
