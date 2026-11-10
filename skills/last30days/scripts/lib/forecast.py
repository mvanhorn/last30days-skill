"""Next30Days: a falsifiable forecast ledger that grades itself.

The hosting model writes the predictions (it has just read the research);
this module does the mechanical half that makes them worth trusting:

- validate that every prediction targets one of the topic's open questions,
  states a baseline, and says more than that baseline already does
- enforce the grading rules (a verdict needs evidence, a miss needs the
  deadline to have passed, a verdict is written once)
- settle market-anchored predictions against resolved Polymarket markets
- compute the scorecard: skill against each call's stored baseline, hit
  rate, Brier score, and calibration
- assemble the Earlier Calls a repeat run on a topic shows

Nothing here talks to SQLite. ``store.py`` owns persistence and the engine
entry point glues the two together, so every function below is a pure
transform that tests can drive without a database or a network.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import time
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

# Read through env.get_config. Default on; the literal value "off" disables it.
CONFIG_KEY = "LAST30DAYS_NEXT30DAYS"
# A question call targets an entry in the topic's open questions. A rehearsal
# call is about the user's own unreleased draft, so no open question exists
# for it; it rests on basis links instead.
KINDS = ("question", "rehearsal")
MAX_PREDICTIONS_PER_ADD = 2
# A market call this close to the market's own price on the same outcome only
# restates it.
RESTATEMENT_MARKET_MARGIN = 0.05
# A non-market call on the status-quo outcome above this is a safe call that
# tells the reader nothing.
RESTATEMENT_STATUS_QUO_CEILING = 0.85
# A logged call departs from its baseline when it sits on the other side of
# even odds, or at least this far from it. Otherwise it agrees.
EVEN_ODDS = 0.5
DEPARTURE_MARGIN = 0.15
MAX_REASON = 280
MAX_BASELINE_OUTCOME = 200
MAX_HORIZON_DAYS = 90
DEFAULT_HORIZON_DAYS = 30
MIN_PROBABILITY = 0.5
MAX_PROBABILITY = 0.99
MIN_TEXT = 12
MAX_CLAIM = 280
MAX_RESOLUTION = 400
MAX_NOTE = 400
MAX_BASIS_URLS = 6
MAX_PAYLOAD_BYTES = 256_000
# Below this many graded calls the hit rate is an anecdote, not a track
# record; the scorecard says so instead of printing a confident percentage.
MIN_SAMPLE = 10
# How long a research run may spend settling the topic's earlier market calls.
SETTLE_BUDGET_SECONDS = 10.0
VERDICTS = ("hit", "miss", "void")
# A resolved binary market prices its winner at 1 and its loser at 0. The
# margins absorb float noise without mistaking a 50/50 refund for a result.
_RESOLVED_WIN = 0.99
_RESOLVED_LOSS = 0.01

CALIBRATION_BUCKETS = ((0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0))


class ForecastError(ValueError):
    """A payload the ledger refuses, with a message the host can act on."""


# --- parsing and validation -------------------------------------------------


def parse_payload(text: str) -> dict[str, Any]:
    if len(text.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ForecastError(f"forecast file exceeds {MAX_PAYLOAD_BYTES} bytes")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ForecastError(f"forecast file is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ForecastError("forecast file must be a JSON object")
    return payload


def _text(value: Any, field: str, where: str, maximum: int, minimum: int = MIN_TEXT) -> str:
    if not isinstance(value, str):
        raise ForecastError(f"{where}: {field} must be a string")
    cleaned = " ".join(value.split())
    if len(cleaned) < minimum:
        raise ForecastError(f"{where}: {field} is too short (min {minimum} characters)")
    if len(cleaned) > maximum:
        raise ForecastError(f"{where}: {field} is too long (max {maximum} characters)")
    return cleaned


def _http_url(value: Any, field: str, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ForecastError(f"{where}: {field} must be an http(s) URL")
    url = value.strip()
    try:
        parts = urlsplit(url)
    except ValueError as exc:  # e.g. an unterminated IPv6 bracket
        raise ForecastError(f"{where}: {field} must be an http(s) URL, got {value!r}") from exc
    if parts.scheme not in ("http", "https") or not parts.netloc or any(c.isspace() for c in url):
        raise ForecastError(f"{where}: {field} must be an http(s) URL, got {value!r}")
    return url


def _probability(value: Any, field: str, where: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ForecastError(f"{where}: {field} must be a number")
    number = float(value)
    if not (low <= number <= high):
        raise ForecastError(f"{where}: {field} must be between {low} and {high}, got {number}")
    return number


def _iso_date(value: Any, field: str, where: str) -> _dt.date:
    if not isinstance(value, str):
        raise ForecastError(f"{where}: {field} must be a YYYY-MM-DD date")
    try:
        return _dt.date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ForecastError(f"{where}: {field} must be a YYYY-MM-DD date, got {value!r}") from exc


def parse_market_url(url: str) -> tuple[str, str | None]:
    """Return (event_slug, market_slug or None) for a Polymarket event URL."""
    parts = urlsplit(url)
    host = parts.netloc.lower().removeprefix("www.")
    match = re.fullmatch(r"/event/([A-Za-z0-9-]+)(?:/([A-Za-z0-9-]+))?/?", parts.path)
    if host != "polymarket.com" or not match:
        raise ForecastError(
            "market.url must be a Polymarket event URL "
            "(https://polymarket.com/event/<event-slug>[/<market-slug>]), "
            f"got {url!r}"
        )
    return match.group(1), match.group(2)


def enabled(config: dict[str, Any]) -> bool:
    """Whether Next30Days is on. Only the literal setting "off" disables it."""
    return str(config.get(CONFIG_KEY) or "").strip().lower() != "off"


def _points(probability: float, baseline: float) -> float:
    # Rounded so 0.70 against 0.55 is fifteen points, not 0.1499999.
    return round(abs(probability - baseline), 6)


def departs_from_baseline(row: dict[str, Any]) -> bool | None:
    """Whether a logged call departed from its baseline or agreed with it.

    None when the row has no baseline (a legacy or rehearsal call). Derived
    from the stored probabilities, which are both stated on the call's own
    outcome.
    """
    baseline = row.get("baseline_probability")
    if baseline is None:
        return None
    probability, baseline = float(row["probability"]), float(baseline)
    other_side = (probability - EVEN_ODDS) * (baseline - EVEN_ODDS) < 0
    return other_side or _points(probability, baseline) >= DEPARTURE_MARGIN


def _resolve_by(value: _dt.date, today: _dt.date, where: str, field: str = "resolve_by") -> str:
    if value <= today:
        raise ForecastError(f"{where}: {field} must be after today ({today.isoformat()})")
    if (value - today).days > MAX_HORIZON_DAYS:
        raise ForecastError(
            f"{where}: {field} is more than {MAX_HORIZON_DAYS} days out; "
            "this ledger scores near-term calls"
        )
    return value.isoformat()


def _market_fields(
    entry: dict[str, Any], question: dict[str, Any], probability: float, where: str,
) -> dict[str, Any]:
    """Everything about the market comes from the stored question."""
    outcomes = [
        (str(pair[0]), float(pair[1])) for pair in question.get("outcomes") or []
        if isinstance(pair, (list, tuple)) and len(pair) == 2
    ]
    wanted = entry.get("outcome")
    matches = [
        pair for pair in outcomes
        if isinstance(wanted, str) and pair[0].casefold() == wanted.strip().casefold()
    ]
    if len(matches) != 1:
        raise ForecastError(
            f"{where}: outcome must be one of this market's outcomes "
            f"({', '.join(label for label, _ in outcomes) or 'none listed'})"
        )
    label, price = matches[0]
    if _points(probability, price) <= RESTATEMENT_MARKET_MARGIN:
        raise ForecastError(
            f"{where}: {_pct(probability)} on {label!r} restates the market, which prices "
            f"it at {_pct(price)}; a call within {round(RESTATEMENT_MARKET_MARGIN * 100)} "
            "points of the price adds nothing. Show the market's odds as the item to "
            "watch instead."
        )
    market_url = str(question.get("url") or "")
    try:
        parse_market_url(market_url)
    except ForecastError as exc:
        raise ForecastError(f"{where}: {exc}") from exc
    return {
        "resolution": f"The linked Polymarket market resolves {label}",
        "resolution_url": market_url,
        "baseline_kind": "market",
        "baseline_outcome": label,
        "baseline_probability": price,
        "market_url": market_url,
        "market_outcome": label,
        "market_price": price,
    }


def _status_quo_fields(entry: dict[str, Any], probability: float, where: str) -> dict[str, Any]:
    baseline = entry.get("baseline")
    if not isinstance(baseline, dict):
        raise ForecastError(
            f"{where}: baseline must state the status-quo outcome and its probability "
            '({"status_quo": "...", "probability": 0.8})'
        )
    status_quo = _text(
        baseline.get("status_quo"), "baseline.status_quo", where, MAX_BASELINE_OUTCOME,
    )
    status_quo_probability = _probability(
        baseline.get("probability"), "baseline.probability", where, 0.01, MAX_PROBABILITY,
    )
    backs = entry.get("backs")
    if backs not in ("status_quo", "change"):
        raise ForecastError(
            f"{where}: backs must be 'status_quo' (the claim is the status-quo outcome) "
            "or 'change' (the claim is that it breaks)"
        )
    if backs == "status_quo" and probability > RESTATEMENT_STATUS_QUO_CEILING:
        raise ForecastError(
            f"{where}: {_pct(probability)} on the status-quo outcome restates the "
            f"baseline; a call above {_pct(RESTATEMENT_STATUS_QUO_CEILING)} that nothing "
            "changes adds nothing. Show the dated item as the one to watch instead."
        )
    resolution = entry.get("resolution")
    if not isinstance(resolution, dict):
        raise ForecastError(
            f"{where}: resolution must give the page that settles the call and the "
            'field or event on it ({"page": "https://...", "field": "..."})'
        )
    return {
        "resolution": _text(resolution.get("field"), "resolution.field", where, MAX_RESOLUTION),
        "resolution_url": _http_url(resolution.get("page"), "resolution.page", where),
        "baseline_kind": "status_quo",
        "baseline_outcome": status_quo,
        # Stored on the claim's own outcome, like a market price, so the two
        # probabilities in a row are always comparable.
        "baseline_probability": (
            status_quo_probability if backs == "status_quo"
            else round(1 - status_quo_probability, 6)
        ),
    }


def _rehearsal_fields(entry: dict[str, Any], today: _dt.date, where: str) -> dict[str, Any]:
    if entry.get("resolve_by") is None:
        resolve_by = today + _dt.timedelta(days=DEFAULT_HORIZON_DAYS)
    else:
        resolve_by = _iso_date(entry.get("resolve_by"), "resolve_by", where)
    resolve_by_text = _resolve_by(resolve_by, today, where)
    resolution = _text(entry.get("resolution"), "resolution", where, MAX_RESOLUTION)
    basis_raw = entry.get("basis")
    if not isinstance(basis_raw, list) or not basis_raw:
        raise ForecastError(
            f"{where}: basis must list at least one evidence URL from the research"
        )
    if len(basis_raw) > MAX_BASIS_URLS:
        raise ForecastError(f"{where}: basis accepts at most {MAX_BASIS_URLS} URLs")
    return {
        "resolve_by": resolve_by_text,
        "resolution": resolution,
        "basis": [_http_url(url, "basis URL", where) for url in basis_raw],
    }


def validate_predictions(
    payload: dict[str, Any],
    today: _dt.date,
    now: _dt.datetime | None = None,
    *,
    questions: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Turn a host-written forecast file into rows ready for the ledger.

    ``questions`` is the topic's stored open-questions list, or None when it
    is missing or expired; every call that is not a rehearsal must name one
    of its ids. The whole file is rejected on the first problem: a partial
    write would leave the host unsure which calls were logged.
    """
    topic = _text(payload.get("topic"), "topic", "forecast", 200, minimum=2)
    kind = payload.get("kind", "question")
    if kind not in KINDS:
        raise ForecastError(f"forecast: kind must be one of {', '.join(KINDS)}")
    logged_at = (now or _dt.datetime.now(_dt.timezone.utc)).astimezone(_dt.timezone.utc)
    predictions = payload.get("predictions")
    if not isinstance(predictions, list) or not predictions:
        raise ForecastError("forecast: predictions must be a non-empty list")
    if len(predictions) > MAX_PREDICTIONS_PER_ADD:
        raise ForecastError(
            f"forecast: at most {MAX_PREDICTIONS_PER_ADD} predictions per file; "
            "keep the calls you would stake a reputation on"
        )
    if kind == "question" and questions is None:
        raise ForecastError(
            f"forecast: no open questions are stored for {topic!r}; the topic's research "
            "run is missing or has expired. Run the topic again, then resubmit."
        )
    by_id = {str(question.get("id")): question for question in questions or []}
    rows: list[dict[str, Any]] = []
    seen: set[Any] = set()
    for index, entry in enumerate(predictions, start=1):
        where = f"prediction {index}"
        if not isinstance(entry, dict):
            raise ForecastError(f"{where}: must be an object")
        claim = _text(entry.get("claim"), "claim", where, MAX_CLAIM)
        probability = _probability(
            entry.get("probability"), "probability", where, MIN_PROBABILITY, MAX_PROBABILITY,
        )
        row: dict[str, Any] = {
            "topic": topic,
            "kind": kind,
            "claim": claim,
            "probability": probability,
            "created_on": today.isoformat(),
            "created_at": logged_at.isoformat(timespec="seconds"),
            "basis": [],
            "reason": None,
            "question_key": None,
            "question_kind": None,
            "resolution_url": None,
            "baseline_kind": None,
            "baseline_outcome": None,
            "baseline_probability": None,
            "market_url": None,
            "market_outcome": None,
            "market_price": None,
        }
        if kind == "rehearsal":
            row.update(_rehearsal_fields(entry, today, where))
            identity: Any = (claim.casefold(), row["resolve_by"])
        else:
            question_id = entry.get("question")
            if not isinstance(question_id, str) or not question_id.strip():
                raise ForecastError(
                    f"{where}: question must name an open question id from this run's list"
                )
            question = by_id.get(question_id.strip())
            if question is None:
                raise ForecastError(
                    f"{where}: question {question_id.strip()!r} is not in this topic's open "
                    f"questions ({', '.join(by_id) or 'the list is empty'})"
                )
            row["reason"] = _text(entry.get("reason"), "reason", where, MAX_REASON)
            row["question_key"] = str(question.get("key"))
            row["question_kind"] = str(question.get("kind"))
            # The question's own date is the deadline; the host does not pick one.
            row["resolve_by"] = _resolve_by(
                _iso_date(question.get("date"), "the question's date", where),
                today, where, "the question's date",
            )
            if question.get("kind") == "market":
                row.update(_market_fields(entry, question, probability, where))
            else:
                row.update(_status_quo_fields(entry, probability, where))
            identity = row["question_key"]
        if identity in seen:
            raise ForecastError(f"{where}: duplicates an earlier prediction in this file")
        seen.add(identity)
        rows.append(row)
    return rows


def validate_grades(payload: dict[str, Any]) -> list[dict[str, Any]]:
    grades = payload.get("grades")
    if not isinstance(grades, list) or not grades:
        raise ForecastError("grades: must be a non-empty list")
    parsed: list[dict[str, Any]] = []
    seen: set[int] = set()
    for index, entry in enumerate(grades, start=1):
        where = f"grade {index}"
        if not isinstance(entry, dict):
            raise ForecastError(f"{where}: must be an object")
        forecast_id = entry.get("id")
        if isinstance(forecast_id, bool) or not isinstance(forecast_id, int) or forecast_id < 1:
            raise ForecastError(f"{where}: id must be a prediction id from 'forecast due'")
        if forecast_id in seen:
            raise ForecastError(f"{where}: prediction #{forecast_id} is graded twice in this file")
        seen.add(forecast_id)
        verdict = entry.get("verdict")
        if verdict not in VERDICTS:
            raise ForecastError(f"{where}: verdict must be one of {', '.join(VERDICTS)}")
        note = entry.get("note")
        if note is not None:
            note = _text(note, "note", where, MAX_NOTE, minimum=1)
        evidence_url = entry.get("evidence_url")
        if verdict == "void":
            if not note:
                raise ForecastError(f"{where}: a void verdict needs a note explaining why")
            if evidence_url is not None:
                evidence_url = _http_url(evidence_url, "evidence_url", where)
        else:
            # A hit or a miss without a link is an opinion, not a grade.
            evidence_url = _http_url(evidence_url, "evidence_url", where)
        parsed.append({
            "id": forecast_id, "verdict": verdict,
            "evidence_url": evidence_url, "note": note,
        })
    return parsed


def grade_refusal(row: dict[str, Any] | None, grade: dict[str, Any], today: _dt.date) -> str | None:
    """Why this grade cannot be written, or None when it can."""
    if row is None:
        return f"no prediction #{grade['id']} in this ledger"
    if row.get("verdict"):
        return (
            f"prediction #{row['id']} was already graded {row['verdict']} on "
            f"{row.get('graded_on')}; verdicts are written once"
        )
    if grade["verdict"] == "miss" and today < _dt.date.fromisoformat(row["resolve_by"]):
        return (
            f"prediction #{row['id']} resolves by {row['resolve_by']}; it cannot be "
            "graded a miss before its deadline (a hit can land early)"
        )
    return None


# --- market settlement ------------------------------------------------------


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    return value if isinstance(value, list) else []


def _pick_market(event: dict[str, Any], market_slug: str | None) -> dict[str, Any] | None:
    markets = [m for m in (event.get("markets") or []) if isinstance(m, dict)]
    if market_slug:
        for market in markets:
            if str(market.get("slug") or "").strip() == market_slug:
                return market
        return None
    # Without a market slug, a multi-market event ("who wins?" with one
    # Yes/No market per candidate) is ambiguous: "Yes" names 39 markets.
    return markets[0] if len(markets) == 1 else None


def _parse_instant(value: Any) -> _dt.datetime | None:
    """Parse Gamma's ``2026-08-08 20:31:58+00`` or an ISO timestamp to UTC."""
    text = str(value or "").strip().replace(" ", "T", 1)
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    elif len(text) > 3 and text[-3] in "+-" and text[-2:].isdigit():
        text += ":00"
    try:
        parsed = _dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed.astimezone(_dt.timezone.utc)


def _closed_before_logging(closed_time: Any, row: dict[str, Any]) -> bool:
    """Whether the market's result was already in when the call was logged.

    Compared to the second: a market that closed at 08:00 and was "predicted"
    at noon the same day is a known result. When either timestamp cannot be
    read, fall back to dates and treat a same-day close as already known,
    since the order within the day cannot be established.
    """
    closed_at = _parse_instant(closed_time)
    logged_at = _parse_instant(row.get("created_at"))
    if closed_at is not None and logged_at is not None:
        return closed_at <= logged_at
    closed_on = str(closed_time or "")[:10]
    return bool(closed_on) and closed_on <= row["created_on"]


def settle_from_event(row: dict[str, Any], events: Any) -> tuple[str, str] | None:
    """Derive (verdict, note) from a Gamma ``/events?slug=`` payload.

    Returns None whenever the market cannot settle the prediction yet or
    cannot be trusted to: still open, wrong event, ambiguous market, unknown
    outcome label. None always means "leave it for a human-readable grade",
    never "miss".

    A resolved market settles the call whenever it resolves, including a
    miss before ``resolve_by``. The host-grading rule that a miss must wait
    for the deadline exists because an unfinished claim can still come true;
    a market that has resolved against the call cannot, and holding the miss
    back would show a lost call as open.
    """
    event_slug, market_slug = parse_market_url(row["market_url"])
    if isinstance(events, dict):
        events = events.get("events") if isinstance(events.get("events"), list) else [events]
    event = next(
        (
            entry for entry in (events if isinstance(events, list) else [])
            if isinstance(entry, dict) and str(entry.get("slug") or "").strip() == event_slug
        ),
        None,
    )
    if event is None:
        return None
    # A slug can be reused by a later event. One created after the prediction
    # was logged is not the market the prediction was anchored to.
    created = str(event.get("createdAt") or "")[:10]
    if created and created > row["created_on"]:
        return None
    market = _pick_market(event, market_slug)
    if market is None or not market.get("closed"):
        return None
    question = str(market.get("question") or event.get("title") or "market").strip()
    if _closed_before_logging(market.get("closedTime"), row):
        # Calling a result that is already in is not a forecast. Void keeps
        # it out of the hit rate instead of padding the record.
        return "void", f"Polymarket had already closed '{question}' when this was logged"
    outcomes = [str(name) for name in _json_list(market.get("outcomes"))]
    prices_raw = _json_list(market.get("outcomePrices"))
    if not outcomes or len(outcomes) != len(prices_raw):
        return None
    try:
        prices = [float(price) for price in prices_raw]
    except (TypeError, ValueError):
        return None
    wanted = str(row["market_outcome"]).casefold()
    matches = [i for i, name in enumerate(outcomes) if name.casefold() == wanted]
    if len(matches) != 1:
        return None
    price = prices[matches[0]]
    if price >= _RESOLVED_WIN:
        return "hit", f"Polymarket resolved '{question}' to {outcomes[matches[0]]}"
    if price <= _RESOLVED_LOSS and max(prices) >= _RESOLVED_WIN:
        winner = outcomes[prices.index(max(prices))]
        return "miss", f"Polymarket resolved '{question}' to {winner}"
    return "void", f"Polymarket closed '{question}' without a clear winner"


def settle_open_markets(
    rows: Iterable[dict[str, Any]],
    fetch_events: Callable[[str], Any],
    *,
    deadline: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[
    list[tuple[dict[str, Any], str, str]],
    list[tuple[dict[str, Any], str]],
    list[dict[str, Any]],
]:
    """Check every open market-anchored prediction, one request per event.

    Returns (settled, unreachable, unchecked). A fetch failure is reported,
    never swallowed into "still open", so an outage is not mistaken for a
    market that has not resolved. ``deadline`` is a ``clock`` reading after
    which no further event is requested; the predictions left over come back
    as unchecked.
    """
    settled: list[tuple[dict[str, Any], str, str]] = []
    unreachable: list[tuple[dict[str, Any], str]] = []
    unchecked: list[dict[str, Any]] = []
    # event slug -> (events, None) or (None, failure); None once out of time.
    fetched: dict[str, tuple[Any, str | None] | None] = {}
    for row in rows:
        if row.get("verdict") or not row.get("market_url"):
            continue
        event_slug, _ = parse_market_url(row["market_url"])
        if event_slug not in fetched:
            if deadline is not None and clock() >= deadline:
                fetched[event_slug] = None
            else:
                try:
                    fetched[event_slug] = (fetch_events(event_slug), None)
                except Exception as exc:  # network, HTTP, or decode failure
                    fetched[event_slug] = (None, f"{type(exc).__name__}: {exc}")
        result = fetched[event_slug]
        if result is None:
            unchecked.append(row)
            continue
        events, failure = result
        if failure is not None:
            unreachable.append((row, failure))
            continue
        verdict = settle_from_event(row, events)
        if verdict is not None:
            settled.append((row, verdict[0], verdict[1]))
    return settled, unreachable, unchecked


# --- earlier calls ----------------------------------------------------------


def earlier_calls(
    open_rows: list[dict[str, Any]],
    graded: dict[int, dict[str, Any]],
    today: _dt.date,
    *,
    other_due: int = 0,
    warnings: Iterable[str] = (),
) -> dict[str, Any] | None:
    """What a repeat run shows about the topic's earlier predictions.

    ``open_rows`` are the topic's calls that were open when the run began and
    ``graded`` holds the ones this run settled, by id. Returns plain data for
    the report's artifacts, or None when there is nothing to show.
    """
    calls = []
    for row in open_rows:
        row = graded.get(row["id"], row)
        if row.get("verdict"):
            status = row["verdict"]
        elif _dt.date.fromisoformat(row["resolve_by"]) <= today:
            status = "due"
        else:
            status = "open"
        calls.append({
            "id": row["id"],
            "status": status,
            "claim": row["claim"],
            "probability": float(row["probability"]),
            "created_on": row["created_on"],
            "resolve_by": row["resolve_by"],
            "resolution": row.get("resolution"),
            "resolution_url": row.get("resolution_url"),
            "market_url": row.get("market_url"),
            "reason": row.get("reason"),
            "note": row.get("note"),
        })
    notes = [str(warning) for warning in warnings]
    if not calls and not other_due:
        return None
    return {"calls": calls, "warnings": notes, "other_due": other_due}


# --- scorecard --------------------------------------------------------------


def _brier(pairs: list[tuple[float, int]]) -> float | None:
    if not pairs:
        return None
    return sum((p - outcome) ** 2 for p, outcome in pairs) / len(pairs)


def is_overdue(row: dict[str, Any], today: _dt.date) -> bool:
    return not row.get("verdict") and _dt.date.fromisoformat(row["resolve_by"]) < today


def _outcome_pairs(rows: list[dict[str, Any]], field: str) -> list[tuple[float, int]]:
    return [(float(r[field]), 1 if r["verdict"] == "hit" else 0) for r in rows]


def _record(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"graded": len(rows), "hits": sum(1 for r in rows if r["verdict"] == "hit")}


def _skill(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Record and Brier skill of graded calls against their stored baselines.

    Skill is 1 - ours / baseline: above zero the calls beat the baseline,
    below zero the baseline was closer. None when the baseline scored a
    perfect zero, which leaves nothing to be measured against.
    """
    ours = _brier(_outcome_pairs(rows, "probability"))
    baseline = _brier(_outcome_pairs(rows, "baseline_probability"))
    return {
        **_record(rows),
        "ours": ours,
        "baseline": baseline,
        "skill": (1 - ours / baseline) if rows and baseline else None,
    }


def _kind_count(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "total": len(rows),
        **_record([r for r in rows if r.get("verdict") in ("hit", "miss")]),
    }


def scorecard(rows: list[dict[str, Any]], today: _dt.date) -> dict[str, Any]:
    graded = [r for r in rows if r.get("verdict") in ("hit", "miss")]
    hits = sum(1 for r in graded if r["verdict"] == "hit")
    pairs = _outcome_pairs(graded, "probability")
    buckets = []
    for low, high in CALIBRATION_BUCKETS:
        members = [(p, o) for p, o in pairs if low <= p < high]
        if not members:
            continue
        buckets.append({
            "low": low,
            "high": high,
            "count": len(members),
            "predicted": sum(p for p, _ in members) / len(members),
            "actual": sum(o for _, o in members) / len(members),
        })
    # A rehearsal call has no baseline by design; any other call without one
    # was logged before baselines were recorded.
    rehearsal = [r for r in rows if r.get("kind") == "rehearsal"]
    legacy = [
        r for r in rows
        if r.get("kind") != "rehearsal" and r.get("baseline_probability") is None
    ]
    measured = [
        r for r in graded
        if r.get("kind") != "rehearsal" and r.get("baseline_probability") is not None
    ]
    departed = [r for r in measured if departs_from_baseline(r)]
    return {
        "total": len(rows),
        "open": sum(1 for r in rows if not r.get("verdict")),
        "overdue": sum(1 for r in rows if is_overdue(r, today)),
        "graded": len(graded),
        "hits": hits,
        "misses": len(graded) - hits,
        "voids": sum(1 for r in rows if r.get("verdict") == "void"),
        "hit_rate": (hits / len(graded)) if graded else None,
        "brier": _brier(pairs),
        "calibration": buckets,
        # The headline. A market price is a baseline nobody chose; a host-stated
        # status quo is one the forecaster picked, so it is scored apart.
        "departed_market": _skill(
            [r for r in departed if r.get("baseline_kind") == "market"]
        ),
        "departed_status_quo": _skill(
            [r for r in departed if r.get("baseline_kind") != "market"]
        ),
        "agreed": _record([r for r in measured if not departs_from_baseline(r)]),
        "legacy": _kind_count(legacy),
        "rehearsal": _kind_count(rehearsal),
        "small_sample": len(graded) < MIN_SAMPLE,
    }


# --- rendering --------------------------------------------------------------


def _pct(value: float) -> str:
    return f"{round(value * 100)}%"


def render_prediction(row: dict[str, Any], today: _dt.date) -> str:
    if row.get("verdict"):
        status = (
            f"{row['verdict'].upper()} (due {row['resolve_by']}, graded "
            f"{row.get('graded_on')} by {row.get('graded_by')})"
        )
    elif is_overdue(row, today):
        status = f"OVERDUE (was due {row['resolve_by']})"
    else:
        status = f"open (due {row['resolve_by']})"
    legacy = (
        " | legacy call, logged before baselines were recorded"
        if row.get("kind") == "legacy" else ""
    )
    lines = [
        f"#{row['id']} [{status}] {_pct(float(row['probability']))} - {row['claim']}",
        f"    topic: {row['topic']} | logged {row['created_on']}{legacy}",
        f"    resolves when: {row['resolution']}",
    ]
    if row.get("baseline_kind") == "status_quo":
        lines.append(f"    resolution page: {row['resolution_url']}")
        lines.append(
            f"    baseline: {_pct(float(row['baseline_probability']))} "
            f"(status quo: {row['baseline_outcome']})"
        )
    if row.get("reason"):
        lines.append(f"    why: {row['reason']}")
    if row.get("market_url"):
        price = (
            f", market priced it at {_pct(float(row['market_price']))} when logged"
            if row.get("market_price") is not None else ""
        )
        lines.append(f"    market: {row['market_url']} -> {row['market_outcome']}{price}")
    if row.get("verdict"):
        if row.get("evidence_url"):
            lines.append(f"    evidence: {row['evidence_url']}")
        if row.get("note"):
            lines.append(f"    note: {row['note']}")
    return "\n".join(lines)


def render_list(rows: list[dict[str, Any]], today: _dt.date, heading: str) -> str:
    if not rows:
        return f"{heading}: none."
    return "\n".join([f"{heading} ({len(rows)}):", ""] + [render_prediction(r, today) for r in rows])


def _skill_text(group: dict[str, Any], baseline_label: str) -> str:
    scores = f"(ours {group['ours']:.3f}, {baseline_label} {group['baseline']:.3f} on Brier)"
    if group["skill"] is None:
        return f"not defined {scores}"
    return f"{group['skill']:+.2f} {scores}"


def _kind_lines(card: dict[str, Any]) -> list[str]:
    lines = []
    for key, label in (
        ("legacy", "Legacy calls (logged before baselines were recorded)"),
        ("rehearsal", "Rehearsal calls"),
    ):
        group = card[key]
        if group["total"]:
            lines.append(
                f"{label}: {group['total']} logged, {group['hits']} of "
                f"{group['graded']} graded right. No skill figure."
            )
    return lines


def render_scorecard(card: dict[str, Any], today: _dt.date) -> str:
    lines = [f"Next30Days scorecard - as of {today.isoformat()}", ""]
    if card["total"] == 0:
        lines.append("No predictions logged yet. Ask for a forecast on any topic to start the ledger.")
        return "\n".join(lines)
    lines.append(
        f"Logged: {card['total']} | Open: {card['open']} | Graded: {card['graded']} "
        f"| Void: {card['voids']}"
    )
    if card["overdue"]:
        lines.append(
            f"Overdue and ungraded: {card['overdue']} - the numbers below exclude "
            "them, so grade these before trusting the record."
        )
    if not card["graded"]:
        lines.append("Nothing graded yet, so there is no track record to report.")
        lines.extend(_kind_lines(card))
        return "\n".join(lines)
    if card["small_sample"]:
        lines.append(
            f"Small sample: fewer than {MIN_SAMPLE} graded calls. Treat this as "
            "an early read, not a track record."
        )
    market, status_quo, agreed = (
        card["departed_market"], card["departed_status_quo"], card["agreed"],
    )
    if market["graded"]:
        lines.extend([
            "",
            f"Calls that departed from a market baseline: {market['hits']} of "
            f"{market['graded']} right",
            f"Skill against the market: {_skill_text(market, 'market')}. Above 0 "
            "beats the market's price at logging.",
        ])
        if market["skill"] is not None and market["skill"] < 0:
            lines.append("The market was closer than these calls.")
    if status_quo["graded"]:
        lines.extend([
            "",
            f"Calls that departed from a host-stated baseline: {status_quo['hits']} of "
            f"{status_quo['graded']} right, skill {_skill_text(status_quo, 'baseline')}. "
            "The forecaster chose this baseline, so it is not part of the market figure.",
        ])
    if agreed["graded"]:
        lines.extend([
            "",
            f"Calls that agreed with their baseline: {agreed['hits']} of "
            f"{agreed['graded']} right. Not counted in either skill figure.",
        ])
    kind_lines = _kind_lines(card)
    if kind_lines:
        lines.extend(["", *kind_lines])
    lines.extend([
        "",
        "All graded calls:",
        f"Hit rate: {_pct(card['hit_rate'])} ({card['hits']} of {card['graded']})",
        f"Brier score: {card['brier']:.3f} (0 is perfect; 0.250 is what always "
        "saying 50% scores)",
    ])
    if card["calibration"]:
        lines.extend(["", "Calibration (said vs happened):"])
        for bucket in card["calibration"]:
            lines.append(
                f"  {_pct(bucket['low'])}-{round(bucket['high'] * 100) - 1}% calls: "
                f"said {_pct(bucket['predicted'])}, happened {_pct(bucket['actual'])} "
                f"(n={bucket['count']})"
            )
    return "\n".join(lines)
