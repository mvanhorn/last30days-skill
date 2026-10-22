"""Auto-resolve subreddits, X handles, and current events context for a topic.

Uses web search (Brave/Exa/Serper) to discover relevant communities and context
before the planner runs. This is the engine-side equivalent of SKILL.md Steps
0.55/0.75 which use Claude Code's WebSearch tool.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Optional

from . import categories, dates, grounding, http, log

MAX_SUBS = 10


def _log(msg: str) -> None:
    log.source_log("Resolve", msg, tty_only=False)


def _merge_category_peers(topic: str, subreddits: list[str]) -> tuple[list[str], Optional[str]]:
    """Extend the WebSearch-extracted subreddit list with category peers.

    Classifies the topic, fetches the category's peer subs, dedupes
    case-insensitively against the existing list, and appends missing
    peers in priority order. Caps the final list at MAX_SUBS, preserving
    every WebSearch-returned sub (they are the freshest signal) and
    trimming from the peer-additions end.

    Returns a tuple of (merged_subs, matched_category_id_or_None).
    Emits a [Resolve] Matched category log line only when peers were
    actually added (not when every peer was already in the WebSearch set).

    Classification failures degrade to "no match" — the unwidened list
    is returned and a warning is logged.
    """
    try:
        category = categories.detect_category(topic)
    except Exception as exc:
        _log(f"Category classification failed: {exc}")
        return list(subreddits)[:MAX_SUBS], None

    if category is None:
        return list(subreddits)[:MAX_SUBS], None

    peers = categories.peer_subs_for(category)
    if not peers:
        return list(subreddits)[:MAX_SUBS], category

    existing_lower = {s.lower() for s in subreddits}
    merged = list(subreddits)
    added: list[str] = []
    for peer in peers:
        if len(merged) >= MAX_SUBS:
            break
        if peer.lower() in existing_lower:
            continue
        merged.append(peer)
        existing_lower.add(peer.lower())
        added.append(peer)

    if added:
        _log(f"Matched category={category}, adding peers: {', '.join(added)}")

    return merged, category


def _has_backend(config: dict) -> bool:
    """Check if any web search backend is available."""
    return bool(
        config.get("BRAVE_API_KEY")
        or config.get("EXA_API_KEY")
        or config.get("SERPER_API_KEY")
        or config.get("PARALLEL_API_KEY")
        or config.get("OPENROUTER_API_KEY")
        or config.get("PERPLEXITY_API_KEY")
    )


def _extract_subreddits(items: list[dict]) -> list[str]:
    """Parse subreddit names from search result titles and snippets."""
    pattern = re.compile(r"r/([A-Za-z0-9_]{2,21})")
    seen: set[str] = set()
    results: list[str] = []
    for item in items:
        text = f"{item.get('title', '')} {item.get('snippet', '')} {item.get('url', '')}"
        for match in pattern.findall(text):
            lower = match.lower()
            if lower not in seen:
                seen.add(lower)
                results.append(match)
    return results


def _extract_x_handle(items: list[dict]) -> str:
    """Extract the most likely X/Twitter handle from search results."""
    pattern = re.compile(r"@([A-Za-z0-9_]{1,15})")
    url_pattern = re.compile(r"(?:twitter\.com|x\.com)/([A-Za-z0-9_]{1,15})(?:/|$|\?)")
    counts: dict[str, int] = {}
    for item in items:
        text = f"{item.get('title', '')} {item.get('snippet', '')}"
        url = item.get("url", "")
        for match in pattern.findall(text):
            lower = match.lower()
            counts[lower] = counts.get(lower, 0) + 1
        for match in url_pattern.findall(url):
            lower = match.lower()
            # URL matches are stronger signals
            counts[lower] = counts.get(lower, 0) + 3
    # Filter out generic handles
    skip = {"twitter", "x", "search", "hashtag", "intent", "share", "i", "home", "explore", "settings"}
    counts = {k: v for k, v in counts.items() if k not in skip}
    if not counts:
        return ""
    return max(counts, key=counts.get)


def _extract_github_user(items: list[dict]) -> str:
    """Extract GitHub username from search results."""
    url_pattern = re.compile(r"github\.com/([A-Za-z0-9_-]{1,39})(?:/|$|\?)")
    counts: dict[str, int] = {}
    for item in items:
        url = item.get("url", "")
        text = f"{item.get('title', '')} {item.get('snippet', '')}"
        for match in url_pattern.findall(url):
            lower = match.lower()
            counts[lower] = counts.get(lower, 0) + 3
        for match in url_pattern.findall(text):
            lower = match.lower()
            counts[lower] = counts.get(lower, 0) + 1
    # Filter out org/repo-like names and generic pages
    skip = {"topics", "explore", "settings", "orgs", "search", "features", "about", "pricing", "enterprise"}
    counts = {k: v for k, v in counts.items() if k not in skip}
    if not counts:
        return ""
    return max(counts, key=counts.get)


# Hosts that can never be a brand's own site; their presence in results is
# platform noise, not an official-domain signal.
_PLATFORM_HOSTS = {
    "reddit.com", "x.com", "twitter.com", "github.com", "youtube.com",
    "facebook.com", "instagram.com", "tiktok.com", "linkedin.com",
    "wikipedia.org", "medium.com", "trustpilot.com", "crunchbase.com",
    "bloomberg.com", "apple.com", "play.google.com", "google.com",
    "threads.com", "pinterest.com", "glassdoor.com", "indeed.com",
    "news.ycombinator.com", "substack.com", "amazon.com", "ebay.com",
}


def _extract_official_domain(topic: str, items: list[dict]) -> str:
    """Extract the brand's own domain from search-result URLs.

    Conservative on purpose: only a hostname whose registrable label
    normalizes to the topic name qualifies ("ThriftBooks" -> thriftbooks.com).
    This feeds Trustpilot targeting as a HINT (the engine retries via the
    CLI's own search when the hint misses), so a miss here is cheap and a
    wrong guess is recoverable.
    """
    want = re.sub(r"[^a-z0-9]", "", topic.lower())
    if not want:
        return ""
    host_pattern = re.compile(r"https?://([A-Za-z0-9.-]+)")
    for item in items:
        for source in [item.get("url", ""), f"{item.get('title', '')} {item.get('snippet', '')}"]:
            for host in host_pattern.findall(source):
                host = host.lower().strip(".")
                bare = host.removeprefix("www.")
                if any(bare == p or bare.endswith("." + p) for p in _PLATFORM_HOSTS):
                    continue
                labels = bare.split(".")
                if len(labels) < 2:
                    continue
                registrable = labels[-2] if labels[-2] not in ("co", "com") or len(labels) < 3 else labels[-3]
                if re.sub(r"[^a-z0-9]", "", registrable) == want:
                    return bare
    return ""


_HREF = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
# Profile links only: x.com/{handle} with nothing after the handle but a slash,
# query or fragment. /status/, /intent/ and /share links name someone else's
# post or a share widget, never the site's own account.
_X_PROFILE_LINK = re.compile(
    r"^(?:https?:)?//(?:www\.|mobile\.)?(?:x|twitter)\.com/([A-Za-z0-9_]{1,15})/?(?:[?#].*)?$",
    re.IGNORECASE,
)
_GITHUB_OWNER_LINK = re.compile(
    r"^(?:https?:)?//(?:www\.)?github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))(?:[/?#].*)?$",
    re.IGNORECASE,
)
_X_NON_ACCOUNTS = {
    "twitter", "x", "search", "hashtag", "intent", "share", "i", "home",
    "explore", "settings", "login", "signup", "tos", "privacy", "messages",
    "notifications", "compose",
}
_GITHUB_NON_OWNERS = {
    "topics", "explore", "settings", "orgs", "search", "features", "about",
    "pricing", "enterprise", "login", "join", "sponsors", "marketplace",
    "apps", "collections", "trending", "site", "security", "customer-stories",
}


def _official_site_url(site: str) -> str:
    """Normalize a domain or URL to the site's homepage URL, or ""."""
    text = (site or "").strip()
    if not text:
        return ""
    host = re.sub(r"^[a-z][a-z0-9+.-]*://", "", text, flags=re.IGNORECASE)
    host = re.split(r"[/?#]", host, maxsplit=1)[0].strip().lower()
    if not re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+(:\d+)?", host):
        return ""
    return f"https://{host}/"


_CHROME_REGION = re.compile(
    r"<(header|footer|nav)\b[^>]*>(.*?)</\1\s*>", re.IGNORECASE | re.DOTALL,
)


def _site_label(url: str) -> str:
    """The registrable label of a homepage URL: https://www.typesafe.ai/ -> typesafe."""
    labels = url.split("://", 1)[-1].strip("/").split(":")[0].split(".")
    labels = [label for label in labels if label not in {"www", "m"}]
    return re.sub(r"[^a-z0-9]", "", labels[0].lower()) if labels else ""


def _own_account(links: list[tuple[str, bool]], site_label: str) -> str:
    """The site's own account among its profile links, or "" when ambiguous.

    A homepage also links customers, testimonials, and dependencies, so
    link counts and page order cannot tell the site's account from someone
    else's. Accept an account only when it is the sole one linked, the sole
    one in the page's header/footer/nav, or the sole one named after the site.
    """
    # X and GitHub names are case-insensitive: TypeSafeAI and typesafeai are
    # one account. Keep the first spelling the page uses.
    spelling: dict[str, str] = {}
    for name, _ in links:
        spelling.setdefault(name.lower(), name)
    distinct = list(spelling.values())
    if len(distinct) <= 1:
        return distinct[0] if distinct else ""
    chrome = list(dict.fromkeys(spelling[name.lower()] for name, in_chrome in links if in_chrome))
    if len(chrome) == 1:
        return chrome[0]
    named = [
        name for name in (chrome or distinct)
        if site_label and len(site_label) >= 3
        and site_label in re.sub(r"[^a-z0-9]", "", name.lower())
    ]
    return named[0] if len(named) == 1 else ""


def official_site_socials(site: str, *, timeout: int = 10) -> dict[str, str]:
    """Read a brand's own X and GitHub accounts from its homepage links.

    A company's homepage links its own accounts (usually in the header or
    footer), so this is the most reliable first-party source for the handle,
    and it needs no search key. Only ``href`` targets count: a handle quoted
    in page copy may be anyone's. Returns ``{"x_handle": ..., "github_user":
    ...}`` with "" for any account the page does not link, and both empty
    when the page cannot be fetched.
    """
    result = {"x_handle": "", "github_user": ""}
    url = _official_site_url(site)
    if not url:
        return result
    html = http.get_text(url, timeout=timeout, retries=1, accept="text/html")
    if not html:
        _log(f"Official site {url} could not be fetched")
        return result
    chrome_hrefs = {
        href.strip()
        for match in _CHROME_REGION.finditer(html)
        for href in _HREF.findall(match.group(2))
    }
    x_links: list[tuple[str, bool]] = []
    github_links: list[tuple[str, bool]] = []
    for href in _HREF.findall(html):
        href = href.strip()
        in_chrome = href in chrome_hrefs
        x_match = _X_PROFILE_LINK.match(href)
        if x_match and x_match.group(1).lower() not in _X_NON_ACCOUNTS:
            x_links.append((x_match.group(1), in_chrome))
            continue
        gh_match = _GITHUB_OWNER_LINK.match(href)
        if gh_match and gh_match.group(1).lower() not in _GITHUB_NON_OWNERS:
            github_links.append((gh_match.group(1), in_chrome))
    label = _site_label(url)
    result["x_handle"] = _own_account(x_links, label)
    result["github_user"] = _own_account(github_links, label)
    _log(
        f"Official site {url}: x_handle={result['x_handle']!r}, "
        f"github_user={result['github_user']!r}"
    )
    return result


def _extract_github_repos(items: list[dict]) -> list[str]:
    """Extract owner/repo strings from search results."""
    repo_pattern = re.compile(r"github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)")
    skip_owners = {"topics", "explore", "settings", "orgs", "search", "features", "about", "pricing", "enterprise"}
    seen: set[str] = set()
    repos: list[str] = []
    for item in items:
        url = item.get("url", "")
        text = f"{item.get('title', '')} {item.get('snippet', '')}"
        for source in [url, text]:
            for match in repo_pattern.findall(source):
                owner = match.split("/")[0].lower()
                if owner in skip_owners:
                    continue
                lower = match.lower()
                if lower not in seen:
                    seen.add(lower)
                    repos.append(match)
    return repos[:5]  # cap at 5 repos


_INTEGRATION_SUFFIX_KEYWORDS: dict[str, set[str]] = {
    "-action": {"action", "actions", "workflow", "workflows"},
    "-sdk": {"sdk", "client", "library"},
    "-plugin": {"plugin", "plugins", "extension", "extensions"},
    "-plugins": {"plugin", "plugins", "extension", "extensions"},
    "-docs": {"docs", "documentation"},
    "-examples": {"example", "examples", "sample", "samples"},
    "-template": {"template", "templates", "starter", "boilerplate"},
}


def _topic_tokens(topic: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (topic or "").lower()))


def _topic_entity_slugs(topic: str) -> list[str]:
    entities = re.split(r"\b(?:vs|versus)\b", (topic or "").lower())
    slugs: list[str] = []
    for entity in entities:
        tokens = re.findall(r"[a-z0-9]+", entity)
        if tokens:
            slugs.append("-".join(tokens))
    return slugs


def _repo_slug(repo: str) -> str:
    parts = repo.split("/", 1)
    if len(parts) != 2:
        return ""
    return parts[1].lower()


def _canonicalize_integration_repo(topic: str, repo: str) -> str:
    """Map integration repos back to canonical product repos when intent allows.

    Example:
      anthropics/claude-code-action -> anthropics/claude-code
    unless topic explicitly asks for "action"/"workflow".
    """
    parts = repo.split("/", 1)
    if len(parts) != 2:
        return repo
    owner, name = parts[0], parts[1]
    lower_name = name.lower()
    topic_words = _topic_tokens(topic)
    for suffix, intent_words in _INTEGRATION_SUFFIX_KEYWORDS.items():
        if not lower_name.endswith(suffix):
            continue
        if topic_words.intersection(intent_words):
            return repo
        base = name[: -len(suffix)]
        if base:
            return f"{owner}/{base}"
    return repo


def canonicalize_github_repos(topic: str, repos: list[str], *, cap: int | None = 5) -> list[str]:
    """Normalize/priority-sort GitHub repos for the current topic.

    - Rewrites common integration suffixes to canonical product repos when
      topic intent does not mention those integrations.
    - Promotes exact topic slug matches (e.g., `claude-code`) over partials.
    """
    canonicalized: list[str] = []
    seen: set[str] = set()
    for repo in repos:
        candidate = _canonicalize_integration_repo(topic, repo.strip())
        if "/" not in candidate:
            continue
        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        canonicalized.append(candidate)

    topic_slugs = set(_topic_entity_slugs(topic))
    if topic_slugs:
        exact = [r for r in canonicalized if _repo_slug(r) in topic_slugs]
        prefixed = [r for r in canonicalized if any(_repo_slug(r).startswith(f"{slug}-") for slug in topic_slugs) and r not in exact]
        rest = [r for r in canonicalized if r not in exact and r not in prefixed]
        canonicalized = exact + prefixed + rest

    if cap is not None:
        return canonicalized[:cap]
    return canonicalized


def _build_context_summary(items: list[dict]) -> str:
    """Build a 1-2 sentence current events summary from news search results."""
    snippets: list[str] = []
    for item in items[:3]:
        snippet = item.get("snippet", "").strip()
        if snippet:
            snippets.append(snippet)
    if not snippets:
        return ""
    # Take the first two meaningful snippets and truncate to keep it concise
    combined = " ".join(snippets[:2])
    if len(combined) > 300:
        combined = combined[:297] + "..."
    return combined


def auto_resolve(topic: str, config: dict) -> dict:
    """Discover subreddits, X handles, and current events context for a topic.

    Args:
        topic: The research topic.
        config: Dict with API keys (BRAVE_API_KEY, EXA_API_KEY, SERPER_API_KEY).

    Returns:
        Dict with keys: subreddits, x_handle, github_user, github_repos,
        context, category, searches_run. Returns empty result if no web
        search backend is available.
    """
    empty = {
        "subreddits": [],
        "x_handle": "",
        "github_user": "",
        "github_repos": [],
        "trustpilot_domain": "",
        "context": "",
        "category": None,
        "searches_run": 0,
    }

    if not _has_backend(config):
        _log("No web search backend available, skipping resolve")
        return empty

    from_date, to_date = dates.get_date_range(30)
    date_range = (from_date, to_date)
    now = datetime.now(timezone.utc)
    current_month = now.strftime("%B")
    current_year = now.strftime("%Y")

    queries = {
        "subreddit": f"{topic} subreddit reddit",
        "news": f"{topic} news {current_month} {current_year}",
        "x_handle": f"{topic} X twitter handle",
        "github": f"{topic} github profile site:github.com",
    }

    results: dict[str, list[dict]] = {}
    searches_run = 0

    def _search(label: str, query: str) -> tuple[str, list[dict]]:
        items, _artifact = grounding.web_search(query, date_range, config)
        return label, items

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(_search, label, q): label
            for label, q in queries.items()
        }
        for future in as_completed(futures):
            label = futures[future]
            try:
                _label, items = future.result()
                results[label] = items
                searches_run += 1
            except Exception as exc:
                _log(f"Search failed for {label}: {exc}")
                results[label] = []

    subreddits = _extract_subreddits(results.get("subreddit", []))
    x_handle = _extract_x_handle(results.get("x_handle", []))
    github_user = _extract_github_user(results.get("github", []))
    github_repos = canonicalize_github_repos(topic, _extract_github_repos(results.get("github", [])))
    context = _build_context_summary(results.get("news", []))
    # Official-site domain doubles as the Trustpilot targeting hint (review
    # pages are keyed by domain). Scan news first (official sites surface in
    # coverage), then the handle query's profile-adjacent results.
    trustpilot_domain = _extract_official_domain(
        topic, (results.get("news") or []) + (results.get("x_handle") or [])
    )

    subreddits, category = _merge_category_peers(topic, subreddits)

    _log(f"Resolved {len(subreddits)} subreddits, x_handle={x_handle!r}, github_user={github_user!r}, github_repos={github_repos!r}, trustpilot_domain={trustpilot_domain!r}, context_len={len(context)}, category={category!r}")

    return {
        "subreddits": subreddits,
        "x_handle": x_handle,
        "github_user": github_user,
        "github_repos": github_repos,
        "trustpilot_domain": trustpilot_domain,
        "context": context,
        "category": category,
        "searches_run": searches_run,
    }
