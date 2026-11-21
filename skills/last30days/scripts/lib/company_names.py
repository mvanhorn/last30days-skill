"""Decide whether a patent applicant or assignee string is a given company.

Stdlib only, no network. Companies file under legal names ("Apple Inc.",
"Microsoft Technology Licensing, LLC"), so names are compared by a key with
case, punctuation and trailing legal designators removed. A name that adds
only corporate-structure words to the company's key ("OpenAI OpCo",
"Intellectual Ventures II") is the company's too.
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

# Words a company adds to its own name for a holding, licensing or numbered
# entity. Two-word phrases count only as a pair: "technology" or "property"
# alone is a business line, not structure.
_STRUCTURE_WORDS = frozenset("opco holdco holding holdings group ip".split())
_STRUCTURE_PHRASES = (("technology", "licensing"), ("intellectual", "property"))
# Bare numerals, and roman numerals up to 39, so ordinary words such as "mix"
# or "civil" are not read as numbers.
_NUMERAL = re.compile(r"\d+|(?=[ivx])x{0,3}(?:ix|iv|v?i{0,3})")


def _structure_only(words: list[str]) -> bool:
    """True when ``words`` is one or more corporate-structure words."""
    if not words:
        return False
    i = 0
    while i < len(words):
        pair = tuple(words[i:i + 2])
        if pair in _STRUCTURE_PHRASES:
            i += 2
        elif words[i] in _STRUCTURE_WORDS or _NUMERAL.fullmatch(words[i]):
            i += 1
        else:
            return False
    return True


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
    name, or is one of those keys followed only by corporate-structure words
    (opco, holdco, holding, holdings, group, technology licensing, ip,
    intellectual property, numerals). NAME_MATCHED: the key starts with one of
    those keys followed by at least one other whole word. NEITHER: anything
    else.
    """
    applicant_key = _usable_key(applicant)
    if not applicant_key:
        return NEITHER
    keys = {_usable_key(company)}
    keys.update(_usable_key(name) for name in known_names or ())
    keys.discard("")
    if applicant_key in keys:
        return CONFIRMED
    extended = [
        applicant_key[len(key) + 1:].split()
        for key in keys
        if applicant_key.startswith(key + " ")
    ]
    if any(_structure_only(rest) for rest in extended):
        return CONFIRMED
    if extended:
        return NAME_MATCHED
    return NEITHER
