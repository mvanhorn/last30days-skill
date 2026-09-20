"""Deterministic topic naming and junk-shape classification for discovery.

Discovery mode surfaces short, named, content-worthy topics instead of raw
post titles. This module is the pure-function, stdlib-only stage-1 fallback
for that pipeline (used when no LLM is available, and as the deterministic
baseline the LLM path is judged against):

- ``distill_topic_name(title, snippet)`` distills a listing title into a
  2-6 word searchable topic name: question/framing scaffolding is stripped,
  proper-noun / digit-bearing entity phrases are preferred and emitted as an
  ORDERED phrase in title order (never a bag of words), and the cleaned,
  truncated title is the final fallback so the result is never empty for any
  title with word content.
- ``is_junk_shape(title, snippet)`` flags listing shapes that should never
  become topics: help-me questions, beginner asks, and first-person musings.
  Launch titles ("Show HN: ...") and entity-bearing news statements are not
  junk.

Both functions take plain strings and return plain values - no candidate
objects, no config, no I/O - so they are trivially testable and reusable.

Names produced here are used downstream as short search queries and grounding
strings, so they never carry trailing punctuation or quote characters. Per the
head-token convention, callers must never assume a distilled name appears as a
contiguous substring of any document.

Token conventions (stopwords, capital/digit entity signals) are inherited from
``entity_extract`` and extended here; unlike ``extract_text_entities`` this
module preserves title order and original casing because the output is a
human-readable phrase, not a matching set. Non-Latin (CJK) titles never crash:
they carry no Latin entity signal, so they fall through to the cleaned-title
path, capped at ``_MAX_NAME_CHARS``.
"""

from __future__ import annotations

import re
from typing import List, NamedTuple, Optional

from .entity_extract import ENTITY_STOPWORDS

_MAX_NAME_WORDS = 6
_MAX_NAME_CHARS = 80

# Extends the shared entity stopwords with pronouns, auxiliaries, contractions
# and musing filler that read as capitalized sentence-openers in titles but are
# never entities ("My", "Everyone", "Don't", ...). Deliberate casualty: the
# acronyms "US" and "IT" are swallowed by their pronoun homographs.
_ANCHOR_STOPWORDS = frozenset(ENTITY_STOPWORDS) | frozenset({
    "i", "i'm", "i've", "i'd", "i'll", "me", "my", "mine", "myself",
    "we", "we're", "we've", "our", "ours", "us",
    "you", "you're", "your", "yours",
    "am", "were", "be", "why", "when", "where", "which", "whom", "whose",
    "does", "did", "doing", "done", "should", "shall", "may", "might", "must",
    "if", "or", "so", "as", "any", "anyone", "anybody", "someone", "somebody",
    "everyone", "everybody", "nobody", "none", "no", "yes",
    "please", "thanks", "thank", "really", "actually", "very", "well",
    "while", "during", "still", "even", "ever", "never", "always",
    "don't", "dont", "can't", "cant", "won't", "wont", "isn't", "isnt",
    "aren't", "arent", "doesn't", "doesnt", "didn't", "didnt",
    "it's", "that's", "there's", "here's", "let's", "what's", "who's", "how's",
    "mean", "means", "meant", "same", "thing", "things", "stuff",
    "way", "ways", "lot", "lots", "kind", "sort",
    "today", "yesterday", "tomorrow",
})

# Characters stripped from token edges for display (internal hyphens/dots in
# "open-source" / "example.com" survive). Includes unicode dashes/ellipsis.
_EDGE_CHARS = "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~–—…"
_TRAILING_JUNK = ".,;:!?…'\"`- "

_POSSESSIVE_RE = re.compile(r"(?<=\w)'s\b", re.IGNORECASE)
_DOUBLE_QUOTE_RE = re.compile(r"[\"“”„«»]")
_LONE_APOSTROPHE_RE = re.compile(r"(?<!\w)'|'(?!\w)")
_SENTENCE_END_RE = re.compile(r"[.!?;:,]$")

# Framing scaffolding stripped (iteratively) from the start of a title before
# naming: forum labels, interrogative openers, first-person setup, politeness
# filler, and leading articles. Junk *classification* has its own patterns
# below; these only clean the string we name from.
_SCAFFOLD_RES = [re.compile(p, re.IGNORECASE) for p in (
    r"^(show hn|ask hn|tell hn|launch hn|psa|eli5|tifu|til|discussion|"
    r"question|help|advice|update|rant|vent|meta)\s*[:\-–—]\s*",
    r"^(how|what|when|where|which|why|who)\s+"
    r"(do|does|did|is|are|was|were|am|can|could|should|would|will|to|i|we|you|your|my|one)\s+",
    r"^(is|are|does|do|did|can|could|should|would|will|has|have|am)\s+"
    r"(there|it|this|anyone|anybody|someone|somebody|we|you|i|they|my|your)\s+",
    r"^(i|we)\s+(think|believe|feel|guess|wonder|noticed|realized|have run|"
    r"have been|have|had|am|was|were|just|finally|recently|need|want|"
    r"would like|tried|keep|built|made|created|wrote|spent)\s+",
    r"^(i'm|i've|i'd|we're|we've)\s+",
    r"^my\s+(coworker|co-worker|colleague|boss|friend|manager|team|company|"
    r"startup|wife|husband|partner|mom|dad|mother|father|brother|sister|"
    r"son|daughter|kid|kids|roommate|neighbor)\s+\w+\s+",
    r"^(hey|hi|hello|guys|folks|please|okay|ok|so|honestly|serious question)[,!\s]\s*",
    r"^(a|an|the)\s+",
)]

# --- junk-shape markers (matched against the cleaned, lowercased title) -----

_LAUNCH_RE = re.compile(r"^(show hn|launch hn)\b")
# Leading interrogatives: wh-words count only with a question follow-through
# ("What is the best..." is junk; "What Gemma 4 means..." is an explainer).
_WH_JUNK_RE = re.compile(
    r"^(how|what|why|when|where|which|who)\s+"
    r"(do|does|did|is|are|was|were|am|can|could|should|would|will|to|i|we|you|your|my|one)\b"
)
_AUX_JUNK_RE = re.compile(
    r"^(is|are|does|do|did|can|could|should|would|will|has|have|am)\s+"
    r"(there|it|this|anyone|anybody|someone|somebody|we|you|i|they|my|your)\b"
)
_HELP_RE = re.compile(
    r"\bneed (some |a little )?(help|advice)\b|\bplease help\b|\bhelp me\b|"
    r"^help\b|\bany (advice|recommendation|recommendations|suggestions|recs|tips)\b|"
    r"\blooking for (advice|recommendations|suggestions|help|tips)\b|"
    r"\bwhere (do|should|would) (i|we) (even )?(start|begin)\b|\bwhere to start\b|"
    r"\bbeginner (question|here)\b|\bnoob (question|here)\b|"
    r"\btotal beginner\b|\bcomplete beginner\b|\bam i missing something\b|"
    r"\brecommend me\b"
)
_MUSING_RE = re.compile(
    r"^(i think|i feel|i believe|i guess|i wonder|i have been|i've been|i keep|"
    r"my thoughts|thoughts on|unpopular opinion|hot take|am i the only one|"
    r"is it just me|anyone else|does anyone else|rant|vent|change my mind|cmv)\b"
)
_EVERYONE_RE = re.compile(
    r"\beveryone (is|does|says|seems|keeps|wants)\b.{0,80}\bbut (do|are|can|should|will|did) we\b"
)


class _Token(NamedTuple):
    display: str      # edge-punctuation-stripped, original casing
    lower: str
    is_anchor: bool   # proper-noun / digit / acronym entity signal
    breaks_after: bool  # sentence/clause boundary follows this token


def distill_topic_name(title: str, snippet: str = "") -> str:
    """Distill a listing title (+ optional snippet) into a 2-6 word topic name.

    The name is an ordered phrase built from entity anchors in title order,
    safe to use as a short search query: <= 6 words, <= 80 chars, no trailing
    punctuation, no quote characters. Never empty for any input with word
    content (the sole exception: title AND snippet contain no word characters,
    which returns "").
    """
    base = _normalize(title) or _normalize(snippet)
    if not base:
        return ""

    stripped = _strip_scaffolding(base)
    tokens = _tokenize(stripped)
    if not tokens:
        tokens = _tokenize(base)
        if not tokens:
            return ""
    words = [t.display for t in tokens]

    # Already-short titles pass through unless a stronger entity phrase is
    # buried mid-title (first word not an anchor while anchors exist).
    if len(words) <= _MAX_NAME_WORDS and (tokens[0].is_anchor or not any(t.is_anchor for t in tokens)):
        return _finalize(words)

    phrase = _entity_phrase(tokens)
    if phrase:
        return _finalize(phrase)

    # Title had no entity anchors: try the snippet's leading entity phrase.
    if snippet:
        snippet_tokens = _tokenize(_strip_scaffolding(_normalize(snippet)))
        snippet_phrase = _entity_phrase(snippet_tokens)
        if snippet_phrase:
            return _finalize(snippet_phrase)

    # Final fallback: cleaned title truncated to the word cap.
    return _finalize(words[:_MAX_NAME_WORDS])


def is_junk_shape(title: str, snippet: str = "") -> bool:
    """True when the listing shape is not content-worthy.

    Rule-based markers: leading interrogatives, help/advice asks, first-person
    musings, and trailing "?" with no named entity in the title. Launch titles
    ("Show HN: ...") and entity-bearing news statements are not junk. The
    snippet is consulted only when the title itself has no entity anchors.
    """
    cleaned = _normalize(title)
    if not cleaned:
        cleaned = _normalize(snippet)
        if not cleaned:
            return True  # nothing nameable at all
    lower = cleaned.lower()

    if _LAUNCH_RE.search(lower):
        return False
    if _WH_JUNK_RE.search(lower) or _AUX_JUNK_RE.search(lower):
        return True
    if _HELP_RE.search(lower) or _MUSING_RE.search(lower) or _EVERYONE_RE.search(lower):
        return True

    has_entity = any(t.is_anchor for t in _tokenize(cleaned))
    if lower.endswith(("?", "？")) and not has_entity:
        return True
    if not has_entity and snippet:
        snippet_lower = _normalize(snippet).lower()
        if (_HELP_RE.search(snippet_lower) or _MUSING_RE.search(snippet_lower)
                or _AUX_JUNK_RE.search(snippet_lower) or _EVERYONE_RE.search(snippet_lower)):
            return True
    return False


# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    """Collapse whitespace, drop quote characters, fold possessives ("4's" -> "4")."""
    if not text:
        return ""
    text = text.replace("’", "'").replace("‘", "'").replace("`", "'").replace("´", "'")
    text = _POSSESSIVE_RE.sub("", text)
    text = _DOUBLE_QUOTE_RE.sub(" ", text)
    text = _LONE_APOSTROPHE_RE.sub(" ", text)
    return " ".join(text.split())


def _strip_scaffolding(text: str) -> str:
    """Iteratively strip question/framing scaffolding from the title start."""
    for _ in range(6):
        before = text
        for pattern in _SCAFFOLD_RES:
            text = pattern.sub("", text, count=1).lstrip(" ,-")
        if text == before:
            break
    return text.strip()


def _is_anchor(display: str) -> bool:
    """Entity signal per entity_extract conventions: capitals, digits, acronyms."""
    if not display:
        return False
    if display.lower() in _ANCHOR_STOPWORDS:
        return False
    if any(c.isdigit() for c in display):
        return True
    if len(display) < 2:
        return False
    if display[0].isupper():
        return True
    return any(c.isupper() for c in display[1:])  # iPhone, gpt4all-style


def _tokenize(text: str) -> List[_Token]:
    """Split into display tokens, tagging entity anchors and clause boundaries."""
    tokens: List[_Token] = []
    for raw in text.split():
        display = raw.strip(_EDGE_CHARS)
        if not display:
            # Pure-punctuation token (a bare dash, "..."): clause boundary.
            if tokens:
                tokens[-1] = tokens[-1]._replace(breaks_after=True)
            continue
        tokens.append(_Token(
            display=display,
            lower=display.lower(),
            is_anchor=_is_anchor(display),
            breaks_after=bool(_SENTENCE_END_RE.search(raw)),
        ))
    return tokens


def _entity_phrase(tokens: List[_Token]) -> Optional[List[str]]:
    """Build an ordered phrase from entity-anchor runs, in title order.

    Adjacent anchor runs separated by <= 2 contentful (non-stopword,
    non-boundary) words are merged with their connecting words kept, so the
    phrase stays readable ("AI agent handle Slack", not "AI Slack"). Runs are
    then concatenated in title order up to the word cap.
    """
    runs: List[tuple[int, int]] = []  # inclusive (start, end) token indices
    i = 0
    while i < len(tokens):
        if tokens[i].is_anchor:
            j = i
            while j + 1 < len(tokens) and tokens[j + 1].is_anchor and not tokens[j].breaks_after:
                j += 1
            runs.append((i, j))
            i = j + 1
        else:
            i += 1
    if not runs:
        return None

    merged = [runs[0]]
    for start, end in runs[1:]:
        prev_start, prev_end = merged[-1]
        gap = tokens[prev_end + 1:start]
        if (
            0 < len(gap) <= 2
            and not tokens[prev_end].breaks_after
            and all(g.lower not in _ANCHOR_STOPWORDS and not g.breaks_after for g in gap)
        ):
            merged[-1] = (prev_start, end)
        else:
            merged.append((start, end))

    words: List[str] = []
    last_index: Optional[int] = None
    for start, end in merged:
        span = [t.display for t in tokens[start:end + 1]]
        if not words and len(span) > _MAX_NAME_WORDS:
            span = span[:_MAX_NAME_WORDS]
            end = start + _MAX_NAME_WORDS - 1
        if len(words) + len(span) > _MAX_NAME_WORDS:
            break
        words.extend(span)
        last_index = end

    # Readability extension: pull in one attached plural noun ("Slack replies").
    if words and len(words) < _MAX_NAME_WORDS and last_index is not None:
        nxt = tokens[last_index + 1] if last_index + 1 < len(tokens) else None
        if (
            nxt is not None
            and not tokens[last_index].breaks_after
            and not nxt.is_anchor
            and nxt.display.islower()
            and nxt.display.endswith("s")
            and nxt.lower not in _ANCHOR_STOPWORDS
        ):
            words.append(nxt.display)

    return words or None


def _finalize(words: List[str]) -> str:
    """Join to a query-safe name: char cap, no trailing punctuation or quotes."""
    name = " ".join(w for w in words if w).strip()
    if len(name) > _MAX_NAME_CHARS:
        name = name[:_MAX_NAME_CHARS].rstrip()
    name = name.strip(_TRAILING_JUNK)
    return " ".join(name.split())
