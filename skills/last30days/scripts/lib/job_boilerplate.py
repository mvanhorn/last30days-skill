"""Company text that a job board repeats across its roles.

Job descriptions open with the same company text on every role, so a sentence
that many distinct roles share says nothing about any one of them. Both the
Jobs Digest and Hiring Signals drop such sentences; the rule lives here so
neither module has to import the other for it.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable

# A sentence found under this many distinct titles of a board is company or
# role-family text, not something one role says. Counting a title in a
# department once (however many cities or postings carry it) makes one fixed
# number work for a three-role board and a 2,900-role one; below three nothing
# is removed.
BOILERPLATE_MIN_ROLES = 3

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"“(])")


def key(text: str) -> str:
    """Case- and whitespace-insensitive form used to compare text."""
    return " ".join(str(text or "").lower().split())


def sentences(text: str) -> list[str]:
    """Split description text into sentences, one line at a time."""
    out: list[str] = []
    for line in str(text or "").splitlines():
        for sentence in _SENTENCE_END.split(line):
            sentence = " ".join(sentence.split())
            if sentence:
                out.append(sentence)
    return out


def shared_sentence_counts(roles: Iterable[tuple[str, str, str]]) -> Counter[str]:
    """How many distinct (title, department) groups carry each sentence.

    ``roles`` holds ``(title, department, description)`` for every posting on
    the board; postings of one title in one department count once.
    """
    by_group: dict[tuple[str, str], set[str]] = {}
    for title, department, text in roles:
        by_group.setdefault((key(title), key(department)), set()).update(
            key(sentence) for sentence in sentences(text)
        )
    shared: Counter[str] = Counter()
    for group_sentences in by_group.values():
        shared.update(group_sentences)
    return shared


def role_sentences(text: str, shared: Counter[str]) -> list[str]:
    """The sentences of ``text`` that fewer than ``BOILERPLATE_MIN_ROLES`` roles share."""
    return [
        sentence for sentence in sentences(text)
        if shared[key(sentence)] < BOILERPLATE_MIN_ROLES
    ]
