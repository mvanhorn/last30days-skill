"""Named teams on a job board, read from its role titles.

A secret hiring signal is rarely one role. It is a team the press has not
named yet ("Applied Scientist, Fauna", "Head of UK, Ad Solutions") whose
name repeats across a handful of titles, or a head, GM or founding hire for
a team whose name no other title carries. Story terms cannot find these,
because nobody has written the story yet, so the board's own titles are read
for them instead.

A title's team is any segment after its first, split on commas, spaced
dashes, bars, slashes, colons and parentheses: "Robotics Software
Development Engineer, Fauna" names "fauna". Shifts, levels, contract types,
languages, legal tags, stores and places are not teams.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import Any

TEAM_LINE_LIMIT = 12      # teams named on the digest's Named teams line
TEAM_ROLE_SLOTS = 8       # digest slots for one role of each top team
LEAD_ROLE_SLOTS = 6       # digest slots for leads of a team no other title names
TEAM_MIN_ROLES = 3
TEAM_MAX_ROLES = 60       # or 2% of the board, whichever is larger
LEAD_MAX_TITLES = 3       # a lead's team phrase is in at most this many titles

_SPLIT = re.compile(r"\s*(?:,|\s[-–|/]\s|\(|\)|:)\s*")
_WORD = re.compile(r"[a-z][a-z&]+")
_DANGLING_AMP = re.compile(r"(?<=\w)&+(?=\s|$)|(?:^|(?<=\s))&+(?=\w)")
_NOISE = re.compile(
    r"(?:\b(?:shift|part[- ]time|full[- ]time|seasonal|temp(?:orary)?|intern(?:ship)?s?|"
    r"co-op|apprentice|new grad|graduate|contract|remote|hybrid|on-?site|speaking|"
    r"nationals?|citizen|clearance|bilingual|fluent|multiple openings|all levels|level|"
    r"req|store|showroom|service center|outlet|dfo|airport|mall|w/m/d|m/f/d|m/w/d|"
    r"f/m/d|h/f|f/h|evergreen|pipeline|talent community|\d{4})\b)"
    r"|^(?:sr\.?|senior|staff|principal|lead|junior|jr\.?|associate|i{1,3}|iv|v|[a-z]{2,4}|\d+)$",
    re.I,
)
_LEAD = re.compile(
    r"\b(?:head|gm|general manager|director|vp|vice president|founding|lead|chief|"
    r"product manager|program manager)\b", re.I,
)
_NOT_LEAD = re.compile(r"\b(?:assistant|coordinator|associate|specialist|technician|advisor|recruiter)\b", re.I)
_SENIOR = re.compile(r"\b(?:head|gm|general manager|vp|vice president|chief|founding|director)\b", re.I)


def _segments(title: str) -> list[str]:
    return [part.strip(" .") for part in _SPLIT.split(title or "") if part and part.strip(" .")]


def _team_names(title: str, company: str, place_words: set[str]) -> list[str]:
    names = []
    for segment in _segments(title)[1:]:
        if company:
            segment = re.sub(rf"\b{re.escape(company)}\b", " ", segment, flags=re.I)
            segment = _DANGLING_AMP.sub(" ", segment)
        name = " ".join(segment.split()).strip(" -,.&").lower()
        words = _WORD.findall(name)
        if len(name) < 3 or not words or _NOISE.search(name):
            continue
        if all(word in place_words for word in words):
            continue
        names.append(name)
    return names


def _posted(item: dict[str, Any]) -> str:
    return str(item.get("date") or "") if item.get("posted_date_true") else ""


def named_teams(
    items: list[dict[str, Any]], *, company: str, window_start: str,
) -> dict[str, Any]:
    """The board's named teams and the leads of its newest small teams.

    ``teams``: up to ``TEAM_LINE_LIMIT`` of ``{"name", "roles", "new"}``,
    the teams whose name is most nearly theirs alone on the board, most
    posted in the window, and largest, in that order of weight. ``new`` is
    the roles posted since ``window_start``, or None when the board gives no
    true posted dates. ``team_items`` is each listed team's newest role.
    ``lead_items``: up to ``LEAD_ROLE_SLOTS`` head, GM, director, founding,
    lead or product-manager roles for a team phrase in at most
    ``LEAD_MAX_TITLES`` titles, one per team, posted-in-window and senior
    first.
    """
    titles = [str(item.get("title") or "") for item in items]
    word_titles = Counter()
    for title in titles:
        word_titles.update(set(_WORD.findall(title.lower())))
    place_words = {
        word for item in items
        for place in item.get("locations") or [item.get("location")]
        for word in _WORD.findall(str(place or "").lower())
    }
    # One role posted in several places is one role: a team counts distinct
    # (title, department) roles, each by its first row, the newest posting.
    members: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen_roles: dict[str, dict[tuple[str, str], dict[str, Any]]] = defaultdict(dict)
    for item, title in zip(items, titles):
        role = (" ".join(title.lower().split()), " ".join(str(item.get("department") or "").lower().split()))
        for name in dict.fromkeys(_team_names(title, company, place_words)):
            first = seen_roles[name].get(role)
            if first is None:
                seen_roles[name][role] = item
                members[name].append(item)
            elif _posted(item) > _posted(first):
                members[name][members[name].index(first)] = item
                seen_roles[name][role] = item

    # "fauna robotics" (one role) is the "fauna" team: fold a name too small
    # to stand alone into the shorter name it starts with, keeping each role
    # once. A team big enough to stand alone ("autonomy systems validation")
    # stays itself rather than vanishing into a broad word ("autonomy").
    for name in sorted(members, key=len):
        if len(members[name]) >= TEAM_MIN_ROLES:
            continue
        words = name.split(" ")
        base = next((
            prefix for prefix in (" ".join(words[:cut]) for cut in range(1, len(words)))
            if prefix in members
        ), None)
        if base is not None:
            seen = {id(row) for row in members[base]}
            members[base].extend(row for row in members[name] if id(row) not in seen)

    position = {id(item): index for index, item in enumerate(items)}
    size_cap = max(TEAM_MAX_ROLES, len(items) // 50)
    scored = []
    for name, rows in members.items():
        if not TEAM_MIN_ROLES <= len(rows) <= size_cap:
            continue
        words = _WORD.findall(name)
        rarest = max(1, min((word_titles[word] for word in words), default=1))
        own = min(1.0, len(rows) / rarest)
        dated = [_posted(row) for row in rows if _posted(row)]
        new = sum(day >= window_start for day in dated) if dated else None
        share = new / len(dated) if dated else 0.5
        scored.append((own * 2 + share * 2 + min(math.log2(len(rows)), 4) / 2, name, rows, new))
    scored.sort(key=lambda entry: (-entry[0], entry[1]))
    teams, team_items = [], []
    for _score, name, rows, new in scored:
        teams.append({"name": name, "roles": len(rows), "new": new})
        team_items.append(max(rows, key=lambda row: (_posted(row), -position[id(row)])))
        if len(teams) == TEAM_LINE_LIMIT:
            break

    lowered = [title.lower() for title in titles]
    leads: list[tuple[tuple, str, dict[str, Any]]] = []
    for name, rows in members.items():
        # Checked cheapest first: a name in more titles than a lead's team
        # may be, or with no lead role, never needs the phrase count.
        if len(rows) > LEAD_MAX_TITLES:
            continue
        candidates = []
        for row in rows:
            role = _segments(str(row.get("title") or ""))[:1]
            if role and _LEAD.search(role[0]) and not _NOT_LEAD.search(role[0]):
                candidates.append((row, role))
        if not candidates:
            continue
        phrase_titles = sum(name in title for title in lowered)
        if phrase_titles > LEAD_MAX_TITLES:
            continue
        for row, role in candidates:
            posted = _posted(row)
            key = (
                posted >= window_start if posted else False,
                bool(_SENIOR.search(role[0])),
                posted,
                -phrase_titles,
            )
            leads.append((key, name, row))
    leads.sort(key=lambda entry: entry[0], reverse=True)
    lead_items, lead_teams = [], set()
    for _key, name, row in leads:
        if name in lead_teams or any(row is chosen for chosen in lead_items):
            continue
        lead_teams.add(name)
        lead_items.append(row)
        if len(lead_items) == LEAD_ROLE_SLOTS:
            break
    return {"teams": teams, "team_items": team_items, "lead_items": lead_items}
