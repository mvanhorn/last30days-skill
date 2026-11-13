"""Decide whether a patent applicant or assignee string is a given company.

Stdlib only, no network. Companies file under legal names ("Apple Inc.",
"Microsoft Technology Licensing, LLC"), so names are compared by a key with
case, punctuation and trailing legal designators removed.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable

CONFIRMED = "confirmed"
NAME_MATCHED = "name-matched"
NEITHER = "neither"

_DESIGNATORS = frozenset(
    "inc incorporated corp corporation co company ltd limited llc lc lp llp "
    "pbc plc gmbh ag sa nv bv ab kk oy sas pte the".split()
)


def name_key(name: str | None) -> str:
    """A name for comparison: case-folded, HTML entities decoded, punctuation
    removed, whitespace collapsed, trailing legal designators dropped. The
    last remaining word is never dropped."""
    if not name:
        return ""
    text = html.unescape(str(name)).casefold()
    text = re.sub(r"[.'’]", "", text)
    words = re.sub(r"[^\w\s]|_", " ", text).split()
    while len(words) > 1 and words[-1] in _DESIGNATORS:
        del words[-1]
    return " ".join(words)


def _usable_key(name: str | None) -> str:
    """The key of a name, or "" when nothing but designators is left."""
    key = name_key(name)
    if not key or key in _DESIGNATORS:
        return ""
    return key


def classify_applicant(
    applicant: str | None,
    company: str | None,
    known_names: Iterable[str | None] | None = None,
) -> str:
    """Return CONFIRMED, NAME_MATCHED or NEITHER for an applicant string.

    CONFIRMED: the applicant's key equals the company's key or that of a known
    name. NAME_MATCHED: the key starts with one of those keys followed by at
    least one more whole word. NEITHER: anything else.
    """
    applicant_key = _usable_key(applicant)
    if not applicant_key:
        return NEITHER
    keys = {_usable_key(company)}
    keys.update(_usable_key(name) for name in known_names or ())
    keys.discard("")
    if applicant_key in keys:
        return CONFIRMED
    if any(applicant_key.startswith(key + " ") for key in keys):
        return NAME_MATCHED
    return NEITHER
