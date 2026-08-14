"""GitHub Issues/PRs search via the public GitHub Search API.

Uses api.github.com/search/issues for issue/PR discovery and
per-item comment enrichment. Auth via GITHUB_TOKEN env var or
`gh auth token` subprocess fallback.
"""

import json
import math
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

from . import dates, env, http, log, schema
from .query import extract_core_subject
from .relevance import token_overlap_relevance

SEARCH_URL = "https://api.github.com/search/issues"

DEPTH_LIMITS = {
    "quick": 15,
    "default": 30,
    "deep": 60,
}

ENRICH_LIMITS = {
    "quick": 3,
    "default": 5,
    "deep": 8,
}

# Unauthenticated GitHub search allows ~10 requests/min, so cap result volume
# conservatively when running without a token to stay within the anon tier.
UNAUTH_COUNT_CAP = 10

USER_AGENT = "last30days/3.0 (research tool)"


def _log(msg: str):
    log.source_log("GitHub", msg, tty_only=False)


def _resolve_token(token: Optional[str] = None) -> Optional[str]:
    """Resolve GitHub auth token from argument, env, or gh CLI."""
    if token:
        return token
    env_token = env.read_secret_env("GITHUB_TOKEN")
    if env_token:
        return env_token
    # Fallback: try gh CLI
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None


def resolve_token(token: Optional[str] = None) -> Optional[str]:
    """Public alias for ``_resolve_token``.

    The pipeline calls this once before ``search_github`` and
    ``enrich_with_comments`` so the ``gh auth token`` subprocess fallback
    only fires once per query when ``GITHUB_TOKEN`` is unset, instead of
    twice (once per call site).
    """
    return _resolve_token(token)


def _fetch_json(
    url: str,
    token: Optional[str] = None,
    timeout: int = 15,
    failure_out: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    """Fetch JSON from GitHub API. Returns None on failure.

    When ``failure_out`` is provided, a short human-readable reason is
    appended for every failure branch so callers can distinguish transport
    failures from genuinely empty results (issue #384).
    """

    def _note(msg: str) -> None:
        if failure_out is not None:
            failure_out.append(msg)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body)
    except urllib.error.HTTPError as e:
        if e.code == 403:
            _log(f"403 rate limited or forbidden: {url}")
            _note("HTTP 403: rate limited or forbidden")
            return None
        if e.code == 422:
            _log(f"422 unprocessable: {url}")
            _note("HTTP 422: unprocessable query")
            return None
        _log(f"HTTP {e.code}: {e.reason}")
        _note(f"HTTP {e.code}: {e.reason}")
        return None
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        _log(f"Network error: {e}")
        _note(f"network error: {e}")
        return None
    except json.JSONDecodeError as e:
        _log(f"JSON decode error: {e}")
        _note(f"invalid JSON: {e}")
        return None


def _parse_repo_from_url(html_url: str) -> str:
    """Extract 'owner/repo' from a GitHub issue/PR URL."""
    parts = html_url.replace("https://github.com/", "").split("/")
    if len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    return ""


def _parse_date(iso_str: Optional[str]) -> Optional[str]:
    """Parse a GitHub ISO 8601 datetime string and return YYYY-MM-DD.

    Returns None for non-date input. GitHub's API always emits ISO 8601
    (e.g. "2026-02-26T16:00:00Z"), but we defer to dates.parse_date() so
    garbage input gets rejected instead of silently sliced.
    """
    dt = dates.parse_date(iso_str)
    return dt.strftime("%Y-%m-%d") if dt else None


def _compute_relevance(
    query: str,
    title: str,
    rank_index: int,
    reactions: int,
    comments: int,
) -> float:
    """Blend text relevance with engagement signals."""
    rank_score = max(0.3, 1.0 - (rank_index * 0.02))
    engagement_boost = min(0.2, math.log1p(reactions + comments) / 20)

    if query:
        content_score = token_overlap_relevance(query, title)
        relevance = min(1.0, 0.6 * rank_score + 0.4 * content_score + engagement_boost)
    else:
        relevance = min(1.0, rank_score * 0.7 + engagement_boost + 0.1)

    return round(relevance, 2)


# GitHub search qualifiers the planner sometimes writes straight into the topic
# string (e.g. "open source AI stars:>1000 created:>2025-03-20"). They must not
# reach the query builder in `search_github`: it appends its own
# `created:>{from_date}`, and when two `created:` qualifiers collide GitHub
# honours the FIRST and silently ignores ours. The API then returns
# out-of-window items that `parse_github_response`'s date filter drops
# wholesale — a source that fetches results and reports zero (issue #949).
QUALIFIER_KEYS = frozenset({
    "archived", "assignee", "author", "base", "closed", "comments", "commenter",
    "created", "fork", "forks", "head", "in", "interactions", "involves", "is",
    "label", "language", "license", "linked", "mentions", "merged", "milestone",
    "no", "org", "project", "pushed", "reactions", "repo", "review",
    "review-requested", "reviewed-by", "size", "sort", "stars", "state", "team",
    "topic", "topics", "type", "updated", "user",
})

_QUALIFIER_RE = re.compile(
    r"(?:(?<=[\s,;])|^)(?:" + "|".join(sorted(QUALIFIER_KEYS)) + r"):(?:[<>]=?)?(?:\"[^\"]*\"|[^\s,;()\[\]]+)[,;]?",
    re.IGNORECASE,
)


def strip_search_qualifiers(text: str) -> str:
    """Strip GitHub search qualifiers from a topic, leaving plain-language text.

    Whitespace is collapsed. Returns an empty string when the topic was nothing
    but qualifiers; callers must handle that rather than searching on an empty
    term, which would match the entire site.
    """
    return " ".join(_QUALIFIER_RE.sub(" ", text).split())


def search_github(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """Search GitHub Issues and PRs (HTTP fetch only).

    Returns a raw envelope shaped like every other adapter's ``search_X``:
    ``{"items": [raw GitHub API items], "context": {core, from_date,
    to_date, count}}``. Normalization, date filtering, and sorting move
    to ``parse_github_response``; comment enrichment moves to
    ``enrich_with_comments``.

    Args:
        topic: Search topic
        from_date: Start date (YYYY-MM-DD)
        to_date: End date (YYYY-MM-DD)
        depth: 'quick', 'default', or 'deep'
        token: Optional GitHub token (falls back to env/gh CLI)

    Returns:
        Dict envelope. Empty ``items`` list on any failure.
    """
    count = DEPTH_LIMITS.get(depth, DEPTH_LIMITS["default"])
    core = extract_core_subject(topic)
    plain_core = strip_search_qualifiers(core)
    if plain_core != core:
        _log(f"Stripped search qualifiers: '{core}' -> '{plain_core}'")
    if not plain_core:
        # A qualifier-only (or empty) topic leaves nothing to search on.
        # Report it instead of querying an empty term, which would match the
        # whole site and then be discarded by the date filter as a bogus
        # "no results" (issue #949).
        _log("Topic contained only search qualifiers or was empty; nothing to search")
        return {
            "items": [],
            "context": {"core": core, "from_date": from_date,
                        "to_date": to_date, "count": count},
            "error": (
                f"GitHub topic contained only search qualifiers or was empty: {topic!r}"
            ),
        }
    core = plain_core
    resolved_token = _resolve_token(token)
    authed = bool(resolved_token)
    if not authed:
        # Fall back to the unauthenticated REST tier instead of returning nothing.
        # It is rate-limited, so cap the request volume.
        count = min(count, UNAUTH_COUNT_CAP)
        _log("No GitHub token; using the unauthenticated REST tier (low rate limit)")
    _log(f"Searching for '{core}' (raw: '{topic}', since {from_date}, count={count})")

    # Build search query with date filter
    base_q = f"{core} created:>{from_date}"

    def _search(qualifier: Optional[str]) -> Optional[Dict[str, Any]]:
        q = f"{base_q} {qualifier}" if qualifier else base_q
        params = {
            "q": q,
            "sort": "reactions",
            "order": "desc",
            "per_page": str(min(count, 100)),
        }
        url = f"{SEARCH_URL}?{urllib.parse.urlencode(params)}"
        return _fetch_json(url, token=resolved_token, timeout=30,
                           failure_out=fetch_failures)

    fetch_failures: List[str] = []
    partition_failure: Optional[str] = None
    if authed:
        # GitHub rejects AUTHENTICATED /search/issues queries that carry
        # neither `is:issue` nor `is:pull-request` (HTTP 422). Anonymous
        # queries are still grandfathered, which is why this only bites once
        # a token is present -- including via the `gh auth token` fallback.
        #
        # Appending a single qualifier would silently halve the corpus: for
        # "rust async created:>2026-08-02" GitHub reports 1,072 issues and
        # 7,044 pull requests against 8,115 combined, so `is:issue` alone
        # drops ~87% of matches. Query both and merge instead, which keeps
        # coverage AND the authenticated rate limit.
        merged: List[Dict[str, Any]] = []
        seen_ids = set()
        failed_qualifiers: List[str] = []
        for qualifier in ("is:issue", "is:pull-request"):
            part = _search(qualifier)
            if part is None:
                failed_qualifiers.append(qualifier)
                continue
            for item in part.get("items", []):
                item_id = item.get("id")
                if item_id in seen_ids:
                    continue
                seen_ids.add(item_id)
                merged.append(item)
        # Both sub-queries are reaction-sorted; the merge is not, so re-sort
        # before truncating or the second query's tail would outrank the
        # first query's head.
        merged.sort(key=lambda i: (i.get("reactions") or {}).get("total_count", 0),
                    reverse=True)
        data = {"items": merged[:count]} if merged else None
        if failed_qualifiers:
            partition_failure = (
                f"GitHub partition(s) failed: {', '.join(failed_qualifiers)}"
                + (f" ({fetch_failures[-1]})" if fetch_failures else "")
            )
    else:
        data = _search(None)
    if not data:
        envelope = {"items": [], "context": {"core": core, "from_date": from_date,
                                             "to_date": to_date, "count": count}}
        if authed and partition_failure:
            envelope["error"] = partition_failure
        elif authed and fetch_failures:
            # Authenticated transport failures must not be laundered into a
            # clean no-results outcome (issue #384).
            envelope["error"] = f"GitHub API request failed: {fetch_failures[-1]}"
        elif not authed:
            # Could be the anon rate limit (403) or an unprocessable query (422)
            # -- _fetch_json maps both to None. Don't over-claim which; suggest a
            # token since that fixes the common (rate-limit) case.
            envelope["error"] = (
                "GitHub unauthenticated request returned no data (anon rate limit "
                "or unprocessable query; set GITHUB_TOKEN or run gh auth login)"
            )
        return envelope

    raw_items = data.get("items", [])
    _log(f"Found {len(raw_items)} issues/PRs")

    envelope: Dict[str, Any] = {
        "items": raw_items,
        "context": {
            "core": core,
            "from_date": from_date,
            "to_date": to_date,
            "count": count,
        },
    }
    if partition_failure:
        envelope["error"] = partition_failure
    return envelope


def parse_github_response(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Normalize a ``search_github`` envelope into the skill's item shape.

    Pure function: no I/O, no token, no enrichment. Applies the date
    filter using the search context and sorts by relevance.
    """
    if not isinstance(response, dict):
        return []
    raw_items = response.get("items") or []
    if not isinstance(raw_items, list):
        return []
    context = response.get("context") or {}
    core = context.get("core") or ""
    from_date = context.get("from_date") or ""
    to_date = context.get("to_date") or ""
    count = context.get("count") or DEPTH_LIMITS["default"]

    items: List[Dict[str, Any]] = []
    for i, item in enumerate(raw_items[:count]):
        html_url = item.get("html_url", "")
        repo = _parse_repo_from_url(html_url)
        title = item.get("title", "")
        body_text = item.get("body") or ""
        reactions_total = item.get("reactions", {}).get("total_count", 0) if isinstance(item.get("reactions"), dict) else 0
        comment_count = item.get("comments") or 0
        labels = [
            lbl.get("name", "") for lbl in (item.get("labels") or [])
            if isinstance(lbl, dict)
        ]
        state = item.get("state", "")
        is_pr = "pull_request" in item
        author = item.get("user", {}).get("login", "") if isinstance(item.get("user"), dict) else ""

        relevance = _compute_relevance(core, title, i, reactions_total, comment_count)

        items.append({
            "id": f"GH{i + 1}",
            "title": title,
            "url": html_url,
            "date": _parse_date(item.get("created_at")),
            "author": author,
            "source": "github",
            "score": reactions_total,
            "container": repo,
            "snippet": body_text[:300] if body_text else "",
            "relevance": relevance,
            "why_relevant": f"GitHub {'PR' if is_pr else 'issue'}: {title[:60]}",
            "engagement": {
                "reactions": reactions_total,
                "comments": comment_count,
            },
            "metadata": {
                "labels": labels,
                "state": state,
                "comment_count": comment_count,
                "reactions": reactions_total,
                "is_pr": is_pr,
            },
        })

    # Date filter
    if from_date and to_date:
        items = [
            item for item in items
            if item.get("date") is None or (from_date <= item["date"] <= to_date)
        ]

    items.sort(key=lambda x: x.get("relevance", 0), reverse=True)
    return items


def enrich_with_comments(
    items: List[Dict[str, Any]],
    depth: str = "default",
    token: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Fetch top comments for top-K items by reactions and attach to metadata.

    Mutates and returns ``items``. Resolves ``token`` via env/gh CLI when
    not supplied, matching ``search_github``'s fallback chain.
    """
    if not items:
        return items
    resolved_token = _resolve_token(token)
    if not resolved_token:
        _log("No GitHub token available for comment enrichment")
        return items
    return _enrich_top_items(items, depth, resolved_token)


def _enrich_top_items(
    items: List[Dict[str, Any]],
    depth: str,
    token: str,
) -> List[Dict[str, Any]]:
    """Fetch comments for top N items by reactions."""
    if not items:
        return items

    limit = ENRICH_LIMITS.get(depth, ENRICH_LIMITS["default"])

    by_reactions = sorted(
        range(len(items)),
        key=lambda i: items[i].get("score", 0),
        reverse=True,
    )
    to_enrich = by_reactions[:limit]

    _log(f"Enriching top {len(to_enrich)} items with comments")

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(
                _fetch_item_comments,
                items[idx]["url"],
                token,
            ): idx
            for idx in to_enrich
        }

        for future in as_completed(futures):
            idx = futures[future]
            try:
                comments = future.result(timeout=15)
                items[idx]["metadata"]["top_comments"] = comments
            except (KeyError, TypeError, OSError) as exc:
                _log(f"Comment enrichment failed for {items[idx].get('url', '?')}: {type(exc).__name__}: {exc}")
                items[idx]["metadata"]["top_comments"] = []

    return items


def _fetch_item_comments(
    issue_url: str,
    token: str,
    max_comments: int = 5,
) -> List[Dict[str, Any]]:
    """Fetch comments for a GitHub issue/PR.

    Args:
        issue_url: HTML URL like https://github.com/owner/repo/issues/123
        token: GitHub auth token
        max_comments: Max comments to return

    Returns:
        List of comment dicts with score, excerpt, author.
    """
    path = issue_url.replace("https://github.com/", "")
    path = path.replace("/pull/", "/issues/")
    api_url = f"https://api.github.com/repos/{path}/comments?per_page={max_comments}&sort=reactions&direction=desc"

    data = _fetch_json(api_url, token=token, timeout=15)
    if not data or not isinstance(data, list):
        return []

    comments = []
    for c in data[:max_comments]:
        body = c.get("body") or ""
        excerpt = body[:300] + "..." if len(body) > 300 else body
        reactions = c.get("reactions", {})
        reaction_count = reactions.get("total_count", 0) if isinstance(reactions, dict) else 0
        author = c.get("user", {}).get("login", "") if isinstance(c.get("user"), dict) else ""

        comments.append({
            "score": reaction_count,
            "excerpt": excerpt,
            "author": author,
        })

    return comments


# ---------------------------------------------------------------------------
# Person-mode search: author-scoped queries, star enrichment, release notes
# ---------------------------------------------------------------------------

PERSON_DEPTH_LIMITS = {
    "quick": {"pr_pages": 1, "own_repos": 3, "external_repos": 5},
    "default": {"pr_pages": 1, "own_repos": 5, "external_repos": 10},
    "deep": {"pr_pages": 2, "own_repos": 5, "external_repos": 15},
}

PERSON_EVENTS_PER_PAGE = 100


def _fetch_readme_snippet(repo: str, token: str, max_chars: int = 500) -> Optional[str]:
    """Fetch README content for a repo, truncated to first ~max_chars."""
    url = f"https://api.github.com/repos/{repo}/readme"
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github.raw+json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, TimeoutError):
        return None

    if not raw:
        return None
    # Try to break at a paragraph boundary
    if len(raw) <= max_chars:
        return raw
    cut = raw[:max_chars]
    last_double_newline = cut.rfind("\n\n")
    if last_double_newline > max_chars // 3:
        return cut[:last_double_newline].rstrip()
    return cut.rstrip() + "..."


def _fetch_latest_releases(
    repo: str, token: str, count: int = 3, max_body: int = 300,
) -> List[Dict[str, str]]:
    """Fetch latest releases for a repo."""
    url = f"https://api.github.com/repos/{repo}/releases?per_page={count}"
    data = _fetch_json(url, token=token, timeout=10)
    if not data or not isinstance(data, list):
        return []
    releases = []
    for r in data[:count]:
        tag = r.get("tag_name", "")
        date = _parse_date(r.get("published_at"))
        body = (r.get("body") or "")[:max_body]
        name = r.get("name") or tag
        releases.append({"tag": tag, "name": name, "date": date, "body": body})
    return releases


def _fetch_top_issues(repo: str, token: str) -> Dict[str, Any]:
    """Fetch top feature request (by reactions) and top complaint (by comments)."""
    result: Dict[str, Any] = {}

    # Top feature request: issues with enhancement label, sorted by reactions
    feat_q = urllib.parse.quote(f"repo:{repo} is:issue is:open label:enhancement")
    feat_url = f"{SEARCH_URL}?q={feat_q}&sort=reactions&order=desc&per_page=1"
    feat_data = _fetch_json(feat_url, token=token, timeout=10)
    if feat_data and feat_data.get("items"):
        item = feat_data["items"][0]
        result["top_feature_request"] = {
            "title": item.get("title", ""),
            "reactions": item.get("reactions", {}).get("total_count", 0) if isinstance(item.get("reactions"), dict) else 0,
            "comments": item.get("comments") or 0,
            "url": item.get("html_url", ""),
        }
    elif feat_data and feat_data.get("total_count", 0) == 0:
        # No enhancement label; fall back to top issue by reactions
        fallback_q = urllib.parse.quote(f"repo:{repo} is:issue is:open")
        fallback_url = f"{SEARCH_URL}?q={fallback_q}&sort=reactions&order=desc&per_page=1"
        fallback_data = _fetch_json(fallback_url, token=token, timeout=10)
        if fallback_data and fallback_data.get("items"):
            item = fallback_data["items"][0]
            result["top_feature_request"] = {
                "title": item.get("title", ""),
                "reactions": item.get("reactions", {}).get("total_count", 0) if isinstance(item.get("reactions"), dict) else 0,
                "comments": item.get("comments") or 0,
                "url": item.get("html_url", ""),
            }

    # Top complaint: most-discussed open issue (by comments)
    bug_q = urllib.parse.quote(f"repo:{repo} is:issue is:open")
    bug_url = f"{SEARCH_URL}?q={bug_q}&sort=comments&order=desc&per_page=1"
    bug_data = _fetch_json(bug_url, token=token, timeout=10)
    if bug_data and bug_data.get("items"):
        item = bug_data["items"][0]
        result["top_complaint"] = {
            "title": item.get("title", ""),
            "reactions": item.get("reactions", {}).get("total_count", 0) if isinstance(item.get("reactions"), dict) else 0,
            "comments": item.get("comments") or 0,
            "url": item.get("html_url", ""),
        }

    return result


def _fetch_repo_info(repo: str, token: str) -> Optional[Dict[str, Any]]:
    """Fetch repo metadata (stars, forks, description, language)."""
    url = f"https://api.github.com/repos/{repo}"
    data = _fetch_json(url, token=token, timeout=10)
    if not data or not isinstance(data, dict):
        return None
    return {
        "stars": data.get("stargazers_count", 0),
        "forks": data.get("forks_count", 0),
        "description": (data.get("description") or "")[:200],
        "language": data.get("language") or "",
        "open_issues": data.get("open_issues_count", 0),
    }


def _format_stars(n: int) -> str:
    """Format star count as human-readable (e.g., 349K, 2.9K, 42)."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K" if n >= 10_000 else f"{n / 1_000:.1f}K"
    return str(n)


def refetch_datum(item: schema.SourceItem | None, datum_key: str) -> dict[str, Any]:
    """Re-fetch one repository counter through the shared HTTP wrapper.

    ``datum_key`` is either the literal ``"stars"`` (item-level claim; the
    repo derives from the grounding item) or an ``owner/repo`` slug
    (candidate-enrichment claim; the repo itself is the refetch subject and
    the item is not consulted, so it may be ``None``).
    """
    if re.fullmatch(r"[^/\s]+/[^/\s]+", datum_key):
        repo = datum_key
    elif datum_key != "stars":
        raise KeyError(f"Unsupported GitHub datum: {datum_key}")
    else:
        if item is None:
            raise ValueError("Item-level star refetch requires the grounding item")
        repo = item.container or ""
        if not re.fullmatch(r"[^/\s]+/[^/\s]+", repo):
            match = re.match(r"https?://github\.com/([^/]+/[^/#?]+)", item.url)
            repo = match.group(1).removesuffix(".git") if match else ""
        if not repo:
            raise ValueError("GitHub item has no owner/repository reference")
    headers = {"Accept": "application/vnd.github+json"}
    token = _resolve_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = http.request(
        "GET", f"https://api.github.com/repos/{repo}",
        headers=headers, timeout=10, retries=2,
    )
    if not isinstance(data, dict) or not isinstance(data.get("stargazers_count"), int):
        raise KeyError("GitHub star count was not returned")
    fallback_url = item.url if item is not None else f"https://github.com/{repo}"
    return {
        "value": data["stargazers_count"],
        "url": str(data.get("html_url") or fallback_url),
        "timestamp": data.get("updated_at"),
    }


def search_github_person(
    username: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    token: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Person-mode GitHub search: author-scoped queries with star enrichment.

    Returns SourceItems for:
    - 1 velocity summary item
    - Per-repo items for top external repos (with stars + release notes)
    - Per-repo items for own repos (with stars + README + top issues + releases)
    """
    resolved_token = _resolve_token(token)
    if not resolved_token:
        _log("No GitHub token available for person-mode search")
        return []

    limits = PERSON_DEPTH_LIMITS.get(depth, PERSON_DEPTH_LIMITS["default"])
    _log(f"Person-mode search for @{username} (since {from_date})")

    # Phase 1: PR velocity via search API
    total_q = urllib.parse.quote(f"author:{username} type:pr created:>{from_date}")
    merged_q = urllib.parse.quote(f"author:{username} type:pr is:merged created:>{from_date}")

    total_url = f"{SEARCH_URL}?q={total_q}&per_page=1"
    merged_url = f"{SEARCH_URL}?q={merged_q}&sort=reactions&order=desc&per_page=100"

    total_data = _fetch_json(total_url, token=resolved_token, timeout=20)
    merged_data = _fetch_json(merged_url, token=resolved_token, timeout=20)

    total_prs = total_data.get("total_count", 0) if total_data else 0
    merged_count = merged_data.get("total_count", 0) if merged_data else 0
    merged_items = merged_data.get("items", []) if merged_data else []

    _log(f"Found {total_prs} total PRs, {merged_count} merged")

    if total_prs == 0 and merged_count == 0:
        # An empty PR search can mean no PRs in the window or an account that
        # GitHub's issue index cannot search. Public PushEvents provide an
        # actor-attributed fallback for either case.
        search_unavailable = total_data is None or merged_data is None
        recent = _person_recent_pushes(
            username, from_date, to_date, limits, resolved_token,
        )
        if recent:
            reason = "account not searchable" if search_unavailable else "no PRs in window"
            _log(f"PR search empty ({reason}); public events returned {len(recent)} items")
            return recent
        _log("No PRs found, falling back to keyword search")
        return []

    # Phase 2: Group merged PRs by repo
    repo_pr_counts: Dict[str, int] = {}
    for item in merged_items:
        repo = _parse_repo_from_url(item.get("html_url", ""))
        if repo:
            repo_pr_counts[repo] = repo_pr_counts.get(repo, 0) + 1

    # Sort repos by PR count (most active first)
    sorted_repos = sorted(repo_pr_counts.items(), key=lambda x: x[1], reverse=True)

    # Phase 3: Fetch own repos
    own_repos_url = f"https://api.github.com/users/{username}/repos?sort=stars&per_page={limits['own_repos']}&direction=desc"
    own_repos_data = _fetch_json(own_repos_url, token=resolved_token, timeout=15)
    own_repo_names = set()
    own_repos_info: List[Dict[str, Any]] = []
    if own_repos_data and isinstance(own_repos_data, list):
        for r in own_repos_data:
            full_name = r.get("full_name", "")
            if full_name and not r.get("fork"):
                own_repo_names.add(full_name)
                own_repos_info.append({
                    "full_name": full_name,
                    "stars": r.get("stargazers_count", 0),
                    "forks": r.get("forks_count", 0),
                    "description": (r.get("description") or "")[:200],
                    "language": r.get("language") or "",
                    "open_issues": r.get("open_issues_count", 0),
                })

    # Separate external repos from own repos
    external_repos = [(repo, count) for repo, count in sorted_repos if repo not in own_repo_names]
    external_repos = external_repos[:limits["external_repos"]]

    # Phase 4: Parallel enrichment (star counts, releases, READMEs, top issues)
    items: List[Dict[str, Any]] = []
    idx = 0

    # Build velocity summary
    open_prs = total_prs - merged_count
    merge_rate = round(100 * merged_count / total_prs) if total_prs > 0 else 0
    num_repos = len(repo_pr_counts)
    velocity_text = (
        f"GitHub Person Profile: @{username}\n\n"
        f"CONTRIBUTION VELOCITY (last {(to_date > from_date) and 30 or 30} days)\n"
        f"- {merged_count} PRs merged across {num_repos} repos ({merge_rate}% merge rate)\n"
        f"- {total_prs} total PRs submitted, {open_prs} still open\n"
    )

    idx += 1
    items.append({
        "id": f"GH{idx}",
        "title": f"@{username}: {merged_count} PRs merged across {num_repos} repos ({merge_rate}% merge rate)",
        "url": f"https://github.com/{username}",
        "date": to_date,
        "author": username,
        "source": "github",
        "score": merged_count,
        "container": f"@{username}",
        "snippet": velocity_text,
        "relevance": 0.95,
        "why_relevant": f"GitHub profile: @{username} - {merged_count} PRs merged across {num_repos} repos",
        "engagement": {"merged_prs": merged_count, "comments": total_prs},
        "metadata": {
            "labels": ["person-profile", "velocity"],
            "state": "open",
            "comment_count": 0,
            "reactions": merged_count,
            "is_pr": False,
        },
    })

    # Phase 5: Enrich external repos (parallel: star counts + releases)
    _log(f"Enriching {len(external_repos)} external repos + {len(own_repos_info)} own repos")

    with ThreadPoolExecutor(max_workers=8) as executor:
        # External repo enrichment: stars + releases
        ext_futures = {}
        for repo, pr_count in external_repos:
            ext_futures[executor.submit(_enrich_external_repo, repo, resolved_token)] = (repo, pr_count)

        # Own repo enrichment: README + releases + top issues
        own_futures = {}
        for own_repo in own_repos_info:
            own_futures[executor.submit(_enrich_own_repo, own_repo["full_name"], resolved_token)] = own_repo

        # Collect external repo results
        for future in as_completed(ext_futures):
            repo, pr_count = ext_futures[future]
            try:
                enrichment = future.result(timeout=20)
            except Exception as exc:
                _log(f"External repo enrichment failed for {repo}: {exc}")
                enrichment = {}

            repo_info = enrichment.get("info")
            releases = enrichment.get("releases", [])

            stars = repo_info["stars"] if repo_info else 0
            stars_str = _format_stars(stars)
            desc = repo_info["description"] if repo_info else ""

            snippet_parts = [f"Contributed {pr_count} merged PRs to {repo} ({stars_str} stars)"]
            if desc:
                snippet_parts.append(f"  {desc}")
            if releases:
                for rel in releases[:2]:
                    body_preview = f" - {rel['body'][:150]}" if rel.get("body") else ""
                    snippet_parts.append(f"  Latest release: {rel['name']} ({rel['date']}){body_preview}")

            idx += 1
            items.append({
                "id": f"GH{idx}",
                "title": f"{repo} ({stars_str} stars) - {pr_count} PRs merged",
                "url": f"https://github.com/{repo}",
                "date": releases[0]["date"] if releases and releases[0].get("date") else to_date,
                "author": username,
                "source": "github",
                "score": stars,
                "container": repo,
                "snippet": "\n".join(snippet_parts),
                "relevance": min(0.9, 0.6 + math.log1p(stars) / 30 + min(0.15, pr_count / 20)),
                "why_relevant": f"GitHub contribution: {pr_count} PRs merged to {repo} ({stars_str} stars)",
                "engagement": {"stars": stars, "comments": pr_count},
                "metadata": {
                    "labels": ["person-profile", "external-repo"],
                    "state": "open",
                    "comment_count": pr_count,
                    "reactions": stars,
                    "is_pr": False,
                },
            })

        # Collect own repo results
        for future in as_completed(own_futures):
            own_repo = own_futures[future]
            try:
                enrichment = future.result(timeout=25)
            except Exception as exc:
                _log(f"Own repo enrichment failed for {own_repo['full_name']}: {exc}")
                enrichment = {}

            repo_name = own_repo["full_name"]
            stars = own_repo["stars"]
            stars_str = _format_stars(stars)
            open_issues = own_repo["open_issues"]
            desc = own_repo["description"]

            readme = enrichment.get("readme")
            releases = enrichment.get("releases", [])
            top_issues = enrichment.get("top_issues", {})

            snippet_parts = [f"Own project: {repo_name} ({stars_str} stars, {open_issues} open issues)"]
            if desc:
                snippet_parts.append(f"  {desc}")
            if readme:
                snippet_parts.append(f"  README: {readme[:300]}")
            if releases:
                for rel in releases[:2]:
                    body_preview = f" - {rel['body'][:150]}" if rel.get("body") else ""
                    snippet_parts.append(f"  Latest release: {rel['name']} ({rel['date']}){body_preview}")
            feat = top_issues.get("top_feature_request")
            if feat:
                snippet_parts.append(f"  Top feature request: \"{feat['title']}\" ({feat['reactions']} reactions, {feat['comments']} comments)")
            complaint = top_issues.get("top_complaint")
            if complaint:
                snippet_parts.append(f"  Top complaint: \"{complaint['title']}\" ({complaint['comments']} comments)")

            idx += 1
            items.append({
                "id": f"GH{idx}",
                "title": f"{repo_name} ({stars_str} stars) - own project, {open_issues} open issues",
                "url": f"https://github.com/{repo_name}",
                "date": releases[0]["date"] if releases and releases[0].get("date") else to_date,
                "author": username,
                "source": "github",
                "score": stars,
                "container": repo_name,
                "snippet": "\n".join(snippet_parts),
                "relevance": min(0.95, 0.7 + math.log1p(stars) / 25),
                "why_relevant": f"GitHub own project: {repo_name} ({stars_str} stars)",
                "engagement": {"stars": stars, "comments": open_issues},
                "metadata": {
                    "labels": ["person-profile", "own-repo"],
                    "state": "open",
                    "comment_count": open_issues,
                    "reactions": stars,
                    "is_pr": False,
                },
            })

    # Sort by relevance
    items.sort(key=lambda x: x.get("relevance", 0), reverse=True)
    _log(f"Person-mode returned {len(items)} items")
    return items


def _person_recent_pushes(
    username: str,
    from_date: str,
    to_date: str,
    limits: Dict[str, int],
    token: str,
) -> List[Dict[str, Any]]:
    """Return repos the selected actor publicly pushed inside the window."""
    latest_by_repo: Dict[str, Dict[str, str]] = {}
    encoded_username = urllib.parse.quote(username, safe="")

    page = 1
    while True:
        url = (
            f"https://api.github.com/users/{encoded_username}/events/public"
            f"?per_page={PERSON_EVENTS_PER_PAGE}&page={page}"
        )
        data = _fetch_json(url, token=token, timeout=15)
        if not data or not isinstance(data, list):
            break

        reached_before_window = False
        for event in data:
            created_at = event.get("created_at")
            pushed = _parse_date(created_at)
            if not pushed:
                continue
            if pushed < from_date:
                reached_before_window = True
                break
            if pushed > to_date or event.get("type") != "PushEvent":
                continue

            actor = event.get("actor")
            actor_login = actor.get("login", "") if isinstance(actor, dict) else ""
            if actor_login.casefold() != username.casefold():
                continue

            repo = event.get("repo")
            full_name = repo.get("name", "") if isinstance(repo, dict) else ""
            if not re.fullmatch(r"[^/\s]+/[^/\s]+", full_name):
                continue

            previous = latest_by_repo.get(full_name)
            if previous is None or created_at > previous["created_at"]:
                latest_by_repo[full_name] = {
                    "full_name": full_name,
                    "pushed": pushed,
                    "created_at": created_at,
                    "actor": actor_login,
                    "event_id": str(event.get("id") or ""),
                }

        if reached_before_window or len(data) < PERSON_EVENTS_PER_PAGE:
            break
        page += 1

    if not latest_by_repo:
        return []

    recent = sorted(
        latest_by_repo.values(),
        key=lambda r: r["created_at"],
        reverse=True,
    )
    _log(
        f"Public events: {len(recent)} actor-attributed repos pushed in window, "
        "loading repository metadata for ranking"
    )

    repo_info: Dict[str, Dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        info_futures = {
            executor.submit(_fetch_repo_info, r["full_name"], token): r["full_name"]
            for r in recent
        }
        for future in as_completed(info_futures):
            name = info_futures[future]
            try:
                repo_info[name] = future.result(timeout=20) or {}
            except Exception as exc:
                _log(f"Push-event repo metadata failed for {name}: {exc}")
                repo_info[name] = {}

    recent.sort(
        key=lambda r: (
            repo_info.get(r["full_name"], {}).get("stars", 0),
            r["created_at"],
        ),
        reverse=True,
    )
    selected = recent[:limits["own_repos"]]

    enrichments: Dict[str, Dict[str, Any]] = {}
    _log(f"Public events: enriching {len(selected)} top-ranked repositories")
    with ThreadPoolExecutor(max_workers=8) as executor:
        enrichment_futures = {
            executor.submit(_enrich_own_repo, r["full_name"], token): r["full_name"]
            for r in selected
        }
        for future in as_completed(enrichment_futures):
            name = enrichment_futures[future]
            try:
                enrichments[name] = future.result(timeout=25)
            except Exception as exc:
                _log(f"Push-event enrichment failed for {name}: {exc}")
                enrichments[name] = {}

    items: List[Dict[str, Any]] = []
    for idx, repo in enumerate(selected, start=1):
        name = repo["full_name"]
        info = repo_info.get(name, {})
        stars = info.get("stars", 0)
        stars_str = _format_stars(stars)
        open_issues = info.get("open_issues", 0)
        enrichment = enrichments.get(name, {})
        readme = enrichment.get("readme")
        releases = enrichment.get("releases", [])

        snippet_parts = [
            f"@{repo['actor']} pushed {name} on {repo['pushed']} "
            f"({stars_str} stars, {open_issues} open issues)"
        ]
        if info.get("description"):
            snippet_parts.append(f"  {info['description']}")
        if readme:
            snippet_parts.append(f"  README: {readme[:300]}")
        for rel in releases[:2]:
            body_preview = f" - {rel['body'][:150]}" if rel.get("body") else ""
            snippet_parts.append(f"  Release: {rel['name']} ({rel['date']}){body_preview}")

        items.append({
            "id": f"GH{idx}",
            "title": f"@{repo['actor']} pushed {name} on {repo['pushed']}",
            "url": f"https://github.com/{name}",
            "date": repo["pushed"],
            "author": repo["actor"],
            "source": "github",
            "score": stars,
            "container": name,
            "snippet": "\n".join(snippet_parts),
            "relevance": min(0.9, 0.6 + math.log1p(stars) / 30),
            "why_relevant": (
                f"GitHub activity: @{repo['actor']} pushed {name} on {repo['pushed']} "
                f"({stars_str} stars)"
            ),
            "engagement": {"stars": stars, "comments": open_issues},
            "metadata": {
                "labels": ["person-profile", "recent-push"],
                "state": "open",
                "comment_count": open_issues,
                "reactions": stars,
                "is_pr": False,
                "event_type": "PushEvent",
                "event_id": repo["event_id"],
            },
        })

    return items


def _enrich_external_repo(repo: str, token: str) -> Dict[str, Any]:
    """Fetch star count + releases for an external repo."""
    info = _fetch_repo_info(repo, token)
    releases = _fetch_latest_releases(repo, token, count=3)
    return {"info": info, "releases": releases}


def _enrich_own_repo(repo: str, token: str) -> Dict[str, Any]:
    """Fetch README + releases + top issues for an own repo."""
    readme = _fetch_readme_snippet(repo, token, max_chars=500)
    releases = _fetch_latest_releases(repo, token, count=3)
    top_issues = _fetch_top_issues(repo, token)
    return {"readme": readme, "releases": releases, "top_issues": top_issues}


# ---------------------------------------------------------------------------
# Project-mode search: fetch comprehensive data for specific repos
# ---------------------------------------------------------------------------

def search_github_project(
    repos: List[str],
    from_date: str,
    to_date: str,
    depth: str = "default",
    token: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Project-mode GitHub search: fetch stars, README, releases, top issues for repos.

    Args:
        repos: List of 'owner/repo' strings.
        from_date: Start date (YYYY-MM-DD).
        to_date: End date (YYYY-MM-DD).
        depth: 'quick', 'default', or 'deep'.
        token: Optional GitHub token.

    Returns:
        List of SourceItems, one per repo.
    """
    resolved_token = _resolve_token(token)
    if not resolved_token:
        _log("No GitHub token available for project-mode search")
        return []

    _log(f"Project-mode search for {len(repos)} repos: {', '.join(repos)}")

    items: List[Dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=min(8, len(repos))) as executor:
        futures = {
            executor.submit(_enrich_project_repo, repo, resolved_token): repo
            for repo in repos
        }

        for idx, future in enumerate(as_completed(futures)):
            repo = futures[future]
            try:
                enrichment = future.result(timeout=25)
            except Exception as exc:
                _log(f"Project enrichment failed for {repo}: {exc}")
                continue

            info = enrichment.get("info")
            if not info:
                _log(f"No repo info for {repo}, skipping")
                continue

            readme = enrichment.get("readme")
            releases = enrichment.get("releases", [])
            top_issues = enrichment.get("top_issues", {})

            stars = info["stars"]
            stars_str = _format_stars(stars)
            open_issues = info["open_issues"]
            desc = info["description"]
            lang = info["language"]

            snippet_parts = [f"Project: {repo} ({stars_str} stars, {open_issues} open issues, {lang})"]
            if desc:
                snippet_parts.append(f"  {desc}")
            if readme:
                snippet_parts.append(f"  README: {readme[:400]}")
            if releases:
                for rel in releases[:2]:
                    body_preview = f" - {rel['body'][:150]}" if rel.get("body") else ""
                    snippet_parts.append(f"  Latest release: {rel['name']} ({rel['date']}){body_preview}")
            feat = top_issues.get("top_feature_request")
            if feat:
                snippet_parts.append(f"  Top feature request: \"{feat['title']}\" ({feat['reactions']} reactions, {feat['comments']} comments)")
            complaint = top_issues.get("top_complaint")
            if complaint:
                snippet_parts.append(f"  Top complaint: \"{complaint['title']}\" ({complaint['comments']} comments)")

            items.append({
                "id": f"GH{idx + 1}",
                "title": f"{repo} ({stars_str} stars) - {open_issues} open issues",
                "url": f"https://github.com/{repo}",
                "date": releases[0]["date"] if releases and releases[0].get("date") else to_date,
                "author": repo.split("/")[0],
                "source": "github",
                "score": stars,
                "container": repo,
                "snippet": "\n".join(snippet_parts),
                "relevance": min(0.95, 0.7 + math.log1p(stars) / 25),
                "why_relevant": f"GitHub project: {repo} ({stars_str} stars, live)",
                "engagement": {"stars": stars, "comments": open_issues},
                "metadata": {
                    "labels": ["project-mode"],
                    "state": "open",
                    "comment_count": open_issues,
                    "reactions": stars,
                    "is_pr": False,
                    "github_stars": {repo: stars},
                },
            })

    items.sort(key=lambda x: x.get("relevance", 0), reverse=True)
    _log(f"Project-mode returned {len(items)} items")
    return items


def _enrich_project_repo(repo: str, token: str) -> Dict[str, Any]:
    """Fetch all project data for a repo: info + README + releases + top issues."""
    info = _fetch_repo_info(repo, token)
    readme = _fetch_readme_snippet(repo, token, max_chars=500)
    releases = _fetch_latest_releases(repo, token, count=3)
    top_issues = _fetch_top_issues(repo, token)
    return {"info": info, "readme": readme, "releases": releases, "top_issues": top_issues}


# ---------------------------------------------------------------------------
# Post-rerank star enrichment: annotate candidates with live star counts
# ---------------------------------------------------------------------------

_REPO_URL_PATTERN = re.compile(r"github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)")
_SKIP_PATHS = {"topics", "search", "orgs", "settings", "features", "about", "pricing", "enterprise", "explore", "marketplace", "sponsors"}


def extract_repo_refs(candidates: List[Any]) -> List[str]:
    """Extract unique owner/repo strings from candidate URLs, titles, and snippets."""
    seen: set = set()
    repos: List[str] = []
    for c in candidates:
        texts = [
            getattr(c, "url", "") or "",
            getattr(c, "title", "") or "",
        ]
        # Also check evidence snippets if available
        evidence = getattr(c, "evidence", None)
        if evidence:
            texts.append(str(evidence))
        for text in texts:
            for match in _REPO_URL_PATTERN.findall(text):
                # Normalize: strip trailing .git, lowercase
                repo = match.rstrip(".git").lower()
                owner = repo.split("/")[0]
                if owner in _SKIP_PATHS:
                    continue
                if repo not in seen:
                    seen.add(repo)
                    repos.append(match)  # preserve original case
    return repos


def enrich_candidates_with_stars(
    candidates: List[Any],
    token: Optional[str] = None,
    already_enriched: Optional[set] = None,
    max_repos: int = 10,
    collect_map: Optional[Dict[str, int]] = None,
) -> int:
    """Annotate candidates with live GitHub star counts.

    Returns the number of repos enriched.
    """
    resolved_token = _resolve_token(token)
    if not resolved_token:
        return 0

    refs = extract_repo_refs(candidates)
    if not refs:
        return 0

    skip = already_enriched or set()
    to_fetch = [r for r in refs if r.lower() not in {s.lower() for s in skip}][:max_repos]
    if not to_fetch:
        return 0

    _log(f"Star enrichment: fetching {len(to_fetch)} repos")

    # Parallel fetch star counts
    star_map: Dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=min(8, len(to_fetch))) as executor:
        futures = {executor.submit(_fetch_repo_info, repo, resolved_token): repo for repo in to_fetch}
        for future in as_completed(futures):
            repo = futures[future]
            try:
                info = future.result(timeout=10)
                if info:
                    star_map[repo.lower()] = info["stars"]
            except Exception:
                pass

    if collect_map is not None:
        collect_map.update(star_map)
    if not star_map:
        return 0

    return apply_star_map(candidates, star_map)


def apply_star_map(candidates: List[Any], star_map: Dict[str, int]) -> int:
    """Annotate candidates from a repo->stars map (fetch/apply split).

    Split out so offline replay (the eval harness) can apply a recorded map
    without any network or gh-credential access.
    """
    if not star_map:
        return 0
    # Annotate candidates
    enriched_count = 0
    for c in candidates:
        texts = [getattr(c, "url", "") or "", getattr(c, "title", "") or ""]
        evidence = getattr(c, "evidence", None)
        if evidence:
            texts.append(str(evidence))
        combined = " ".join(texts)
        for match in _REPO_URL_PATTERN.findall(combined):
            repo_lower = match.rstrip(".git").lower()
            if repo_lower in star_map:
                stars = star_map[repo_lower]
                stars_str = _format_stars(stars)
                # Add to metadata
                if not hasattr(c, "metadata") or c.metadata is None:
                    continue
                if "github_stars" not in c.metadata:
                    c.metadata["github_stars"] = {}
                c.metadata["github_stars"][match] = stars
                # Append to evidence if present
                if hasattr(c, "evidence") and c.evidence and f"(live:" not in c.evidence:
                    c.evidence = c.evidence + f" (live: {stars_str} stars)"
                enriched_count += 1
                break  # one annotation per candidate

    _log(f"Star enrichment: annotated {enriched_count} candidates")
    return enriched_count
