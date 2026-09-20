"""NFL prediction-market lane for nfl30 (Polymarket Gamma API, keyless).

The generic ``polymarket`` source is tuned to strip sports vocabulary
(``division``, ``conference``, ``season``, ``west``, ...) and drops every result
when the best one is weakly relevant. That is exactly wrong for NFL topics, so
this lane matches structurally instead: game slugs by team abbreviation,
futures by ``groupItemTitle`` (the team's full name), awards by player name.

What it pulls for a resolved entity, in four buckets:

* **game**   — the team's next moneyline (win probability), plus the main spread
  and total. Found by searching ``"<nickname> vs"`` and matching the
  ``nfl-<away>-<home>-<YYYY-MM-DD>`` slug on ``Team.pm_abbr``. Polymarket has no
  team filter and bulk listings are 5-17 MB, so a last-game result is not
  fetched; the beat-writer and team-official lanes cover the recap.
* **future** — champion, conference, division, playoffs, and win total for the
  team, with the 7-day change from ``oneWeekPriceChange``.
* **award**  — MVP / OPOY / DPOY / rookie / coach-of-the-year markets, kept to
  the resolved player or the team's players (league topics: the top few).
* **roster** — coaching and roster markets that mention the team (head coach out,
  traded, starting QB, ...).

Design notes from live probing (2026-09-20): ``/events?tag_id=450`` works but is
unusable at scale (each game event carries 60-380 markets). ``/public-search``
returns full events at 5 per page in 0.6-4 s, and ``/events/slug/<slug>`` is a
20-100 KB exact fetch. Divisions, the postseason market, and the conference
champion have stable slugs; the champion event carries a timestamp suffix and
is found by search. Polymarket abbreviates the Rams as ``la``.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import http, log, nfl

GAMMA_SEARCH_URL = "https://gamma-api.polymarket.com/public-search"
GAMMA_EVENT_URL = "https://gamma-api.polymarket.com/events/slug/{slug}"
EVENT_PAGE_URL = "https://polymarket.com/event/{slug}"
NFL_TAG_ID = 450  # kept for reference/diagnostics; search uses events_tag=nfl

REQUEST_TIMEOUT = 25
LANE_BUDGET_SECONDS = 45

GAME_SLUG_RE = re.compile(r"^nfl-([a-z0-9]+)-([a-z0-9]+)-(\d{4})-(\d{2})-(\d{2})$")
AWARD_TITLE_RE = re.compile(
    r"\b(mvp|offensive player of the year|defensive player of the year|opoy|dpoy|"
    r"offensive rookie|defensive rookie|rookie of the year|coach of the year|"
    r"comeback player)\b",
    re.IGNORECASE,
)
ROSTER_KEYWORD_RE = re.compile(
    r"\b(head coach|fired|out as|hired|traded|trade|starting qb|starter|"
    r"retire|retirement|suspend|suspension|extension|contract|release[ds]?|"
    r"holdout|cut|waive[ds]?|resign|step down|interim)\b",
    re.IGNORECASE,
)
# Events that mention a team but are not roster/coaching markets.
NOISE_TITLE_RE = re.compile(
    r"season series|announcers say|fantasy|player props|first td|first touchdown|"
    r"anytime touchdown|win total|mentions|super bowl (?:lix|lx|lviii)|"
    r"team totals|margin of victory|alternate|spread|total",
    re.IGNORECASE,
)

DEPTH_CONFIG: Dict[str, Dict[str, Any]] = {
    # searches: extra team searches; awards: award events fetched; roster_cap: roster events kept
    "quick": {"game_limit": 10, "team_search": False, "champion": False, "awards": 0, "roster_cap": 0},
    "default": {"game_limit": 10, "team_search": True, "champion": True, "awards": 1, "roster_cap": 3},
    "deep": {"game_limit": 10, "team_search": True, "champion": True, "awards": 4, "roster_cap": 5},
}


def _log(msg: str) -> None:
    log.source_log("NFL_PM", msg, tty_only=False)


# ---------------------------------------------------------------------------
# Small parsers
# ---------------------------------------------------------------------------


def season_year(now: Optional[datetime] = None) -> int:
    """NFL season starting year: the 2026 season runs Sep 2026 - Feb 2027."""
    now = now or datetime.now(timezone.utc)
    return now.year if now.month >= 3 else now.year - 1


def _loads_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def market_outcomes(market: Dict[str, Any]) -> List[Tuple[str, float]]:
    """``[(outcome, probability 0-1), ...]`` for a Gamma market."""
    names = [str(n) for n in _loads_list(market.get("outcomes"))]
    prices = [_to_float(p) for p in _loads_list(market.get("outcomePrices"))]
    return [(n, p) for n, p in zip(names, prices) if p is not None]


def yes_price(market: Dict[str, Any]) -> Optional[float]:
    for name, price in market_outcomes(market):
        if name.strip().lower() == "yes":
            return price
    pairs = market_outcomes(market)
    return pairs[0][1] if pairs else None


def _market_live(market: Dict[str, Any]) -> bool:
    return not market.get("closed") and market.get("active", True) is not False


def parse_game_start(event: Dict[str, Any]) -> Optional[datetime]:
    """UTC kickoff from the moneyline market (the event itself has no start time)."""
    for market in event.get("markets") or []:
        raw = market.get("gameStartTime")
        if raw:
            try:
                text = str(raw).replace(" ", "T", 1)
                if text.endswith("+00"):
                    text += ":00"
                dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


def _team_price(market: Dict[str, Any], team: Dict[str, Any]) -> Tuple[Optional[float], Optional[float], Optional[str], Optional[float]]:
    """(team prob, opponent prob, opponent name, 7d change of team) for a two-way market."""
    pairs = market_outcomes(market)
    if len(pairs) != 2:
        return None, None, None, None
    nick = str(team.get("nickname", "")).lower()
    name = str(team.get("name", "")).lower()
    for idx, (label, price) in enumerate(pairs):
        low = label.strip().lower()
        if low == nick or low == name or nick in low:
            opp_label, opp_price = pairs[1 - idx]
            change = _to_float(market.get("oneWeekPriceChange"))
            if change is not None and idx == 1:
                change = -change
            return price, opp_price, opp_label, change
    return None, None, None, None


# ---------------------------------------------------------------------------
# HTTP (never raises)
# ---------------------------------------------------------------------------


def _get(url: str, params: Optional[Dict[str, str]] = None) -> Tuple[Any, Optional[str]]:
    try:
        return http.get(url, params=params, timeout=REQUEST_TIMEOUT, retries=1), None
    except Exception as exc:  # http.HTTPError and transport errors alike
        return None, f"{type(exc).__name__}: {str(exc)[:100]}"


def search_events(query: str, limit: int = 10, status: str = "active") -> Tuple[List[Dict[str, Any]], Optional[str]]:
    params = {
        "q": query,
        "events_tag": "nfl",
        "events_status": status,
        "keep_closed_markets": "0",
        "limit_per_type": str(limit),
    }
    data, err = _get(GAMMA_SEARCH_URL, params)
    if err or not isinstance(data, dict):
        return [], err or "unexpected search payload"
    return [e for e in (data.get("events") or []) if isinstance(e, dict)], None


def event_by_slug(slug: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    data, err = _get(GAMMA_EVENT_URL.format(slug=slug))
    if err:
        return None, err
    if isinstance(data, list):
        data = data[0] if data else None
    return (data if isinstance(data, dict) and data.get("slug") else None), None


# ---------------------------------------------------------------------------
# Selection helpers
# ---------------------------------------------------------------------------


def _entity_teams(entity: Dict[str, Any]) -> List[Dict[str, Any]]:
    teams = []
    for key in ("team", "opponent"):
        team = entity.get(key)
        if isinstance(team, dict) and team.get("abbr"):
            teams.append(team)
    return teams


def _title_mentions(text: str, team: Dict[str, Any]) -> bool:
    low = text.lower()
    for token in (str(team.get("nickname", "")), str(team.get("city", "")), str(team.get("name", ""))):
        token = token.lower().strip()
        if token and re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", low):
            return True
    return False


def _division_slug(team: Dict[str, Any]) -> str:
    return "pro-football-" + str(team.get("division", "")).lower().replace(" ", "-") + "-champion"


def select_game_events(
    events: List[Dict[str, Any]], entity: Dict[str, Any], now: datetime
) -> List[Tuple[Dict[str, Any], datetime]]:
    """Open moneyline events involving the entity's team(s), soonest first.

    A game entity keeps only the exact pairing. Games that kicked off more than
    six hours ago are dropped (finished, or settling).
    """
    teams = _entity_teams(entity)
    if not teams:
        return []
    abbrs = {str(t["pm_abbr"]).lower() for t in teams}
    is_game = entity.get("kind") == "game" and len(teams) == 2
    out: List[Tuple[Dict[str, Any], datetime]] = []
    seen: set[str] = set()
    for event in events:
        slug = str(event.get("slug") or "")
        match = GAME_SLUG_RE.match(slug)
        if not match or event.get("closed") or slug in seen:
            continue
        a, b = match.group(1), match.group(2)
        if is_game:
            if {a, b} != abbrs:
                continue
        elif not ({a, b} & abbrs):
            continue
        start = parse_game_start(event)
        if start is None:
            continue
        if (now - start).total_seconds() > 6 * 3600:
            continue
        seen.add(slug)
        out.append((event, start))
    out.sort(key=lambda pair: pair[1])
    return out


def _pick_main_line(markets: List[Dict[str, Any]], kind: str) -> Optional[Dict[str, Any]]:
    """The spread/total market priced closest to 50% (the 'main' line)."""
    best: Optional[Dict[str, Any]] = None
    best_gap = 9.0
    for market in markets:
        stype = str(market.get("sportsMarketType") or "").lower()
        if not stype.startswith(kind) or not _market_live(market):
            continue
        outcomes = market_outcomes(market)
        if not outcomes:
            continue
        gap = abs(outcomes[0][1] - 0.5)
        if gap < best_gap:
            best, best_gap = market, gap
    return best


def _moneyline(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    slug = str(event.get("slug") or "")
    for market in event.get("markets") or []:
        if str(market.get("sportsMarketType") or "").lower() == "moneyline" or market.get("slug") == slug:
            return market
    return None


# ---------------------------------------------------------------------------
# Bucket builders (each returns a list of plain "card" dicts)
# ---------------------------------------------------------------------------


def _volume(event: Dict[str, Any]) -> float:
    return _to_float(event.get("volume24hr")) or _to_float(event.get("volume")) or 0.0


def build_game_cards(events: List[Dict[str, Any]], entity: Dict[str, Any], now: datetime) -> List[Dict[str, Any]]:
    teams = _entity_teams(entity)
    if not teams:
        return []
    picked = select_game_events(events, entity, now)
    if not picked:
        return []
    if entity.get("kind") != "game":
        picked = picked[:1]  # the next (or live) game only
    cards: List[Dict[str, Any]] = []
    for event, start in picked[:1] if entity.get("kind") == "game" else picked:
        moneyline = _moneyline(event)
        if not moneyline:
            continue
        team = teams[0]
        prob, opp_prob, opp_name, change = _team_price(moneyline, team)
        if prob is None:
            # The entity team may be the opponent side of this slug's pairing.
            for alt in teams[1:]:
                prob, opp_prob, opp_name, change = _team_price(moneyline, alt)
                if prob is not None:
                    team = alt
                    break
        if prob is None:
            continue
        markets = event.get("markets") or []
        spread = _pick_main_line(markets, "spread")
        total = _pick_main_line(markets, "total")
        spread_label = None
        if spread:
            spread_label = str(spread.get("question") or "").replace("Spread:", "").strip() or None
        total_line = _to_float(total.get("line")) if total else None
        cards.append({
            "bucket": "game",
            "market_type": "moneyline",
            "event_slug": event["slug"],
            "title": str(event.get("title") or moneyline.get("question") or ""),
            "team": team.get("nickname"),
            "team_abbr": team.get("abbr"),
            "team_prob": prob,
            "opp": opp_name,
            "opp_prob": opp_prob,
            "week_change": change,
            "spread": spread_label,
            "total": total_line,
            "game_start": start.isoformat(),
            "week": event.get("eventWeek"),
            "live": bool((now - start).total_seconds() > 0),
            "volume": _volume(event),
            "end_date": event.get("endDate"),
            "url": EVENT_PAGE_URL.format(slug=event["slug"]),
        })
    return cards


def _market_for_team(event: Dict[str, Any], team: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    name = str(team.get("name", "")).lower()
    nick = str(team.get("nickname", "")).lower()
    for market in event.get("markets") or []:
        if not _market_live(market):
            continue
        group = str(market.get("groupItemTitle") or "").strip().lower()
        if group and (group == name or group == nick or group.endswith(" " + nick)):
            return market
    return None


def _future_kind(title: str) -> Optional[Tuple[str, str]]:
    """``(market_type, label)`` for a league futures event title, else None."""
    t = title.lower()
    if re.search(r"team to make postseason|make the playoffs|make postseason", t):
        return "playoffs", "Playoffs"
    m = re.search(r"\b(afc|nfc)\s+(east|north|south|west)\s+champion\b", t)
    if m:
        return "division", f"{m.group(1).upper()} {m.group(2).title()}"
    m = re.search(r"\b(afc|nfc)\s+champion\b", t)
    if m:
        return "conference", m.group(1).upper()
    if re.search(r"\b\d{4}\s+champion\b|super bowl champion|big game champion", t):
        return "champion", "Champion"
    return None


def build_future_cards(events: List[Dict[str, Any]], entity: Dict[str, Any], league_top: int = 4) -> List[Dict[str, Any]]:
    teams = _entity_teams(entity)
    cards: List[Dict[str, Any]] = []
    for event in events:
        kind = _future_kind(str(event.get("title") or ""))
        if not kind or event.get("closed"):
            continue
        market_type, label = kind
        if teams:
            for team in teams:
                market = _market_for_team(event, team)
                price = yes_price(market) if market else None
                if price is None:
                    continue
                # A division market only applies to that division's teams.
                if market_type == "division" and str(team.get("division", "")).lower() not in str(event.get("title", "")).lower():
                    continue
                cards.append(_future_card(event, market, market_type, label, team.get("nickname"), price, team.get("abbr")))
        elif entity.get("kind") == "league" and market_type in {"champion", "conference"}:
            ranked = sorted(
                (m for m in event.get("markets") or [] if _market_live(m) and yes_price(m) is not None),
                key=lambda m: -(yes_price(m) or 0),
            )[:league_top]
            for market in ranked:
                cards.append(_future_card(event, market, market_type, label,
                                          str(market.get("groupItemTitle") or ""), yes_price(market) or 0.0, None))
    return cards


def _future_card(event, market, market_type, label, subject, price, abbr) -> Dict[str, Any]:
    return {
        "bucket": "future",
        "market_type": market_type,
        "label": label,
        "event_slug": event.get("slug"),
        "title": str(event.get("title") or "").strip(),
        "question": str(market.get("question") or ""),
        "team": subject,
        "team_abbr": abbr,
        "team_prob": price,
        "week_change": _to_float(market.get("oneWeekPriceChange")),
        "volume": _to_float(market.get("volumeNum")) or _volume(event),
        "end_date": event.get("endDate"),
        "url": EVENT_PAGE_URL.format(slug=event.get("slug")),
    }


def build_win_total_cards(events: List[Dict[str, Any]], entity: Dict[str, Any]) -> List[Dict[str, Any]]:
    teams = _entity_teams(entity)
    if not teams:
        return []
    team = teams[0]
    for event in events:
        title = str(event.get("title") or "")
        if event.get("closed") or not re.search(r"win total", title, re.IGNORECASE) or not _title_mentions(title, team):
            continue
        lines = [(m, yes_price(m)) for m in event.get("markets") or [] if _market_live(m) and yes_price(m) is not None]
        # Untouched lines sit at a default 50% with no 7-day change; prefer traded ones.
        lines = [mp for mp in lines if mp[0].get("oneWeekPriceChange") is not None] or lines
        if not lines:
            continue
        market, price = min(lines, key=lambda mp: abs((mp[1] or 0) - 0.5))
        label = str(market.get("groupItemTitle") or "").strip()
        return [{
            "bucket": "future",
            "market_type": "win_total",
            "label": label or "Win total",
            "event_slug": event.get("slug"),
            "title": title.strip(),
            "question": str(market.get("question") or ""),
            "team": team.get("nickname"),
            "team_abbr": team.get("abbr"),
            "team_prob": price,
            "week_change": _to_float(market.get("oneWeekPriceChange")),
            "volume": _to_float(market.get("volumeNum")) or _volume(event),
            "end_date": event.get("endDate"),
            "url": EVENT_PAGE_URL.format(slug=event.get("slug")),
        }]
    return []


def _team_player_names(entity: Dict[str, Any]) -> set[str]:
    abbrs = {str(t["abbr"]).upper() for t in _entity_teams(entity)}
    names = {n.lower() for n, row in nfl.load_players().items() if str(row.get("team", "")).upper() in abbrs}
    # load_players keys are lower-cased names already
    person = entity.get("person")
    if person:
        names.add(str(person).lower())
    return names


def build_award_cards(events: List[Dict[str, Any]], entity: Dict[str, Any], top_league: int = 3) -> List[Dict[str, Any]]:
    cards: List[Dict[str, Any]] = []
    wanted = _team_player_names(entity)
    league_view = entity.get("kind") == "league" or not (wanted or entity.get("person"))
    for event in events:
        title = str(event.get("title") or "")
        if event.get("closed") or not AWARD_TITLE_RE.search(title) or re.search(r"super bowl", title, re.IGNORECASE):
            continue
        label = AWARD_TITLE_RE.search(title).group(1).upper()
        label = {"OFFENSIVE PLAYER OF THE YEAR": "OPOY", "DEFENSIVE PLAYER OF THE YEAR": "DPOY",
                 "OFFENSIVE ROOKIE": "OROY", "DEFENSIVE ROOKIE": "DROY", "COACH OF THE YEAR": "COY",
                 "ROOKIE OF THE YEAR": "ROY", "COMEBACK PLAYER": "CPOY"}.get(label, label)
        live = [m for m in event.get("markets") or [] if _market_live(m) and yes_price(m) is not None]
        if league_view:
            chosen = sorted(live, key=lambda m: -(yes_price(m) or 0))[:top_league]
        else:
            chosen = [m for m in live if str(m.get("groupItemTitle") or "").strip().lower() in wanted]
            chosen = sorted(chosen, key=lambda m: -(yes_price(m) or 0))[:2]
        for market in chosen:
            cards.append({
                "bucket": "award",
                "market_type": "award",
                "label": label,
                "event_slug": event.get("slug"),
                "title": title.strip(),
                "question": str(market.get("question") or ""),
                "team": str(market.get("groupItemTitle") or ""),
                "team_abbr": None,
                "team_prob": yes_price(market),
                "week_change": _to_float(market.get("oneWeekPriceChange")),
                "volume": _to_float(market.get("volumeNum")) or _volume(event),
                "end_date": event.get("endDate"),
                "url": EVENT_PAGE_URL.format(slug=event.get("slug")),
            })
    return cards


def build_roster_cards(events: List[Dict[str, Any]], entity: Dict[str, Any], cap: int) -> List[Dict[str, Any]]:
    teams = _entity_teams(entity)
    if not teams or cap <= 0:
        return []
    person = str(entity.get("person") or "").lower()
    scored: List[Tuple[float, Dict[str, Any]]] = []
    seen: set[str] = set()
    for event in events:
        title = str(event.get("title") or "")
        slug = str(event.get("slug") or "")
        if event.get("closed") or slug in seen or GAME_SLUG_RE.match(slug):
            continue
        if NOISE_TITLE_RE.search(title) or AWARD_TITLE_RE.search(title) or _future_kind(title):
            continue
        if not ROSTER_KEYWORD_RE.search(title):
            continue
        if not (any(_title_mentions(title, t) for t in teams) or (person and person in title.lower())):
            continue
        seen.add(slug)
        scored.append((_volume(event), event))
    scored.sort(key=lambda pair: -pair[0])
    cards: List[Dict[str, Any]] = []
    for _vol, event in scored[:cap]:
        live = [m for m in event.get("markets") or [] if _market_live(m) and yes_price(m) is not None]
        if not live:
            continue
        top = sorted(live, key=lambda m: -(yes_price(m) or 0))[:2]
        outcomes = []
        for market in top:
            subject = str(market.get("groupItemTitle") or "").strip() or "Yes"
            outcomes.append((subject, yes_price(market) or 0.0))
        lead = top[0]
        cards.append({
            "bucket": "roster",
            "market_type": "roster",
            "label": "Roster",
            "event_slug": event.get("slug"),
            "title": str(event.get("title") or "").strip(),
            "question": str(lead.get("question") or ""),
            "team": teams[0].get("nickname"),
            "team_abbr": teams[0].get("abbr"),
            "team_prob": outcomes[0][1],
            "outcomes": outcomes,
            "week_change": _to_float(lead.get("oneWeekPriceChange")),
            "volume": _volume(event),
            "end_date": event.get("endDate"),
            "url": EVENT_PAGE_URL.format(slug=event.get("slug")),
        })
    return cards


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def search_nfl_markets(
    entity: Dict[str, Any],
    from_date: str,
    to_date: str,
    depth: str = "default",
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Fetch and bucket NFL markets for the resolved entity. Never raises."""
    now = now or datetime.now(timezone.utc)
    cfg = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    result: Dict[str, Any] = {"cards": [], "errors": [], "fetched": {}}
    if not isinstance(entity, dict) or not entity.get("kind"):
        result["error"] = "no NFL entity resolved"
        return result
    teams = _entity_teams(entity)
    season = season_year(now)
    label_year = season + 1  # Polymarket labels the 2026 season's champion "2027 Champion"

    events_pool: Dict[str, List[Dict[str, Any]]] = {"games": [], "futures": [], "awards": [], "team": []}
    tasks: List[Tuple[str, str, Callable[[], Tuple[Any, Optional[str]]]]] = []

    def add_search(bucket: str, query: str, limit: int = 10) -> None:
        tasks.append((bucket, f"search:{query}", lambda q=query, n=limit: search_events(q, n)))

    def add_slug(bucket: str, slug: str) -> None:
        def _run(s=slug):
            event, err = event_by_slug(s)
            return ([event] if event else []), err
        tasks.append((bucket, f"slug:{slug}", _run))

    if teams:
        primary = teams[0]
        add_search("games", f"{primary['nickname']} vs", cfg["game_limit"])
        if entity.get("kind") == "game" and len(teams) == 2:
            add_search("games", f"{teams[1]['nickname']} vs", cfg["game_limit"])
        for team in teams:
            add_slug("futures", _division_slug(team))
        add_slug("futures", "nfl-team-to-make-postseason")
        if cfg["champion"]:
            add_slug("futures", f"pro-football-{label_year}-{'afc' if str(primary.get('division','')).startswith('AFC') else 'nfc'}-champion")
            add_search("futures", f"pro football {label_year} champion", 5)
        if cfg["team_search"]:
            add_search("team", f"{primary['city']} {primary['nickname']}", 10)
            add_search("team", f"{primary['nickname']} head coach", 10)
    else:
        # League or team-less player: futures/awards only.
        if entity.get("kind") == "league" or cfg["champion"]:
            add_search("futures", f"pro football {label_year} champion", 5)
            add_slug("futures", f"pro-football-{label_year}-afc-champion")
            add_slug("futures", f"pro-football-{label_year}-nfc-champion")
    if cfg["awards"] > 0:
        add_slug("awards", f"pro-football-{season}-mvp-winner")
        if cfg["awards"] > 1:
            for q in ("offensive player of the year", "defensive player of the year",
                      "offensive rookie of the year", "coach of the year")[: cfg["awards"] - 1]:
                add_search("awards", f"pro football {season} {q}", 5)

    def _run_task(task):
        bucket, key, fn = task
        try:
            events, err = fn()
        except Exception as exc:  # defensive: the lane must not raise
            events, err = [], f"{type(exc).__name__}: {str(exc)[:100]}"
        return bucket, key, events, err

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [http.submit_with_context(executor, _run_task, task) for task in tasks]
        try:
            for future in as_completed(futures, timeout=LANE_BUDGET_SECONDS):
                bucket, key, events, err = future.result()
                events_pool[bucket].extend(events or [])
                result["fetched"][key] = len(events or [])
                if err and not events:
                    result["errors"].append(f"{key}: {err}")
        except Exception as exc:  # TimeoutError from the lane budget
            result["errors"].append(f"lane budget exceeded: {type(exc).__name__}")

    cards: List[Dict[str, Any]] = []
    cards += build_game_cards(events_pool["games"], entity, now)
    future_events = events_pool["futures"] + events_pool["team"]
    cards += build_future_cards(future_events, entity)
    cards += build_win_total_cards(events_pool["team"], entity)
    cards += build_award_cards(events_pool["awards"] + events_pool["team"], entity)
    cards += build_roster_cards(events_pool["team"], entity, cfg["roster_cap"])
    # Deduplicate (a futures event can arrive by slug and by search).
    seen: set[Tuple[Any, ...]] = set()
    unique: List[Dict[str, Any]] = []
    for card in cards:
        key = (card["bucket"], card["market_type"], card.get("event_slug"), card.get("team"), card.get("label"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(card)
    result["cards"] = unique
    counts = {b: sum(1 for c in unique if c["bucket"] == b) for b in ("game", "future", "award", "roster")}
    _log(
        f"{entity.get('label') or entity.get('kind')}: "
        + ", ".join(f"{b} {n}" for b, n in counts.items())
        + (f"; {len(result['errors'])} fetch error(s)" if result["errors"] else "")
    )
    if not unique and result["errors"]:
        result["error"] = "; ".join(result["errors"][:3])
    return result


def _pct(prob: float) -> str:
    value = prob * 100
    return f"{value:.0f}%" if value >= 10 or value == 0 else f"{value:.1f}%"


def _change_str(change: Optional[float]) -> str:
    """``▲3.5`` / ``▼1.2`` percentage points over 7 days; empty when under 1 point."""
    if change is None:
        return ""
    points = change * 100
    if abs(points) < 1:
        return ""
    arrow = "▲" if points > 0 else "▼"
    return f" ({arrow}{abs(points):.1f} 7d)"


def _kickoff_label(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return ""
    try:
        from zoneinfo import ZoneInfo

        local = dt.astimezone(ZoneInfo("America/New_York"))
    except Exception:  # tzdata missing: fall back to UTC
        local = dt
    return f"{local:%a} {local.month}/{local.day}"


def parse_nfl_polymarket_response(result: Dict[str, Any], query: str = "", *, today: Optional[str] = None) -> List[Dict[str, Any]]:
    """Turn bucket cards into engine item dicts (one per market card)."""
    today = today or datetime.now(timezone.utc).date().isoformat()
    items: List[Dict[str, Any]] = []
    for idx, card in enumerate(result.get("cards") or []):
        bucket = card["bucket"]
        prob = card.get("team_prob")
        team = str(card.get("team") or "")
        if bucket == "game":
            when = _kickoff_label(card.get("game_start") or "")
            headline = f"{card['title']}: {team} {_pct(prob)} to win" + (f" ({when})" if when else "")
            extras = []
            if card.get("spread"):
                extras.append(f"spread {card['spread']}")
            if card.get("total") is not None:
                extras.append(f"total {card['total']:g}")
            body = headline + (" · " + ", ".join(extras) if extras else "")
            outcomes = [(team, prob), (str(card.get("opp") or ""), card.get("opp_prob") or 0.0)]
            relevance = 0.95
            why = "Polymarket moneyline for the game"
        elif bucket == "future":
            subject = team or "Team"
            headline = f"{subject}: {card['label']} {_pct(prob)}"
            body = f"{headline}{_change_str(card.get('week_change'))} — {card.get('title')}"
            outcomes = [(subject, prob)]
            relevance = 0.85 if card["market_type"] in {"division", "playoffs", "win_total"} else 0.8
            why = "Polymarket season future"
        elif bucket == "award":
            headline = f"{team}: {card['label']} {_pct(prob)}"
            body = f"{headline}{_change_str(card.get('week_change'))} — {card.get('title')}"
            outcomes = [(team, prob)]
            relevance = 0.8
            why = "Polymarket award market"
        else:  # roster
            outcomes = card.get("outcomes") or [(team, prob)]
            leaders = ", ".join(f"{n} {_pct(p)}" for n, p in outcomes)
            headline = f"{card.get('title')}: {leaders}"
            body = headline + _change_str(card.get("week_change"))
            relevance = 0.75
            why = "Polymarket coaching/roster market"
        items.append({
            "id": f"NPM-{card.get('event_slug')}-{card['market_type']}-{idx}",
            "title": headline,
            "text": body,
            "question": card.get("question") or card.get("title"),
            "url": card.get("url") or "",
            "date": today,
            "bucket": bucket,
            "market_type": card["market_type"],
            "label": card.get("label"),
            "team": team,
            "team_abbr": card.get("team_abbr"),
            "team_prob": prob,
            "opp": card.get("opp"),
            "opp_prob": card.get("opp_prob"),
            "spread": card.get("spread"),
            "total": card.get("total"),
            "week_change": card.get("week_change"),
            "game_start": card.get("game_start"),
            "live": bool(card.get("live")),
            "outcome_prices": [(n, p) for n, p in outcomes],
            "end_date": card.get("end_date"),
            "engagement": {"volume": card.get("volume") or 0},
            "relevance": relevance,
            "why_relevant": why,
        })
    return items


_LABEL_ORDER = {"game": 0, "future": 1, "award": 2, "roster": 3}
_FUTURE_ORDER = {"champion": 0, "conference": 1, "division": 2, "playoffs": 3, "win_total": 4}


def format_market_says_line(items: List[Any]) -> Optional[str]:
    """Header line: ``📊 Market says: Chiefs 70% vs Colts (Sun 9/20) │ Champion 9.9% ...``.

    Accepts engine item dicts or ``SourceItem`` objects carrying the same
    fields in ``metadata``.
    """
    rows: List[Dict[str, Any]] = []
    for item in items:
        meta = getattr(item, "metadata", None)
        rows.append(meta if isinstance(meta, dict) else item)
    rows = [r for r in rows if r.get("bucket")]
    if not rows:
        return None

    def order(r: Dict[str, Any]) -> Tuple[int, int]:
        return _LABEL_ORDER.get(r["bucket"], 9), _FUTURE_ORDER.get(r.get("market_type"), 9)

    parts: List[str] = []
    seen_future: set[Tuple[Any, ...]] = set()
    # Name the subject when futures cover more than one team (game or league topics).
    multi_team = len({r.get("team") for r in rows if r["bucket"] == "future"}) > 1
    caps = {"game": 3, "future": 8 if multi_team else 5, "award": 3, "roster": 1}
    used = {b: 0 for b in caps}
    for row in sorted(rows, key=order):
        prob = row.get("team_prob")
        if prob is None:
            continue
        bucket = row["bucket"]
        if used.get(bucket, 0) >= caps.get(bucket, 0):
            continue
        used[bucket] = used.get(bucket, 0) + 1
        if bucket == "game":
            when = _kickoff_label(row.get("game_start") or "")
            if row.get("live"):
                when = f"{when}, live" if when else "live"
            part = f"{row.get('team')} {_pct(prob)} vs {row.get('opp')}" + (f" ({when})" if when else "")
            if row.get("spread"):
                part += f", {row['spread']}"
            parts.append(part)
        elif bucket == "future":
            key = (row.get("market_type"), row.get("team"))
            if key in seen_future:
                continue
            seen_future.add(key)
            subject = f"{row.get('team')} " if multi_team and row.get("team") else ""
            parts.append(f"{subject}{row.get('label')} {_pct(prob)}{_change_str(row.get('week_change'))}")
        elif bucket == "award":
            parts.append(f"{row.get('label')}: {row.get('team')} {_pct(prob)}")
        elif bucket == "roster":
            parts.append(f"{str(row.get('question') or row.get('label'))[:60]} {_pct(prob)}")
    if not parts:
        return None
    return "📊 Market says: " + " │ ".join(parts)
