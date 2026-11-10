"""Patient Zero: where a story was first seen and how it crossed platforms.

Builds a spread timeline from the dated, relevance-qualified evidence a run
already collected. Everything here is "earliest FOUND", never "the origin":
search APIs return a sample, so the trail can only prove a lower bound on
how early a story existed. The output says so, and flags the two cases where
the true start is probably earlier than what was found - the trail reaching
the edge of the search window, and sources that did not report cleanly.

The timeline is plain JSON data (days are ISO strings) so a run can store it
in the report and a later render can read it back. The report serializer
drops null values, so an absent fact is an absent key (``links_to``,
``extension``), never a null. It names venues and links
posts; it never carries the handle of an individual poster.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any, Callable, Iterable, Mapping

# Sources whose dates describe something other than a post entering the
# conversation: a market's creation, a job listing, a product page, a local file.
EXCLUDED_SOURCES = frozenset({
    "polymarket", "jobs", "corpus", "amazon", "trustpilot", "meta_ads",
})
# An earliest sighting this close to the window start means the search was
# cut off by the window, not that the story began there.
WINDOW_EDGE_DAYS = 2
_TITLE_LIMIT = 110
# Sources whose author field names a channel, which is a venue, not a person.
_CHANNEL_SOURCES = frozenset({"youtube"})
_X_STATUS = re.compile(r"(?:x|twitter)\.com/[^/\s]+/status/(\d+)", re.IGNORECASE)


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

    An X post is addressed by its id alone, so its link never needs the
    poster's handle.
    """
    if source == "hackernews":
        metadata = getattr(item, "metadata", None) or {}
        hn_url = str(metadata.get("hn_url") or "").strip()
        if hn_url:
            return hn_url
    url = str(getattr(item, "url", "") or "")
    if source == "x":
        status = _X_STATUS.search(url)
        if status:
            return f"https://x.com/i/status/{status.group(1)}"
    return url


def _venue(item: Any, source: str) -> str:
    """Where the post appeared: a subreddit, a channel, a site. Never a poster."""
    venue = str(getattr(item, "container", "") or "").strip()
    if not venue and source in _CHANNEL_SOURCES:
        venue = str(getattr(item, "author", "") or "").strip()
    if source == "reddit" and venue and not venue.lower().startswith("r/"):
        venue = f"r/{venue}"
    # A venue that only repeats the platform's name says nothing.
    if re.sub(r"[\W_]+", "", venue).lower() == re.sub(r"[\W_]+", "", source).lower():
        return ""
    return venue


def _address_pattern(url: str) -> re.Pattern[str] | None:
    """Match a post's address inside another post's text or link target."""
    status = _X_STATUS.search(url)
    if status:
        return re.compile(
            r"(?:x|twitter)\.com/[^/\s]+/status/" + status.group(1) + r"(?!\d)", re.IGNORECASE,
        )
    address = re.sub(r"^https?://(?:www\.)?", "", url.split("#", 1)[0], flags=re.IGNORECASE).rstrip("/")
    if "/" not in address:
        # A bare site is not a post address; every mention of the site would match.
        return None
    return re.compile(re.escape(address) + r"(?![\w-])", re.IGNORECASE)


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
                "day": day.isoformat(),
                "url": url,
                "title": " ".join(str(getattr(item, "title", "") or "").split()),
                "venue": _venue(item, source),
                "engagement": engagement,
                # Where an earlier post's address could appear; dropped before return.
                "_text": f"{getattr(item, 'body', '') or ''} {getattr(item, 'url', '') or ''}",
            })
    return sightings, undated


def _mark_carries(sightings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mark each post that contains the address of a post from an earlier day.

    The only evidence accepted is the address itself. Day resolution cannot
    order two posts from one date, so a same-day link is not a carry.
    """
    carries: list[dict[str, Any]] = []
    patterns = [_address_pattern(sighting["url"]) for sighting in sightings]
    for later in sightings:
        for earlier, pattern in zip(sightings, patterns):
            if earlier["day"] >= later["day"]:
                break
            if pattern is not None and pattern.search(later["_text"]):
                later["links_to"] = {
                    "source": earlier["source"], "day": earlier["day"], "url": earlier["url"],
                }
                carries.append(later)
                break
    for sighting in sightings:
        del sighting["_text"]
    return carries


def build_timeline(
    candidates: Iterable[Any],
    range_from: str,
    range_to: str,
    engagement_of: Callable[[Any], float],
    incomplete_sources: Iterable[str] = (),
    reach: Mapping[str, str] | None = None,
    extension: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return the spread timeline, or None when nothing dated qualifies.

    ``range_from`` is the start of the widest range searched. ``reach`` maps a
    source to the day its own search actually started when that differs; it
    may name sources that returned no posts. ``extension`` describes a search
    that was widened past the original window.
    """
    sightings, undated = _sightings(candidates, engagement_of)
    if not sightings:
        return None
    # Ties on the day go to the more-engaged post: it is the likelier carrier,
    # and day resolution cannot order two posts from the same date.
    sightings.sort(key=lambda s: (s["day"], -s["engagement"], s["source"], s["url"]))
    carries = _mark_carries(sightings)
    earliest = sightings[0]

    reach = {
        source: start for source, start in (reach or {}).items()
        if source not in EXCLUDED_SOURCES
    }
    first_by_source: dict[str, dict[str, Any]] = {}
    for sighting in sightings:
        first_by_source.setdefault(sighting["source"], sighting)
    hops = [
        {**sighting, "searched_from": reach.get(sighting["source"], range_from), "searched_to": range_to}
        for sighting in sorted(
            first_by_source.values(),
            key=lambda s: (s["day"], -s["engagement"], s["source"]),
        )
    ]
    for hop in hops:
        # A platform searched only from after the trail's start could hold an
        # earlier post that was never looked for, so its place is unproven.
        hop["order_uncertain"] = hop["searched_from"] > hops[0]["day"]

    by_day: dict[str, dict[str, float]] = {}
    for sighting in sightings:
        bucket = by_day.setdefault(sighting["day"], {"count": 0, "engagement": 0.0})
        bucket["count"] += 1
        bucket["engagement"] += sighting["engagement"]
    peak_day = max(by_day, key=lambda d: (by_day[d]["engagement"], by_day[d]["count"], d))
    breakout = max(sightings, key=lambda s: (s["engagement"], -_dt.date.fromisoformat(s["day"]).toordinal()))

    window_start = _parse_day(range_from)
    at_window_edge = (
        window_start is not None
        and (_dt.date.fromisoformat(earliest["day"]) - window_start).days <= WINDOW_EDGE_DAYS
    )
    timeline = {
        "earliest": earliest,
        "hops": hops,
        "carries": carries,
        "peak_day": peak_day,
        "peak": by_day[peak_day],
        "breakout": breakout,
        "sighting_count": len(sightings),
        "undated_count": undated,
        "range_from": range_from,
        "range_to": range_to,
        "reach": [
            {"source": source, "from": reach.get(source, range_from), "to": range_to}
            for source in sorted(set(reach) | set(first_by_source))
        ],
        "at_window_edge": at_window_edge,
        "incomplete_sources": sorted(set(incomplete_sources) - EXCLUDED_SOURCES),
    }
    if extension:
        timeline["extension"] = extension
    return timeline


def _count(value: float) -> str:
    number = int(round(value))
    return f"{number:,}"


def _link(sighting: dict[str, Any], label_of: Callable[[str], str]) -> str:
    return f"[{label_of(sighting['source'])} post]({sighting['url']})"


def _describe(sighting: dict[str, Any], label_of: Callable[[str], str]) -> str:
    title = sighting["title"]
    if len(title) > _TITLE_LIMIT:
        title = title[: _TITLE_LIMIT - 3].rstrip() + "..."
    text = label_of(sighting["source"])
    if sighting["venue"]:
        text += f" in {sighting['venue']}"
    if title:
        text += f': "{title}"'
    if sighting["engagement"] > 0:
        text += f" [{_count(sighting['engagement'])} engagement]"
    return f"{text} - {_link(sighting, label_of)}"


def limits(timeline: dict[str, Any], label_of: Callable[[str], str]) -> list[str]:
    """Every reason the true start may be earlier than what was found."""
    caveats = []
    extension = timeline.get("extension")
    if extension and extension["stopped"] == "budget":
        caveats.append(
            "The extended search stopped at its time budget, so the trail may run further "
            "back than shown."
        )
    elif extension and extension["stopped"] == "failed":
        caveats.append(
            "The extended search failed on "
            + ", ".join(label_of(source) for source in extension["failed_sources"])
            + ", so the trail may run further back than shown."
        )
    elif timeline["at_window_edge"] and extension:
        caveats.append(
            "The earliest post still sits at the start of the widest range searched "
            f"({extension['days']} days), so the trail may run further back than shown."
        )
    elif timeline["at_window_edge"]:
        caveats.append(
            "The earliest post sits at the start of the search window and the search was not "
            "extended, so the story almost certainly began before it."
        )
    uncertain = [hop["source"] for hop in timeline["hops"] if hop["order_uncertain"]]
    if uncertain:
        names = ", ".join(label_of(source) for source in uncertain)
        verb, whose = ("was", "its") if len(uncertain) == 1 else ("were", "their")
        caveats.append(
            f"{names} {verb} not searched as far back as the earlier first sightings, so {whose} "
            "place in the order may come from the shorter search rather than from the story."
        )
    shorter = [entry for entry in timeline["reach"] if entry["from"] > timeline["range_from"]]
    if shorter:
        caveats.append(
            "Searched over a shorter range than the rest: "
            + ", ".join(
                f"{label_of(entry['source'])} ({entry['from']} to {entry['to']})" for entry in shorter
            )
            + "."
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
    return caveats


def render_timeline(timeline: dict[str, Any], label_of: Callable[[str], str]) -> list[str]:
    earliest = timeline["earliest"]
    lines = [
        "## Spread Timeline",
        "",
        f"Built from {timeline['sighting_count']} dated, on-topic posts between "
        f"{timeline['range_from']} and {timeline['range_to']}. Dates are day-resolution. "
        "\"Earliest found\" is the earliest post this search returned, not proof of the origin.",
    ]
    extension = timeline.get("extension")
    if extension and extension["days"]:
        lines.append(
            f"The earliest post sat at the start of the original window ({extension['window_from']}), "
            f"so the search was extended to {extension['days']} days on: "
            + ", ".join(label_of(source) for source in extension["sources"]) + "."
        )
    lines.extend([
        "",
        f"- Earliest found: {earliest['day']} - {_describe(earliest, label_of)}",
        "",
        "First sighting per platform, in order, with the range that platform was searched over:",
    ])
    previous: _dt.date | None = None
    for index, hop in enumerate(timeline["hops"], start=1):
        day = _dt.date.fromisoformat(hop["day"])
        if previous is None:
            gap = "start"
        else:
            days = (day - previous).days
            gap = "same day" if days == 0 else f"+{days} day{'s' if days != 1 else ''}"
        searched = f"searched {hop['searched_from']} to {hop['searched_to']}"
        if hop["order_uncertain"]:
            searched += "; order not established"
        lines.append(f"{index}. {hop['day']} ({gap}) - {_describe(hop, label_of)} ({searched})")
        previous = day
    if timeline["carries"]:
        lines.extend(["", "Posts that contain the address of an earlier sighted post:"])
        lines.extend(
            f"- {carry['day']} {_link(carry, label_of)} links to the {carry['links_to']['day']} "
            f"{_link(carry['links_to'], label_of)}"
            for carry in timeline["carries"]
        )
    peak = timeline["peak"]
    lines.extend([
        "",
        f"- Peak day: {timeline['peak_day']} - {int(peak['count'])} "
        f"post{'s' if int(peak['count']) != 1 else ''}, "
        f"{_count(peak['engagement'])} combined engagement",
        f"- Biggest single post: {timeline['breakout']['day']} - "
        f"{_describe(timeline['breakout'], label_of)}",
    ])
    caveats = limits(timeline, label_of)
    if caveats:
        lines.extend(["", "Limits of this trace:"])
        lines.extend(f"- {caveat}" for caveat in caveats)
    return lines
