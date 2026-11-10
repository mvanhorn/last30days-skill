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

# A sentence found in this many distinct roles of a board is company or
# role-family text, not something one role says. Counting distinct roles (a
# role posted in five cities counts once) makes one fixed number work for a
# three-role board and a 2,900-role one; below three roles nothing is removed.
BOILERPLATE_MIN_ROLES = 3
# Roles in the digest, and role-unique characters kept for each.
DIGEST_MAX_ROLES = 40
DIGEST_ROLE_CHARS = 600
# Ceiling for the whole rendered digest section: room for every role at its
# full text plus about 300 characters of title, department, location and URL.
DIGEST_MAX_CHARS = 36_000
# Locations named on one entry before the rest are counted.
DIGEST_ENTRY_LOCATIONS = 3

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"“(])")
_TITLE_TERM = re.compile(r"[a-z][a-z0-9+#-]{2,}")


def _key(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def _text(item: dict[str, Any]) -> str:
    return str(item.get("description_blocks") or item.get("description") or "")


def _sentences(text: str) -> list[str]:
    out: list[str] = []
    for line in text.splitlines():
        for sentence in _SENTENCE_END.split(line):
            sentence = " ".join(sentence.split())
            if sentence:
                out.append(sentence)
    return out


def distinct_roles(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group board rows into roles: one title in one department is one role.

    Each role is ``{"item": first row, "locations": [...]}``. The first row
    stands for the role; the other rows only add their locations.
    """
    roles: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        title = _key(item.get("title"))
        if not title:
            continue
        role = roles.setdefault(
            (title, _key(item.get("department"))), {"item": item, "locations": []},
        )
        location = str(item.get("location") or "").strip()
        if location and location not in role["locations"]:
            role["locations"].append(location)
    return list(roles.values())


def select_roles(
    items: list[dict[str, Any]], *, limit: int = DIGEST_MAX_ROLES,
) -> list[dict[str, Any]]:
    """Pick the roles that are rarest on this board, at most ``limit``.

    A role scores for sitting in a department no other role is in, for a
    location no other row names, for a title word no other role uses, and for
    a founding, leadership or specialized title. Ties go to the newest role
    with a true posted date, then to board order.
    """
    roles = distinct_roles(items)
    departments = Counter(
        _key(role["item"].get("department")) for role in roles
        if _key(role["item"].get("department"))
    )
    locations = Counter(_key(item.get("location")) for item in items if _key(item.get("location")))
    terms = Counter(
        term for role in roles
        for term in set(_TITLE_TERM.findall(_key(role["item"].get("title"))))
    )

    def score(role: dict[str, Any]) -> int:
        item = role["item"]
        department = departments.get(_key(item.get("department")), 0)
        flags = hiring_signals._title_flags(str(item.get("title") or ""))
        return (
            3 * (department == 1)
            + 1 * (department == 2)
            + 3 * any(locations[_key(location)] == 1 for location in role["locations"])
            + 2 * any(
                terms[term] == 1
                for term in _TITLE_TERM.findall(_key(item.get("title")))
            )
            + 4 * ("founding" in flags)
            + 2 * ("leadership" in flags)
            + 1 * ("specialized" in flags)
        )

    def posted(role: dict[str, Any]) -> str:
        item = role["item"]
        return str(item.get("date") or "") if item.get("posted_date_true") else ""

    order = sorted(
        range(len(roles)),
        key=lambda index: (-score(roles[index]), _reverse(posted(roles[index])), index),
    )
    return [roles[index] for index in order[:limit]]


def _reverse(date: str) -> tuple[int, ...]:
    """Sort key that puts later ISO dates first and undated roles last."""
    return tuple(-ord(char) for char in date) if date else (1,)


def build_digest(
    items: list[dict[str, Any]], selected: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return the digest of ``selected`` roles against the whole board.

    Sentences are counted across every distinct role that has text, then each
    selected role keeps its sentences that fewer than
    ``BOILERPLATE_MIN_ROLES`` roles share, cut to ``DIGEST_ROLE_CHARS``.
    """
    roles = distinct_roles(items)
    shared: Counter[str] = Counter()
    for role in roles:
        shared.update({_key(sentence) for sentence in _sentences(_text(role["item"]))})

    entries: list[dict[str, Any]] = []
    for role in selected:
        item = role["item"]
        unique = " ".join(
            sentence for sentence in _sentences(_text(item))
            if shared[_key(sentence)] < BOILERPLATE_MIN_ROLES
        )
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
            "text": unique,
        })
    return {"distinct_roles": len(roles), "roles": entries}
