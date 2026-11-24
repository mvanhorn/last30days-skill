"""Polymarket prediction market search via Gamma API (free, no auth required).

Uses gamma-api.polymarket.com for event/market discovery.
No API key needed - public read-only API.
"""

import datetime
import json
import math
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlencode

from . import http, log

GAMMA_SEARCH_URL = "https://gamma-api.polymarket.com/public-search"
GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"

SEARCH_LIMIT = {
    "quick": 10,
    "default": 20,
    "deep": 50,
}
# Gamma answers bursts with 429s that carry no Retry-After, so searches run
# two at a time and a rate-limited call is retried with backoff.
_SEARCH_WORKERS = 2
_SEARCH_ATTEMPTS = 3
# Most aliases and related names a plan may give (planner.py validates it).
HINT_CAP = 5

# Max events to return after merge + dedup + re-ranking
RESULT_CAP = {
    "quick": 5,
    "default": 15,
    "deep": 25,
}

# The selection rule. A market is live ("in band") under PRICE_CEILING with
# MIN_DAYS_TO_CLOSE or more days left and at or above its floor:
# RACE_PRICE_FLOOR when the subject (or a related name) is itself an outcome
# of a mutually exclusive race of RACE_MIN_OUTCOMES or more live outcomes,
# PRICE_FLOOR for anything else, including the brackets of a race the event
# title names the subject in ("OpenAI IPO Closing Market Cap"). An
# event scores ln(1 + money traded on its live entity markets), plus
# CONTENDER_BONUS when one of them is a top-CONTENDER_TOP outcome of such a
# race with at least CONTENDER_MIN_VOLUME traded on its own market. An event
# of more than GAME_SHEET_MARKETS markets contributes one market.
PRICE_CEILING = 0.97
PRICE_FLOOR = 0.03
RACE_PRICE_FLOOR = 0.005
RACE_MIN_OUTCOMES = 4
MIN_DAYS_TO_CLOSE = 7
CONTENDER_TOP = 3
CONTENDER_MIN_VOLUME = 1000.0
CONTENDER_BONUS = 1.0
GAME_SHEET_MARKETS = 30


def _log(msg: str):
    log.source_log("PM", msg, tty_only=False)


def _extract_core_subject(topic: str) -> str:
    """Extract core subject from topic string.

    Strips common prefixes like 'last 7 days', 'what are people saying about', etc.
    """
    topic = topic.strip()
    # Remove common leading phrases
    prefixes = [
        r"^last \d+ days?\s+",
        r"^what(?:'s| is| are) (?:people saying about|happening with|going on with)\s+",
        r"^how (?:is|are)\s+",
        r"^tell me about\s+",
        r"^research\s+",
    ]
    for pattern in prefixes:
        topic = re.sub(pattern, "", topic, flags=re.IGNORECASE)
    return topic.strip()


# Words that are too generic to serve as the sole topic-match signal.
# If ALL core words from the topic are in this set, we skip filtering (can't meaningfully filter).
# But if some words are informative and some are generic, we require at least one informative word.
_NOISE_WORDS = frozenset({
    # Articles, prepositions, conjunctions
    "the", "a", "an", "in", "on", "at", "of", "for", "and", "or", "to", "is", "are",
    "was", "were", "will", "be", "by", "with", "from", "as", "it", "its", "not", "no",
    "but", "if", "so", "do", "has", "had", "have", "this", "that", "what", "who",
    # Directional / geographic terms that cause false matches
    "west", "east", "north", "south", "central", "southern", "northern", "eastern", "western",
    # Common sports / category terms
    "champion", "championship", "league", "division", "conference", "cup", "series",
    "team", "game", "match", "season", "win", "winner", "finals",
    # Common geographic / place nouns that cause false matches
    # "club" -> Athletic Club, Racing Club; "island" -> Epstein's Island, Rhode Island
    "club", "island", "city", "park", "hill", "lake", "bay", "beach", "valley",
    "river", "mountain", "county", "state", "village", "town", "point", "creek",
    "springs", "heights", "ridge", "bridge", "harbor", "port", "station", "center",
    "square", "field", "forest", "garden", "tower", "school", "church", "camp",
    "ranch", "crossing", "shore", "rock", "summit", "falls", "grove", "haven",
    # Generic tech terms — see _DOMAIN_WORDS below, which is folded in here
    # Generic prediction market terms
    "market", "odds", "prediction", "forecast", "chance", "probability",
    # Comparison-query conjunctions — should not count as informative filter tokens
    # when the topic is "X vs Y vs Z"
    "vs", "versus",
})

# Generic tech terms that match too broadly to be the sole signal for a NARROW
# topic ("cli" -> any CLI tool market; "ai" -> every AI market), but which ARE
# the subject when the topic is a domain sweep rather than one product. Kept
# separate from the rest of _NOISE_WORDS — the directional/sports/place words
# there exist to PREVENT false matches ("NFC West" vs a "Kanye West" search),
# so they must never be used as a positive signal.
_DOMAIN_WORDS = frozenset({
    "cli", "mcp", "protocol", "tool", "app", "code", "model", "ai", "api",
    "software", "plugin", "skill", "agent", "bot", "search", "research",
})

# Soft residue left after stripping domain words from a sweep topic
# ("AI frontier developments"). Domain-word fallback may fire when these are
# the only informative leftovers. Distinctive terms like "benchmark" block it.
_SWEEP_RESIDUE = frozenset({
    "frontier", "developments", "development", "news", "trends", "trend",
    "latest", "industry", "space", "ecosystem", "landscape", "overview",
    "updates", "update", "future", "outlook", "sector", "field", "world",
})

_NOISE_WORDS = _NOISE_WORDS | _DOMAIN_WORDS


def _domain_stem(word: str) -> str | None:
    """Return the canonical domain token if ``word`` is a domain term or plural.

    Exact-set membership alone treats ``models`` as a hard narrowing term even
    though ``model`` is a domain word — which blocked soft AI sweeps and broke
    ``AI models`` → ``New AI prediction``.
    """
    if word in _DOMAIN_WORDS:
        return word
    if word.endswith("ies") and len(word) > 4:
        stem = word[:-3] + "y"
        if stem in _DOMAIN_WORDS:
            return stem
    if len(word) > 3 and word.endswith("es") and word[:-2] in _DOMAIN_WORDS:
        return word[:-2]
    if len(word) > 2 and word.endswith("s") and word[:-1] in _DOMAIN_WORDS:
        return word[:-1]
    return None


def _informative_words(core_words: list[str]) -> list[str]:
    """Topic words that are neither noise nor (possibly plural) domain terms."""
    return [
        w for w in core_words
        if w not in _NOISE_WORDS and _domain_stem(w) is None
    ]


def _domain_word_fallback_allows(core_words: list[str], informative: list[str],
                                 title_lower: str, title_words: set[str]) -> bool:
    """Allow domain-word title matches only for pure/soft domain sweeps.

    Blocks mixed topics like \"MCP protocol benchmark\" from accepting a Kyoto
    Protocol market via the shared domain token \"protocol\" when the distinctive
    informative word (\"benchmark\") missed.
    """
    hard_informative = [w for w in informative if w not in _SWEEP_RESIDUE]
    if hard_informative:
        return False
    domain_stems = []
    seen: set[str] = set()
    for w in core_words:
        stem = _domain_stem(w)
        if stem and stem not in seen:
            seen.add(stem)
            domain_stems.append(stem)
    if not domain_stems:
        return False
    for word in domain_stems:
        if word in title_words or f"{word}s" in title_words or f"{word}es" in title_words:
            return True
        if len(word) >= 4 and word in title_lower:
            return True
    return False


_UPPERCASE_ACRONYM_RE = re.compile(r"(?<!\w)[A-Z]{3,}(?!\w)")


def _uppercase_acronyms(text: Any) -> frozenset[str]:
    """Extract original-case all-caps 3+ letter acronym tokens before casefolding."""
    return frozenset(_UPPERCASE_ACRONYM_RE.findall(str(text or "")))


def _acronym_credit(core_words: list[str], title_acronyms: set[str] | frozenset[str]) -> int:
    """Credit matches when the title abbreviates a phrase the topic spells out.

    Prediction-market titles use shorthand ("AGI by 2030?") while topics arrive
    spelled out ("artificial general intelligence"), so word overlap scores zero
    on a title that is squarely on topic. For each run of 3+ consecutive
    informative words, build its initialism and, if the original-case title
    carries it as an all-caps whole word (excluding noise words even in all-caps
    titles), credit one match per abbreviated word. Requiring at least three
    letters avoids treating ambiguous tokens such as "ML" as expanded phrases.
    """
    informative_set = set(_informative_words(core_words))
    credit = 0
    run: list[str] = []
    for word in core_words + [""]:
        if word in informative_set:
            run.append(word)
            continue
        if len(run) >= 3:
            acronym = "".join(w[0] for w in run)
            if (
                len(acronym) >= 3
                and acronym not in _NOISE_WORDS
                and acronym.upper() in title_acronyms
            ):
                credit = max(credit, len(run))
        run = []
    return credit


@dataclass(frozen=True)
class RelatedName:
    """A person or thing tied to the subject (a team's player, a company's CEO)."""

    name: str
    relation: str = ""


@dataclass(frozen=True)
class PolymarketHints:
    """What the host planner knows about the subject and Gamma cannot say.

    ``kind`` is the subject's kind ("team", "company", "person", "product",
    "other"); only "team" changes selection. ``aliases`` name the subject
    itself ("F1" for "Formula 1") and are searched and matched like the topic.
    ``related`` names count only when they lead a race. Every field is
    optional: with none, the topic text alone drives search and matching.
    """

    kind: Optional[str] = None
    aliases: Tuple[str, ...] = ()
    related: Tuple[RelatedName, ...] = ()

    @classmethod
    def coerce(cls, value: Any) -> "PolymarketHints":
        """Hints from None, a ``PolymarketHints``, a plan dict, or an object
        with ``kind``/``aliases``/``related`` attributes."""
        if isinstance(value, cls):
            return value
        if value is None:
            return cls()

        def get(key: str) -> Any:
            if isinstance(value, dict):
                return value.get(key)
            return getattr(value, key, None)

        kind = str(get("kind") or "").strip().casefold() or None
        aliases = tuple(
            alias.strip() for alias in get("aliases") or []
            if isinstance(alias, str) and alias.strip()
        )
        related = []
        for entry in get("related") or []:
            if isinstance(entry, dict):
                name, relation = entry.get("name"), entry.get("relation")
            else:
                name, relation = getattr(entry, "name", None), getattr(entry, "relation", None)
            name = str(name or "").strip()
            if name:
                related.append(RelatedName(name, str(relation or "").strip()))
        return cls(kind, aliases, tuple(related))


def _match_text(text: Any) -> str:
    """Text as the entity matcher compares it: casefolded, possessive "'s"
    dropped, punctuation turned to spaces."""
    text = str(text or "").casefold().replace("’", "'")
    text = re.sub(r"'s\b", "", text)
    return " ".join(re.sub(r"[^\w\s]", " ", text).split())


def _has_word(word: str, words: set) -> bool:
    """Whether ``words`` holds ``word`` or its simple plural or singular form
    ("rate" and "rates", "tax" and "taxes")."""
    if word in words or f"{word}s" in words or f"{word}es" in words:
        return True
    if word.endswith("es") and word[:-2] in words:
        return True
    return word.endswith("s") and word[:-1] in words


class _Term:
    """One name the matcher looks for, as a whole phrase."""

    def __init__(self, name: str):
        self.phrase = re.sub(r"^the\s+", "", _match_text(name))
        self.pattern = (
            re.compile(rf"(?<!\w){re.escape(self.phrase)}(?!\w)") if self.phrase else None
        )
        self.words = [word for word in self.phrase.split() if len(word) > 1]
        self.informative = _informative_words(self.words)

    def found_in(
        self,
        text: str,
        *,
        acronyms: Optional[frozenset[str] | set[str]] = None,
    ) -> bool:
        """Whether ``text`` (already through ``_match_text``) names this term:
        the whole phrase at word boundaries, an all-caps initialism of a
        spelled-out phrase ("AGI"), every informative word of a descriptive
        topic in any order ("Nvidia earnings"), or, for a soft domain sweep
        ("AI frontier developments"), its domain word."""
        if self.pattern is None:
            return False
        if self.pattern.search(text):
            return True
        words = set(text.split())
        title_acronyms = _uppercase_acronyms(text) if acronyms is None else acronyms
        if title_acronyms and _acronym_credit(self.words, title_acronyms):
            return True
        if len(self.informative) >= 2 and all(_has_word(word, words) for word in self.informative):
            return True
        return _domain_word_fallback_allows(self.words, self.informative, text, words)


def _topic_sides(topic: str) -> List[str]:
    """Each side of a "vs" topic, or the topic's core subject alone."""
    return re.split(r"\s+(?:vs\.?|versus)\s+", _extract_core_subject(topic), flags=re.IGNORECASE)


def _prepare_match_text(raw: Any) -> Tuple[str, frozenset[str]]:
    return (_match_text(raw), _uppercase_acronyms(raw))


def _names_any(
    terms: List[_Term],
    texts: List[str | Tuple[str, frozenset[str]]],
) -> bool:
    """Whether any of ``terms`` is found in any non-empty text of ``texts``."""
    for entry in texts:
        if isinstance(entry, tuple):
            text, acronyms = entry
        else:
            text, acronyms = entry, None
        if not text:
            continue
        if any(term.found_in(text, acronyms=acronyms) for term in terms):
            return True
    return False


def _entity_terms(topic: str, aliases: Tuple[str, ...] = ()) -> List[str]:
    """The names that stand for the subject: each side of a "vs" topic, then
    each alias, once each."""
    sides = _topic_sides(topic or "")
    terms: List[str] = []
    seen: set = set()
    for term in [*sides, *aliases]:
        term = term.strip()
        key = _Term(term).phrase
        if key and key not in seen:
            seen.add(key)
            terms.append(term)
    return terms


def _json_list(value: Any) -> List[Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return []
    return list(value) if isinstance(value, (list, tuple)) else []


def _market_texts(market: Dict[str, Any]) -> List[str]:
    """A market's own text: its question, its ``groupItemTitle`` and its
    outcome labels other than Yes and No."""
    labels = [
        str(outcome) for outcome in _json_list(market.get("outcomes"))
        if str(outcome).strip().casefold() not in ("yes", "no")
    ]
    return [str(market.get("question") or ""), str(market.get("groupItemTitle") or ""), *labels]


@dataclass(frozen=True)
class EntityMatch:
    """How a market names the subject.

    ``field`` is "market" when the market's own question, label or outcome
    names it and "title" when only its event title does. ``related`` is the
    related name that matched, None when the subject (or an alias) did.
    """

    field: str
    related: Optional[str] = None
    relation: str = ""


class EntityMatcher:
    """Decides whether a market names the research subject.

    The topic and each alias match as whole phrases, ignoring a leading "the"
    and a possessive "'s", in a market's question, ``groupItemTitle``,
    non-Yes/No outcome labels, or its event title. A related name matches
    only the market's own text, and only when the subject does not.
    """

    def __init__(self, topic: str, hints: Any = None):
        self.hints = PolymarketHints.coerce(hints)
        self.terms = [_Term(term) for term in _entity_terms(topic, self.hints.aliases)]
        self.related = [(entry, _Term(entry.name)) for entry in self.hints.related]

    def match(
        self,
        market: Dict[str, Any],
        event_title: str = "",
        *,
        title_text: Optional[str] = None,
        title_acronyms: Optional[frozenset[str]] = None,
    ) -> Optional[EntityMatch]:
        """``title_text``, when given, is ``event_title`` already through
        ``_match_text``, so a caller matching many markets of one event
        normalizes the title once."""
        own = [_prepare_match_text(text) for text in _market_texts(market)]
        if _names_any(self.terms, own):
            return EntityMatch("market")
        title_pair = (
            _match_text(event_title) if title_text is None else title_text,
            _uppercase_acronyms(event_title) if title_acronyms is None else title_acronyms,
        )
        if _names_any(self.terms, [title_pair]):
            return EntityMatch("title")
        for entry, term in self.related:
            if _names_any([term], own):
                return EntityMatch("market", entry.name, entry.relation)
        return None

    def outcome_kind(
        self,
        market: Dict[str, Any],
        match: EntityMatch,
        event_title: str = "",
        *,
        title_text: Optional[str] = None,
        title_acronyms: Optional[frozenset[str]] = None,
    ) -> Optional[str]:
        """How the subject (or the related name) is an outcome of the event:
        "label" when the market's ``groupItemTitle`` names it and, for the
        subject, the event title does not; "outcome" when only a non-Yes/No
        outcome label names the subject; None when the market is about the
        subject without the subject being one of the event's outcomes (a
        bracket of "OpenAI IPO Closing Market Cap"). ``title_text`` is as for
        ``match``."""
        label = _prepare_match_text(market.get("groupItemTitle"))
        if match.related:
            terms = [term for entry, term in self.related if entry.name == match.related]
            return "label" if label[0] and _names_any(terms, [label]) else None
        title_pair = (
            _match_text(event_title) if title_text is None else title_text,
            _uppercase_acronyms(event_title) if title_acronyms is None else title_acronyms,
        )
        if _names_any(self.terms, [title_pair]):
            return None
        if label[0] and _names_any(self.terms, [label]):
            return "label"
        if _names_any(self.terms, [_prepare_match_text(market.get("question"))]):
            return None
        return "outcome" if self.matched_outcome(market, match) is not None else None

    def matched_outcome(self, market: Dict[str, Any], match: EntityMatch) -> Optional[str]:
        """The non-Yes/No outcome label that names the subject (or the related
        name that matched), for a market whose outcomes are names."""
        terms = (
            [term for entry, term in self.related if entry.name == match.related]
            if match.related else self.terms
        )
        for outcome in _json_list(market.get("outcomes")):
            name = str(outcome)
            if name.strip().casefold() in ("yes", "no"):
                continue
            if _names_any(terms, [_prepare_match_text(name)]):
                return name
        return None


def _item_matches(matcher: "EntityMatcher", item: Any) -> bool:
    """Whether a parsed or normalized item names what ``matcher`` looks for.

    Reads the event title, the matched market's question and label, the
    headline market's question and label, and the item's own question. A raw
    parse dict carries these at the top level, a ``SourceItem`` in metadata.
    """
    if isinstance(item, dict):
        fields = {**(item.get("metadata") or {}), **item}
    else:
        fields = {**(getattr(item, "metadata", None) or {}), "title": getattr(item, "title", "")}
    matched = fields.get("matched_market") if isinstance(fields.get("matched_market"), dict) else {}
    headline = fields.get("headline") if isinstance(fields.get("headline"), dict) else {}
    market = {
        "question": str(matched.get("question") or fields.get("question") or ""),
        "groupItemTitle": str(matched.get("label") or ""),
        "outcomes": [
            str(text) for text in (
                headline.get("question"), headline.get("label"), fields.get("question"),
            ) if text
        ],
    }
    return matcher.match(market, str(fields.get("title") or "")) is not None


def filter_items_against_topic(topic: str, items: List[Any], hints: Any = None) -> List[Any]:
    """Keep the items whose market names the research topic, by ``EntityMatcher``.

    The Polymarket topic bar, called post-merge from pipeline.py on the stable
    research topic and the run's hints. The event title alone is not enough:
    "Pro Football: 2027 Champion" names the 49ers only in its matched market,
    and a related player's award market only through a host-supplied name.
    Each side of a "vs" topic counts, so a market naming one compared entity
    stays.

    Accepts raw parse dicts or SourceItem-like objects and returns the kept
    items in the same order.
    """
    if not topic:
        return items
    matcher = EntityMatcher(topic, hints)
    filtered = [item for item in items if _item_matches(matcher, item)]

    dropped = len(items) - len(filtered)
    if dropped:
        _log(f"Post-merge topic filter dropped {dropped} Polymarket items against full topic '{topic}'")

    return filtered


def filter_items_against_keywords(items: List[Any], keywords: List[str]) -> List[Any]:
    """Keep only items whose market names at least one keyword, by ``EntityMatcher``.

    Intended for disambiguating ambiguous single-token topics like 'Warriors'
    via --polymarket-keywords (e.g., 'nba,gsw,golden-state') to filter out
    Glasgow Warriors rugby, Honor of Kings Rogue Warriors markets that share
    the 'Warriors' token but are not the target entity. Each keyword matches
    as a whole phrase, any case, in the same fields as the topic filter.
    """
    normalized_keywords = [kw.strip() for kw in keywords or [] if kw and kw.strip()]
    if not normalized_keywords:
        return items
    matcher = EntityMatcher("", PolymarketHints(aliases=tuple(normalized_keywords)))
    filtered = [item for item in items if _item_matches(matcher, item)]

    dropped = len(items) - len(filtered)
    if dropped:
        _log(
            f"Keyword filter dropped {dropped} Polymarket items; "
            f"kept {len(filtered)} matching {normalized_keywords}"
        )

    return filtered


def _search_term(term: str, limit: int, deadline: Optional[float] = None) -> Dict[str, Any]:
    """One volume-sorted public-search for one term, retries included, ending
    by ``deadline`` (a ``time.monotonic()`` value) when one is given."""
    params = {
        "q": term,
        "sort": "volume",
        "events_status": "active",
        "keep_closed_markets": "0",
        "limit_per_type": str(limit),
    }
    url = f"{GAMMA_SEARCH_URL}?{urlencode(params)}"
    try:
        return http.request(
            "GET", url, timeout=15,
            retries=_SEARCH_ATTEMPTS, max_429_retries=_SEARCH_ATTEMPTS,
            deadline_monotonic=deadline,
        )
    except Exception as e:
        _log(f"Search failed for '{term}': {e}")
        return {"events": [], "error": str(e)}


def _run_searches(
    terms: List[str], limit: int, events: Dict[str, Dict[str, Any]], errors: List[str],
    deadline: Optional[float] = None,
) -> None:
    """Search each term, adding open events to ``events`` by id. An event
    found by an earlier term keeps that term's copy."""
    if not terms:
        return
    with ThreadPoolExecutor(max_workers=min(_SEARCH_WORKERS, len(terms))) as executor:
        futures = [
            http.submit_with_context(executor, _search_term, term, limit, deadline) for term in terms
        ]
        for future in futures:
            try:
                response = future.result()
            except Exception as e:
                errors.append(str(e))
                continue
            if response.get("error"):
                errors.append(str(response["error"]))
            for event in response.get("events") or []:
                event_id = str(event.get("id") or "")
                # events_status=active can still return a closed event.
                if event_id and not event.get("closed") and event_id not in events:
                    events[event_id] = event


def search_polymarket(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    hints: Any = None,
    deadline: Optional[float] = None,
) -> Dict[str, Any]:
    """Search Polymarket for the subject's events, biggest money first.

    One volume-sorted public-search per subject name (the topic, each side of
    a "vs" topic, each host alias). Then each related name the merged events
    not already an outcome of a race in the merged events is searched on its
    own.

    Args:
        topic: Search topic
        from_date: Start date (YYYY-MM-DD); Gamma searches open events, so unused
        to_date: End date (YYYY-MM-DD); unused for the same reason
        depth: 'quick', 'default', or 'deep'
        hints: Optional ``PolymarketHints`` (or a plan dict of the same shape)
        deadline: Optional ``time.monotonic()`` value every search, retries
            included, must finish by; the related-name searches are skipped
            once it has passed.

    Returns:
        Dict with 'events' list, plus 'error' when any search failed and
        'partial' when some searches failed but others returned events.
    """
    hints = PolymarketHints.coerce(hints)
    limit = SEARCH_LIMIT.get(depth, SEARCH_LIMIT["default"])
    cap = RESULT_CAP.get(depth, RESULT_CAP["default"])
    terms = _entity_terms(topic, hints.aliases)

    _log(f"Searching for '{topic}' with terms: {terms} (limit={limit})")

    events: Dict[str, Dict[str, Any]] = {}
    errors: List[str] = []
    _run_searches(terms, limit, events, errors, deadline)

    # A related name is searched unless the pool already holds a race it is
    # an outcome of; a game prop naming the player is no award race.
    race_labels = []
    for event in events.values():
        live = _live_markets(event)
        if _is_race(event, live):
            race_labels += [_prepare_match_text(market.get("groupItemTitle")) for market in live]
    related_terms = [
        entry.name for entry in hints.related[:HINT_CAP]
        if not _names_any([_Term(entry.name)], race_labels)
    ]
    if related_terms and deadline is not None and time.monotonic() >= deadline:
        errors.append(f"skipped related-name searches {related_terms}: deadline passed")
        related_terms = []
    if related_terms:
        _log(f"Searching related names: {related_terms}")
        _run_searches(related_terms, limit, events, errors, deadline)

    merged_events = list(events.values())
    _log(f"Found {len(merged_events)} unique events across {len(terms) + len(related_terms)} searches")

    result = {"events": merged_events, "_cap": cap}
    if errors:
        result["error"] = "; ".join(errors[:2])
        if merged_events:
            result["partial"] = True
    return result


def _format_price_movement(market: Dict[str, Any]) -> Optional[str]:
    """Pick the most significant price change and format it.

    Returns string like 'down 11.7% this month' or None if no significant change.
    """
    changes = [
        (abs(market.get("oneDayPriceChange") or 0), market.get("oneDayPriceChange"), "today"),
        (abs(market.get("oneWeekPriceChange") or 0), market.get("oneWeekPriceChange"), "this week"),
        (abs(market.get("oneMonthPriceChange") or 0), market.get("oneMonthPriceChange"), "this month"),
    ]

    # Pick the largest absolute change
    changes.sort(key=lambda x: x[0], reverse=True)
    abs_change, raw_change, period = changes[0]

    # Skip if change is less than 1% (noise)
    if abs_change < 0.01:
        return None

    direction = "up" if raw_change > 0 else "down"
    pct = abs_change * 100
    return f"{direction} {pct:.1f}% {period}"


def _price_movement_points(market: Dict[str, Any]) -> Optional[List[Any]]:
    """The same change ``_format_price_movement`` words, as ``[points, period]``.

    Gamma's ``oneDayPriceChange`` / ``oneWeekPriceChange`` /
    ``oneMonthPriceChange`` are differences between two prices on the 0-to-1
    scale, so times 100 they are percentage points, signed, not a relative
    change. None under one point.
    """
    changes = [
        (market.get("oneDayPriceChange") or 0, "today"),
        (market.get("oneWeekPriceChange") or 0, "this week"),
        (market.get("oneMonthPriceChange") or 0, "this month"),
    ]
    changes = [(c, p) for c, p in changes if isinstance(c, (int, float)) and not isinstance(c, bool)]
    if not changes:
        return None
    # Stable sort: the shorter period wins a tie, as in _format_price_movement.
    change, period = sorted(changes, key=lambda pair: -abs(pair[0]))[0]
    if abs(change) < 0.01:
        return None
    return [round(change * 100, 1), period]


# Polymarket displays a market's midpoint only while its book is tight: a bid
# and an ask no more than 10 cents apart. A wider or one-sided book shows the
# last trade instead, since the midpoint of an empty book (no bid, ask 0.87)
# is no one's price.
_MAX_MIDPOINT_SPREAD = 0.10
_BOOK_FIELDS = ("bestBid", "bestAsk", "spread")
# The most a mutually exclusive (negRisk) set's Yes prices may sum to and
# still be shown as one distribution.
_MAX_EXCLUSIVE_YES_TOTAL = 1.10


def _optional_float(val: Any) -> Optional[float]:
    """Parse a finite non-bool float, returning None when absent or malformed."""
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, str) and not val.strip():
        return None
    try:
        parsed = float(val)
    except (ValueError, TypeError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _quoted_price(market: Dict[str, Any], midpoint: float) -> Optional[float]:
    """The price Polymarket would display for a market's first (Yes) outcome.

    The midpoint (``outcomePrices``) when the book has a bid and an ask within
    ``_MAX_MIDPOINT_SPREAD``; otherwise the last trade; otherwise None (the
    market is unpriced). A market served without any book field (older
    fixtures, saved replays) and a closed market (its price is the
    resolution) keep the midpoint.
    """
    if market.get("closed") or all(market.get(field) is None for field in _BOOK_FIELDS):
        return midpoint
    bid = _optional_float(market.get("bestBid"))
    ask = _optional_float(market.get("bestAsk"))
    raw_spread = _optional_float(market.get("spread"))
    if bid is not None and ask is not None and bid > 0 and ask >= bid:
        spread = raw_spread if (raw_spread is not None and raw_spread >= 0) else ask - bid
        # Float noise: an ask of 0.13 over a bid of 0.03 is a 0.10 spread.
        if round(spread, 6) <= _MAX_MIDPOINT_SPREAD:
            return midpoint
    last = _optional_float(market.get("lastTradePrice"))
    if last is not None and 0 < last < 1:
        return last
    return None


def _parse_outcome_prices(market: Dict[str, Any], *, book_aware: bool = True) -> List[tuple]:
    """Parse outcomePrices JSON string into list of (outcome_name, price) tuples.

    ``book_aware`` prices the market the way Polymarket displays it (see
    ``_quoted_price``): an unpriced market parses to no outcomes, and a
    two-outcome market quoted from its last trade prices its second outcome
    as the complement.
    """
    outcomes = _json_list(market.get("outcomes"))
    prices = _json_list(market.get("outcomePrices"))

    if not prices:
        return []

    result = []
    has_first = False
    for i, price in enumerate(prices):
        if isinstance(price, bool):
            continue
        try:
            p = float(price)
        except (ValueError, TypeError, OverflowError):
            continue
        if not math.isfinite(p) or p < 0.0 or p > 1.0:
            continue
        if i == 0:
            has_first = True
        raw_name = outcomes[i] if i < len(outcomes) else None
        name = str(raw_name) if raw_name is not None else f"Outcome {i+1}"
        result.append((name, p))

    if book_aware and result:
        if not has_first:
            return []
        quoted = _quoted_price(market, result[0][1])
        if quoted is None:
            return []
        if quoted != result[0][1]:
            result[0] = (result[0][0], quoted)
            if len(prices) == 2 and len(result) == 2:
                result[1] = (result[1][0], round(1 - quoted, 4))

    return result


# Longest name _shorten_question keeps when a question has no shorter subject.
_MAX_QUESTION_NAME_CHARS = 80
_UNBROKEN_NAME_CHARS = 40


def _question_subject(q: str) -> Optional[str]:
    """The subject of a "Will X win/be/..." question, or None."""
    # Common patterns: "Will X win/be/...", "X wins/loses..."
    m = re.match(r"^Will\s+(.+?)\s+(?:win|be|make|reach|have|lose|qualify|advance|strike|agree|pass|sign|get|become|remain|stay|leave|survive|next)\b", q, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m = re.match(r"^Will\s+(.+?)\s+", q, re.IGNORECASE)
    if m and len(m.group(1).split()) <= 4:
        return m.group(1).strip()
    return None


def _shorten_question(question: str) -> str:
    """Extract a short display name from a market question.

    'Will Arizona win the 2026 NCAA Tournament?' -> 'Arizona'
    'Will Duke be a number 1 seed in the 2026 NCAA...' -> 'Duke'
    """
    q = question.strip().rstrip("?")
    subject = _question_subject(q)
    if subject is not None:
        return subject
    return _whole_question_name(q)


def _whole_question_name(q: str) -> str:
    """The question itself as a name. Sub-markets of one event often differ
    only in their last words ("... by March 31, 2027"), so it is kept whole
    and only a very long one is cut, at a word boundary. A leading article is
    dropped so the name doesn't read "an"/"the"."""
    q = q.strip().rstrip("?")
    text = q
    if len(q) > _MAX_QUESTION_NAME_CHARS:
        head = q[: _MAX_QUESTION_NAME_CHARS + 1]
        if " " in head:
            text = head.rsplit(" ", 1)[0].rstrip(" ,;:-")
        else:
            # One unbroken run has no word boundary to cut at.
            text = q[:_UNBROKEN_NAME_CHARS]
    return re.sub(r"^(?:a|an|the)\s+", "", text, flags=re.I)


_ARTICLES = frozenset({"a", "an", "the"})


def _sibling_outcome_labels(sibling_markets: List[Dict[str, Any]]) -> Dict[int, str]:
    """Compute unambiguous per-market labels across all child markets in an event.

    Running label disambiguation across the entire event before price filtering
    guarantees that a market keeps the same label during display parsing
    (``include_all_outcomes=False``) and verification (``include_all_outcomes=True``),
    even when a sub-floor sibling shares its ``groupItemTitle`` or another
    market's ``groupItemTitle`` collides with its question subject.
    """
    questions = [str(market["question"]) for market in sibling_markets]
    names = [_shorten_question(q) for q in questions]
    subjects = [_question_subject(q.strip().rstrip("?")) is not None for q in questions]
    repeats: Dict[str, int] = {}
    for name in names:
        repeats[name.casefold()] = repeats.get(name.casefold(), 0) + 1
    relabel = [
        is_subject and (repeats.get(name.casefold(), 0) > 1 or name.casefold() in _ARTICLES)
        for name, is_subject in zip(names, subjects)
    ]
    group_titles = [
        " ".join(str(market.get("groupItemTitle") or "").split()) for market in sibling_markets
    ]
    title_counts: Dict[str, int] = {}
    for title in group_titles:
        if title:
            title_counts[title.casefold()] = title_counts.get(title.casefold(), 0) + 1
    labels = [
        title
        if change or (is_subject and title and title_counts.get(title.casefold(), 0) == 1)
        else name
        for name, change, is_subject, title in zip(names, relabel, subjects, group_titles)
    ]
    taken: Dict[str, int] = {}
    for label in labels:
        if label:
            taken[label.casefold()] = taken.get(label.casefold(), 0) + 1
    resolved: Dict[int, str] = {}
    for market, label, change, question in zip(sibling_markets, labels, relabel, questions):
        if (
            not label
            or taken.get(label.casefold(), 0) > 1
            or (change and title_counts.get(label.casefold(), 0) > 1)
        ):
            resolved[id(market)] = _whole_question_name(question)
        else:
            resolved[id(market)] = label
    final_counts: Dict[str, int] = {}
    for label in resolved.values():
        if label:
            final_counts[label.casefold()] = final_counts.get(label.casefold(), 0) + 1
    for market, question in zip(sibling_markets, questions):
        label = resolved[id(market)]
        if final_counts.get(label.casefold(), 0) > 1:
            resolved[id(market)] = _whole_question_name(question)
    return resolved


def _own_outcome_labels(
    outcome_prices: List[tuple],
    outcome_questions: List[Optional[str]],
    outcome_markets: List[Dict[str, Any]],
    sibling_markets: Optional[List[Dict[str, Any]]] = None,
) -> List[tuple]:
    """Give each sub-market of an event a label of its own.

    ``_shorten_question`` names a sub-market by its question's subject, which
    tells teams apart ("Will Arizona win ...") but not the brackets, cities
    or prices of one subject: every "Will Waymo operate in N cities ..."
    market came out as "Waymo". A subject name is replaced by the label
    Polymarket gives that sub-market (``groupItemTitle``: "San Francisco
    49ers", "12-15", "Denver, CO") when that label is its own across the
    event's child markets. Any collision across all child markets—whether
    from repeated subjects, repeated ``groupItemTitle``s, or a subject
    colliding with another market's ``groupItemTitle``—falls back to
    ``_whole_question_name``.

    ``sibling_markets`` covers every child market of the event, priced or
    above-floor or not, so labels never shift when filtering or verification
    changes which markets are kept.

    Only subject names are replaced, and the order is kept, so every name an
    older build saved still reads its own market through
    ``_legacy_shorten_question``.
    """
    if not any(question is not None for question in outcome_questions):
        return list(outcome_prices)
    pool = (
        sibling_markets
        if sibling_markets is not None
        else [
            market
            for market, question in zip(outcome_markets, outcome_questions)
            if question is not None and market.get("question")
        ]
    )
    resolved = _sibling_outcome_labels(pool)
    return [
        (
            resolved[id(market)]
            if question is not None and id(market) in resolved
            else name,
            price,
        )
        for (name, price), question, market in zip(
            outcome_prices, outcome_questions, outcome_markets,
        )
    ]


_MONTH_NAMES = (
    "January|February|March|April|May|June|July|August|September|October|"
    "November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec"
)
# "by December 31, 2026", "before March 31": a deadline, not "on" a day.
_DEADLINE = re.compile(
    rf"\b(?:by|before)\s+(?:the\s+end\s+of\s+)?({_MONTH_NAMES})\.?\s+(\d{{1,2}})"
    r"(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?",
    re.IGNORECASE,
)
_MONTH_NUMBERS = {
    name: number
    for number, name in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1,
    )
}


def _deadline_date(question: str, market: Dict[str, Any]) -> Optional[str]:
    """The one deadline a question names, as an ISO date: the date it spells
    out when that has a year, and the market's end date otherwise. None when
    the question names no deadline, or more than one."""
    found = _DEADLINE.findall(question)
    if len(found) != 1:
        return None
    month, day, year = found[0]
    if year:
        try:
            return datetime.date(int(year), _MONTH_NUMBERS[month[:3].lower()], int(day)).isoformat()
        except ValueError:
            return None
    end = str(market.get("endDate") or "")[:10]
    return end if re.fullmatch(r"\d{4}-\d{2}-\d{2}", end) else None


def _deadline_label(question: str) -> Optional[List[Any]]:
    """The deadline a question names, as ``[month, day, year or None]``, the
    way the question spells it (a market's end date is often the day after)."""
    found = _DEADLINE.findall(question)
    if len(found) != 1:
        return None
    month, day, year = found[0]
    return [month[:3].title(), int(day), int(year) if year else None]


def _today() -> str:
    return datetime.datetime.now(datetime.timezone.utc).date().isoformat()


# A deadline rung priced under this says only that the date is too soon.
_LIVE_RUNG_PRICE = 0.05


def _ladder_ends(
    outcome_questions: List[Optional[str]],
    outcome_markets: List[Dict[str, Any]],
) -> Optional[List[str]]:
    """Each sub-market's deadline as an ISO date when the sub-markets are one
    question asked for several deadlines, else None."""
    if len(outcome_questions) < 2:
        return None
    stems = set()
    ends: List[str] = []
    for question, market in zip(outcome_questions, outcome_markets):
        if not question:
            return None
        end = _deadline_date(question, market)
        if end is None:
            return None
        stems.add(" ".join(_DEADLINE.sub("", question).casefold().split()))
        ends.append(end)
    return ends if len(stems) == 1 else None


# The most outcomes one odds line carries.
ODDS_LINE_OUTCOMES = 3


def _market_id(market: Dict[str, Any]) -> str:
    for key in ("id", "conditionId", "slug"):
        value = str(market.get(key) or "").strip()
        if value:
            return value
    question = " ".join(str(market.get("question") or "").split())
    group = " ".join(str(market.get("groupItemTitle") or "").split())
    if question and group:
        return f"{question}\x1f{group}"
    return question or group


def _parse_clob_token_ids(market: Dict[str, Any]) -> List[str]:
    raw = market.get("clobTokenIds")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
    if not isinstance(raw, list):
        return []
    return [str(tok).strip() for tok in raw if tok is not None and str(tok).strip()]


def _outcome_id(market: Dict[str, Any], outcome_name: Optional[str]) -> str:
    """Stable identifier for one outcome within ``market``.

    Uses the matching ``clobTokenIds`` entry when aligned with the market's
    ``outcomes`` list; otherwise falls back to the normalized outcome name.
    """
    raw_outcomes = market.get("outcomes")
    if isinstance(raw_outcomes, str):
        try:
            raw_outcomes = json.loads(raw_outcomes)
        except (json.JSONDecodeError, TypeError):
            raw_outcomes = []
    outcomes = [str(name) for name in raw_outcomes] if isinstance(raw_outcomes, list) else []
    target = outcome_name if outcome_name is not None else (outcomes[0] if outcomes else "")
    tokens = _parse_clob_token_ids(market)
    if target and outcomes and len(tokens) == len(outcomes):
        target_cf = target.casefold()
        for idx, name in enumerate(outcomes):
            if name.casefold() == target_cf:
                return tokens[idx]
    return " ".join(str(target or "").split())


def _odds_summary(
    title: str,
    top_market: Dict[str, Any],
    outcome_prices: List[tuple],
    outcome_questions: List[Optional[str]],
    outcome_markets: List[Dict[str, Any]],
    today: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """What a one-line odds summary of the event is built from.

    ``kind`` is ``ladder`` (one question, several deadlines: the nearest live
    deadlines in date order, each with its date in ``by``), ``outcomes``
    (brackets, places, candidates: the leading outcomes under their own
    labels) or ``single`` (one Yes/No market: its Yes price). Each part's
    ``move`` is that outcome's own change in points, where the outcome is a
    market of its own. Never a volume.
    """
    if not outcome_prices:
        return None

    def _part_ids(market: Dict[str, Any], outcome_name: Optional[str]) -> Dict[str, Any]:
        extra_ids: Dict[str, Any] = {}
        mid = _market_id(market)
        if mid:
            extra_ids["market_id"] = mid
        oid = _outcome_id(market, outcome_name)
        if oid:
            extra_ids["outcome_id"] = oid
        return extra_ids

    def part(index: int, **extra: Any) -> Dict[str, Any]:
        market = outcome_markets[index]
        priced_alone = outcome_questions[index] is not None
        outcome_name = "Yes" if priced_alone else str(outcome_prices[index][0])
        return {
            "price": outcome_prices[index][1],
            "move": _price_movement_points(market) if priced_alone else None,
            **_part_ids(market, outcome_name),
            **extra,
        }

    if outcome_questions[0] is not None:
        prices = [price for _name, price in outcome_prices]
        ends = _ladder_ends(outcome_questions, outcome_markets)
        start = _nearest_deadline(outcome_questions, outcome_markets, prices, today)
        if ends is not None and start is not None:
            rungs = sorted(
                (index for index in range(len(ends)) if ends[index] >= ends[start]),
                key=lambda index: ends[index],
            )[:ODDS_LINE_OUTCOMES]
            return {
                "kind": "ladder",
                "subject": " ".join(_DEADLINE.sub("", outcome_questions[start]).split()),
                "parts": [
                    part(index, by=_deadline_label(outcome_questions[index])) for index in rungs
                ],
            }
        indexes = range(min(len(outcome_prices), ODDS_LINE_OUTCOMES))
        return {
            "kind": "outcomes",
            "subject": title,
            "parts": [part(index, label=str(outcome_prices[index][0])) for index in indexes],
        }
    question = str(top_market.get("question") or title)
    by_name = {str(name).casefold(): price for name, price in outcome_prices}
    if set(by_name) == {"yes", "no"}:
        yes_label = next(
            (str(name) for name, _price in outcome_prices if str(name).casefold() == "yes"),
            "Yes",
        )
        return {
            "kind": "single",
            "subject": question,
            "parts": [
                {
                    "price": by_name["yes"],
                    "move": _price_movement_points(top_market),
                    **_part_ids(top_market, yes_label),
                }
            ],
        }
    return {
        "kind": "outcomes",
        "subject": question,
        "parts": [
            {
                "label": str(name),
                "price": price,
                "move": None,
                **_part_ids(top_market, str(name)),
            }
            for name, price in outcome_prices[:ODDS_LINE_OUTCOMES]
        ],
    }


def _nearest_deadline(
    outcome_questions: List[Optional[str]],
    outcome_markets: List[Dict[str, Any]],
    prices: List[float],
    today: Optional[str] = None,
) -> Optional[int]:
    """Index of the nearest deadline that has not passed, for a deadline ladder.

    A rung the market gives under 5% is passed over for the next one, so an
    "IPO by" ladder shows the first date traders think is live, not a date
    days away at 0.8%. When every open rung is that low the nearest is kept.

    A ladder is one question asked for several deadlines ("... by December 31,
    2026?", "... by March 31, 2027?"). Its later deadlines always price
    higher, so the highest price is only the furthest date. None when the
    sub-markets are not one question with different deadlines.
    """
    ends = _ladder_ends(outcome_questions, outcome_markets)
    if ends is None:
        return None
    today = today or _today()
    open_ends = [index for index, end in enumerate(ends) if end >= today]
    live = [index for index in open_ends if prices[index] >= _LIVE_RUNG_PRICE]
    return min(live or open_ends or range(len(ends)), key=lambda index: ends[index])


def _headline_market(
    title: str,
    top_market: Dict[str, Any],
    outcome_prices: List[tuple],
    outcome_questions: List[Optional[str]],
    outcome_markets: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """The one market and price that stand for an event in a one-line summary.

    The question and the price always come from the same market. For an event
    of Yes/No sub-markets that is the nearest open deadline of a deadline
    ladder, and otherwise the first outcome (the topic's own, or the most
    likely). For a single market it is its Yes price, or its leading outcome
    with that outcome's ``label``.
    """
    if not outcome_prices:
        return None
    if outcome_questions[0] is not None:
        index = _nearest_deadline(
            outcome_questions, outcome_markets, [price for _name, price in outcome_prices],
        ) or 0
        return {"question": outcome_questions[index], "price": outcome_prices[index][1]}
    question = str(top_market.get("question") or title)
    by_name = {str(name).casefold(): price for name, price in outcome_prices}
    if set(by_name) == {"yes", "no"}:
        return {"question": question, "price": by_name["yes"]}
    name, price = outcome_prices[0]
    return {"question": question, "label": str(name), "price": price}


def names_whole_topic(topic: str, *texts: str) -> bool:
    """Whether a market's text names the whole topic, not one word of it.

    The event filter keeps a market that shares one informative word with a
    two-word topic, so "Jensen Huang" keeps a table tennis match played by
    another Huang. Every informative word of the topic (of either side of a
    "vs" topic) must appear as a word of its own. A topic with no informative
    word is not checked.
    """
    def words(text: str) -> List[str]:
        text = re.sub(r"['\u2019]s\b", "", text.casefold())
        return [word for word in re.sub(r"[^\w\s]", " ", text).split() if len(word) > 1]

    found = {word for text in texts for word in words(text or "")}
    sides = _topic_sides(topic)
    checked = False
    for side in sides:
        side_words = words(side)
        informative = _informative_words(side_words) or side_words
        if not informative:
            continue
        checked = True
        if all(word in found for word in informative):
            return True
    return not checked


_SERIES_DROP_WORDS = frozenset(
    {name.casefold() for name in _MONTH_NAMES.split("|")}
    | {"will", "the", "a", "an", "in", "on", "by", "from", "to", "of", "between",
       "and", "be", "before", "after", "through", "until", "week", "month"}
)
# A recurring count market: "Elon Musk # tweets November 6 - November 13,
# 2026?", "How many times will X say ...", "... post 260-279 tweets ...",
# "What will X say during ...", "X mentions ...".
_COUNT_SERIES = re.compile(
    r"#\s*(?:of\s+)?(?:tweets|posts|mentions|truths)\b"
    r"|\b(?:how many|number of)\s+(?:times|tweets|posts|mentions)\b"
    r"|\b(?:post|tweet|say|mention)s?\s+[<>]?\d[\d,]*\s*(?:[-–+]\s*\d*[\d,]*)?\+?\s*(?:tweets|posts|times)\b"
    r"|\bmentions?\s+market\b"
    r"|^what will .+ say\b",
    re.IGNORECASE,
)


def series_key(*texts: str) -> str:
    """A market's question stem with its numbers, dates and brackets removed.

    Weekly reruns of one question ("Elon Musk # tweets November 6 - November
    13, 2026?", "Elon Musk # tweets in November 2026?") share a key, so a
    caller can name the series once. The first non-empty text is used.
    """
    text = next((t for t in texts if t and t.strip()), "")
    text = re.sub(r"['’]s\b", "", text.casefold())
    text = re.sub(r"[<>$]?\d[\d,.]*\s*[kmbt%+]?", " ", text)
    words = [w for w in re.sub(r"[^\w\s]", " ", text).split() if w not in _SERIES_DROP_WORDS]
    return " ".join(words)


def is_count_series(*texts: str) -> bool:
    """Whether a market is a recurring count bracket (tweets, posts, mentions)."""
    return any(_COUNT_SERIES.search(text or "") for text in texts)


def _legacy_shorten_question(question: str) -> str:
    """The name older builds gave a market question; saved reports hold it.

    They cut a question with no shorter subject at 40 characters and only
    then dropped a leading article.
    """
    q = question.strip().rstrip("?")
    subject = _question_subject(q)
    if subject is not None:
        return subject
    return re.sub(r"^(?:a|an|the)\s+", "", q[:40], flags=re.I)


def _safe_float(val, default=0.0) -> float:
    """Safely convert a value to float."""
    if isinstance(val, bool):
        return default
    try:
        parsed = float(val or default)
    except (ValueError, TypeError, OverflowError):
        return default
    return parsed if math.isfinite(parsed) else default


def _market_volume(market: Dict[str, Any]) -> float:
    """Money traded on one market over its life."""
    return _safe_float(market.get("volumeNum")) or _safe_float(market.get("volume"))


def _yes_price(market: Dict[str, Any]) -> Optional[float]:
    """The displayed price of a market's first outcome, None when unpriced."""
    pairs = _parse_outcome_prices(market)
    return pairs[0][1] if pairs else None


def _live_markets(event: Dict[str, Any], include_closed: bool = False) -> List[Dict[str, Any]]:
    """An event's open, active markets with liquidity (resolved markets have
    0 or None). ``include_closed`` keeps every market."""
    live = []
    for market in event.get("markets") or []:
        if not include_closed and (market.get("closed") or not market.get("active", True)):
            continue
        if _safe_float(market.get("liquidity")) > 0 or include_closed:
            live.append(market)
    return live


def _days_to_close(market: Dict[str, Any], event: Dict[str, Any], today: str) -> Optional[int]:
    end = str(market.get("endDate") or event.get("endDate") or "")[:10]
    try:
        return (datetime.date.fromisoformat(end) - datetime.date.fromisoformat(today)).days
    except ValueError:
        return None


@dataclass
class _EntityMarket:
    """One market of an event that names the subject (or a related name)."""

    market: Dict[str, Any]
    match: EntityMatch
    outcome: Optional[str]
    price: Optional[float]
    volume: float
    days: Optional[int]
    rank: Optional[int] = None
    race_size: Optional[int] = None
    race_outcome: bool = False
    in_band: bool = False
    contender: bool = False
    priced_outcome: Optional[str] = None

    @property
    def label(self) -> str:
        title = " ".join(str(self.market.get("groupItemTitle") or "").split())
        return title or self.outcome or _shorten_question(str(self.market.get("question") or ""))


@dataclass
class _EventEntities:
    """The subject's markets in one event, before the selection rule."""

    race: bool
    live_count: int
    named_by_market: bool
    markets: List[_EntityMarket] = field(default_factory=list)


def _is_exclusive(event: Dict[str, Any], markets: List[Dict[str, Any]]) -> bool:
    """Whether an event's markets are mutually exclusive (``negRisk``): only
    one of them can resolve Yes."""
    return bool(event.get("negRisk")) or any(m.get("negRisk") for m in markets)


def _is_race(event: Dict[str, Any], live_markets: List[Dict[str, Any]]) -> bool:
    """Whether an event's live markets are a race: mutually exclusive
    (``negRisk``), RACE_MIN_OUTCOMES or more of them, labelled by
    ``groupItemTitle``."""
    exclusive = _is_exclusive(event, live_markets)
    labelled = sum(1 for m in live_markets if str(m.get("groupItemTitle") or "").strip())
    return exclusive and len(live_markets) >= RACE_MIN_OUTCOMES and labelled >= 2


def _event_entities(
    event: Dict[str, Any],
    active_markets: List[Dict[str, Any]],
    matcher: EntityMatcher,
    race: bool,
    today: str,
) -> _EventEntities:
    """Match each live market of an event and price its entity markets.

    Recurring count brackets ("# tweets") are dropped. A market with no
    displayable price is kept but can never be live. In a race (a mutually
    exclusive event of RACE_MIN_OUTCOMES or more live outcomes, labelled by
    ``groupItemTitle``) a market whose outcome is the subject or a related
    name is a race outcome, and when its label names it, it is ranked by
    price among the live outcomes.
    """
    title = str(event.get("title") or "")
    title_text, title_acronyms = _prepare_match_text(title)
    yes_prices = [_yes_price(market) for market in active_markets]
    found = _EventEntities(race=race, live_count=len(active_markets), named_by_market=False)
    for market in active_markets:
        match = matcher.match(
            market, title, title_text=title_text, title_acronyms=title_acronyms,
        )
        if match is None:
            continue
        if match.field == "market" and not match.related:
            found.named_by_market = True
        if is_count_series(title, str(market.get("question") or "")):
            continue
        outcome = matcher.matched_outcome(market, match)
        pairs = _parse_outcome_prices(market)
        if outcome is not None:
            price = next((p for name, p in pairs if str(name) == outcome), None)
            priced_outcome = outcome
        else:
            price = pairs[0][1] if pairs else None
            priced_outcome = str(pairs[0][0]) if pairs else None
        entity = _EntityMarket(
            market=market, match=match, outcome=outcome, price=price,
            volume=_market_volume(market), days=_days_to_close(market, event, today),
            priced_outcome=priced_outcome,
        )
        kind = (
            matcher.outcome_kind(
                market, match, title, title_text=title_text, title_acronyms=title_acronyms,
            )
            if race else None
        )
        entity.race_outcome = kind is not None
        if kind == "label" and price is not None:
            entity.rank = 1 + sum(1 for other in yes_prices if other is not None and other > price)
            entity.race_size = len(active_markets)
        found.markets.append(entity)
    return found


def _teams_biggest_race(analyses: Dict[int, _EventEntities]) -> Optional[int]:
    """The race where the team's own outcome has the most money on it, among
    open outcomes the selection rule would otherwise floor."""
    best: Optional[Tuple[float, int]] = None
    for key, found in analyses.items():
        for entity in found.markets:
            if entity.match.related or not entity.race_outcome:
                continue
            if entity.price is None or not 0 < entity.price < PRICE_CEILING:
                continue
            if entity.days is not None and entity.days < MIN_DAYS_TO_CLOSE:
                continue
            if best is None or entity.volume > best[0]:
                best = (entity.volume, key)
    return best[1] if best else None


def _apply_band(found: _EventEntities, exempt: bool) -> None:
    for entity in found.markets:
        floor = (
            0.0 if exempt and not entity.match.related
            else RACE_PRICE_FLOOR if entity.race_outcome else PRICE_FLOOR
        )
        entity.in_band = (
            entity.price is not None
            and entity.price < PRICE_CEILING
            and (entity.price > 0 if floor == 0 else entity.price >= floor)
            and (entity.days is None or entity.days >= MIN_DAYS_TO_CLOSE)
        )
        entity.contender = (
            entity.rank is not None
            and entity.rank <= CONTENDER_TOP
            and entity.volume >= CONTENDER_MIN_VOLUME
        )


def _chosen_markets(found: _EventEntities) -> List[_EntityMarket]:
    """The entity markets an event contributes.

    A related name enters only as a live top contender. An event of more than
    GAME_SHEET_MARKETS markets (a game's prop sheet, a league-wide race)
    contributes one market: its busiest live market that names the subject on
    its own text, or when no market does (the subject is only in the title),
    its busiest live market.
    """
    entity = [m for m in found.markets if not m.match.related]
    related = [m for m in found.markets if m.match.related and m.in_band and m.contender]
    if found.live_count <= GAME_SHEET_MARKETS:
        return entity + related
    pool = [m for m in entity if m.match.field == "market"] if found.named_by_market else entity
    for group in ([m for m in pool if m.in_band], related, pool):
        if group:
            return [max(group, key=lambda m: m.volume)]
    return []


def _headline_entity(group: List[_EntityMarket], today: str) -> _EntityMarket:
    """The market that stands for the event: the nearest live deadline of a
    date ladder, the rung closest to even of a value ladder ("8.5+ wins",
    "9.5+ wins"), else the likeliest outcome."""
    priced = [m for m in group if m.price is not None]
    if not priced:
        return max(group, key=lambda m: m.volume)
    if len(priced) > 1:
        questions: List[Optional[str]] = [str(m.market.get("question") or "") for m in priced]
        prices = [m.price for m in priced if m.price is not None]
        index = _nearest_deadline(questions, [m.market for m in priced], prices, today)
        if index is not None:
            return priced[index]
        if len({series_key(question or "") for question in questions}) == 1:
            return min(priced, key=lambda m: abs((m.price or 0) - 0.5))
    return max(priced, key=lambda m: (m.price or 0, m.volume))


def parse_polymarket_response(
    response: Dict[str, Any],
    topic: str = "",
    *,
    hints: Any = None,
    to_date: Optional[str] = None,
    include_all_outcomes: bool = False,
    include_closed: bool = False,
) -> List[Dict[str, Any]]:
    """Parse Gamma API response into normalized item dicts.

    Each event becomes one item showing its title and top markets.

    With a topic, an event is kept only when one of its markets names the
    subject (``EntityMatcher``), and the item headlines the subject's own
    market. Items are ranked by the selection rule (see PRICE_CEILING):
    events with a live entity market first, by ``selection_score``, then the
    rest by money on the subject. Without a topic (re-checking a saved claim)
    every market is kept and nothing is gated.

    Args:
        response: Raw Gamma API response
        topic: Original search topic
        hints: Optional ``PolymarketHints`` (or a plan dict of the same shape)
        to_date: The run's end date, the "now" for days to close and live
            deadlines; today when absent

    Returns:
        List of item dicts ready for normalization.
    """
    events = response.get("events", [])
    today = to_date or _today()
    matcher = EntityMatcher(topic, hints) if topic else None

    # Pass one: each event's live markets and, with a topic, its entity markets.
    prepared = []
    analyses: Dict[int, _EventEntities] = {}
    filtered_count = 0
    for event in events:
        # Filter: skip closed/resolved events
        if not include_closed:
            if event.get("closed", False):
                continue
            if not event.get("active", True):
                continue

        active_markets = _live_markets(event, include_closed)
        if not active_markets:
            continue

        mutually_exclusive = _is_exclusive(event, active_markets)
        if matcher is not None:
            found = _event_entities(
                event, active_markets, matcher,
                race=_is_race(event, active_markets),
                today=today,
            )
            if not found.markets:
                filtered_count += 1
                continue
            analyses[len(prepared)] = found
        prepared.append((event, active_markets, mutually_exclusive))

    exempt = (
        _teams_biggest_race(analyses)
        if matcher is not None and matcher.hints.kind == "team" else None
    )

    items = []
    for index, (event, active_markets, mutually_exclusive) in enumerate(prepared):
        event_id = event.get("id", "")
        title = event.get("title", "")
        slug = event.get("slug", "")

        chosen: List[_EntityMarket] = []
        entity: Optional[_EntityMarket] = None
        tier = 0
        score = 0.0
        found = analyses.get(index)
        if found is not None:
            _apply_band(found, exempt=index == exempt)
            chosen = _chosen_markets(found)
            if not chosen:
                filtered_count += 1
                continue
            live = [m for m in chosen if m.in_band]
            if live:
                tier = 1
                score = math.log1p(sum(m.volume for m in live)) + (
                    CONTENDER_BONUS if any(m.contender for m in live) else 0.0
                )
            else:
                score = math.log1p(sum(m.volume for m in chosen))
            entity = _headline_entity(live or chosen, today)
        chosen_ids = {id(m.market) for m in chosen}

        # Sort markets by volume (most liquid first)
        def market_volume(m):
            try:
                return float(m.get("volume", 0) or 0)
            except (ValueError, TypeError):
                return 0
        active_markets.sort(key=market_volume, reverse=True)

        # Take top market for the event
        top_market = active_markets[0]
        # The market whose outcomes and question the odds show: the subject's
        # own when matched, so a busier sibling with its own outcome labels
        # ("O 11.5" for another team) never speaks for it.
        shown_market = entity.market if entity is not None else top_market

        # Parse outcome prices - for multi-market events with Yes/No binary
        # sub-markets, synthesize from market questions to show actual
        # team/entity probabilities instead of a single market's Yes/No
        outcome_prices = _parse_outcome_prices(shown_market)
        # The market question each outcome was named from, None for a label.
        outcome_questions: List[Optional[str]] = [None] * len(outcome_prices)
        # The market each outcome is priced in.
        outcome_markets: List[Dict[str, Any]] = [shown_market] * len(outcome_prices)
        # Whether the sub-markets are Yes/No is read from the raw outcome
        # labels, so a malformed or unpriced top market still leads to its
        # priced siblings.
        raw_top_outcomes = [
            str(name).strip().lower()
            for name in _json_list(shown_market.get("outcomes"))
            if name is not None and str(name).strip()
        ]
        top_outcomes_are_binary = (
            len(raw_top_outcomes) == 2 and set(raw_top_outcomes) == {"yes", "no"}
        )
        impossible_set = False
        sibling_markets: Optional[List[Dict[str, Any]]] = None
        if top_outcomes_are_binary and len(active_markets) > 1:
            sibling_markets = [m for m in active_markets if m.get("question")]
            synth_outcomes = []
            for m in sibling_markets:
                q = m["question"]
                pairs = _parse_outcome_prices(m)
                yes_price = next((p for name, p in pairs if name.strip().lower() == "yes"), None)
                if yes_price is not None and (
                    include_all_outcomes
                    or yes_price >= RACE_PRICE_FLOOR
                    or id(m) in chosen_ids
                ):
                    synth_outcomes.append((q, yes_price, m))
            if synth_outcomes:
                synth_outcomes.sort(key=lambda x: x[1], reverse=True)
                outcome_prices = [(_shorten_question(q), p) for q, p, _m in synth_outcomes]
                outcome_questions = [q for q, _p, _m in synth_outcomes]
                outcome_markets = [m for _q, _p, m in synth_outcomes]
                # Only one bracket of a negRisk set can resolve Yes, so its
                # Yes prices sum to about 1. A set still well over 1 after
                # the book check is no distribution to show.
                yes_total = sum(p for _q, p, _m in synth_outcomes)
                if mutually_exclusive and yes_total > _MAX_EXCLUSIVE_YES_TOTAL:
                    impossible_set = True
                    _log(
                        f"Suppressed odds for '{title[:60]}': mutually exclusive "
                        f"Yes prices sum to {yes_total:.2f}"
                    )

        # The subject's own markets lead the outcome list, in price order.
        if chosen and outcome_prices:
            rows = list(zip(outcome_prices, outcome_questions, outcome_markets))
            if outcome_questions[0] is not None:
                own = [row for row in rows if id(row[2]) in chosen_ids]
            else:
                own = [row for row in rows if entity is not None and row[0][0] == entity.outcome]
            if own:
                rows = own + [row for row in rows if row not in own]
                outcome_prices = [priced for priced, _q, _m in rows]
                outcome_questions = [q for _priced, q, _m in rows]
                outcome_markets = [m for _priced, _q, m in rows]

        # The market the item speaks for: the subject's own with a topic.
        lead_market = entity.market if entity is not None else top_market

        # Format price movement
        price_movement = _format_price_movement(lead_market)

        # Volume and liquidity - prefer event-level (more stable), fall back to market-level
        event_volume1mo = _safe_float(event.get("volume1mo"))
        event_volume1wk = _safe_float(event.get("volume1wk"))
        event_liquidity = _safe_float(event.get("liquidity"))
        event_competitive = _safe_float(event.get("competitive"))
        volume24hr = _safe_float(event.get("volume24hr")) or _safe_float(top_market.get("volume24hr"))
        liquidity = event_liquidity or _safe_float(top_market.get("liquidity"))

        # Event URL
        url = f"https://polymarket.com/event/{slug}" if slug else f"https://polymarket.com/event/{event_id}"

        # Date: use updatedAt from event
        updated_at = event.get("updatedAt", "")
        date_str = None
        if updated_at:
            try:
                date_str = updated_at[:10]  # YYYY-MM-DD
            except (IndexError, TypeError):
                pass

        # End date for the market
        end_date = lead_market.get("endDate")
        if end_date:
            try:
                end_date = end_date[:10]
            except (IndexError, TypeError):
                end_date = None

        # Volume signal: log-scaled monthly volume (most stable signal)
        vol_raw = event_volume1mo or event_volume1wk or volume24hr
        vol_score = min(1.0, math.log1p(vol_raw) / 16)  # ~$9M = 1.0

        # Liquidity signal
        liq_score = min(1.0, math.log1p(liquidity) / 14)  # ~$1.2M = 1.0

        # Price movement: daily weighted more than monthly
        day_change = abs(lead_market.get("oneDayPriceChange") or 0) * 3
        week_change = abs(lead_market.get("oneWeekPriceChange") or 0) * 2
        month_change = abs(lead_market.get("oneMonthPriceChange") or 0)
        max_change = max(day_change, week_change, month_change)
        movement_score = min(1.0, max_change * 5)  # 20% change = 1.0

        market_quality = (
            0.50 * vol_score +
            0.25 * liq_score +
            0.15 * movement_score +
            0.10 * event_competitive
        )
        # How firmly the event is about the subject: the subject named, then a
        # related name leading a race; an event with no live market counts less.
        if entity is None:
            match_strength = 0.5
        else:
            match_strength = 1.0 if any(not m.match.related for m in chosen) else 0.8
            if not tier:
                match_strength *= 0.6
        relevance = min(1.0, match_strength * (0.75 + 0.25 * market_quality))

        # After the ordering above, which reads the subject names.
        outcome_prices = _own_outcome_labels(
            outcome_prices,
            outcome_questions,
            outcome_markets,
            sibling_markets,
        )
        if entity is not None and entity.price is None:
            headline = None
        elif entity is not None:
            headline = {
                "question": str(entity.market.get("question") or title),
                "price": entity.price,
            }
            if entity.outcome is not None:
                headline["label"] = entity.outcome
        else:
            headline = _headline_market(
                title, top_market, outcome_prices, outcome_questions, outcome_markets,
            )

        # Normal display payloads stay compact. Verification requests the
        # complete snapshot so topic-promoted outcomes remain re-checkable.
        top_outcomes = outcome_prices if include_all_outcomes else outcome_prices[:3]
        remaining = len(outcome_prices) - 3
        if remaining < 0:
            remaining = 0
        odds = _odds_summary(
            title, shown_market, outcome_prices, outcome_questions, outcome_markets, today,
        )
        if impossible_set:
            # The event stays as evidence, with no odds line or listed
            # outcomes. Verification still sees each market's quote. The
            # subject's own outcome of the race keeps its headline: its price
            # is that market's own quote, not read off the bogus set.
            odds = None
            if entity is None or not entity.race_outcome:
                headline = None
            remaining = 0
            if not include_all_outcomes:
                top_outcomes = []

        outcome_market_ids = (
            [_market_id(m) for m in outcome_markets[: len(top_outcomes)]]
            if top_outcomes and outcome_questions and outcome_questions[0] is not None
            else []
        )
        item = {
            "event_id": event_id,
            "title": title,
            "question": lead_market.get("question", title),
            "url": url,
            "outcome_prices": top_outcomes,
            "outcome_questions": outcome_questions[: len(top_outcomes)],
            **({"outcome_market_ids": outcome_market_ids} if outcome_market_ids else {}),
            "outcomes_remaining": remaining,
            "headline": headline,
            "odds": odds,
            "price_movement": price_movement,
            "volume24hr": volume24hr,
            "volume1mo": event_volume1mo,
            "liquidity": liquidity,
            "date": date_str,
            "end_date": end_date,
            "relevance": round(relevance, 2),
            "why_relevant": f"Prediction market: {title[:60]}",
        }
        if entity is not None:
            item["selection_score"] = score
            item["selection_tier"] = tier
            matched_payload = {
                "label": entity.label,
                "question": str(entity.market.get("question") or ""),
                "price": entity.price,
                "volume": entity.volume,
                "end_date": end_date,
                "rank": entity.rank,
                "race_size": entity.race_size,
                "contender": entity.contender,
                "in_band": entity.in_band,
                "match_field": entity.match.field,
                "related_name": entity.match.related,
                "relation": entity.match.relation or None,
            }
            matched_mid = _market_id(entity.market)
            if matched_mid:
                matched_payload["market_id"] = matched_mid
            matched_oid = _outcome_id(entity.market, entity.priced_outcome or entity.outcome)
            if matched_oid:
                matched_payload["outcome_id"] = matched_oid
            item["matched_market"] = matched_payload
        items.append(item)

    if filtered_count:
        _log(f"Filtered {filtered_count} events that do not name '{topic}'")

    if matcher is not None:
        # The selection rule decides the order, and the cap comes after it.
        items.sort(key=lambda x: (x["selection_tier"], x["selection_score"]), reverse=True)
    else:
        items.sort(key=lambda x: x["relevance"], reverse=True)

    cap = response.get("_cap", len(items))
    return items[:cap]


def _normalize_snapshot_key(key: str) -> str:
    if "\x1f" in key:
        name, occurrence = key.rsplit("\x1f", 1)
        return f"{name.strip().casefold()}\x1f{occurrence.strip()}"
    return key.strip().casefold()


def _has_base_in_map(normalized_values: Dict[str, Any], base: str) -> bool:
    if base in normalized_values:
        return True
    prefix = f"{base}\x1f"
    return any(key.startswith(prefix) for key in normalized_values)


def _resolve_value_in_map(
    values: Dict[str, Any],
    datum_key: str,
    *,
    blocked_repeats: frozenset[str] = frozenset(),
    allow_occurrence_zero: bool = True,
) -> Any:
    if not values:
        return None
    if datum_key == "end_date" or not allow_occurrence_zero:
        if datum_key in values:
            return values[datum_key]
        target = datum_key.strip().casefold()
        for raw_key, raw_value in values.items():
            if str(raw_key).strip().casefold() == target:
                return raw_value
        return None

    has_suffix = "\x1f" in datum_key
    if has_suffix:
        outcome_name, raw_occurrence = datum_key.rsplit("\x1f", 1)
        raw_occ_stripped = raw_occurrence.strip()
        if not (raw_occ_stripped.isascii() and raw_occ_stripped.isdigit()):
            return None
        try:
            occurrence = int(raw_occ_stripped)
        except ValueError:
            return None
        base = outcome_name.strip().casefold()
        if base in blocked_repeats:
            return None
        target = f"{base}\x1f{occurrence}"
    else:
        base = datum_key.strip().casefold()
        occurrence = 0
        target = base

    if datum_key in values:
        return values[datum_key]

    normalized_values: Dict[str, Any] = {}
    for raw_key, raw_value in values.items():
        normalized_values.setdefault(_normalize_snapshot_key(str(raw_key)), raw_value)

    if target in normalized_values:
        return normalized_values[target]
    if not has_suffix and base not in blocked_repeats:
        # Resolve an unsuffixed name against \x1f0 only when no sibling
        # occurrence exists; when multiple occurrences share the label, an
        # unsuffixed name cannot identify which market it came from.
        prefix = f"{base}\x1f"
        occ_keys = [key for key in normalized_values if key.startswith(prefix)]
        if occ_keys == [f"{base}\x1f0"]:
            return normalized_values[occ_keys[0]]
    elif has_suffix and occurrence == 0 and base in normalized_values:
        return normalized_values[base]
    return None


def resolve_snapshot_value(
    values: Dict[str, Any],
    datum_key: str,
    *,
    legacy_values: Optional[Dict[str, Any]] = None,
    blocked_repeats: frozenset[str] = frozenset(),
    legacy_blocked_repeats: Optional[frozenset[str]] = None,
    allow_occurrence_zero: bool = True,
) -> Any:
    """Resolve ``datum_key`` against current ``values`` first, then ``legacy_values``.

    Shared by both ``refetch_datum`` (direct fetch) and ``freshness._lookup_cached_datum``
    (cached hit) so direct and cached verification always produce identical results
    and legacy cut names never shadow or leak into a current outcome name.
    """
    value = _resolve_value_in_map(
        values,
        datum_key,
        blocked_repeats=blocked_repeats,
        allow_occurrence_zero=allow_occurrence_zero,
    )
    if value is not None:
        return value
    if legacy_values and datum_key != "end_date" and allow_occurrence_zero:
        base = (
            datum_key.rsplit("\x1f", 1)[0].strip().casefold()
            if "\x1f" in datum_key
            else datum_key.strip().casefold()
        )
        normalized_current = {
            _normalize_snapshot_key(str(k)): v for k, v in values.items()
        }
        if _has_base_in_map(normalized_current, base):
            return None
        return _resolve_value_in_map(
            legacy_values,
            datum_key,
            blocked_repeats=(
                blocked_repeats
                if legacy_blocked_repeats is None
                else legacy_blocked_repeats
            ),
            allow_occurrence_zero=allow_occurrence_zero,
        )
    return None


def _record_outcome_values(
    values: Dict[str, Any],
    pairs: List[tuple],
    *,
    overwrite: bool,
) -> None:
    totals: Dict[str, int] = {}
    for name, _price in pairs:
        normalized = str(name).strip().casefold()
        totals[normalized] = totals.get(normalized, 0) + 1
    existing_norm = {_normalize_snapshot_key(k) for k in values}
    counts: Dict[str, int] = {}
    for name, price in pairs:
        clean_name = str(name).strip()
        normalized = clean_name.casefold()
        occurrence = counts.get(normalized, 0)
        counts[normalized] = occurrence + 1
        key = f"{clean_name}\x1f{occurrence}" if totals[normalized] > 1 else clean_name
        norm_key = _normalize_snapshot_key(key)
        if overwrite:
            values[key] = price
            existing_norm.add(norm_key)
        elif norm_key not in existing_norm:
            values[key] = price
            existing_norm.add(norm_key)


def _saved_outcome_market_id(item: Any, datum_key: str) -> Optional[str]:
    """Return the saved sub-market ID aligned with ``datum_key`` on ``item``, if present."""
    if datum_key == "end_date":
        return None
    metadata = getattr(item, "metadata", None)
    if not isinstance(metadata, dict):
        return None
    outcome_pairs = metadata.get("outcome_prices")
    outcome_market_ids = metadata.get("outcome_market_ids")
    if (
        not isinstance(outcome_pairs, (list, tuple))
        or not isinstance(outcome_market_ids, (list, tuple))
        or len(outcome_pairs) != len(outcome_market_ids)
    ):
        return None
    valid: List[Tuple[str, str]] = []
    for pair, raw_mid in zip(outcome_pairs, outcome_market_ids):
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            continue
        name, value = pair
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        try:
            numeric = float(value)
        except OverflowError:
            continue
        if not math.isfinite(numeric):
            continue
        key = str(name).strip()
        if not key:
            continue
        valid.append((key, str(raw_mid or "").strip()))
    counts: Dict[str, int] = {}
    for key, _mid in valid:
        norm = key.casefold()
        counts[norm] = counts.get(norm, 0) + 1
    seen: Dict[str, int] = {}
    key_to_mid: Dict[str, str] = {}
    for key, mid in valid:
        norm = key.casefold()
        occ = seen.get(norm, 0)
        seen[norm] = occ + 1
        full_key = f"{norm}\x1f{occ}" if counts[norm] > 1 else norm
        if mid:
            key_to_mid[full_key] = mid
    return key_to_mid.get(_normalize_snapshot_key(datum_key))


class DatumNotFound(KeyError):
    """The event re-fetched, but it no longer prices this one datum.

    ``datum_only`` tells verification the failure belongs to this claim, so
    other claims on the same event are still re-checked (a market dropped as
    unpriced must not fail its priced siblings).
    """

    datum_only = True
    snapshot: Optional[Dict[str, Any]] = None


def refetch_datum(item: Any, datum_key: str) -> dict[str, Any]:
    """Re-fetch one event datum through the replay-aware HTTP wrapper."""
    event_id = str(getattr(item, "metadata", {}).get("event_id") or "").strip()
    slug_match = re.search(r"/event/([^/?#]+)", str(getattr(item, "url", "")))
    cached_item_id = str(getattr(item, "item_id", "") or "").strip()
    # On the slug fallback, a slug can be re-used by a re-created event. When
    # the cached item still carries the original Gamma event id (numeric; the
    # synthetic PM<N> parse fallback carries no identity), the response id
    # must match it too, or the verdict would come from another market.
    expected_id = (
        cached_item_id
        if not event_id and re.fullmatch(r"\d+", cached_item_id)
        else ""
    )
    if event_id:
        payload = http.request(
            "GET", f"{GAMMA_EVENTS_URL}/{quote(event_id)}", timeout=10, retries=2,
        )
    elif slug_match:
        if not expected_id:
            # No event id anywhere: slug equality alone cannot verify event
            # identity, so fail closed (unsupported) instead of re-deriving a
            # verdict from whatever event currently owns the slug.
            raise ValueError(
                "Polymarket item carries no event id; slug equality alone "
                "cannot verify event identity"
            )
        requested_slug = slug_match.group(1)
        payload = http.request(
            "GET", GAMMA_EVENTS_URL, params={"slug": requested_slug},
            timeout=10, retries=2,
        )
    else:
        raise ValueError("Polymarket item has no event id or slug")

    requested_slug = slug_match.group(1) if slug_match else None

    def _matches_identity(entry: dict) -> bool:
        if str(entry.get("slug") or "").strip() != requested_slug:
            return False
        if expected_id and str(entry.get("id") or "").strip() != expected_id:
            return False
        return True

    def _pick_event(events: list) -> Any:
        candidates = [entry for entry in events if isinstance(entry, dict)]
        if requested_slug is None:
            return candidates[0] if candidates else None
        # Verify identity: Gamma slug queries can return multiple or loosely
        # matched events, and verifying a claim against another market's
        # prices would fabricate current/stale verdicts.
        for entry in candidates:
            if _matches_identity(entry):
                return entry
        return None

    if isinstance(payload, list):
        event = _pick_event(payload)
    elif isinstance(payload, dict) and isinstance(payload.get("events"), list):
        event = _pick_event(payload.get("events") or [])
    else:
        event = payload
        if (
            requested_slug is not None
            and isinstance(event, dict)
            and (
                str(event.get("slug") or "").strip() not in ("", requested_slug)
                or (
                    expected_id
                    and str(event.get("id") or "").strip() not in ("", expected_id)
                )
            )
        ):
            event = None
    if not isinstance(event, dict):
        raise KeyError("Polymarket event was not found")
    # Mixed events: an active event can carry resolved child markets whose
    # high volume would win the parse and swap the outcome labels. Only fall
    # back to closed markets when nothing is active (fully resolved event -
    # the stale-odds transition verification exists to catch).
    markets = event.get("markets") or []
    has_active = any(
        isinstance(m, dict) and m.get("active", True) and not m.get("closed", False)
        for m in markets
    )
    parsed = parse_polymarket_response(
        {"events": [event]},
        include_all_outcomes=True,
        include_closed=not has_active,
    )
    if not parsed:
        raise KeyError("Polymarket event is closed, unavailable, or malformed")
    refreshed = parsed[0]
    values: dict[str, Any] = {}
    outcome_pairs = refreshed.get("outcome_prices") or []
    _record_outcome_values(values, outcome_pairs, overwrite=True)
    # Reports saved by older builds hold each market under its old cut name.
    # Keep them in a separate map so legacy cut names are tried only after
    # current names (including occurrence-0 collapse on current names).
    questions = refreshed.get("outcome_questions") or []
    legacy_pairs = [
        (
            _legacy_shorten_question(question).strip() if question else str(name).strip(),
            price,
        )
        for (name, price), question in zip(outcome_pairs, questions)
    ]
    legacy_values: dict[str, Any] = {}
    _record_outcome_values(legacy_values, legacy_pairs, overwrite=True)
    # A repeated name is told apart by its rank among the priced markets. When
    # the book check drops one market carrying that name, the ranks shift and a
    # saved occurrence could read a sibling's price, so repeated names of a
    # dropped market are not answered at all. Track current and legacy dropped
    # labels separately so a dropped market's legacy cut cannot block an
    # unrelated current outcome name.
    live_event_markets = _live_markets(event, include_closed=not has_active)
    sibling_event_markets = [
        m for m in live_event_markets if isinstance(m, dict) and m.get("question")
    ]
    sibling_labels = (
        _sibling_outcome_labels(sibling_event_markets)
        if len(sibling_event_markets) > 1
        else {}
    )
    dropped_current: set[str] = set()
    dropped_legacy: set[str] = set()
    for market in markets:
        if not isinstance(market, dict) or not market.get("question"):
            continue
        if has_active and (not market.get("active", True) or market.get("closed", False)):
            continue
        if _parse_outcome_prices(market) or not _parse_outcome_prices(market, book_aware=False):
            continue
        question = str(market["question"])
        is_subject = _question_subject(question.strip().rstrip("?")) is not None
        full_label = sibling_labels.get(id(market))
        if full_label:
            dropped_current.add(full_label.strip().casefold())
        else:
            dropped_current.add(_shorten_question(question).strip().casefold())
        if is_subject:
            group_title = " ".join(str(market.get("groupItemTitle") or "").split())
            if group_title:
                dropped_current.add(group_title.casefold())
        dropped_legacy.add(_legacy_shorten_question(question).strip().casefold())
    # If another child market could have claimed a label via its current label,
    # shortened subject, legacy cut, or groupItemTitle (only when it has a
    # question subject), an older report without outcome_market_ids could have
    # saved that other market under that label. Purge such cross-market
    # ambiguous labels from both values and legacy_values so label-only
    # verification returns unsupported instead of switching markets.
    active_question_markets = [
        m
        for m in markets
        if isinstance(m, dict)
        and m.get("question")
        and (not has_active or (m.get("active", True) and not m.get("closed", False)))
    ]
    refreshed_market_ids = refreshed.get("outcome_market_ids") or []
    market_values: dict[str, Any] = {}
    if refreshed_market_ids and len(refreshed_market_ids) == len(outcome_pairs):
        all_mid_counts: dict[str, int] = {}
        for m in active_question_markets:
            clean_mid = _market_id(m)
            if clean_mid:
                all_mid_counts[clean_mid] = all_mid_counts.get(clean_mid, 0) + 1
        refreshed_mid_counts: dict[str, int] = {}
        for raw_mid in refreshed_market_ids:
            clean_mid = str(raw_mid or "").strip()
            if clean_mid:
                refreshed_mid_counts[clean_mid] = refreshed_mid_counts.get(clean_mid, 0) + 1
        for (_name, price), raw_mid in zip(outcome_pairs, refreshed_market_ids):
            clean_mid = str(raw_mid or "").strip()
            if (
                clean_mid
                and refreshed_mid_counts.get(clean_mid) == 1
                and all_mid_counts.get(clean_mid, 1) == 1
            ):
                market_values[clean_mid] = price

    ambiguous_current: set[str] = set()
    ambiguous_legacy: set[str] = set()
    if len(active_question_markets) > 1:
        current_owners: dict[str, set[int]] = {}
        legacy_owners: dict[str, set[int]] = {}
        possible_owners: dict[str, set[int]] = {}
        for idx, market in enumerate(active_question_markets):
            question = str(market["question"])
            current_name = (sibling_labels.get(id(market)) or "").strip().casefold()
            if current_name:
                current_owners.setdefault(current_name, set()).add(idx)
            legacy_name = _legacy_shorten_question(question).strip().casefold()
            if legacy_name:
                legacy_owners.setdefault(legacy_name, set()).add(idx)
            possible_names = {
                current_name,
                legacy_name,
                _shorten_question(question).strip().casefold(),
            }
            if _question_subject(question.strip().rstrip("?")) is not None:
                possible_names.add(
                    " ".join(str(market.get("groupItemTitle") or "").split()).casefold()
                )
            possible_names.discard("")
            for name in possible_names:
                possible_owners.setdefault(name, set()).add(idx)
        ambiguous_current = {
            name
            for name, owners in current_owners.items()
            if possible_owners.get(name, set()) - owners
        }
        ambiguous_legacy = {
            name
            for name, owners in legacy_owners.items()
            if possible_owners.get(name, set()) - owners
        }
    for key in list(values):
        if key == "end_date":
            continue
        base = (
            key.rsplit("\x1f", 1)[0].strip().casefold()
            if "\x1f" in key
            else key.strip().casefold()
        )
        if base in ambiguous_current or ("\x1f" in key and base in dropped_current):
            del values[key]
    dropped_current.update(ambiguous_current)
    for key in list(legacy_values):
        base = (
            key.rsplit("\x1f", 1)[0].strip().casefold()
            if "\x1f" in key
            else key.strip().casefold()
        )
        if base in ambiguous_legacy or ("\x1f" in key and base in dropped_legacy):
            del legacy_values[key]
    dropped_legacy.update(ambiguous_legacy)
    if refreshed.get("end_date") is not None:
        values["end_date"] = refreshed["end_date"]

    blocked_current = frozenset(dropped_current)
    blocked_legacy = frozenset(dropped_legacy)
    saved_market_id = _saved_outcome_market_id(item, datum_key)
    if saved_market_id:
        value = market_values.get(saved_market_id)
    else:
        value = resolve_snapshot_value(
            values,
            datum_key,
            legacy_values=legacy_values,
            blocked_repeats=blocked_current,
            legacy_blocked_repeats=blocked_legacy,
            allow_occurrence_zero=(datum_key != "end_date"),
        )
    snapshot = {
        "values": values,
        "legacy_values": legacy_values,
        "market_values": market_values,
        "blocked_repeats": sorted(blocked_current),
        "legacy_blocked_repeats": sorted(blocked_legacy),
        "url": str(getattr(item, "url", "")),
        "timestamp": event.get("updatedAt"),
    }
    if value is None:
        missing = DatumNotFound(f"Polymarket datum {datum_key!r} was not found")
        # The event was fetched; sibling claims read this snapshot instead of
        # fetching it again.
        missing.snapshot = snapshot
        raise missing
    return {"value": value, **snapshot}


def by_selection(items: list) -> list:
    """Markets in the parser's selection order: a live entity market first,
    by ``selection_score``. An item saved before the parser scored markets
    follows, by 30-day volume as before."""
    def key(item) -> tuple:
        volume = (item.engagement or {}).get("volume") or 0
        score = item.metadata.get("selection_score")
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            return (0, 0, 0.0, volume)
        return (1, item.metadata.get("selection_tier") or 0, score, volume)

    return sorted(items, key=key, reverse=True)
