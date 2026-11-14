"""The bounded evidence block for one company's patent filings.

``lib.patents`` returns an artifact: whose names the filings were found under,
counts, a few filings with their abstract and first claim, and more by title.
This module turns it into the lines of the ``## Patent Digest`` block and
keeps the block under one size. It decides nothing about what a filing means.

The caller supplies the two formatters, so every title, name and passage of
patent office text is escaped the way the rest of the evidence is.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from . import patents
from .company_names import CONFIRMED, NAME_MATCHED

HEADING = "## Patent Digest"
# Ceiling for the whole block. Entries are added before titles, so when the
# block is cut it is the title list that is shortened.
DIGEST_MAX_CHARS = 24_000
# Room kept for the note line, written after it is known what was cut.
_NOTE_RESERVE = 400
_NAME_CHARS = 120
_TITLE_CHARS = 300
_FIELD_CHARS = 60
# What the reader sees for filers whose names extend the company's name and
# could not be attributed to it: the line that lists them, and the marker on
# each of their entries.
OTHER_FILERS_LABEL = "- Other filers with the company's name, not verified:"
NOT_VERIFIED = "not verified"


def count(value: Any) -> int:
    """A count from the artifact; anything that is not one is zero."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def quantity(number: int, noun: str, *, floor: bool = False, plural: str | None = None) -> str:
    """``3 grants``, ``1 grant``, ``1,200+ grants`` when the count is a floor."""
    if number == 1 and not floor:
        word = noun
    else:
        word = plural or f"{noun}s"
    return f"{number:,}{'+' if floor else ''} {word}"


def groups(artifact: dict, kind: str) -> list[dict]:
    """The artifact's filed-under name groups of one kind."""
    found = artifact.get("groups")
    return [
        group for group in (found if isinstance(found, list) else [])
        if isinstance(group, dict) and group.get("kind") == kind and group.get("name")
    ]


def has_own_filings(artifact: dict, counts: dict) -> bool:
    """True when any filing in the window is the company's own, so its counts
    describe filings rather than sit at zero beside other filers'."""
    return bool(
        groups(artifact, CONFIRMED)
        or count(counts.get("applications"))
        or count(counts.get("grants"))
        or count(counts.get("first_publications"))
    )


def _dicts(value: Any) -> list[dict]:
    return [item for item in (value if isinstance(value, list) else []) if isinstance(item, dict)]


def digest_lines(
    artifact: Any,
    *,
    link: Callable[[str, str], str],
    untrusted: Callable[[str, int], str],
) -> list[str]:
    """The lines of the block, or none when the artifact holds no digest.

    ``link(url, title)`` renders a filing's title linked to its document and
    ``untrusted(text, limit)`` renders text that came from the patent office.
    Entries filed under the company's own names come before other filers',
    each in the lane's order. Entries, then titles, are added until the next one
    would pass ``DIGEST_MAX_CHARS``; the note line counts what was left out.
    """
    if not isinstance(artifact, dict) or artifact.get("status") != "digest":
        return []
    entries = _dicts(artifact.get("entries"))
    entries = [
        *(entry for entry in entries if entry.get("group_kind") != NAME_MATCHED),
        *(entry for entry in entries if entry.get("group_kind") == NAME_MATCHED),
    ]
    index = _dicts(artifact.get("index"))
    if not entries and not index:
        return []

    def inline(value: Any, limit: int = _FIELD_CHARS) -> str:
        return untrusted(" ".join(str(value or "").split()), limit)

    counts = artifact.get("counts") if isinstance(artifact.get("counts"), dict) else {}
    floor = bool(counts.get("floor"))

    head = [HEADING, "", _filings_line(artifact, counts, floor, inline)]
    matched = groups(artifact, NAME_MATCHED)
    if matched:
        matched_first = count(counts.get("name_matched_first_publications"))
        head.append(
            f"{OTHER_FILERS_LABEL} "
            + "; ".join(
                f"{inline(group['name'], _NAME_CHARS)} ("
                f"{quantity(count(group.get('applications')), 'application', floor=floor)}, "
                f"{quantity(count(group.get('grants')), 'grant', floor=floor)})"
                for group in matched
            )
            + (
                f"; {quantity(matched_first, 'first US publication', floor=floor)} among them"
                if matched_first
                else ""
            )
        )
    if has_own_filings(artifact, counts):
        head.append(_first_publications_line(counts, floor))

    tail = []
    others = [item for item in _dicts(artifact.get("other_names")) if item.get("name")]
    if others:
        tail.append(
            "- Other names in the search, not read: "
            + ", ".join(
                f"{inline(item['name'], _NAME_CHARS)} ({count(item.get('count')):,})"
                for item in others
            )
        )

    room = DIGEST_MAX_CHARS - _NOTE_RESERVE - sum(len(line) + 1 for line in (*head, *tail))
    shown_entries: list[str] = []
    for entry in entries:
        line = _entry_line(entry, link, inline)
        if len(line) + 1 > room:
            break
        room -= len(line) + 1
        shown_entries.append(line)
    shown_titles: list[str] = []
    titles_heading = "- More first US publications (titles only):"
    if len(shown_entries) == len(entries) and index:
        room -= len(titles_heading) + 1
        for item in index:
            line = _index_line(item, link, inline)
            if len(line) + 1 > room:
                break
            room -= len(line) + 1
            shown_titles.append(line)

    note = _note(
        artifact.get("partial"),
        len(entries) - len(shown_entries),
        len(index) - len(shown_titles),
    )
    return [
        *head,
        *([f"- Note: {note}"] if note else []),
        *shown_entries,
        *([titles_heading, *shown_titles] if shown_titles else []),
        *tail,
    ]


def _filings_line(artifact: dict, counts: dict, floor: bool, inline: Callable[..., str]) -> str:
    company = inline(artifact.get("company"), _NAME_CHARS) or "the company"
    if artifact.get("company_via_person"):
        company += ", the company the person leads (the company's filings, not the person's)"
    window = artifact.get("window") if isinstance(artifact.get("window"), dict) else {}
    parts = [
        company,
        f"US patent filings published {inline(window.get('from'))} to {inline(window.get('to'))}",
    ]
    if not has_own_filings(artifact, counts):
        return "- Filings: " + " | ".join([*parts, "none filed under the company's own name"])
    own = groups(artifact, CONFIRMED)
    parts += [
        quantity(count(counts.get("applications")), "application", floor=floor) + " published",
        quantity(count(counts.get("grants")), "patent", floor=floor) + " granted",
    ]
    if own:
        parts.append("filed as " + "; ".join(inline(group["name"], _NAME_CHARS) for group in own))
    return "- Filings: " + " | ".join(parts)


def _first_publications_line(counts: dict, floor: bool) -> str:
    """The company's own first publications, and what else was counted under
    its own names. Other filers' filings are on their own line."""
    line = (
        f"- First US publications: {count(counts.get('first_publications')):,}"
        f"{'+' if floor else ''} (an application, or a grant with no earlier publication, "
        "that does not continue an older application)."
    )
    not_listed = [
        quantity(number, noun, floor=floor, plural=plural) + suffix
        for number, noun, plural, suffix in (
            (
                count(counts.get("continuing")), "continuing application", None,
                " (continuations, divisionals)",
            ),
            (
                count(counts.get("previously_published_grants")),
                "grant of an application published earlier",
                "grants of applications published earlier", "",
            ),
            (count(counts.get("designs")), "design patent", None, ""),
            (count(counts.get("other_kinds")), "of another kind", "of other kinds", ""),
        )
        if number
    ]
    if not_listed:
        line += (
            " Counted and not listed, because none is the first US publication of an "
            "invention: " + ", ".join(not_listed) + "."
        )
    return line


def _note(partial: Any, cut_entries: int, cut_titles: int) -> str:
    notes = []
    missing = count(partial.get("missing_text")) if isinstance(partial, dict) else 0
    if missing:
        notes.append(
            f"{missing:,} of the filings listed by title only "
            f"{'has' if missing == 1 else 'have'} no abstract or claim text because the "
            "patent office did not return it or time ran out"
        )
    cut = [
        quantity(number, noun, plural=plural)
        for number, noun, plural in (
            (cut_entries, "entry", "entries"), (cut_titles, "title", None),
        )
        if number
    ]
    if cut:
        notes.append(
            f"{' and '.join(cut)} left out to keep this block under "
            f"{DIGEST_MAX_CHARS:,} characters"
        )
    return "; ".join(notes) + "." if notes else ""


def _kind(item: dict) -> str:
    return "grant" if item.get("kind") == "grant" else "application"


def _filed_as(item: dict, inline: Callable[..., str], location: str = "") -> list[str]:
    parts = []
    name = inline(item.get("filed_as"), _NAME_CHARS)
    if name:
        parts.append(f"filed as {name}" + (f" ({location})" if location else ""))
    if item.get("group_kind") == NAME_MATCHED:
        parts.append(NOT_VERIFIED)
    return parts


def _title(item: dict, link: Callable[[str, str], str]) -> str:
    title = " ".join(str(item.get("title") or "").split())[:_TITLE_CHARS] or "Untitled filing"
    return link(str(item.get("url") or ""), title)


def _entry_line(
    entry: dict,
    link: Callable[[str, str], str],
    inline: Callable[..., str],
) -> str:
    parts = [_title(entry, link), f"{_kind(entry)} {inline(entry.get('number'))}".rstrip()]
    filed = inline(entry.get("filed"))
    priority = inline(entry.get("earliest_priority"))
    for label, value in (("published", inline(entry.get("published"))), ("filed", filed)):
        if value:
            parts.append(f"{label} {value}")
    if priority and priority != filed:
        parts.append(f"earliest priority {priority}")
    codes = entry.get("cpc")
    cpc = "; ".join(inline(code) for code in (codes if isinstance(codes, list) else []) if code)
    if cpc:
        parts.append(f"CPC {cpc}")
    parts.extend(_filed_as(entry, inline, inline(entry.get("applicant_location"), _NAME_CHARS)))
    for label, key, limit in (
        ("abstract", "abstract", patents.ABSTRACT_CHARS),
        ("first claim", "first_claim", patents.FIRST_CLAIM_CHARS),
    ):
        text = inline(entry.get(key), limit)
        if text:
            parts.append(f"{label}: {text}")
    return "- " + " | ".join(parts)


def _index_line(item: dict, link: Callable[[str, str], str], inline: Callable[..., str]) -> str:
    parts = [_title(item, link), _kind(item)]
    published = inline(item.get("published"))
    if published:
        parts.append(f"published {published}")
    cpc = inline(item.get("cpc"), _NAME_CHARS)
    if cpc:
        parts.append(f"CPC {cpc}")
    parts.extend(_filed_as(item, inline))
    return "  - " + " | ".join(parts)
