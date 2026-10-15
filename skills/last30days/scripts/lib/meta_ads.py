"""Meta Ad Library source for last30days.

What a brand is *paying* to say this month, next to what everyone else is
saying about it in the other sources. Discovery resolves the advertiser page
behind a brand topic, enrichment pulls that page's creatives, and the newest
video creatives get their spoken script transcribed.

Two-stage shape, following the Amazon buyer-signal lane:

1. **Discovery** -- one keyword ad search resolves which advertiser page the
   topic actually belongs to. Ad-search-first, because the company-search
   endpoint misses brands whose pages carry product names rather than the
   corporate name. Company search is the fallback; an explicit page override
   skips both.
2. **Enrichment** -- the resolved page's ads inside the run window, cursor
   paginated under a depth cap, deduped to distinct creatives, then a small
   capped set of transcripts.

Metering: one credit per request regardless of records returned, so the caps
below bound paid *requests*, not records. A default-depth run is 1 discovery
+ at most 1 company search + up to 2 enrichment pages + up to 3 transcripts,
so at most 7 credits. Every cap here counts requests issued, never results
obtained: an upstream that returns nothing still bills for being asked.

Two live-verified quirks drive the code:

* The keyword endpoint returns its rows under ``searchResults`` while the
  company endpoint returns the same shape under ``results``. Both are read
  tolerantly rather than by endpoint.
* ``status`` defaults to ACTIVE upstream. The window fetch overrides it, or a
  creative that launched inside the window and already ended -- a one-week
  promo push, exactly the signal this source exists for -- never comes back.

Meta exposes ``reach_estimate`` and ``spend`` only for political and issue
ads; both are null on commercial ads, so items carry no engagement beyond
the creative variant count.

Requires SCRAPECREATORS_API_KEY.
"""

from __future__ import annotations

import datetime
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from . import dates, http, log

SC_BASE = "https://api.scrapecreators.com/v1/facebook/adLibrary"

SEARCH_ADS_URL = f"{SC_BASE}/search/ads"
SEARCH_COMPANIES_URL = f"{SC_BASE}/search/companies"
COMPANY_ADS_URL = f"{SC_BASE}/company/ads"
AD_TRANSCRIPT_URL = f"{SC_BASE}/ad/transcript"

DEFAULT_COUNTRY = "US"

# Discovery only needs live creatives to identify the advertiser, and asking
# for live ones keeps that call cheap and current. The window fetch is the
# opposite case: see the module docstring.
DISCOVERY_STATUS = "ACTIVE"
ENRICHMENT_STATUS = "ALL"

# Discovery deliberately uses the endpoint's default (unordered) search type.
# Exact-phrase mode narrows so hard that a brand advertising under a product
# name disappears from its own search.
DISCOVERY_SEARCH_TYPE = "keyword_unordered"

DEPTH_CONFIG: Dict[str, Dict[str, int]] = {
    "quick": {"pages": 1, "transcripts": 0},
    "default": {"pages": 2, "transcripts": 3},
    "deep": {"pages": 4, "transcripts": 5},
}

# Whole-lane wall clock. Pagination bounds the request COUNT, not time, so
# each request is additionally clamped to what is left of this.
LANE_BUDGET_SECONDS = 120.0
REQUEST_TIMEOUT = 30.0
MIN_REQUEST_TIMEOUT = 5.0

# Transcripts are slower than list calls: the live median was well over 15s.
TRANSCRIPT_TIMEOUT = 45.0

# Credential- or account-scoped failures. Retrying the next call would fail
# identically, and retrying a 429 deepens the limit already being hit.
FATAL_STATUS_CODES = frozenset({401, 402, 403, 429})

# Shortest token that may establish a name match. Below this, a topic ending
# in a short word ("... AI") matches every advertiser that shares it: a live
# probe returned 1,467 unrelated advertisers for one such topic.
MIN_MATCH_TOKEN = 4

# Absolute pagination bound, independent of the depth cap.
MAX_PAGES_HARD = 10

# Resolution outcomes carried on the tally so the footer can tell an honest
# empty from a wrong-entity match.
RESOLVED = "resolved"
UNRESOLVED = "unresolved"
NO_CANDIDATES = "no_candidates"

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# A promo code is only recognized when the copy actually calls it one. Model
# numbers and SKUs ("E-325") share the token shape and must not match.
_PROMO_RE = re.compile(
    r"\b(?:promo|discount|coupon)?\s*code\s*[:\-]?\s*([A-Za-z0-9]{4,12})\b",
    re.IGNORECASE,
)


def _log(msg: str) -> None:
    log.source_log("Meta Ads", msg, tty_only=False)


# --------------------------------------------------------------- matching


def _tokens(text: str) -> List[str]:
    return _TOKEN_RE.findall(str(text or "").lower())


def _match_tokens(text: str) -> set[str]:
    """Tokens long enough to establish a match.

    Deliberately not ``relevance.tokenize``: that helper expands short tokens
    through a synonym table, which is right for scoring body text and wrong
    here -- it would let an advertiser sharing one short word pass as the
    topic's own brand.
    """
    return {tok for tok in _tokens(text) if len(tok) >= MIN_MATCH_TOKEN}


def _compact(text: str) -> str:
    """Normalized form for exact-identity comparison ("Bright iQ" -> "brightiq")."""
    return "".join(_tokens(text))


# How an advertiser page name matched the topic, strongest first. Resolution
# prefers a whole tier over ad volume: volume measures how much a page is
# spending, never whether it is the right company.
MATCH_EXACT = "exact"
MATCH_TOKEN = "token"
MATCH_CONTAINED = "contained"
MATCH_NONE = ""


def match_strength(topic: str, name: str) -> str:
    """How strongly an advertiser page name belongs to the topic.

    Three tiers, because they are not equally trustworthy:

    * ``exact`` -- the normalized names are the same string. Unambiguous.
    * ``token`` -- they share a whole word of at least ``MIN_MATCH_TOKEN``.
    * ``contained`` -- a whole word of one appears inside the other's
      normalized form. This is what lets an umbrella topic find a brand
      advertising under product-line page names, and it is also the weakest
      signal: substring containment cannot distinguish a product line from a
      coincidence, so a short brand can be found inside an unrelated longer
      word. It is accepted only when no stronger tier matched, and the footer
      says when resolution rested on it.

    A topic whose every word is shorter than the token floor (an initialism
    brand) has no usable tokens at all, so it can only ever match exactly.
    Returning False for it outright would make such a brand unresolvable even
    against its own identically-named page.
    """
    topic_compact = _compact(topic)
    name_compact = _compact(name)
    if not topic_compact or not name_compact:
        return MATCH_NONE
    if topic_compact == name_compact:
        return MATCH_EXACT
    topic_tokens = _match_tokens(topic)
    name_tokens = _match_tokens(name)
    if not topic_tokens or not name_tokens:
        return MATCH_NONE
    if topic_tokens & name_tokens:
        return MATCH_TOKEN
    if any(tok in name_compact for tok in topic_tokens):
        return MATCH_CONTAINED
    if any(tok in topic_compact for tok in name_tokens):
        return MATCH_CONTAINED
    return MATCH_NONE


def names_match(topic: str, name: str) -> bool:
    """True when an advertiser page name plausibly belongs to the topic."""
    return match_strength(topic, name) != MATCH_NONE


def _group_advertisers(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Collapse ad rows into advertiser pages, busiest first."""
    groups: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        page_id = str(row.get("page_id") or "").strip()
        if not page_id:
            continue
        name = str(row.get("page_name") or "").strip()
        group = groups.setdefault(page_id, {"id": page_id, "name": name, "ads": 0})
        group["ads"] += 1
        if not group["name"] and name:
            group["name"] = name
    return sorted(
        groups.values(), key=lambda g: (-g["ads"], g["name"].lower(), g["id"])
    )


def _token_spread(pages: List[Dict[str, Any]]) -> Dict[str, int]:
    """How many distinct advertisers in this result set carry each word."""
    spread: Dict[str, int] = {}
    for page in pages:
        for token in _match_tokens(page["name"]):
            spread[token] = spread.get(token, 0) + 1
    return spread


def _match_rarity(topic: str, name: str, spread: Dict[str, int]) -> int:
    """How distinctive the word this page matched on is. Lower is better.

    A word carried by many advertisers in the same result set is a category,
    not an identity: "kitchen" sits in every kitchen brand's name, so matching
    on it resolves whoever is advertising hardest rather than the right
    company. A word carried by one advertiser names that advertiser.

    Rarity is measured against the candidates themselves rather than a fixed
    stopword list, because the same word can be a category in one search and
    the brand's own name in another. Counting alone would not separate those:
    an umbrella brand's name also appears across several of its product-line
    pages. What distinguishes them is that a *rarer* alternative exists --
    "Acme Kitchen" carries "acme" as well, while "Kitchen World" carries only
    the shared word -- so the best available word decides, not the fact of
    sharing.
    """
    topic_tokens = _match_tokens(topic)
    name_tokens = _match_tokens(name)
    shared = topic_tokens & name_tokens
    if not shared:
        # Containment match: score the word that did the containing.
        shared = {
            tok for tok in topic_tokens if tok in _compact(name)
        } | {tok for tok in name_tokens if tok in _compact(topic)}
    if not shared:
        return 0
    return min(spread.get(tok, 1) for tok in shared)


def _matched_tokens(topic: str, name: str) -> set[str]:
    """Topic words that actually took part in the match.

    For a containment match the participating topic word has to be identified,
    not assumed: crediting every topic word whenever any page word appears
    somewhere in the topic would let "Grill World" claim "best Acme grills" on
    the strength of "grill", carrying the brand word along with it.
    """
    topic_tokens = _match_tokens(topic)
    name_tokens = _match_tokens(name)
    shared = topic_tokens & name_tokens
    if shared:
        return shared
    name_compact = _compact(name)
    topic_compact = _compact(topic)
    matched = {tok for tok in topic_tokens if tok in name_compact}
    for name_token in name_tokens:
        if name_token in topic_compact:
            matched |= {
                tok
                for tok in topic_tokens
                if name_token in tok or tok in name_token
            }
    return matched


def _head_token(topic: str) -> str:
    """The leading word of the topic's primary entity.

    Brand names lead and category words trail: in "Acme Kitchen" the brand is
    "acme" and "kitchen" says what it sells. This mirrors the entity-grounding
    rule the engine already applies elsewhere, where grounding keys on the head
    token of the primary entity because trailing words are usually descriptors.

    A research topic does not always open with the brand, though: "best Acme
    grills" leads with an intent modifier, and treating "best" as the identity
    would reject the brand's own page. So the head is taken from the primary
    entity rather than the raw phrase, skipping the same descriptor vocabulary
    the engine's other adapters strip before searching.
    """
    from .query import NOISE_WORDS

    eligible = [tok for tok in _tokens(topic) if len(tok) >= MIN_MATCH_TOKEN]
    for token in eligible:
        if token not in NOISE_WORDS:
            return token
    # Every word was a descriptor, so there is no identity to key on; fall
    # back to the leading word rather than matching anything at all.
    return eligible[0] if eligible else ""


def _name_covered_by_topic(topic: str, name: str) -> bool:
    """True when the topic spells out this advertiser's whole name.

    Whole means whole. Every word of the page name must appear in the topic as
    a word, short ones included and without dissolving word boundaries: an
    advertiser called "Acme AI" is not named by a topic about "Acme Kitchen"
    just because the two share "acme", and "Cart Wheel" is not named by "Acme
    Cartwheel" just because its letters appear inside a longer word. Ignoring
    either would let a fragment of one company's name stand in for another's.

    The name must also account for more than a single word of the topic, unless
    it accounts for the topic entirely, so a page called just "Kitchen" cannot
    claim a topic about "Acme Kitchen" on the category word alone.
    """
    topic_words = set(_tokens(topic))
    name_words = set(_tokens(name))
    if not topic_words or not name_words:
        return False
    if not name_words <= topic_words:
        return False
    topic_tokens = _match_tokens(topic)
    covered = topic_tokens & name_words
    return len(covered) >= 2 or (bool(covered) and covered == topic_tokens)


def _is_category_only(topic: str, name: str, spread: Dict[str, int]) -> bool:
    """True when this page shares some of the topic's words but is not its brand.

    A single-word topic is its own identity, so any page carrying that word --
    including an umbrella brand's product-line page -- is the brand.

    A multi-word topic has to be matched as a name, not by one of its words. The
    page qualifies when the topic spells its name out, or when it carries every
    identifying word of the topic. Sharing only one of them is what a category
    lookalike and a same-word different-company both look like, and neither
    should have another firm's paid creatives attributed to it.
    """
    matched = _matched_tokens(topic, name)
    if not matched:
        return True
    topic_tokens = _match_tokens(topic)
    if len(topic_tokens) <= 1:
        return False
    if _name_covered_by_topic(topic, name):
        return False
    name_words = set(_tokens(name))
    name_compact = _compact(name)
    covered = {
        token
        for token in topic_tokens
        if token in name_words or token in name_compact
    }
    return covered != topic_tokens


def resolve_page(
    topic: str, rows: List[Dict[str, Any]]
) -> Tuple[Optional[Dict[str, Any]], List[str], str, str]:
    """Pick the advertiser page for a topic from a list of ad rows.

    Strength decides before volume. Ad count only breaks ties *inside* the
    strongest tier that matched, because volume measures how much a page is
    spending and says nothing about whether it is the right company: a reseller
    or outlet page running more ads than the brand it resells would otherwise
    claim the brand's own topic.

    Returns ``(page, runner_up_names, top_unmatched_name, strength)``. ``page``
    is None when nothing matched, and ``top_unmatched_name`` is empty only when
    no rows came back at all, which is what separates "wrong advertiser" from
    "nothing there". ``strength`` is the tier that won, so the caller can tell
    the reader when resolution rested on the weakest one.
    """
    ordered = _group_advertisers(rows)
    spread = _token_spread(ordered)
    scored = [(match_strength(topic, g["name"]), g) for g in ordered]
    for tier in (MATCH_EXACT, MATCH_TOKEN, MATCH_CONTAINED):
        group = [
            g
            for strength, g in scored
            if strength == tier
            # An exact normalized-name match is unambiguous, so it is never
            # second-guessed: the guard below exists for the weaker tiers,
            # where a shared word is the only thing holding the match up.
            and (tier == MATCH_EXACT or not _is_category_only(topic, g["name"], spread))
        ]
        if not group:
            continue
        # Within a tier: the most distinctive matched word first, then the
        # page accounting for the most of the topic, and only then ad volume.
        # Two pages can share a brand word while one also matches the rest of
        # the topic, and that one is the better answer however much the other
        # is spending.
        group.sort(
            key=lambda g: (
                _match_rarity(topic, g["name"], spread),
                -len(_matched_tokens(topic, g["name"])),
                -g["ads"],
                g["id"],
            )
        )
        winner = group[0]
        # Runner-ups come from every tier that matched at all: a weaker
        # same-name-family page is exactly what a reader checking a
        # questionable resolution wants to see.
        runner_ups = [
            g["name"]
            for strength, g in scored
            if strength != MATCH_NONE and g["id"] != winner["id"]
        ][:2]
        return winner, runner_ups, "", tier
    return None, [], (ordered[0]["name"] if ordered else ""), MATCH_NONE


# ----------------------------------------------------------- ad row fields


def _envelope_rows(response: Any) -> List[Dict[str, Any]]:
    """Read ad rows from either endpoint's envelope."""
    if not isinstance(response, dict):
        return []
    for key in ("searchResults", "results", "ads", "data"):
        value = response.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    return []


def _envelope_total(response: Any) -> int:
    if not isinstance(response, dict):
        return 0
    try:
        return int(response.get("searchResultsCount") or 0)
    except (TypeError, ValueError):
        return 0


def _envelope_cursor(response: Any) -> Optional[str]:
    if not isinstance(response, dict):
        return None
    cursor = response.get("cursor")
    if isinstance(cursor, (str, int)) and str(cursor).strip():
        return str(cursor)
    return None


def _snapshot(row: Dict[str, Any]) -> Dict[str, Any]:
    snapshot = row.get("snapshot")
    return snapshot if isinstance(snapshot, dict) else {}


def _body_text(snapshot: Dict[str, Any]) -> str:
    body = snapshot.get("body")
    if isinstance(body, dict):
        return str(body.get("text") or "").strip()
    return str(body or "").strip()


def _cards(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    cards = snapshot.get("cards")
    if not isinstance(cards, list):
        return []
    return [card for card in cards if isinstance(card, dict)]


def launch_date(row: Dict[str, Any]) -> Optional[str]:
    """YYYY-MM-DD the creative started running, or None."""
    raw = str(row.get("start_date_string") or "")[:10]
    try:
        datetime.date.fromisoformat(raw)
        return raw
    except ValueError:
        pass
    epoch = row.get("start_date")
    if isinstance(epoch, (int, float)) and epoch > 0:
        return dates.timestamp_to_date(epoch)
    return None


def dedupe_key(row: Dict[str, Any]) -> str:
    """Identity for one creative.

    ``collation_id`` groups the variants of one creative, which is the unit a
    reader cares about. It is frequently absent though (9 of 30 ads on one
    live page carried none), and keying on it alone would collapse every such
    creative into a single item.
    """
    collation = str(row.get("collation_id") or "").strip()
    if collation:
        return f"collation:{collation}"
    return f"ad:{str(row.get('ad_archive_id') or '').strip()}"


def has_video(row: Dict[str, Any]) -> bool:
    snapshot = _snapshot(row)
    videos = snapshot.get("videos")
    if isinstance(videos, list) and any(isinstance(v, dict) and v for v in videos):
        return True
    for card in _cards(snapshot):
        if card.get("video_hd_url") or card.get("video_sd_url"):
            return True
    return False


def extract_promo_code(text: str) -> Optional[str]:
    """Return an uppercase promo code the copy explicitly labels as one."""
    for match in _PROMO_RE.finditer(str(text or "")):
        token = match.group(1)
        if token.isupper() and any(ch.isalpha() for ch in token):
            return token
    return None


def _landing_url(snapshot: Dict[str, Any]) -> str:
    link = str(snapshot.get("link_url") or "").strip()
    if link:
        return link
    for card in _cards(snapshot):
        card_link = str(card.get("link_url") or "").strip()
        if card_link:
            return card_link
    return ""


def _placements(row: Dict[str, Any]) -> List[str]:
    raw = row.get("publisher_platform")
    if not isinstance(raw, list):
        return []
    return [str(p).strip() for p in raw if str(p or "").strip()]


def _variants(row: Dict[str, Any]) -> int:
    try:
        count = int(row.get("collation_count") or 0)
    except (TypeError, ValueError):
        count = 0
    return max(1, count)


def build_item(row: Dict[str, Any], page: Dict[str, Any]) -> Dict[str, Any]:
    """Turn one ad row into a normalizer-ready item dict."""
    snapshot = _snapshot(row)
    body = _body_text(snapshot)
    title = str(snapshot.get("title") or "").strip()
    if not title:
        title = body.split("\n", 1)[0][:140]
    archive_id = str(row.get("ad_archive_id") or "").strip()
    url = str(row.get("url") or "").strip()
    if not url and archive_id:
        url = f"https://www.facebook.com/ads/library/?id={archive_id}"
    placements = _placements(row)
    return {
        "id": archive_id or dedupe_key(row),
        "title": title,
        "text": body,
        "url": url,
        "date": launch_date(row),
        "advertiser": page.get("name") or "",
        "page_id": page.get("id") or "",
        "is_active": bool(row.get("is_active")),
        "ended_on": str(row.get("end_date_string") or "")[:10] or None,
        "display_format": str(snapshot.get("display_format") or "").strip(),
        "cta": str(snapshot.get("cta_text") or "").strip(),
        "landing_url": _landing_url(snapshot),
        "placements": placements,
        "promo_code": extract_promo_code(body),
        "variants": _variants(row),
        "has_video": has_video(row),
        "transcript": "",
    }


# ------------------------------------------------------------ HTTP helpers


class _Budget:
    """Monotonic wall-clock budget shared by every call in one lane run."""

    def __init__(self, seconds: float = LANE_BUDGET_SECONDS) -> None:
        self.deadline = time.monotonic() + seconds

    @property
    def remaining(self) -> float:
        return self.deadline - time.monotonic()

    def exhausted(self) -> bool:
        return self.remaining <= 0

    def timeout(self, ceiling: float = REQUEST_TIMEOUT) -> float:
        return max(MIN_REQUEST_TIMEOUT, min(ceiling, self.remaining))


class _Fatal(Exception):
    """A lane-ending failure: no further calls may be made."""

    def __init__(self, message: str, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


class _StopFetching(Exception):
    """Stop making calls, but keep whatever already came back.

    Distinct from ``_Fatal`` on purpose. A credential or account failure
    invalidates the lane itself, so its output is discarded. Running out of
    wall clock, or a transient upstream failure partway through pagination,
    only ends the *fetching*: the creatives already in hand are real evidence
    and are reported as a partial result rather than thrown away.
    """


def _call(
    url: str,
    params: Dict[str, Any],
    token: str,
    budget: _Budget,
    ceiling: float = REQUEST_TIMEOUT,
) -> Dict[str, Any]:
    """One metered GET.

    ``max_429_retries=0`` matters: the shared client retries a 429 twice by
    default, which both contradicts the no-further-calls contract for a rate
    limit and spends the lane budget sleeping between attempts.
    """
    if budget.exhausted():
        raise _StopFetching("lane budget exhausted")
    try:
        response = http.get(
            url,
            params=params,
            headers=http.scrapecreators_headers(token),
            timeout=budget.timeout(ceiling),
            retries=1,
            max_429_retries=0,
            deadline_monotonic=budget.deadline,
        )
    except http.HTTPError as exc:
        status = exc.status_code
        message = f"HTTP {status}: {exc}" if status else str(exc)
        if status in FATAL_STATUS_CODES:
            # Credential- or account-scoped: every later call fails the same
            # way, so the lane ends and keeps nothing.
            raise _Fatal(message, status) from exc
        # Anything else (a 5xx, a bad gateway partway through pagination) is
        # about this one request. Stop fetching, but keep the creatives
        # already retrieved rather than discarding paid-for work.
        raise _StopFetching(message) from exc
    except Exception as exc:  # noqa: BLE001 - an unclassifiable transport failure
        raise _Fatal(f"{type(exc).__name__}: {exc}") from exc
    return response if isinstance(response, dict) else {}


# ------------------------------------------------------------ lane stages


def _discover(
    topic: str, country: str, token: str, budget: _Budget
) -> Tuple[Optional[Dict[str, Any]], List[str], str, str, str]:
    """Resolve the advertiser page.

    Returns ``(page, runner_ups, top_unmatched, state, strength)``.
    """
    response = _call(
        SEARCH_ADS_URL,
        {
            "query": topic,
            "country": country,
            "status": DISCOVERY_STATUS,
            "search_type": DISCOVERY_SEARCH_TYPE,
            "trim": "true",
        },
        token,
        budget,
    )
    rows = _envelope_rows(response)
    page, runner_ups, top, strength = resolve_page(topic, rows)
    if page:
        _log(
            f"Resolved advertiser '{page['name']}' (page {page['id']}) "
            f"from ad search by {strength} name match"
        )
        return page, runner_ups, "", RESOLVED, strength

    companies = _call(SEARCH_COMPANIES_URL, {"query": topic}, token, budget)
    # Reshape company rows into the same shape resolve_page reads, so the
    # fallback gets the identical exact-before-partial tiering. Taking the
    # first name that merely matched would let this path resolve a lookalike
    # the primary path would have rejected.
    company_rows = [
        {"page_id": str(row.get("page_id") or "").strip(),
         "page_name": str(row.get("name") or "").strip()}
        for row in _envelope_rows(companies)
        if str(row.get("page_id") or "").strip()
    ]
    company_page, company_runner_ups, company_top, company_strength = resolve_page(
        topic, company_rows
    )
    if company_page:
        _log(
            f"Resolved advertiser '{company_page['name']}' "
            f"(page {company_page['id']}) from company search by "
            f"{company_strength} name match"
        )
        return company_page, company_runner_ups, "", RESOLVED, company_strength

    fallback_top = top or company_top
    if not rows and not company_rows:
        _log("No advertiser candidates returned by either search")
        return None, [], "", NO_CANDIDATES, MATCH_NONE
    _log(f"No advertiser matched '{topic}'; closest was '{fallback_top}'")
    return None, [], fallback_top, UNRESOLVED, MATCH_NONE


def _fetch_window(
    page: Dict[str, Any],
    country: str,
    from_date: str,
    to_date: str,
    max_pages: int,
    token: str,
    budget: _Budget,
) -> Tuple[List[Dict[str, Any]], int, bool, str]:
    """Cursor-paginate the page's window ads.

    Returns ``(rows, endpoint_total, more_available, interruption)``, where
    ``interruption`` is the reason fetching stopped early (empty when it ran
    to its natural end). It is carried verbatim rather than summarized: a
    transient upstream failure and an exhausted clock produce the same partial
    shape, and reporting one as the other sends the reader to fix the wrong
    thing.
    """
    rows: List[Dict[str, Any]] = []
    total = 0
    cursor: Optional[str] = None
    prev_cursor: Optional[str] = None
    more = False
    interruption = ""
    pages = min(max_pages, MAX_PAGES_HARD)
    stop = "page cap reached"

    for _ in range(pages):
        params: Dict[str, Any] = {
            "pageId": page["id"],
            "country": country,
            "status": ENRICHMENT_STATUS,
            "start_date": from_date,
            "end_date": to_date,
        }
        if cursor:
            params["cursor"] = cursor
        try:
            response = _call(COMPANY_ADS_URL, params, token, budget)
        except (_Fatal, _StopFetching) as exc:
            # Whatever the reason, the creatives already fetched were paid for
            # and are valid: a rate limit or expired credential says "stop
            # calling", not "the pages that already returned 200 were wrong".
            stop = str(exc)
            interruption = str(exc)
            more = True
            break
        page_rows = _envelope_rows(response)
        total = _envelope_total(response) or total
        if not page_rows:
            stop = "empty page"
            break
        rows.extend(page_rows)
        cursor = _envelope_cursor(response)
        if not cursor:
            stop = "no cursor"
            break
        if cursor == prev_cursor:
            stop = "cursor stopped advancing"
            break
        prev_cursor = cursor
    else:
        more = bool(cursor)

    _log(f"  fetched {len(rows)} ad rows of {total or len(rows)}, stopped: {stop}")
    return rows, total, more, interruption


def _classify(
    rows: List[Dict[str, Any]],
    page: Dict[str, Any],
    from_date: str,
    to_date: str,
) -> Tuple[List[Dict[str, Any]], int]:
    """Split deduped rows into in-window items and a still-running tally.

    The endpoint's date filter returns everything *active during* the window,
    which on a busy page is mostly creatives launched months earlier. The
    last-30-days signal is what launched inside it; the rest are counted so
    the footer can say how much steady-state advertising sits behind them.
    """
    seen: set[str] = set()
    items: List[Dict[str, Any]] = []
    still_running = 0
    for row in rows:
        key = dedupe_key(row)
        if key in seen:
            continue
        seen.add(key)
        launched = launch_date(row)
        if launched and from_date <= launched <= to_date:
            items.append(build_item(row, page))
        else:
            still_running += 1
    items.sort(key=lambda item: item.get("date") or "", reverse=True)
    return items, still_running


def _add_transcripts(
    items: List[Dict[str, Any]], cap: int, token: str, budget: _Budget
) -> Tuple[int, str]:
    """Transcribe the newest video creatives.

    Candidates are chosen from the whole fetched set, after every page is in,
    so a newer creative on page two is not passed over for an older one on
    page one. Returns ``(transcribed, interruption)``.
    """
    if cap <= 0:
        return 0, ""
    transcribed = 0
    # The cap bounds paid REQUESTS, not successes. Counting only successes
    # would keep calling for every remaining video creative whenever the
    # upstream has no transcript available -- each of those still costs a
    # credit, so a page of silent video ads would blow through the run's whole
    # documented credit ceiling while the tally still read zero.
    candidates = [item for item in items if item.get("has_video")][:cap]
    for item in candidates:
        try:
            response = _call(
                AD_TRANSCRIPT_URL,
                {"id": item["id"]},
                token,
                budget,
                ceiling=TRANSCRIPT_TIMEOUT,
            )
        except (_Fatal, _StopFetching) as exc:
            return transcribed, str(exc)
        if not response.get("transcript_available"):
            continue
        text = str(response.get("transcript") or "").strip()
        if not text:
            continue
        item["transcript"] = text
        transcribed += 1
    return transcribed, ""


def _empty_tally(state: str, **extra: Any) -> Dict[str, Any]:
    tally = {
        "resolution": state,
        "launched_in_window": 0,
        "still_running": 0,
        "video": 0,
        "transcribed": 0,
        "fetched": 0,
        "endpoint_total": 0,
        "cursor_remaining": False,
        "placements": [],
        "promo_codes": [],
        "advertiser": "",
        "page_id": "",
        "top_candidate": "",
        "runner_ups": [],
        "match_strength": MATCH_NONE,
    }
    tally.update(extra)
    return tally


# ------------------------------------------------------------ public entry


def search_meta_ads(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    token: str = "",
    country: str = DEFAULT_COUNTRY,
    page_override: str = "",
) -> Dict[str, Any]:
    """Resolve an advertiser and return its in-window creatives.

    Returns ``{"ads", "page", "tally", "partial"?, "error"?}``. ``ads`` are
    normalizer-ready item dicts; ``tally`` carries every count the footer
    renders, computed here rather than downstream because the pipeline
    truncates each source's stream to a per-depth limit before rendering.
    """
    topic = (topic or "").strip()
    if not token:
        _log("No SCRAPECREATORS_API_KEY - skipping")
        return {"ads": [], "page": None, "tally": _empty_tally(UNRESOLVED)}
    if not topic and not page_override:
        _log("Empty topic - skipping")
        return {"ads": [], "page": None, "tally": _empty_tally(UNRESOLVED)}

    cfg = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    country = (country or DEFAULT_COUNTRY).strip() or DEFAULT_COUNTRY
    budget = _Budget()

    try:
        if page_override:
            page: Optional[Dict[str, Any]] = {"id": page_override, "name": topic or page_override}
            runner_ups: List[str] = []
            top_candidate = ""
            state = RESOLVED
            strength = MATCH_EXACT
            _log(f"Using page override {page_override}")
        else:
            page, runner_ups, top_candidate, state, strength = _discover(
                topic, country, token, budget
            )

        if page is None:
            return {
                "ads": [],
                "page": None,
                "tally": _empty_tally(state, top_candidate=top_candidate),
            }

        rows, endpoint_total, more, interruption = _fetch_window(
            page, country, from_date, to_date, cfg["pages"], token, budget
        )
        items, still_running = _classify(rows, page, from_date, to_date)
        transcribed, transcript_interruption = _add_transcripts(
            items, cfg["transcripts"], token, budget
        )
        interruption = interruption or transcript_interruption
    except (_Fatal, _StopFetching) as exc:
        # Nothing was salvageable: either a credential/account failure, or the
        # clock ran out before the advertiser was even resolved.
        _log(f"Lane stopped: {exc}")
        return {
            "ads": [],
            "page": None,
            "tally": _empty_tally(UNRESOLVED),
            "error": str(exc),
        }

    placements: List[str] = []
    promo_codes: List[str] = []
    for item in items:
        for placement in item["placements"]:
            if placement not in placements:
                placements.append(placement)
        code = item.get("promo_code")
        if code and code not in promo_codes:
            promo_codes.append(code)

    tally = _empty_tally(
        RESOLVED,
        launched_in_window=len(items),
        still_running=still_running,
        video=sum(1 for item in items if item.get("has_video")),
        transcribed=transcribed,
        fetched=len(rows),
        endpoint_total=endpoint_total,
        cursor_remaining=more,
        placements=placements,
        promo_codes=promo_codes,
        advertiser=page.get("name") or "",
        page_id=page.get("id") or "",
        runner_ups=runner_ups,
        match_strength=strength,
    )

    result: Dict[str, Any] = {"ads": items, "page": page, "tally": tally}
    if interruption:
        # Keep what came back. Thin coverage caused by our own clock or one
        # failed request must not read as a finding about how much the
        # advertiser is running.
        result["partial"] = True
        result["error"] = interruption

    _log(
        f"{len(items)} creative(s) launched in window for '{page['name']}' "
        f"({still_running} still running from before, {transcribed} transcribed)"
    )
    return result
