"""A bounded, role-unique digest of one company's job board.

Job descriptions open with the same company text on every role, so the first
few hundred characters of each say nothing about that role. The digest keeps
what a role says that the rest of the board does not, for a few roles chosen by
how rare they are on that board. It works on the raw board rows from
``lib.jobs`` and decides nothing about what a role means.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from . import hiring_signals
from .job_boilerplate import BOILERPLATE_MIN_ROLES  # noqa: F401 (re-exported)
from .job_boilerplate import key as _key
from .job_boilerplate import role_sentences, shared_sentence_counts
# Roles in the digest, and role-unique characters kept for each.
DIGEST_MAX_ROLES = 40
# Of those, how many may go to roles the run itself ranked highest (its ranked
# job postings and the roles its hiring themes name) when the rarity pick left
# them out. The rest stay with the rarest roles.
ENGINE_ROLE_SLOTS = 10
# Of the rest, how many go first to roles matching the run's story terms,
# shared evenly by the terms that matched (at least two each).
STORY_ROLE_SLOTS = 8
STORY_TERM_MIN_SLOTS = 2
# On a board with more open roles than this, a place or a title word only one
# role has is common enough to say nothing, and earns no score.
RARITY_MAX_OPEN_ROLES = 500
# A title tail shared by this many roles, on a large board, is a team cluster;
# the upper bound grows to 1% of the board.
CLUSTER_MIN_ROLES = 3
CLUSTER_MAX_ROLES = 20
DIGEST_ROLE_CHARS = 600
# Ceiling for the whole rendered digest section: room for every role at its
# full text plus about 300 characters of title, department, location and URL.
DIGEST_MAX_CHARS = 36_000
# Locations named on one entry before the rest are counted.
DIGEST_ENTRY_LOCATIONS = 3

def unreadable_job_site_line(name: str) -> str:
    """What the run says about a company's job site it knows and cannot read."""
    return f"{name}'s job site can't be read automatically"


_TITLE_TERM = re.compile(r"[a-z][a-z0-9+#-]{2,}")


def _text(item: dict[str, Any]) -> str:
    return str(item.get("description_blocks") or item.get("description") or "")


def _group(item: dict[str, Any]) -> tuple[str, str]:
    return _key(item.get("title")), _key(item.get("department"))


def _role_key(item: dict[str, Any]) -> tuple[str, ...]:
    """What makes two board rows the same role posted in more than one place.

    They share a title and a department, and they say the same thing: their
    description text is equal. Rows with no text are told apart only when the
    board lists a whole posting per row (``whole_posting``); then each row is
    its own role. Other rows with no text merge on title and department, since
    such a board lists one row per location and nothing else separates them.
    """
    text = _key(_text(item))
    if text:
        return (*_group(item), "text", text)
    if item.get("whole_posting"):
        return (*_group(item), "posting", str(item.get("url") or item.get("id") or ""))
    return (*_group(item), "", "")


def distinct_roles(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group board rows into roles; ``_role_key`` says which rows are one role.

    Each role is ``{"item": first row, "locations": [...], "urls": [...]}``.
    The first row stands for the role; the other rows only add their locations
    and posting URLs.
    """
    roles: dict[tuple[str, ...], dict[str, Any]] = {}
    for item in items:
        if not _key(item.get("title")):
            continue
        role = roles.setdefault(_role_key(item), {"item": item, "locations": [], "urls": []})
        if item.get("story_term") and not role.get("story_term"):
            role["story_term"] = str(item["story_term"])
            role["story_rank"] = item.get("story_rank")
        # A row a reader already merged across locations names them all itself.
        for place in item.get("locations") or [item.get("location")]:
            location = str(place or "").strip()
            if location and location not in role["locations"]:
                role["locations"].append(location)
        url = str(item.get("url") or "").strip()
        if url and url not in role["urls"]:
            role["urls"].append(url)
    return list(roles.values())


def select_roles(
    items: list[dict[str, Any]], *, limit: int = DIGEST_MAX_ROLES,
    open_roles: int | None = None,
) -> list[dict[str, Any]]:
    """Pick the story roles, then the roles rarest on this board, at most ``limit``.

    Roles a story term matched (``story_term``) come first, up to
    ``STORY_ROLE_SLOTS`` of them; see ``_story_picks``. The rest are scored:
    a role scores for sitting in a department no other role is in, for a
    location no other row names, for a title word no other role uses, and for
    a founding, leadership or specialized title. On a board of more than
    ``RARITY_MAX_OPEN_ROLES`` open roles (``open_roles``, else the rows
    given) the location and title word earn nothing. Ties go to the newest
    role with a true posted date, then to board order. Two postings with one
    title in one department count once toward how common that title's
    department and words are.
    """
    roles = distinct_roles(items)
    rarity = (len(items) if open_roles is None else open_roles) <= RARITY_MAX_OPEN_ROLES
    groups = {_group(role["item"]) for role in roles}
    departments = Counter(department for _title, department in groups if department)
    # Counted from the same places a role is shown with, so a place that is
    # one role's first and several others' extra is not taken for rare.
    locations = Counter(
        _key(place) for item in items
        for place in item.get("locations") or [item.get("location")] if _key(place)
    )
    terms = Counter(
        term for title in {title for title, _department in groups}
        for term in set(_TITLE_TERM.findall(title))
    )

    places = {
        hiring_signals.place_key(place) for item in items
        for place in item.get("locations") or [item.get("location")] if _key(place)
    }

    # On a board too large for one-offs to mean anything, a small team named
    # in several titles ("..., Air Manufacturing Innovation") is the signal.
    tails = Counter(
        tail.lower() for tail in (
            hiring_signals._specialization(title, places=places)
            for title in {title for title, _department in groups}
        ) if tail
    )
    cluster_max = max(CLUSTER_MAX_ROLES, (open_roles or len(items)) // 100)

    def clustered(item: dict[str, Any]) -> bool:
        tail = hiring_signals._specialization(str(item.get("title") or ""), places=places)
        return bool(tail) and CLUSTER_MIN_ROLES <= tails[tail.lower()] <= cluster_max

    def score(role: dict[str, Any]) -> int:
        item = role["item"]
        department = departments.get(_key(item.get("department")), 0)
        flags = hiring_signals._title_flags(str(item.get("title") or ""), places=places)
        return (
            3 * (department == 1)
            + 1 * (department == 2)
            + 3 * rarity * any(locations[_key(location)] == 1 for location in role["locations"])
            + 2 * rarity * any(
                terms[term] == 1
                for term in _TITLE_TERM.findall(_key(item.get("title")))
            )
            + 3 * (not rarity) * clustered(item)
            + 4 * ("founding" in flags)
            + 2 * ("leadership" in flags)
            + 1 * ("specialized" in flags)
        )

    def posted(role: dict[str, Any]) -> str:
        item = role["item"]
        return str(item.get("date") or "") if item.get("posted_date_true") else ""

    story = _story_picks(roles, min(limit, STORY_ROLE_SLOTS))
    taken = set(story)
    order = sorted(
        (index for index in range(len(roles)) if index not in taken),
        key=lambda index: (-score(roles[index]), _reverse(posted(roles[index])), index),
    )
    return [roles[index] for index in [*story, *order][:limit]]


def _story_picks(roles: list[dict[str, Any]], slots: int) -> list[int]:
    """Indexes of the story roles that take the reserved slots.

    The slots are shared evenly by the terms that matched a role, at least
    ``STORY_TERM_MIN_SLOTS`` each, in the order the terms first matched. Each
    term's roles go in the order the board's search returned them
    (``story_rank``), else board order. A term with fewer roles than its
    share passes the unused slots on to the terms that have more.
    """
    queues: dict[str, list[int]] = {}
    ranked = sorted(
        (index for index, role in enumerate(roles) if role.get("story_term")),
        key=lambda index: (
            roles[index].get("story_rank") is None, roles[index].get("story_rank") or 0, index,
        ),
    )
    for index in ranked:
        queues.setdefault(roles[index]["story_term"], []).append(index)
    if not queues or slots <= 0:
        return []
    share = max(STORY_TERM_MIN_SLOTS, slots // len(queues))
    counts: dict[str, int] = {}
    left = slots
    for term, queue in queues.items():
        counts[term] = min(share, len(queue), left)
        left -= counts[term]
    # Unused shares, one slot at a time to each term with roles left.
    while left > 0 and any(counts[term] < len(queue) for term, queue in queues.items()):
        for term, queue in queues.items():
            if left > 0 and counts[term] < len(queue):
                counts[term] += 1
                left -= 1
    return [index for term, queue in queues.items() for index in queue[:counts[term]]]


def add_engine_roles(
    digest: dict[str, Any],
    items: list[dict[str, Any]],
    urls: list[str],
    *,
    slots: int = ENGINE_ROLE_SLOTS,
    limit: int = DIGEST_MAX_ROLES,
) -> dict[str, Any]:
    """Return ``digest`` with the roles the run ranked highest kept in it.

    ``urls`` are posting URLs in the run's own order of importance and
    ``items`` the board rows the digest was built from. The first ``slots``
    ranked roles on the board are reserved a place: one the digest already
    lists is marked ``engine`` where it stands, and the others are added at
    the front with their role-unique text. The places they need are given up
    by the rarity picks that are not reserved, rarest kept longest, so the
    digest never holds more than ``limit`` roles and never loses a reserved
    one. A ranked role past the reserved places is left as it is, and a URL
    that is not on the board is passed over. An added role the board gave no
    text for is counted in ``note``.
    """
    entries = [dict(entry) for entry in digest.get("roles") or []]
    listed = {
        url: entry
        for entry in entries
        for url in (entry.get("url"), *(entry.get("urls") or [])) if url
    }
    by_url = {url: role for role in distinct_roles(items) for url in role["urls"]}
    picked: list[dict[str, Any]] = []
    reserved = 0
    for url in urls:
        if reserved >= slots:
            break
        if url in listed:
            if not listed[url].get("engine"):
                listed[url]["engine"] = True
                reserved += 1
            continue
        role = by_url.get(url)
        if role is None or any(role is chosen for chosen in picked):
            continue
        picked.append(role)
        reserved += 1
    added = build_digest(items, picked)["roles"] if picked else []
    for entry in added:
        entry["engine"] = True
    # The unreserved rarity picks give up places from their end.
    spare = max(0, limit - len(added) - sum(1 for entry in entries if entry.get("engine")))
    kept = []
    for entry in entries:
        if entry.get("engine"):
            kept.append(entry)
        elif spare > 0:
            kept.append(entry)
            spare -= 1
    out = {
        **digest,
        "roles": [*added, *kept][:limit],
        "engine_roles": reserved,
    }
    # A board that gives a role's text one call at a time (Workday) was asked
    # only for the rarity picks, before anything was ranked.
    bare = sum(1 for entry in added if not entry["text"])
    if bare:
        note = (
            f"{bare} of the ranked roles listed first have no role text "
            "(this board gives it one call per role, made before ranking)"
        )
        out["note"] = f"{digest['note']}; {note}" if digest.get("note") else note
    return out


def _reverse(date: str) -> tuple[int, ...]:
    """Sort key that puts later ISO dates first and undated roles last."""
    return tuple(-ord(char) for char in date) if date else (1,)


def build_digest(
    items: list[dict[str, Any]], selected: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return the digest of ``selected`` roles against the whole board.

    Sentences are counted once per title in a department, then each selected
    role keeps its sentences that fewer than ``BOILERPLATE_MIN_ROLES`` such
    titles share, cut to ``DIGEST_ROLE_CHARS``.
    """
    roles = distinct_roles(items)
    shared = shared_sentence_counts(
        (str(role["item"].get("title") or ""), str(role["item"].get("department") or ""),
         _text(role["item"]))
        for role in roles
    )

    entries: list[dict[str, Any]] = []
    for role in selected:
        item = role["item"]
        unique = " ".join(role_sentences(_text(item), shared))
        if len(unique) > DIGEST_ROLE_CHARS:
            unique = unique[: DIGEST_ROLE_CHARS - 3].rstrip() + "..."
        named = role["locations"][:DIGEST_ENTRY_LOCATIONS]
        more = len(role["locations"]) - len(named)
        entries.append({
            "title": str(item.get("title") or "").strip(),
            "department": str(item.get("department") or "").strip(),
            "location": "; ".join(named) + (f"; +{more} more" if more > 0 else ""),
            "posted": str(item.get("date") or "") if item.get("posted_date_true") else "",
            "url": str(item.get("url") or "").strip(),
            # Every posting the entry stands for (one per location it merged).
            "urls": list(role.get("urls") or []),
            "text": unique,
        })
        if role.get("story_term"):
            entries[-1]["story_term"] = role["story_term"]
    return {"distinct_roles": len(roles), "roles": entries}
