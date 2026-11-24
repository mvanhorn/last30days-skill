"""Shared query preprocessing utilities: noise-word stripping, core subject
extraction, and compound term detection. Used by all search modules."""

import re
from typing import FrozenSet, List, Optional, Set

# Common multi-word prefixes stripped from all queries (identical across modules)
PREFIXES = [
    'what are the best', 'what is the best', 'what are the latest',
    'what are people saying about', 'what do people think about',
    'how do i use', 'how do you use', 'how can i use', 'how to use',
    'how do you', 'how can i', 'how to',
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
    # Drop the question's closing punctuation first, so that it neither keeps
    # the last word from matching a suffix or noise word nor survives the
    # max_words path ("What is Claude Code?" -> "claude code", not "claude code?")
    text = text.rstrip('?!.').strip() or text

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


_COMPOUND_BRAND_TOKEN = r'(?:[A-Z][A-Za-z0-9]*|[a-z]+[A-Z][A-Za-z0-9]*)'
_COMPOUND_BRAND_TOKEN_RE = re.compile(rf'^{_COMPOUND_BRAND_TOKEN}$')
# Unicode- and symbol-safe boundaries: reject mid-word slices across non-ASCII
# letters (\w), dotted brands (Next.JS), straight/curly apostrophes (O'Reilly,
# O’Reilly), ampersands (AT&T), hyphens (Multi-Agent), and symbol suffixes
# (C++, C#), while still allowing trailing sentence punctuation like periods
# not followed by a word character.
_COMPOUND_CAPITALIZED_RUN_RE = re.compile(
    rf"(?<![\w'.\u2019+#&-])(?:{_COMPOUND_BRAND_TOKEN}\s+)+{_COMPOUND_BRAND_TOKEN}"
    rf"(?![\w'\u2019+#&-]|\.\w)"
)
COMPOUND_ENTITY_PHRASE_RE = re.compile(
    rf"^{_COMPOUND_BRAND_TOKEN}(?:\s+{_COMPOUND_BRAND_TOKEN}){{1,2}}$"
)
_COMPOUND_EDGE_NOISE = NOISE_WORDS | frozenset({
    'i', 'you', 'we', 'my', 'your', 'our', 'me', 'can', 'do', 'did',
    'this', 'that', 'from',
    'free', 'cheap', 'recommended', 'favorite', 'favourite',
    'alternative', 'alternatives',
    'workflow', 'workflows',
    'example', 'tutorials',
    'application', 'applications',
    'practice', 'production',
    'case', 'cases',
})
# Question and research/meta words that never open a brand name and are always
# trimmed from the start of a capitalized run.
_UNAMBIGUOUS_LEADING_PROMPT_WORDS = frozenset({
    'how', 'what', 'which', 'who', 'why', 'when', 'where',
    'does', 'do', 'did', 'should', 'could', 'would', 'can',
    'is', 'are', 'was', 'were',
    'i', 'you', 'we', 'my', 'your', 'our', 'me',
    'using', 'uses', 'use',
    'latest', 'trending', 'trendiest', 'hottest', 'popular', 'viral',
    'compare', 'versus', 'vs', 'comparison',
    'alternative', 'alternatives',
    'review', 'reviews', 'guide', 'tutorial', 'tutorials',
    'tips', 'tricks', 'recommendations', 'advice',
    'news', 'update', 'updates',
})
_QUESTION_STARTERS = frozenset({'how', 'what', 'which', 'who', 'why', 'when', 'where'})
# Soft leading fillers trimmed only when 3+ words remain (e.g. "Best GitHub
# Actions" -> "GitHub Actions", "Best React Native Tips" -> "React Native"),
# keeping 2-word brands ("Best Buy", "Best Egg", "The Verge") and "New"/"On"-
# prefixed multi-word brands ("New Balance", "New York Times", "On Running",
# "On Running Cloudmonster") intact.
_SOFT_LEADING_NOISE = (
    _COMPOUND_EDGE_NOISE - _UNAMBIGUOUS_LEADING_PROMPT_WORDS - frozenset({'new', 'on'})
)
_TITLE_CASE_CONNECTORS = frozenset({
    'a', 'an', 'the', 'for', 'of', 'in', 'on', 'to', 'with', 'and', 'or', 'vs',
})
_ENTITY_JOINERS = frozenset({'and', 'or', 'vs'})


def _is_capitalized_or_brand(tok: str) -> bool:
    return tok[0].isupper() or _COMPOUND_BRAND_TOKEN_RE.fullmatch(tok) is not None


def _is_title_cased_prompt(topic: str) -> bool:
    """True when a topic uses Title Case across the whole prompt, leaving no
    case signal to distinguish proper nouns from prompt framing.

    Recognizes:
    - 4+ word strict every-word Title Case ("Best AI Coding Tools For Python
      Developers", "Hermes Agent Actual Use Cases")
    - 3+ word Title-Cased question/how-to prompts starting with a question word
      ("How to Install Docker", "What Is Docker")
    - Standard English Title Case with lowercase minor connectors and at least
      4 capitalized words ("Best AI Coding Tools for Python Developers"), while
      keeping short brand-plus-subject queries ("The Verge on AI") and bare
      entity comparisons/lists ("Claude Code and React Native", "New Balance vs
      On Running", "Best Buy vs Target") eligible.
    """
    alpha_words = [
        tok for tok in re.findall(r"\w+(?:['\u2019-]\w+)*", topic)
        if tok[0].isalpha()
    ]
    if len(alpha_words) < 3:
        return False
    all_capped = all(_is_capitalized_or_brand(tok) for tok in alpha_words)
    if all_capped and len(alpha_words) >= 4:
        return True
    lowercase_words = [tok for tok in alpha_words if not _is_capitalized_or_brand(tok)]
    if not all(tok in _TITLE_CASE_CONNECTORS for tok in lowercase_words):
        return False
    if alpha_words[0].lower() in _QUESTION_STARTERS:
        return True
    if len(alpha_words) < 4:
        return False
    non_connector_words = [tok for tok in alpha_words if tok not in _TITLE_CASE_CONNECTORS]
    if len(non_connector_words) < 4:
        return False
    if all(tok in _ENTITY_JOINERS for tok in lowercase_words) and not any(
        tok.lower() in _UNAMBIGUOUS_LEADING_PROMPT_WORDS for tok in non_connector_words
    ):
        return False
    return True


def extract_compound_terms(topic: str) -> List[str]:
    """Detect multi-word terms that should be quoted in search queries.

    Identifies:
    - Hyphenated terms: "multi-agent", "vc-backed"
    - Short capitalized/brand multi-word names (2-3 words): "Claude Code",
      "React Native", "GitHub Actions", "MacBook Pro", "DeepSeek R1",
      "iPhone Pro", "New Balance", "On Running", "Best Buy", "Best Egg",
      "The Verge"

    Anchored to full Unicode/symbol-safe word boundaries so PascalCase and
    camelCase brands are never sliced mid-word, requires mixed-case brand
    tokens (avoiding ALL-CAPS generic phrases like "Best VPN" / "Top CRM"),
    and skips full Title-Cased prompts or runs longer than 3 words after
    trimming noise/intent-modifier edges.
    """
    terms: List[str] = []

    # Hyphenated terms
    for match in re.finditer(r'\b\w+-\w+(?:-\w+)*\b', topic):
        terms.append(match.group())

    if _is_title_cased_prompt(topic):
        return terms

    # Word-bounded capitalized / brand sequences (2-3 words after edge trim)
    for match in _COMPOUND_CAPITALIZED_RUN_RE.finditer(topic):
        words = match.group().split()
        while words and (
            words[0].lower() in _UNAMBIGUOUS_LEADING_PROMPT_WORDS
            or (len(words) > 2 and words[0].lower() in _SOFT_LEADING_NOISE)
        ):
            words.pop(0)
        while words and words[-1].lower() in _COMPOUND_EDGE_NOISE:
            words.pop()
        if (
            len(words) == 2
            and words[0].lower() in _COMPOUND_EDGE_NOISE
            and words[1].isupper()
        ):
            continue
        if (
            2 <= len(words) <= 3
            and not all(len(w) == 1 for w in words)
            and not all(w.lower() in _COMPOUND_EDGE_NOISE for w in words)
        ):
            phrase = " ".join(words)
            if phrase not in terms:
                terms.append(phrase)

    return terms


def leading_mentions(text: Optional[str]) -> List[str]:
    """Return the handles a post is directed at: the leading run of @mentions in the text.

    X replies open with the target handle(s) (e.g. "@someone thanks!"), so the
    leading run identifies who the post is addressed to. A mention later in the
    body is not a reply target and is intentionally ignored. Returns normalized
    (``@``-stripped, lowercased) handles, in order. Shared by every X-shaped
    source adapter (bird, xquik) so leading-mention parsing has one definition.
    """
    out: List[str] = []
    for token in (text or "").split():
        tok = token.strip(",.:;!?")
        if tok.startswith("@") and len(tok) > 1:
            out.append(tok[1:].lower())
        else:
            break
    return out
