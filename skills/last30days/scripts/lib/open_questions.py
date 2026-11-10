"""Open Questions: the dated, unresolved items a run can make a prediction on.

A run's list is merged from three places: live prediction markets the run
retrieved, a host-written envelope of dated items (``--open-questions``), and
the engine's own forward-looking web results. ``build`` drops excluded
subjects and anything whose date has passed, removes duplicates, ranks the
rest soonest first, and gives each entry an ``id`` (its position in this
run's list) and a ``key`` that stays the same across runs.

Envelope shape (``schema: "last30days-open-questions/1"``)::

    {
      "schema": "last30days-open-questions/1",
      "items": [
        {"kind": "event" | "decision" | "release",
         "question": "<one line>", "date": "YYYY-MM-DD", "url": "https://..."}
      ]
    }

The envelope is strict at the top level (a malformed or oversized file is a
contract error, exit 2) and lenient per row (a bad row is dropped and
counted). Market entries are never taken from the envelope: the engine fills
them from the markets it retrieved.

One entry::

    {"id": "Q1", "key": "market:<slug>" | "<kind>:<date>:<canonical link>",
     "kind": "market" | "event" | "decision" | "release",
     "origin": "market" | "host" | "web",
     "question": "<one line>", "date": "YYYY-MM-DD", "url": "<source link>",
     "outcomes": [["Yes", 0.62], ...],   # markets only
     "related": ["Q3"]}                  # a market and a dated item on one question

Question text can come from web pages. This module keeps it to one line; the
renderer defangs it and prints it only inside the evidence envelope.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlparse

from . import env
from .private_files import write_private_text

SCHEMA = "last30days-open-questions/1"
LIST_SCHEMA = "last30days-open-questions-list/1"
ARTIFACT_KEY = "open_questions"
STORE_DIRNAME = "open-questions"

MAX_BYTES = 256 * 1024
MAX_ROWS = 100
MAX_QUESTION_CHARS = 300
MAX_URL_CHARS = 2000
MAX_ENTRIES = 12
# A date written without a year means its next occurrence, but only this
# close: "announced November 3" read a week later is not next November.
YEARLESS_HORIZON_DAYS = 180

KINDS = ("market", "event", "decision", "release")
HOST_KINDS = ("event", "decision", "release")
COUNTERS = (
    "bad-row", "bad-link", "undated", "past", "no-future-date", "excluded",
    "unpriced", "same-event", "over-cap",
)
# Dated items the host or the web pass found are the topic's own calendar;
# a sports or election topic can have hundreds of markets, so this many list
# places are theirs before any market is considered.
RESERVED_FOR_DATED_ITEMS = 6
# One event can hold dozens of near-identical markets (a ladder of dates or
# of candidates). Only the most undecided few are worth a prediction; the
# rest would crowd every other question out of the list.
MAX_MARKETS_PER_EVENT = 2
# A market priced this far to one side is all but settled: still worth
# showing, but behind every question that is actually open.
SETTLED_PRICE = 0.95

# The engine's forward-looking web queries. The host runs the same ones in
# its own search and passes the results through the envelope.
FORWARD_QUERIES = (
    "{topic} upcoming dates deadline",
    "{topic} scheduled announcement release date",
    "{topic} decision vote ruling expected",
)

_REMEDY = (
    "Rewrite the file to the last30days-open-questions/1 shape, "
    "or re-run without --open-questions."
)

# Subjects that are never an open question: a death, a person's health,
# violence against a person, a private individual's personal life. The
# engine cannot tell a public figure from a private one, so personal-life
# terms exclude either.
# A person's death, health, or personal life, and violence against a person.
# Disease and surgery words alone are not excluded: a drug approval or a
# health-policy vote is about a subject, not about someone's health.
_EXCLUDED_RE = re.compile(
    r"\b(?:"
    r"died|dying|death|deceased|pass(?:es)? away|suicide|obituar\w*|funeral"
    r"|killed|killing|murder\w*|assassinat\w*|shot|shooting|stabb\w*|kidnap\w*"
    r"|abduct\w*|assault\w*|beaten|executed|raped?"
    r"|hospitali[sz]\w*|diagnosed with|undergo\w* surgery|terminally ill|in a coma"
    r"|overdose\w*|rehab|health (?:scare|condition|issues?|problems?|crisis)"
    r"|(?:his|her|their) (?:health|illness|cancer|stroke|dementia|surgery)"
    r"|medical (?:leave|condition|emergency)"
    r"|custody|divorc\w*|paternity|pregnan\w*|affair|marry|marries|married|wedding"
    r"|engaged to|broke up with|breaks up with|restraining order"
    r")\b|'s (?:health|illness|cancer|stroke|dementia|surgery)\b",
    re.IGNORECASE,
)
# "die" and "dead" are about a person unless the sentence is plainly about a
# bill, a deal, or a product (bills die in committee; a deal is dead).
_DIE_RE = re.compile(r"\b(?:die|dies|dead)\b", re.IGNORECASE)
_NOT_A_PERSON_RE = re.compile(
    r"\b(?:bills?|legislation|measures?|amendments?|deals?|proposals?|mergers?|projects?"
    r"|products?|features?|apps?)\b",
    re.IGNORECASE,
)


def _is_excluded(subject: str) -> bool:
    if _EXCLUDED_RE.search(subject):
        return True
    return bool(_DIE_RE.search(subject)) and not _NOT_A_PERSON_RE.search(subject)

_DECISION_RE = re.compile(
    r"\b(?:ruling|rules?|decision|decides?|vote|verdict|deadline|hearing|approval|sentencing)\b",
    re.IGNORECASE,
)
_RELEASE_RE = re.compile(
    r"\b(?:release[sd]?|launch(?:es|ed)?|ships?|shipping|rollout|premieres?|debuts?)\b",
    re.IGNORECASE,
)

_MONTH = (
    r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?"
    r"|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?"
)
_DAY = r"(\d{1,2})(?:st|nd|rd|th)?"
_YEAR = r"(?:,?\s+(20\d{2}))?"
# Month names are matched capitalized only, so "may" and "march" as verbs
# never read as dates.
_MONTH_DAY_RE = re.compile(rf"\b{_MONTH}\s+{_DAY}{_YEAR}\b")
# Day-first dates must carry a year: "Top 10 March Madness picks" is not a date.
_DAY_MONTH_RE = re.compile(rf"\b{_DAY}\s+(?:of\s+)?{_MONTH},?\s+(20\d{{2}})\b")
_ISO_RE = re.compile(r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?!\d)")
_MONTH_NUMBERS = {
    name: number for number, name in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"),
        start=1,
    )
}

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_TOKEN_RE = re.compile(r"[a-z0-9]{4,}")
_TOPIC_STRIP = "\"'“”‘’ \t"


class EnvelopeContractError(Exception):
    """The ``--open-questions`` file failed its contract. The CLI maps this
    to exit code 2. The message names the path and the field, never a value."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


# ---------------------------------------------------------------------------
# Host envelope
# ---------------------------------------------------------------------------


def parse_envelope(text: str) -> list[Any]:
    """Check the envelope's top level and return its rows, unvalidated."""
    try:
        payload = json.loads(text)
    except (RecursionError, MemoryError):
        raise EnvelopeContractError("is too deeply nested or too large to parse") from None
    except ValueError:
        raise EnvelopeContractError("is not valid JSON") from None
    if not isinstance(payload, dict):
        raise EnvelopeContractError("must be a JSON object")
    if payload.get("schema") != SCHEMA:
        raise EnvelopeContractError('has a missing or unsupported "schema" field')
    rows = payload.get("items")
    if not isinstance(rows, list):
        raise EnvelopeContractError('has an "items" field that is not a list')
    if len(rows) > MAX_ROWS:
        raise EnvelopeContractError(f'has more than {MAX_ROWS} entries in "items"')
    return rows


def read_envelope(path: str | os.PathLike[str]) -> list[Any]:
    """Read and check a ``--open-questions`` file; return its rows.

    Raises ``EnvelopeContractError`` for an unreadable, oversized, or
    malformed file. Rows are validated later, one by one, in ``build``.
    """
    raw_path = os.fspath(path)

    def fail(what: str) -> EnvelopeContractError:
        return EnvelopeContractError(f"--open-questions file {raw_path} {what}. {_REMEDY}")

    try:
        info = os.stat(raw_path)
    except OSError as exc:
        raise fail(f"cannot be read ({type(exc).__name__})") from None
    if not stat.S_ISREG(info.st_mode):
        raise fail("is not a regular file")
    if info.st_size > MAX_BYTES:
        raise fail(f"exceeds the {MAX_BYTES // 1024} KiB limit")
    try:
        text = Path(raw_path).read_bytes().decode("utf-8")
    except OSError as exc:
        raise fail(f"cannot be read ({type(exc).__name__})") from None
    except UnicodeDecodeError:
        raise fail("is not valid UTF-8") from None
    try:
        return parse_envelope(text)
    except EnvelopeContractError as exc:
        raise fail(exc.message) from None


# ---------------------------------------------------------------------------
# Field helpers
# ---------------------------------------------------------------------------


def _one_line(value: Any, limit: int = MAX_QUESTION_CHARS) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(_CONTROL_RE.sub(" ", value).split())[:limit].strip()


def _http_url(value: Any) -> str:
    """The link when it is a plain http or https URL, else an empty string."""
    if not isinstance(value, str):
        return ""
    url = value.strip()
    if not url or len(url) > MAX_URL_CHARS or _CONTROL_RE.search(url) or " " in url:
        return ""
    try:
        parsed = urlparse(url)
    except ValueError:
        return ""
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        return ""
    return url


def canonical_link(url: str) -> str:
    """Scheme-less form of a link, stable across the ways one page is cited."""
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    query = urlencode([
        (name, value) for name, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not name.lower().startswith("utm_")
    ])
    return host + parsed.path.rstrip("/") + (f"?{query}" if query else "")


def _day(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _calendar_day(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_future_date(text: str, today: str) -> str | None:
    """The earliest explicit calendar date in ``text`` that is after ``today``.

    Reads "December 1, 2026", "1 Dec 2026", "2026-12-01", and a month and day
    without a year when its next occurrence is close. A month alone, a
    relative phrase, or a numeric slash date is not an explicit date.
    """
    now = date.fromisoformat(today)
    found: list[date] = []

    def add(year: str | None, month: int, day: int) -> None:
        if year:
            candidate = _calendar_day(int(year), month, day)
        else:
            candidate = _calendar_day(now.year, month, day)
            if candidate is not None and candidate <= now:
                candidate = _calendar_day(now.year + 1, month, day)
            if candidate is not None and (candidate - now).days > YEARLESS_HORIZON_DAYS:
                candidate = None
        if candidate is not None and candidate > now:
            found.append(candidate)

    for match in _ISO_RE.finditer(text):
        add(match.group(1), int(match.group(2)), int(match.group(3)))
    for match in _MONTH_DAY_RE.finditer(text):
        add(match.group(3), _MONTH_NUMBERS[match.group(1)[:3].lower()], int(match.group(2)))
    for match in _DAY_MONTH_RE.finditer(text):
        add(match.group(3), _MONTH_NUMBERS[match.group(2)[:3].lower()], int(match.group(1)))
    return min(found).isoformat() if found else None


def _web_kind(text: str) -> str:
    if _DECISION_RE.search(text):
        return "decision"
    if _RELEASE_RE.search(text):
        return "release"
    return "event"


def markets_from_items(items: Iterable[Any]) -> list[dict[str, Any]]:
    """Flatten Polymarket source items into one market list, loudest event first."""
    ordered = sorted(
        items, key=lambda item: (item.engagement or {}).get("volume") or 0, reverse=True,
    )
    return [
        {
            "event_url": item.url,
            "event_title": item.title,
            "slug": market.get("slug"),
            "question": market.get("question"),
            "end_date": market.get("end_date"),
            "outcome_prices": market.get("outcome_prices") or [],
        }
        for item in ordered
        for market in (item.metadata.get("markets") or [])
        if isinstance(market, dict)
    ]


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def _outcomes(raw: Any) -> list[list[Any]]:
    outcomes: list[list[Any]] = []
    for pair in raw if isinstance(raw, (list, tuple)) else []:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            continue
        name, price = _one_line(pair[0], 80), pair[1]
        if name and isinstance(price, (int, float)) and not isinstance(price, bool):
            outcomes.append([name, float(price)])
    return outcomes


def _undecidedness(outcomes: list[list[Any]]) -> float:
    """1.0 for an even market, 0.0 for a settled one or one with no prices."""
    prices = [price for _, price in outcomes]
    if not prices:
        return 0.0
    return max(0.0, 1.0 - abs(max(prices) - 0.5) * 2)


def _is_unpriced(outcomes: list[list[Any]]) -> bool:
    prices = [price for _, price in outcomes]
    return len(prices) >= 2 and all(abs(price - 1 / len(prices)) < 0.005 for price in prices)


def _is_settled(entry: dict[str, Any]) -> bool:
    prices = [price for _, price in entry.get("outcomes") or []]
    return bool(prices) and max(prices) >= SETTLED_PRICE


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def build(
    *,
    today: str,
    markets: Iterable[dict[str, Any]] = (),
    host_rows: Iterable[Any] = (),
    web_results: Iterable[dict[str, Any]] = (),
    warnings: Iterable[str] = (),
) -> dict[str, Any]:
    """Merge the three inputs into the run's ranked, id-bearing list.

    Returns ``{"entries": [...], "dropped": {counter: n}, "warnings": [...]}``.
    ``today`` is the run's end date; a market or host item dated before it has
    passed, and a web result must name a date after it.
    """
    now = date.fromisoformat(today)
    dropped = dict.fromkeys(COUNTERS, 0)
    kept: list[dict[str, Any]] = []
    seen: set[Any] = set()

    def admit(entry: dict[str, Any], identity: Any, subject: str) -> None:
        if _is_excluded(subject):
            dropped["excluded"] += 1
        elif identity not in seen:
            seen.add(identity)
            kept.append(entry)

    candidates: list[tuple[str, float, dict[str, Any], Any, str]] = []
    # Markets arrive loudest event first; that order decides which events
    # get the list places left after the dated items.
    event_rank: dict[str, int] = {}
    for market in markets:
        slug = _one_line(market.get("slug"), 200)
        question = _one_line(market.get("question"))
        event_url = _http_url(market.get("event_url"))
        if not slug or not question or not event_url:
            dropped["bad-row"] += 1
            continue
        day = _day(market.get("end_date"))
        if day is None:
            dropped["undated"] += 1
            continue
        if day < now:
            dropped["past"] += 1
            continue
        outcomes = _outcomes(market.get("outcome_prices"))
        if _is_unpriced(outcomes):
            # A market sitting at exactly even odds has not been traded: it
            # is a placeholder, and any outside number would look like an edge.
            dropped["unpriced"] += 1
            continue
        event_rank.setdefault(event_url, len(event_rank))
        candidates.append((
            event_url,
            _undecidedness(outcomes),
            {
                "key": f"market:{slug}", "kind": "market", "origin": "market",
                "question": question, "date": day.isoformat(),
                "url": f"{event_url.rstrip('/')}/{slug}",
                "outcomes": outcomes,
            },
            ("market", slug),
            f"{question} {_one_line(market.get('event_title'))}",
        ))
    per_event: dict[str, int] = {}
    loudness: dict[str, int] = {}
    # Most undecided first, so each event's quota goes to its open questions.
    for event_url, _, entry, identity, subject in sorted(
        candidates, key=lambda candidate: -candidate[1],
    ):
        if per_event.get(event_url, 0) >= MAX_MARKETS_PER_EVENT:
            dropped["same-event"] += 1
            continue
        before = len(kept)
        admit(entry, identity, subject)
        if len(kept) > before:
            per_event[event_url] = per_event.get(event_url, 0) + 1
            loudness[entry["key"]] = event_rank[event_url]

    for row in host_rows:
        question = _one_line(row.get("question")) if isinstance(row, dict) else ""
        if not question or row.get("kind") not in HOST_KINDS:
            dropped["bad-row"] += 1
            continue
        url = _http_url(row.get("url"))
        if not url:
            dropped["bad-link"] += 1
            continue
        day = _day(row.get("date"))
        if day is None:
            dropped["undated"] += 1
            continue
        if day < now:
            dropped["past"] += 1
            continue
        link = canonical_link(url)
        admit(
            {
                "key": f"{row['kind']}:{day.isoformat()}:{link}", "kind": row["kind"],
                "origin": "host", "question": question, "date": day.isoformat(), "url": url,
            },
            (day.isoformat(), link),
            question,
        )

    for result in web_results:
        title = _one_line(result.get("title"))
        url = _http_url(result.get("url"))
        if not title or not url:
            dropped["bad-link"] += 1
            continue
        text = f"{title} {_one_line(result.get('snippet'), 2000)}"
        # The result's own publication date is never the question's date.
        day = parse_future_date(text, today)
        if day is None:
            dropped["no-future-date"] += 1
            continue
        kind, link = _web_kind(text), canonical_link(url)
        admit(
            {
                "key": f"{kind}:{day}:{link}", "kind": kind, "origin": "web",
                "question": title, "date": day, "url": url,
            },
            (day, link),
            text,
        )

    dated = sorted(
        (entry for entry in kept if entry["kind"] != "market"), key=lambda entry: entry["date"],
    )[:RESERVED_FOR_DATED_ITEMS]
    market_entries = sorted(
        (entry for entry in kept if entry["kind"] == "market"),
        key=lambda entry: (_is_settled(entry), loudness[entry["key"]], entry["date"]),
    )
    chosen = dated + market_entries[: MAX_ENTRIES - len(dated)]
    # Leftover places go to the next dated items rather than staying empty.
    chosen_ids = {id(entry) for entry in chosen}
    spare = sorted(
        (entry for entry in kept if id(entry) not in chosen_ids), key=lambda entry: entry["date"],
    )
    chosen += spare[: MAX_ENTRIES - len(chosen)]
    entries = sorted(
        chosen,
        key=lambda entry: (_is_settled(entry), entry["date"], entry["kind"] != "market"),
    )
    dropped["over-cap"] = len(kept) - len(entries)
    entries = [{"id": f"Q{position}", **entry, "related": []} for position, entry in enumerate(entries, 1)]
    # A market and a dated item on the same question are both kept; each
    # names the other so a prediction can cite the market as its baseline.
    for market in (entry for entry in entries if entry["kind"] == "market"):
        for other in (entry for entry in entries if entry["kind"] != "market"):
            shared = _tokens(market["question"]) & _tokens(other["question"])
            if other["date"] == market["date"] and len(shared) >= 2:
                market["related"].append(other["id"])
                other["related"].append(market["id"])
    return {"entries": entries, "dropped": dropped, "warnings": list(warnings)}


# ---------------------------------------------------------------------------
# Per-topic file
# ---------------------------------------------------------------------------


def normalize_topic(topic: str) -> str:
    cleaned = _CONTROL_RE.sub("", str(topic or "")).strip(_TOPIC_STRIP).casefold()
    return " ".join(cleaned.split())


def _topic_path(topic: str, config_dir: Path) -> Path:
    digest = hashlib.sha256(normalize_topic(topic).encode("utf-8")).hexdigest()[:32]
    return config_dir / STORE_DIRNAME / f"{digest}.json"


def store(
    topic: str, entries: list[dict[str, Any]], *, config_dir: Path | None = None,
) -> Path | None:
    """Write a topic's list to its own owner-only file; return the path.

    One file per normalized topic, so a run on another topic never replaces
    it. Returns ``None`` when there is no configuration directory.
    """
    config_dir = config_dir or env.CONFIG_DIR
    if config_dir is None:
        return None
    path = _topic_path(topic, Path(config_dir))
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = {
        "schema": LIST_SCHEMA,
        "topic": normalize_topic(topic),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "entries": entries,
    }
    write_private_text(path, json.dumps(payload, indent=2), encoding="utf-8")
    return path


def load(
    topic: str, ttl_seconds: int, *, config_dir: Path | None = None,
) -> list[dict[str, Any]] | None:
    """A topic's stored list, or ``None`` when absent, unreadable, or older
    than ``ttl_seconds`` (the report cache's lifetime)."""
    config_dir = config_dir or env.CONFIG_DIR
    if config_dir is None:
        return None
    try:
        payload = json.loads(_topic_path(topic, Path(config_dir)).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != LIST_SCHEMA
        or payload.get("topic") != normalize_topic(topic)
        or not isinstance(payload.get("entries"), list)
        or not env.is_timestamp_fresh(payload.get("timestamp"), ttl_seconds)
    ):
        return None
    return payload["entries"]
