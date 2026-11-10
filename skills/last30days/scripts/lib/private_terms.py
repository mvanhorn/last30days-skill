"""Private terms: text the engine must never send, store, or print.

A rehearsal run is about a draft the user has not shipped. The hosting model
writes the draft and the names the user marks private to a file
(``--private-file``), and the engine checks every host-supplied value against
it before any request or write. The file is JSON::

    {"terms": ["Project Falcon", "Acme Labs"], "draft": "<the draft's full text>"}

Either key may be omitted, but not both. A value matches when it contains a
term, or shares a run of ``DRAFT_RUN_WORDS`` consecutive words with the draft.

Nothing here logs, and no error message or repr carries the file's contents:
a refusal names the input that was refused, never what matched.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
from typing import Any

MAX_FILE_BYTES = 1_000_000
# A shorter term would match ordinary words everywhere and refuse every run.
MIN_TERM_CHARS = 3
# Shorter than a sentence, so a quoted line or a distinctive phrase is caught;
# longer than a category name ("open source note taking app"), so searching
# for the category still passes.
DRAFT_RUN_WORDS = 5

_JOINERS = re.compile(r"[\s\-_.]+")
_WORD = re.compile(r"\w+")


class PrivateFileError(ValueError):
    """The private file is unreadable, malformed, or empty."""


@dataclasses.dataclass(frozen=True)
class Guard:
    """The loaded private file, in the form the check compares against."""

    terms: tuple[str, ...] = dataclasses.field(repr=False)
    draft_runs: frozenset[tuple[str, ...]] = dataclasses.field(repr=False)
    run_words: int = dataclasses.field(repr=False)


def _squash(text: str) -> str:
    """Case-folded with spaces, hyphens, underscores, and dots removed, so a
    name reads the same as a handle, a subreddit, a hashtag, or a domain."""
    return _JOINERS.sub("", text.casefold())


def _words(text: str) -> list[str]:
    return _WORD.findall(text.casefold())


def _runs(words: list[str], size: int) -> set[tuple[str, ...]]:
    return {tuple(words[i:i + size]) for i in range(len(words) - size + 1)}


def parse(text: str) -> Guard:
    """Build the guard from the file's text."""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, RecursionError):
        raise PrivateFileError("is not valid JSON") from None
    if not isinstance(data, dict):
        raise PrivateFileError("must be a JSON object with 'terms' and 'draft'")
    raw_terms = data.get("terms", [])
    if not isinstance(raw_terms, list):
        raise PrivateFileError("'terms' must be a list of strings")
    terms: list[str] = []
    for index, term in enumerate(raw_terms):
        squashed = _squash(term) if isinstance(term, str) else ""
        if len(squashed) < MIN_TERM_CHARS:
            raise PrivateFileError(
                f"terms[{index}] must be a string of at least {MIN_TERM_CHARS} "
                "characters, not counting spaces, hyphens, underscores, and dots"
            )
        terms.append(squashed)
    draft = data.get("draft", "")
    if not isinstance(draft, str):
        raise PrivateFileError("'draft' must be a string")
    words = _words(draft)
    if not terms and not words:
        raise PrivateFileError("holds no terms and no draft")
    run_words = min(DRAFT_RUN_WORDS, len(words)) or DRAFT_RUN_WORDS
    return Guard(
        terms=tuple(dict.fromkeys(terms)),
        draft_runs=frozenset(_runs(words, run_words)),
        run_words=run_words,
    )


def load(path: str | os.PathLike[str]) -> Guard:
    """Read and parse the private file."""
    try:
        if os.stat(path).st_size > MAX_FILE_BYTES:
            raise PrivateFileError(f"exceeds {MAX_FILE_BYTES} bytes")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except (OSError, UnicodeDecodeError):
        raise PrivateFileError("cannot be read") from None
    return parse(text)


def _text_matches(guard: Guard, text: str) -> bool:
    if guard.terms:
        squashed = _squash(text)
        if any(term in squashed for term in guard.terms):
            return True
    if guard.draft_runs:
        return not guard.draft_runs.isdisjoint(_runs(_words(text), guard.run_words))
    return False


def contains(guard: Guard, value: Any) -> bool:
    """Whether any string inside ``value`` holds a term or a run of the draft.

    Walks dicts (keys and values), sequences, and dataclasses, so a parsed
    plan or envelope is checked as a whole.
    """
    if isinstance(value, str):
        return _text_matches(guard, value)
    if isinstance(value, dict):
        return any(contains(guard, key) or contains(guard, item) for key, item in value.items())
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(contains(guard, item) for item in value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return any(
            contains(guard, getattr(value, field.name)) for field in dataclasses.fields(value)
        )
    return False
