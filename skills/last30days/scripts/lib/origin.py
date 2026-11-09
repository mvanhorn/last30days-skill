"""Patient Zero: where a story was first seen and how it crossed platforms.

Builds a spread timeline from the dated, relevance-qualified evidence a run
already collected. Everything here is "earliest FOUND", never "the origin":
search APIs return a sample, so the trail can only prove a lower bound on
how early a story existed. The output says so, and flags the two cases where
the true start is probably earlier than what was found - the trail reaching
the edge of the search window, and sources that did not report cleanly.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Callable, Iterable

# Sources whose dates describe something other than a post entering the
# conversation: a market's creation, a job listing, a product page, a local file.
EXCLUDED_SOURCES = frozenset({
    "polymarket", "jobs", "corpus", "amazon", "trustpilot", "meta_ads",
})
# An earliest sighting this close to the window start means the search was
# cut off by the window, not that the story began there.
WINDOW_EDGE_DAYS = 2
_TITLE_LIMIT = 110


def _parse_day(value: str | None) -> _dt.date | None:
    if not value:
        return None
    try:
        return _dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _post_url(item: Any, source: str) -> str:
    """The URL of the post itself, which is also its identity.

    A Hacker News item's ``url`` is the article it links to, shared by every
    submission of that article; the submission lives at ``hn_url``. Keying on
    the article would collapse separate submissions (hiding the earliest)
    and link to the article instead of the post being described.
    """
    if source == "hackernews":
        metadata = getattr(item, "metadata", None) or {}
        hn_url = str(metadata.get("hn_url") or "").strip()
        if hn_url:
            return hn_url
    return str(getattr(item, "url", "") or "")


def _sightings(
    candidates: Iterable[Any],
    engagement_of: Callable[[Any], float],
) -> tuple[list[dict[str, Any]], int]:
    """One sighting per dated source item, plus the count left undated."""
    sightings: list[dict[str, Any]] = []
    undated = 0
    seen: set[tuple[str, str]] = set()
    for candidate in candidates:
        for item in getattr(candidate, "source_items", None) or []:
            source = str(getattr(item, "source", "") or "")
            url = _post_url(item, source)
            if not source or source in EXCLUDED_SOURCES or not url:
                continue
            # Each post carries its own counts: a candidate can merge a web
            # page with the thread discussing it, and they are not one number.
            engagement = float(engagement_of(item) or 0.0)
            if (source, url) in seen:
                continue
            seen.add((source, url))
            day = _parse_day(getattr(item, "published_at", None))
            # A low-confidence date is a guess; a guess cannot anchor an origin.
            if day is None or getattr(item, "date_confidence", "low") == "low":
                undated += 1
                continue
            sightings.append({
                "source": source,
                "day": day,
                "url": url,
                "title": " ".join(str(getattr(item, "title", "") or "").split()),
                "author": str(getattr(item, "author", "") or "").strip(),
                "container": str(getattr(item, "container", "") or "").strip(),
                "engagement": engagement,
            })
    return sightings, undated


def build_timeline(
    candidates: Iterable[Any],
    range_from: str,
    range_to: str,
    engagement_of: Callable[[Any], float],
    incomplete_sources: Iterable[str] = (),
) -> dict[str, Any] | None:
    """Return the spread timeline, or None when nothing dated qualifies."""
    sightings, undated = _sightings(candidates, engagement_of)
    if not sightings:
        return None
    # Ties on the day go to the more-engaged post: it is the likelier carrier,
    # and day resolution cannot order two posts from the same date.
    sightings.sort(key=lambda s: (s["day"], -s["engagement"], s["source"], s["url"]))
    earliest = sightings[0]

    first_by_source: dict[str, dict[str, Any]] = {}
    for sighting in sightings:
        first_by_source.setdefault(sighting["source"], sighting)
    hops = sorted(
        first_by_source.values(),
        key=lambda s: (s["day"], -s["engagement"], s["source"]),
    )

    by_day: dict[_dt.date, dict[str, float]] = {}
    for sighting in sightings:
        bucket = by_day.setdefault(sighting["day"], {"count": 0, "engagement": 0.0})
        bucket["count"] += 1
        bucket["engagement"] += sighting["engagement"]
    peak_day = max(by_day, key=lambda d: (by_day[d]["engagement"], by_day[d]["count"], d))
    breakout = max(sightings, key=lambda s: (s["engagement"], -s["day"].toordinal()))

    window_start = _parse_day(range_from)
    at_window_edge = (
        window_start is not None
        and (earliest["day"] - window_start).days <= WINDOW_EDGE_DAYS
    )
    return {
        "earliest": earliest,
        "hops": hops,
        "peak_day": peak_day,
        "peak": by_day[peak_day],
        "breakout": breakout,
        "sighting_count": len(sightings),
        "undated_count": undated,
        "range_from": range_from,
        "range_to": range_to,
        "at_window_edge": at_window_edge,
        "incomplete_sources": sorted(set(incomplete_sources) - EXCLUDED_SOURCES),
    }


def _count(value: float) -> str:
    number = int(round(value))
    return f"{number:,}"


def _describe(sighting: dict[str, Any], label_of: Callable[[str], str]) -> str:
    title = sighting["title"]
    if len(title) > _TITLE_LIMIT:
        title = title[: _TITLE_LIMIT - 3].rstrip() + "..."
    parts = [label_of(sighting["source"])]
    # A container (subreddit, channel) is a place, not the poster: "by" is
    # reserved for an actual author so a venue is never named as a person.
    if sighting["author"]:
        parts.append(f"by {sighting['author']}")
    elif sighting["container"]:
        parts.append(f"in {sighting['container']}")
    text = " ".join(parts)
    if title:
        text += f': "{title}"'
    if sighting["engagement"] > 0:
        text += f" [{_count(sighting['engagement'])} engagement]"
    return f"{text} - {sighting['url']}"


def render_timeline(timeline: dict[str, Any], label_of: Callable[[str], str]) -> list[str]:
    earliest = timeline["earliest"]
    lines = [
        "## Spread Timeline",
        "",
        f"Built from {timeline['sighting_count']} dated, on-topic posts between "
        f"{timeline['range_from']} and {timeline['range_to']}. Dates are day-resolution. "
        "\"Earliest found\" is the earliest post this search returned, not proof of the origin.",
        "",
        f"- Earliest found: {earliest['day'].isoformat()} - {_describe(earliest, label_of)}",
        "",
        "First sighting per platform, in order:",
    ]
    previous: _dt.date | None = None
    for index, hop in enumerate(timeline["hops"], start=1):
        if previous is None:
            gap = "start"
        else:
            days = (hop["day"] - previous).days
            gap = "same day" if days == 0 else f"+{days} day{'s' if days != 1 else ''}"
        lines.append(f"{index}. {hop['day'].isoformat()} ({gap}) - {_describe(hop, label_of)}")
        previous = hop["day"]
    peak = timeline["peak"]
    lines.extend([
        "",
        f"- Peak day: {timeline['peak_day'].isoformat()} - {int(peak['count'])} "
        f"post{'s' if int(peak['count']) != 1 else ''}, "
        f"{_count(peak['engagement'])} combined engagement",
        f"- Biggest single post: {timeline['breakout']['day'].isoformat()} - "
        f"{_describe(timeline['breakout'], label_of)}",
    ])
    caveats = []
    if timeline["at_window_edge"]:
        caveats.append(
            "The earliest post sits at the start of the search window, so the story "
            "almost certainly began before it. Re-run with a longer window to push the trail back."
        )
    if len(timeline["hops"]) == 1:
        caveats.append(
            "Only one platform returned dated posts, so there is no cross-platform spread to report."
        )
    if timeline["incomplete_sources"]:
        caveats.append(
            "These sources did not report cleanly and may hold earlier posts: "
            + ", ".join(label_of(source) for source in timeline["incomplete_sources"]) + "."
        )
    if timeline["undated_count"]:
        caveats.append(
            f"{timeline['undated_count']} on-topic posts had no reliable date and are left out."
        )
    if caveats:
        lines.extend(["", "Limits of this trace:"])
        lines.extend(f"- {caveat}" for caveat in caveats)
    return lines
