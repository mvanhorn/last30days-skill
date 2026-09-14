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
so at most 7 credits.

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

from . import http, log

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
    """Normalized form for exact-identity comparison ("CHEF iQ" -> "chefiq")."""
    return "".join(_tokens(text))


def names_match(topic: str, name: str) -> bool:
    """True when an advertiser page name plausibly belongs to the topic.

    A shared long token, or a long token of one contained in the compacted
    form of the other. Containment in both directions is what lets a brand
    whose pages carry product names resolve from its umbrella topic.
    """
    topic_tokens = _match_tokens(topic)
    name_tokens = _match_tokens(name)
    if not topic_tokens or not name_tokens:
        return False
    if topic_tokens & name_tokens:
        return True
    topic_compact = _compact(topic)
    name_compact = _compact(name)
    if any(tok in name_compact for tok in topic_tokens):
        return True
    return any(tok in topic_compact for tok in name_tokens)


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


def resolve_page(
    topic: str, rows: List[Dict[str, Any]]
) -> Tuple[Optional[Dict[str, Any]], List[str], str]:
    """Pick the advertiser page for a topic from discovery rows.

    Two tiers, because ad volume measures delivery, not identity: a page whose
    normalized name *equals* the topic wins outright, and only when none does
    is the busiest partial match taken. Without that, a reseller or outlet page
    running more ads than the brand itself would claim the brand's own topic.

    Returns ``(page, runner_up_names, top_unmatched_name)``. ``page`` is None
    when nothing matched; ``top_unmatched_name`` is empty when no rows at all
    came back, which is what separates "wrong advertiser" from "nothing there".
    """
    ordered = _group_advertisers(rows)
    matches = [g for g in ordered if names_match(topic, g["name"])]
    topic_compact = _compact(topic)
    exact = [g for g in matches if _compact(g["name"]) == topic_compact]
    tier = exact or matches
    if not tier:
        return None, [], (ordered[0]["name"] if ordered else "")
    winner = tier[0]
    runner_ups = [g["name"] for g in matches if g["id"] != winner["id"]][:2]
    return winner, runner_ups, ""


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
        return (
            datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc)
            .date()
            .isoformat()
        )
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


class _BudgetOut(Exception):
    """The wall clock ran out.

    Distinct from ``_Fatal`` on purpose: a credential failure invalidates the
    whole lane, but running out of time only ends the *fetching*. Whatever
    already came back is real evidence and is reported as a partial result
    rather than discarded.
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
        raise _BudgetOut("lane budget exhausted")
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
            raise _Fatal(message, status) from exc
        raise _Fatal(message, status) from exc
    except Exception as exc:  # noqa: BLE001 - any transport failure ends the lane
        raise _Fatal(f"{type(exc).__name__}: {exc}") from exc
    return response if isinstance(response, dict) else {}


# ------------------------------------------------------------ lane stages


def _discover(
    topic: str, country: str, token: str, budget: _Budget
) -> Tuple[Optional[Dict[str, Any]], List[str], str, str]:
    """Resolve the advertiser page. Returns (page, runner_ups, top, state)."""
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
    page, runner_ups, top = resolve_page(topic, rows)
    if page:
        _log(f"Resolved advertiser '{page['name']}' (page {page['id']}) from ad search")
        return page, runner_ups, "", RESOLVED

    companies = _call(SEARCH_COMPANIES_URL, {"query": topic}, token, budget)
    company_rows = _envelope_rows(companies)
    named = [
        {
            "id": str(row.get("page_id") or "").strip(),
            "name": str(row.get("name") or "").strip(),
        }
        for row in company_rows
        if str(row.get("page_id") or "").strip()
    ]
    for candidate in named:
        if names_match(topic, candidate["name"]):
            _log(
                f"Resolved advertiser '{candidate['name']}' "
                f"(page {candidate['id']}) from company search"
            )
            return candidate, [], "", RESOLVED

    fallback_top = top or (named[0]["name"] if named else "")
    if not rows and not named:
        _log("No advertiser candidates returned by either search")
        return None, [], "", NO_CANDIDATES
    _log(f"No advertiser matched '{topic}'; closest was '{fallback_top}'")
    return None, [], fallback_top, UNRESOLVED


def _fetch_window(
    page: Dict[str, Any],
    country: str,
    from_date: str,
    to_date: str,
    max_pages: int,
    token: str,
    budget: _Budget,
) -> Tuple[List[Dict[str, Any]], int, bool, bool]:
    """Cursor-paginate the page's window ads.

    Returns ``(rows, endpoint_total, more_available, ran_out_of_time)``.
    """
    rows: List[Dict[str, Any]] = []
    total = 0
    cursor: Optional[str] = None
    prev_cursor: Optional[str] = None
    more = False
    timed_out = False
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
        except _BudgetOut:
            stop = "wall-clock budget exceeded"
            timed_out = True
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
    return rows, total, more, timed_out


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
) -> Tuple[int, bool]:
    """Transcribe the newest video creatives.

    Candidates are chosen from the whole fetched set, after every page is in,
    so a newer creative on page two is not passed over for an older one on
    page one. Returns ``(transcribed, ran_out_of_time)``.
    """
    if cap <= 0:
        return 0, False
    transcribed = 0
    for item in items:
        if transcribed >= cap:
            break
        if not item.get("has_video"):
            continue
        try:
            response = _call(
                AD_TRANSCRIPT_URL,
                {"id": item["id"]},
                token,
                budget,
                ceiling=TRANSCRIPT_TIMEOUT,
            )
        except _BudgetOut:
            return transcribed, True
        if not response.get("transcript_available"):
            continue
        text = str(response.get("transcript") or "").strip()
        if not text:
            continue
        item["transcript"] = text
        transcribed += 1
    return transcribed, False


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
            _log(f"Using page override {page_override}")
        else:
            page, runner_ups, top_candidate, state = _discover(
                topic, country, token, budget
            )

        if page is None:
            return {
                "ads": [],
                "page": None,
                "tally": _empty_tally(state, top_candidate=top_candidate),
            }

        rows, endpoint_total, more, fetch_timed_out = _fetch_window(
            page, country, from_date, to_date, cfg["pages"], token, budget
        )
        items, still_running = _classify(rows, page, from_date, to_date)
        transcribed, transcript_timed_out = _add_transcripts(
            items, cfg["transcripts"], token, budget
        )
        timed_out = fetch_timed_out or transcript_timed_out
    except (_Fatal, _BudgetOut) as exc:
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
    )

    result: Dict[str, Any] = {"ads": items, "page": page, "tally": tally}
    if timed_out:
        # Keep what came back. Thin coverage caused by our own clock must not
        # read as a finding about how much the advertiser is running.
        result["partial"] = True
        result["error"] = f"lane budget of {LANE_BUDGET_SECONDS}s exceeded"

    _log(
        f"{len(items)} creative(s) launched in window for '{page['name']}' "
        f"({still_running} still running from before, {transcribed} transcribed)"
    )
    return result
