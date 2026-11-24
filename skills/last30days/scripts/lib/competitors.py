"""Discover peer entities ("competitors") for a topic via web search.

Mirrors the `resolve.auto_resolve()` pattern: fan out 2-3 web searches via
`grounding.web_search()`, then extract capitalized entity candidates from
titles and snippets with deterministic text mining. No LLM call — the
hosting reasoning model can always override discovery via
`--competitors-list`.

Returned list is ordered by score (frequency across queries) and capped to
the caller's requested count.
"""

from __future__ import annotations

import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import company_resolve, dates, grounding, log
from .resolve import _has_backend

# Peer cap vs total vs-entity cap (main + peers).
COMPETITORS_MIN = 1
COMPETITORS_MAX = 6
COMPETITORS_DEFAULT = 2
COMPARISON_ENTITY_MAX = COMPETITORS_MAX + 1
# Discovery SERP fan-out is small (3 queries today) but still needs a ceiling
# so a future query expansion cannot open one worker per query unbounded.
MAX_DISCOVERY_WORKERS = 3

# A "brand-shaped" token starts with uppercase OR is camelCase with an
# uppercase letter later. Catches "Anthropic", "OpenAI", "xAI", "iPhone",
# "eBay", "Hugging", "Face".
_BRAND_TOKEN = (
    r"(?:[A-Z][A-Za-z0-9&.\-]*"
    r"|[a-z][A-Za-z0-9&.\-]*[A-Z][A-Za-z0-9&.\-]*)"
)

# A capitalized phrase of 1-4 brand tokens separated by whitespace.
_CAPITALIZED_PHRASE = re.compile(
    rf"\b{_BRAND_TOKEN}(?:\s+{_BRAND_TOKEN}){{0,3}}\b"
)

# Title-case fillers common in listicle SERPs. Kept flat — extraction
# rejects a candidate whose entire tokens are stopwords, not candidates
# that merely contain one.
_STOPWORD_TOKENS: frozenset[str] = frozenset(
    token.lower()
    for token in (
        # Listicle fillers
        "Top", "Best", "Worst", "Popular", "Leading", "Similar",
        "Alternatives", "Alternative", "Competitor", "Competitors",
        "vs", "Vs", "Versus", "Review", "Reviews", "Comparison",
        "Guide", "List", "Lists", "Full", "Complete", "Free", "Paid",
        "Tools", "Tool", "Options", "Rivals", "Rival", "Similar",
        "Pick", "Picks", "Ranking", "Ranked", "Recommended",
        # Grammar / time
        "The", "A", "An", "Of", "In", "For", "To", "With", "On", "At",
        "By", "From", "Is", "Are", "And", "Or", "But", "Than", "As",
        "This", "That", "These", "Those", "Our", "Your", "Their",
        "January", "February", "March", "April", "May", "June", "July",
        "August", "September", "October", "November", "December",
        # Years likely to appear as standalone tokens
        *(str(year) for year in range(2018, 2031)),
        # Miscellaneous SERP noise
        "AI", "Apps", "App", "Software", "Platform", "Service", "Startups",
        "Companies", "Company", "Products", "Product", "Brands", "Brand",
    )
)


def _log(msg: str) -> None:
    log.source_log("Competitors", msg, tty_only=False)


_BRAND_SUFFIX_TOKENS: frozenset[str] = frozenset({
    "com", "org", "net", "io", "co", "ai", "dev", "js", "ts", "py",
    "app", "so", "gg", "sh", "tv", "me", "us", "uk", "de", "fr",
    "inc", "llc", "ltd", "corp",
})


def _brand_tokens(text: str) -> list[str]:
    """Extract lowercase brand-preserving tokens, trimming stray edge punctuation."""
    return [
        cleaned
        for raw in re.findall(r"[A-Za-z0-9&.\-]+", text.lower())
        if (cleaned := raw.strip(".-&"))
    ]


def _identity_token_set(tokens: list[str]) -> set[str]:
    """Expand brand tokens into comparable self-identity keys without leaking
    generic suffixes (.com/.js/.io/.dev/.ai) or single-letter fragments."""
    keys: set[str] = set()
    for tok in tokens:
        keys.add(tok)
        if any(ch in tok for ch in "&.-"):
            joined = re.sub(r"[^a-z0-9]", "", tok)
            if len(joined) >= 2:
                keys.add(joined)
            if "." in tok:
                base, suffix = tok.rsplit(".", 1)
                if suffix in _BRAND_SUFFIX_TOKENS and len(base) >= 2 and base not in _STOPWORD_TOKENS:
                    keys.add(base)
    max_span = min(len(tokens), 4)
    for span in range(2, max_span + 1):
        for start in range(len(tokens) - span + 1):
            joined_slice = re.sub(r"[^a-z0-9]", "", "".join(tokens[start : start + span]))
            if len(joined_slice) >= 2:
                keys.add(joined_slice)
    if len(tokens) > max_span:
        whole_joined = re.sub(r"[^a-z0-9]", "", "".join(tokens))
        if len(whole_joined) >= 2:
            keys.add(whole_joined)
    return keys


def _topic_tokens(topic: str) -> set[str]:
    """Return comparable lowercase identity tokens of the topic, excluding
    stopwords unless every topic token is a stopword."""
    tokens = _brand_tokens(topic)
    if not tokens:
        return set()
    all_keys = _identity_token_set(tokens)
    filtered = {tok for tok in all_keys if tok not in _STOPWORD_TOKENS}
    return filtered or all_keys


def _candidate_ok(candidate: str, topic_tokens: set[str]) -> bool:
    """Filter a candidate phrase against stopwords and topic overlap."""
    tokens = _brand_tokens(candidate)
    if not tokens:
        return False
    # Reject candidates made entirely of stopwords (e.g., "Top Alternatives").
    if all(tok in _STOPWORD_TOKENS for tok in tokens):
        return False
    # Reject candidates that overlap with the topic (e.g., topic="OpenAI"
    # should not return "OpenAI Alternatives" or "OpenAI", and punctuated
    # brands like "AT&T", "Node.js", or "Coca-Cola" must not match themselves).
    if _identity_token_set(tokens) & topic_tokens:
        return False
    # Reject too-short one-letter tokens like "I" or single digits.
    if len(tokens) == 1 and len(tokens[0]) < 2:
        return False
    return True


def _normalize_candidate(candidate: str) -> str:
    """Collapse whitespace and strip trailing punctuation."""
    return re.sub(r"\s+", " ", candidate).strip(".,;:!?'\"()[] ")


def _extract_peer_entities(
    items: list[dict], topic: str, limit: int,
) -> list[str]:
    """Score capitalized candidates across SERP items and return top `limit`.

    Scoring is bag-of-phrases frequency across all items in the input. Ties
    are broken by first-seen order so the output is deterministic.
    """
    topic_tokens = _topic_tokens(topic)
    counts: Counter[str] = Counter()
    first_seen: dict[str, int] = {}
    order = 0
    # Group candidates into a frequency map keyed by lowercased normalized
    # form so "xAI" and "xAI" count together regardless of case.
    canonical: dict[str, str] = {}
    for item in items:
        text = f"{item.get('title', '')} {item.get('snippet', '')}"
        for raw in _CAPITALIZED_PHRASE.findall(text):
            candidate = _normalize_candidate(raw)
            if not _candidate_ok(candidate, topic_tokens):
                continue
            key = candidate.lower()
            if key not in canonical:
                canonical[key] = candidate
                first_seen[key] = order
                order += 1
            counts[key] += 1

    ranked_keys = sorted(
        counts.keys(),
        key=lambda k: (-counts[k], first_seen[k]),
    )
    return [canonical[k] for k in ranked_keys[:limit]]


def _queries_for(topic: str) -> dict[str, str]:
    return {
        "competitors": f"{topic} competitors",
        "alternatives": f"{topic} alternatives",
        "vs": f"{topic} vs",
    }


_TRAILING_QUALIFIER = re.compile(r"\s*[\(\[][^)\]]*[\)\]]?\s*$")


def _strip_trailing_qualifiers(name: str) -> str:
    """Drop trailing '(YC W24)' / '[stealth]' tags, including unbalanced leftovers."""
    cleaned = name
    while True:
        next_name = _TRAILING_QUALIFIER.sub("", cleaned)
        if next_name == cleaned:
            return cleaned.strip()
        cleaned = next_name


def _company_name_from_title(title: str) -> str:
    """Strip listicle suffixes ('Anthropic | Building…', 'Adyen - payments')."""
    title = title.split("|")[0]
    for sep in (" — ", " – ", " - "):
        title = title.split(sep, 1)[0]
    return _normalize_candidate(_strip_trailing_qualifiers(title))


def _exa_competitors(topic: str, count: int, api_key: str) -> list[str]:
    """Discover peers from Exa's company index. No publication-date filters."""
    try:
        items, _artifact = grounding.exa_company_search(
            f"companies developing products like {topic} or competing with {topic}",
            api_key,
            count=max(count + 3, 8),
            objective=(
                f"Find independent companies that compete with {topic} or build "
                f"similar products. Do not return {topic} itself, subsidiaries, "
                "or directory and list pages."
            ),
        )
    except Exception as exc:
        _log(f"Exa company search failed: {exc}")
        return []
    topic_tokens = _topic_tokens(topic)
    names: list[str] = []
    seen: set[str] = set()
    for item in items:
        name = _company_name_from_title(str(item.get("title") or ""))
        if not _candidate_ok(name, topic_tokens):
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
        if len(names) >= count:
            break
    return names


def _merge_peer_names(*groups: list[str], limit: int) -> list[str]:
    """Keep first-seen order across sources, case-insensitive."""
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for name in group:
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(name)
            if len(merged) >= limit:
                return merged
    return merged


def _topic_is_company(config: dict, is_company: bool | None) -> bool:
    """True when the topic is already established as a company, not a person."""
    if is_company is False:
        return False
    company = config.get("_company") if isinstance(config.get("_company"), dict) else {}
    if company.get("company_via_person"):
        return False
    if company.get("company_peer_discovery") == company_resolve.COMPANY_PEER_KEYWORD:
        return False
    if is_company is True:
        return True
    if company.get("company"):
        return True
    return bool(config.get("_company_signal"))


def discover_competitors(
    topic: str,
    count: int,
    config: dict,
    *,
    lookback_days: int = 30,
    is_company: bool | None = None,
) -> list[str]:
    """Discover `count` peer entities for `topic` via web search.

    When ``EXA_API_KEY`` is set and the topic is a resolved company, Exa's
    ``company`` category runs first even if Brave (or another backend) is
    the general web search. Artist and person topics keep keyword SERP
    mining only. Keyword SERPs fill any remaining slots and are the only
    path without that key or without a company signal.

    Args:
        topic: The primary research topic.
        count: Desired number of competitor entities (1..N).
        config: Runtime config dict — expects the same shape as the engine
            config (BRAVE_API_KEY / EXA_API_KEY / SERPER_API_KEY / etc.).
        lookback_days: Date range for freshness. Defaults to 30.
        is_company: True when company resolution (or a host company
            signal) already established this topic as a company. False
            skips the company index. None reads ``config["_company"]`` /
            ``config["_company_signal"]``.

    Returns:
        A list of up to `count` entity names, deduped and ordered by score.
        Empty list when no web backend is configured or every search fails
        or returns zero usable candidates.
    """
    if count < 1:
        return []
    if not _has_backend(config):
        _log("No web search backend available, skipping competitor discovery")
        return []

    exa_names: list[str] = []
    exa_key = grounding.exa_api_key(config)
    if exa_key and _topic_is_company(config, is_company):
        exa_names = _exa_competitors(topic, count, exa_key)
        if len(exa_names) >= count:
            _log(
                f"Discovered {len(exa_names)} competitor(s) for {topic!r} "
                f"via Exa company search: {exa_names}"
            )
            return exa_names

    date_range = dates.get_date_range(lookback_days)
    queries = _queries_for(topic)
    collected: list[dict] = []
    searches_run = 0

    def _search(label: str, query: str) -> tuple[str, list[dict]]:
        items, _artifact = grounding.web_search(query, date_range, config)
        return label, items

    with ThreadPoolExecutor(max_workers=min(len(queries), MAX_DISCOVERY_WORKERS)) as executor:
        futures = {
            executor.submit(_search, label, q): label
            for label, q in queries.items()
        }
        for future in as_completed(futures):
            label = futures[future]
            try:
                _label, items = future.result()
                collected.extend(items)
                searches_run += 1
            except Exception as exc:
                _log(f"Search failed for {label}: {exc}")

    serp_names = _extract_peer_entities(collected, topic, limit=count) if collected else []
    entities = _merge_peer_names(exa_names, serp_names, limit=count)
    if not entities:
        if not collected and not exa_names:
            _log(
                f"No SERP results for {topic!r} across "
                f"{searches_run}/{len(queries)} queries"
            )
        return []

    source = "Exa company search and " if exa_names else ""
    _log(
        f"Discovered {len(entities)} competitor(s) for {topic!r} "
        f"from {source}{searches_run}/{len(queries)} queries: {entities}"
    )
    return entities
