"""Shared query preprocessing utilities: noise-word stripping, core subject
extraction, and compound term detection. Used by all search modules."""

import re
from typing import FrozenSet, List, Optional, Set

# Common multi-word prefixes stripped from all queries (identical across modules)
PREFIXES = [
    'what are the best', 'what is the best', 'what are the latest',
    'what are people saying about', 'what do people think about',
    'how do i use', 'how to use', 'how to',
    'what are', 'what is', 'tips for', 'best practices for',
    # Hebrew question/meta prefixes
    'מה יש חדש ב', 'מה יש חדש על', 'מה אנשים אומרים על',
    'מה חדש ב', 'מה חדש על', 'איך להשתמש ב',
    'מהם המוצרים של', 'מה הם', 'מהם',
]

# Multi-word suffixes (used by bird_x)
SUFFIXES = [
    'best practices', 'use cases', 'prompt techniques',
    'prompting techniques', 'prompting tips',
]

# Base noise words shared across most modules
NOISE_WORDS = frozenset({
    # Articles/prepositions/conjunctions
    'a', 'an', 'the', 'is', 'are', 'was', 'were', 'and', 'or',
    'of', 'in', 'on', 'for', 'with', 'about', 'to',
    # Question words
    'how', 'what', 'which', 'who', 'why', 'when', 'where',
    'does', 'should', 'could', 'would',
    # Research/meta descriptors
    'best', 'top', 'good', 'great', 'awesome', 'killer',
    'latest', 'new', 'news', 'update', 'updates',
    'trendiest', 'trending', 'hottest', 'hot', 'popular', 'viral',
    'practices', 'features', 'guide', 'tutorial',
    'recommendations', 'advice', 'review', 'reviews',
    'usecases', 'examples', 'comparison', 'versus', 'vs',
    'plugin', 'plugins', 'skill', 'skills', 'tool', 'tools',
    # Prompting meta words
    'prompt', 'prompts', 'prompting', 'techniques', 'tips',
    'tricks', 'methods', 'strategies', 'approaches',
    # Action words
    'using', 'uses', 'use',
    # Misc filler
    'people', 'saying', 'think', 'said', 'lately',
    # Hebrew function words / prepositions / filler
    'מה', 'מי', 'איך', 'למה', 'איפה', 'מתי', 'כמה', 'האם',
    'של', 'על', 'עם', 'אל', 'את', 'בין', 'כי', 'כן', 'לא',
    'יש', 'אין', 'כבר', 'רק', 'גם', 'אבל', 'כך', 'זה', 'זו',
    'חדש', 'חדשים', 'טוב', 'טובים', 'הכי', 'ביותר',
    'מוצרים', 'מבצע', 'מבצעים', 'חדשות', 'עדכונים',
})


# Shared noise sets for adapter `_extract_core_subject` wrappers.
#
# SOCIAL_NOISE: short-form micro-social platforms (Bluesky, Threads, Truth Social)
# where research/meta words rarely appear in the body of a post.
SOCIAL_NOISE = frozenset({
    'best', 'top', 'good', 'great', 'awesome',
    'latest', 'new', 'news', 'update', 'updates',
    'trending', 'hottest', 'popular', 'viral',
    'practices', 'features', 'recommendations', 'advice',
    'or', 'and',
})

# VIRAL_NOISE: viral / discovery platforms (TikTok, Instagram, Pinterest) and
# the base for YouTube. Adds 'killer', the prompt-meta cluster, and the
# methodology cluster on top of SOCIAL_NOISE.
VIRAL_NOISE = SOCIAL_NOISE | frozenset({
    'killer',
    'prompt', 'prompts', 'prompting',
    'methods', 'strategies', 'approaches',
})


def extract_core_subject(
    topic: str,
    *,
    noise: Optional[FrozenSet[str]] = None,
    max_words: Optional[int] = None,
    strip_suffixes: bool = False,
) -> str:
    """Extract core subject from a verbose search query.

    Strips common question/meta prefixes and noise words to produce a
    compact search-friendly query. Platforms customize via parameters.

    Args:
        topic: Raw user query
        noise: Override noise word set (default: NOISE_WORDS)
        max_words: Cap result to N words (default: no cap)
        strip_suffixes: Also strip trailing multi-word suffixes (bird_x uses this)

    Returns:
        Cleaned query string
    """
    text = topic.lower().strip()
    if not text:
        return text

    # Phase 1: Strip multi-word prefixes (longest first, stop after first match)
    for p in PREFIXES:
        if text.startswith(p + ' '):
            text = text[len(p):].strip()
            break

    # Phase 2: Strip multi-word suffixes (opt-in)
    if strip_suffixes:
        for s in SUFFIXES:
            if text.endswith(' ' + s):
                text = text[:-len(s)].strip()
                break

    # Phase 3: Filter individual noise words
    noise_set = noise if noise is not None else NOISE_WORDS
    words = text.split()
    filtered = [w for w in words if w not in noise_set]

    # Apply word cap if requested
    if max_words is not None and filtered:
        filtered = filtered[:max_words]

    result = ' '.join(filtered) if filtered else text
    return result.rstrip('?!.') if not max_words else (result or topic.lower().strip())


def infer_query_intent(topic: str) -> str:
    """Classify a topic into a coarse intent for adapter query expansion.

    Returns one of: ``comparison``, ``how_to``, ``opinion``, ``product``,
    ``prediction``, ``breaking_news`` (default).

    The ``how_to`` regex covers both prefixed forms (``how to install``)
    and bare imperatives (``configure``, ``troubleshoot``, ``debug``,
    ``fix``). Adapters that previously kept their own copy of this
    classifier had drifted to subtly different word lists; this is the
    superset.

    Polymarket keeps a custom narrower classifier (prediction-only) and
    does NOT delegate here; its expansion only needs that signal.
    """
    text = topic.lower().strip()
    if re.search(r"\b(vs|versus|compare|difference between)\b", text):
        return "comparison"
    if re.search(
        r"\b(how to|tutorial|guide|setup|step by step|deploy|install|"
        r"configuration|configure|troubleshoot|troubleshooting|error|errors|"
        r"fix|debug)\b",
        text,
    ):
        return "how_to"
    if re.search(r"\b(thoughts on|worth it|should i|opinion|review)\b", text):
        return "opinion"
    if re.search(r"\b(pricing|feature|features|best .* for)\b", text):
        return "product"
    if re.search(r"\b(predict|prediction|odds|forecast|chance)\b", text):
        return "prediction"
    return "breaking_news"


def extract_compound_terms(topic: str) -> List[str]:
    """Detect multi-word terms that should be quoted in search queries.

    Identifies:
    - Hyphenated terms: "multi-agent", "vc-backed"
    - Title-cased multi-word names: "Claude Code", "React Native"

    Returns list of terms suitable for quoting (e.g., '"multi-agent"').
    """
    terms: List[str] = []

    # Hyphenated terms
    for match in re.finditer(r'\b\w+-\w+(?:-\w+)*\b', topic):
        terms.append(match.group())

    # Title-cased sequences (2+ capitalized words in a row)
    for match in re.finditer(r'(?:[A-Z][a-z]+\s+){1,}[A-Z][a-z]+', topic):
        terms.append(match.group())

    return terms
