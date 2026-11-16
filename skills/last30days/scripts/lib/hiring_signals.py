"""Hiring Signals analysis from normalized jobs SourceItems."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

from . import job_boilerplate, schema


# Bare words a company's own text uses on every role ("growth", "design",
# "solutions", "platform", "model", "insights", "admin", "warehouse") are
# left out or narrowed to the role phrase that carries them.
THEME_KEYWORDS: dict[str, tuple[str, ...]] = {
    "enterprise readiness": (
        "enterprise", "soc 2", "sso", "security", "compliance", "procurement",
        "governance", "audit",
    ),
    "go-to-market": (
        "sales", "account executive", "customer success", "solutions engineer",
        "solution engineer", "solutions architect", "solution architect",
        "partnership", "revenue", "demand generation", "marketing",
    ),
    "ai and machine learning": (
        "machine learning", "ml", "ai", "llm", "language model", "foundation model",
        "research scientist", "applied scientist", "data scientist",
    ),
    "infrastructure and reliability": (
        "infrastructure", "platform engineer", "platform engineering", "devops", "sre",
        "site reliability", "reliability", "cloud", "distributed systems", "backend",
        "data center", "data centre",
    ),
    "product expansion": (
        "product manager", "product designer", "product design", "growth engineer",
        "growth marketing", "growth product", "mobile", "frontend", "ux",
    ),
    "data and analytics": (
        "data", "analytics", "business intelligence", "data warehouse", "etl",
    ),
}

# Phrases a keyword must not be followed by: a "Data Center Technician" works on
# infrastructure, not data and analytics.
_KEYWORD_EXCLUSIONS: dict[str, str] = {
    "data": r"(?!\s+cent(?:er|re)s?\b)",
}

SENIORITY_TERMS = ("head of", "director", "vp", "principal", "staff", "lead", "founding")

# Leadership markers that establish/own a function (a first-of-function hire).
LEADERSHIP_TERMS = ("head of", "chief", "global head", "svp", "vp of", "vp,", "director of")

# Title qualifiers that are level/logistics noise, not a specialized capability.
_GENERIC_QUALIFIERS = {
    "senior", "staff", "principal", "lead", "junior", "mid", "sr", "jr",
    "i", "ii", "iii", "iv", "remote", "hybrid", "onsite", "on-site",
    "contract", "intern", "full-time", "part-time", "us", "uk", "emea",
}
# Words a title tail made only of says a level or a team, not a capability:
# "Engineer, Entry Level", "Engineer, Multiple Teams".
_LEVEL_AND_TEAM_WORDS = {
    *_GENERIC_QUALIFIERS, "entry", "level", "new", "grad", "graduate", "associate",
    "experienced", "internship", "team", "teams", "multiple", "various", "all", "any",
    "org", "organization", "group", "department", "division", "general",
}
# Regions a title tail names that are places, not specialties.
_REGION_WORDS = {
    "apac", "latam", "americas", "north america", "europe", "global", "worldwide",
    "usa", "united states",
}


def analyze(
    items: list[schema.SourceItem],
    *,
    explicit: bool,
    topic: str = "",
) -> dict[str, Any]:
    """Return a structured Hiring Signals summary for report artifacts."""
    if not items:
        return {
            "mode": "explicit" if explicit else "standard",
            "company_size_tier": "unknown",
            "include": False,
            "signals": [],
            "strategic_candidates": [],
            "omitted_reason": "no current public jobs evidence found",
        }

    size_tier = infer_company_size(items, topic=topic)
    themes = _theme_items(items)
    signals = [_build_signal(theme, theme_items, size_tier) for theme, theme_items in themes.items()]
    signals = [signal for signal in signals if signal["evidence_count"] > 0]
    signals.sort(key=lambda s: (s["confidence_score"], s["evidence_count"]), reverse=True)

    # Strategic single-role signals are NOT count-gated: a founding or
    # first-of-function role can outweigh a department's worth of headcount.
    # The engine only FLAGS these; the reasoning model judges true novelty
    # (e.g. whether "Human Simulation" is a new bet for this company).
    strategic_candidates = _strategic_candidates(items)

    include = (
        bool(signals) or bool(strategic_candidates)
    ) if explicit else any(_passes_standard_threshold(signal, size_tier) for signal in signals)
    if not explicit:
        signals = [signal for signal in signals if _passes_standard_threshold(signal, size_tier)]
    _assign_evidence(signals, themes)

    return {
        "mode": "explicit" if explicit else "standard",
        "company_size_tier": size_tier,
        "include": include,
        "signals": signals,
        "strategic_candidates": strategic_candidates,
        "omitted_reason": "" if include else _omitted_reason(items, size_tier, signals),
    }


def infer_company_size(items: list[schema.SourceItem], *, topic: str = "") -> str:
    """Infer a coarse company-size tier from jobs evidence."""
    topic_lower = topic.lower()
    firmographic_text = " ".join(
        " ".join([
            str(item.metadata.get("company_size") or ""),
            topic,
        ])
        for item in items
    ).lower()
    text = " ".join(
        " ".join([
            item.title,
            item.body[:1000],
            str(item.metadata.get("company_size") or ""),
            str(item.metadata.get("source_domain") or ""),
            topic,
        ])
        for item in items
    ).lower()
    count = len(items)
    # Brand-name shortcut must match the COMPANY being researched (the topic),
    # never the job-description body - JDs list enterprise customers (e.g.
    # "trusted by Microsoft, Google"), which would misclassify a startup as
    # mega-cap and suppress its real signals.
    if re.search(r"\b(apple|uber|google|microsoft|amazon|meta|netflix)\b", topic_lower):
        return "mega-cap"
    if count >= 200 or re.search(r"\b(fortune 500|thousands of employees)\b", firmographic_text):
        return "large-enterprise"
    if count >= 35 or re.search(r"\b(series [cd]|public company)\b", text):
        return "growth"
    if count <= 12 or re.search(r"\b(founding|seed|series a|early[- ]stage|startup)\b", text):
        return "startup"
    return "mid-market"


# Keywords match as whole words (plural "s" allowed), so "ai" does not match "retail" or
# "training" and "ml" does not match "html".
_THEME_PATTERNS: dict[str, re.Pattern[str]] = {
    theme: re.compile(r"\b(?:" + "|".join(
        re.escape(keyword) + r"s?\b" + _KEYWORD_EXCLUSIONS.get(keyword, "")
        for keyword in keywords
    ) + ")")
    for theme, keywords in THEME_KEYWORDS.items()
}
_ALL_KEYWORDS = frozenset(keyword for keywords in THEME_KEYWORDS.values() for keyword in keywords)


def _keyword(matched: str) -> str:
    """The keyword a match stands for: its text, less a plural "s"."""
    return matched if matched in _ALL_KEYWORDS else matched[:-1]


# Where a theme keyword was found, strongest first. A title says what the role
# is; a department says where it sits; a description sentence only hints.
_TITLE_MATCH, _DEPARTMENT_MATCH, _BODY_MATCH = 3, 2, 1

# Examples kept per theme.
EVIDENCE_PER_THEME = 5


def _department(item: schema.SourceItem) -> str:
    return str(item.metadata.get("department") or item.container or "").strip()


def _description(item: schema.SourceItem) -> str:
    """The posting's description, without the title, department and location lines.

    Normalized job bodies open with those fields; they are matched on their own.
    """
    header = {
        job_boilerplate.key(part)
        for part in (item.title, _department(item), str(item.metadata.get("location") or ""))
        if part
    }
    return "\n".join(
        line for line in str(item.body or "").splitlines()
        if job_boilerplate.key(line) not in header
    )


def _match_strength(
    pattern: re.Pattern[str], title: str, department: str, body: str,
) -> tuple[int, int]:
    """(strongest field matched, distinct keywords matched), or (0, 0)."""
    best = 0
    keywords: set[str] = set()
    for field, text in ((_TITLE_MATCH, title), (_DEPARTMENT_MATCH, department), (_BODY_MATCH, body)):
        hits = {_keyword(match.group()) for match in pattern.finditer(text)}
        if hits:
            best = max(best, field)
            keywords |= hits
    return best, len(keywords)


def _ranked_theme_items(
    items: list[schema.SourceItem],
) -> dict[str, list[tuple[tuple[int, int], schema.SourceItem]]]:
    """Each theme's roles with their match strength, strongest first.

    Themes match a role's title and department, and its description only after
    sentences that ``job_boilerplate.BOILERPLATE_MIN_ROLES`` or more distinct
    roles share are removed: company text every posting repeats themes nothing.
    """
    descriptions = [_description(item) for item in items]
    shared = job_boilerplate.shared_sentence_counts(
        (item.title, _department(item), text) for item, text in zip(items, descriptions)
    )
    themed: dict[str, list[tuple[tuple[int, int], schema.SourceItem]]] = defaultdict(list)
    for item, text in zip(items, descriptions):
        title = item.title.lower()
        department = _department(item).lower()
        body = " ".join(job_boilerplate.role_sentences(text, shared)).lower()
        matched = False
        for theme, pattern in _THEME_PATTERNS.items():
            strength = _match_strength(pattern, title, department, body)
            if strength[0]:
                themed[theme].append((strength, item))
                matched = True
        if not matched:
            fallback = department or "general hiring"
            themed[fallback].append(((0, 0), item))
    # sort is stable: equal strengths keep the board's order.
    return {
        theme: sorted(ranked, key=lambda pair: pair[0], reverse=True)
        for theme, ranked in themed.items()
    }


def _theme_items(items: list[schema.SourceItem]) -> dict[str, list[schema.SourceItem]]:
    return {
        theme: [item for _strength, item in ranked]
        for theme, ranked in _ranked_theme_items(items).items()
    }


def _evidence_entry(item: schema.SourceItem) -> dict[str, Any]:
    return {
        "title": item.title,
        "url": item.url,
        "department": item.metadata.get("department") or item.container or "",
        "published_at": item.published_at,
    }


def _assign_evidence(
    signals: list[dict[str, Any]], themes: dict[str, list[schema.SourceItem]],
) -> None:
    """Give each signal up to ``EVIDENCE_PER_THEME`` examples no other signal shows.

    Signals take turns in their ranked order, each taking its strongest role not
    yet shown, so every theme gets its best distinct example before any gets a
    second. ``evidence_count`` stays the theme's full role count.
    """
    shown: set[str] = set()
    picks: list[list[schema.SourceItem]] = [[] for _signal in signals]
    cursors = [0] * len(signals)
    for _round in range(EVIDENCE_PER_THEME):
        for index, signal in enumerate(signals):
            ranked = themes.get(signal["theme"], [])
            while cursors[index] < len(ranked):
                item = ranked[cursors[index]]
                cursors[index] += 1
                role = item.url or item.item_id
                if role not in shown:
                    shown.add(role)
                    picks[index].append(item)
                    break
    for signal, items in zip(signals, picks):
        signal["evidence"] = [_evidence_entry(item) for item in items]


def _build_signal(theme: str, items: list[schema.SourceItem], size_tier: str) -> dict[str, Any]:
    titles = [item.title for item in items if item.title]
    departments = [
        str(item.metadata.get("department") or item.container or "").strip()
        for item in items
        if str(item.metadata.get("department") or item.container or "").strip()
    ]
    senior_roles = [
        title for title in titles
        if any(term in title.lower() for term in SENIORITY_TERMS)
    ]
    strategic_count = sum(1 for title in titles if _is_strategic_title(title))
    score = _confidence_score(
        len(items), len(set(departments)), len(senior_roles), size_tier,
        strategic_count=strategic_count,
    )
    evidence = [_evidence_entry(item) for item in items[:EVIDENCE_PER_THEME]]
    return {
        "theme": theme,
        "interpretation": _interpretation(theme),
        "confidence": _confidence_label(score),
        "confidence_score": score,
        "evidence_count": len(items),
        "departments": [name for name, _count in Counter(departments).most_common(3)],
        "senior_roles": senior_roles[:3],
        "evidence": evidence,
    }


def _confidence_score(
    count: int,
    department_count: int,
    senior_count: int,
    size_tier: str,
    strategic_count: int = 0,
) -> int:
    # Count no longer dominates: founding/first-of-function and seniority can
    # let a small cluster outrank a large generic one (a "new bet" beating
    # "doubling down"). The reasoning model still makes the final novelty call.
    score = count * 12 + min(department_count, 3) * 6 + senior_count * 10 + strategic_count * 14
    if size_tier == "startup":
        score += 20
    elif size_tier == "mid-market":
        score += 10
    elif size_tier == "growth":
        score -= 5
    elif size_tier == "large-enterprise":
        score -= 25
    elif size_tier == "mega-cap":
        score -= 40
    return max(0, min(100, score))


def _passes_standard_threshold(signal: dict[str, Any], size_tier: str) -> bool:
    thresholds = {
        "startup": (2, 50),
        "mid-market": (3, 58),
        "growth": (4, 65),
        "large-enterprise": (6, 78),
        "mega-cap": (8, 86),
        "unknown": (3, 62),
    }
    min_count, min_score = thresholds.get(size_tier, thresholds["unknown"])
    return signal["evidence_count"] >= min_count and signal["confidence_score"] >= min_score


def _confidence_label(score: int) -> str:
    if score >= 75:
        return "high"
    if score >= 50:
        return "medium"
    return "low"


def _interpretation(theme: str) -> str:
    if theme == "general hiring":
        return "hiring activity is visible, but the priority signal is diffuse"
    return f"appears to be increasing focus on {theme}"


def _strategic_candidates(items: list[schema.SourceItem]) -> list[dict[str, Any]]:
    """Flag individual roles worth surfacing regardless of how many share a theme.

    Pure structural detection (founding, first-of-function, specialized
    qualifier, geographic novelty) - no semantic novelty judgment, which is
    left to the reasoning model. Guarantees these roles reach synthesis instead
    of being averaged away by count-weighting.
    """
    item_locations = [(item, _norm_location(item)) for item in items]
    # A place counts once however the board spells it: "Seattle, WA" is
    # the "Seattle" another role names.
    place_counts = Counter(place_key(loc) for _item, loc in item_locations if place_key(loc))
    dominant = max(place_counts.values()) if place_counts else 0
    places = set(place_counts)

    scored: list[tuple[int, dict[str, Any]]] = []
    for item, location in item_locations:
        flags = _title_flags(item.title or "", places=places)
        if location and place_counts.get(place_key(location), 0) == 1 and dominant >= 3:
            flags.append("new-geo")
        if not flags:
            continue
        priority = (
            ("founding" in flags) * 4
            + ("new-geo" in flags) * 3
            + ("leadership" in flags) * 2
            + ("specialized" in flags) * 1
        )
        scored.append((priority, {
            "title": item.title,
            "url": item.url,
            "department": str(item.metadata.get("department") or item.container or "").strip(),
            "location": location,
            "published_at": item.published_at,
            "flags": flags,
        }))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [candidate for _priority, candidate in scored[:10]]


def _title_flags(title: str, *, places: set[str] | frozenset[str] = frozenset()) -> list[str]:
    """Structural strategic flags derivable from a title alone (no geo).

    ``places`` are the ``place_key`` of every location on the board, so a
    title tail naming one of them is not taken for a specialty.
    """
    lowered = title.lower()
    flags: list[str] = []
    if "founding" in lowered or re.search(r"\bfirst\b", lowered):
        flags.append("founding")
    if any(term in lowered for term in LEADERSHIP_TERMS):
        flags.append("leadership")
    if _specialization(title, places=places):
        flags.append("specialized")
    return flags


def _is_strategic_title(title: str) -> bool:
    return bool(_title_flags(title))


def _specialization(title: str, *, places: set[str] | frozenset[str] = frozenset()) -> str:
    """Return a specialized sub-domain qualifier from a title, or ''.

    "Research Scientist, Human Simulation" -> "Human Simulation".
    "Engineer (Forward Deployed)" -> "Forward Deployed".
    "Engineer, Senior" -> "" (generic level word, not a capability).
    "Engineer, Multiple Teams" -> "" (team words only).
    "Engineer, Seattle" -> "" when ``places`` holds "seattle".
    """
    tail = ""
    paren = re.search(r"\(([^)]+)\)", title)
    if paren:
        tail = paren.group(1).strip()
    elif "," in title:
        tail = title.rsplit(",", 1)[1].strip()
    if not tail or len(tail) < 4:
        return ""
    lowered = tail.lower()
    if lowered in _GENERIC_QUALIFIERS or lowered in _REGION_WORDS:
        return ""
    if place_key(lowered) in places:
        return ""
    words = re.findall(r"[a-z0-9]+", lowered)
    if all(word in _LEVEL_AND_TEAM_WORDS or word.isdigit() for word in words):
        return ""
    return tail


def place_key(location: str) -> str:
    """The city, or the country when that is all it names, a location
    string starts with: "Seattle, WA" and "Seattle, Washington, USA" are
    both "seattle"."""
    return location.split(",", 1)[0].strip().lower()


def _norm_location(item: schema.SourceItem) -> str:
    return str(item.metadata.get("location") or "").strip().lower()


def _omitted_reason(
    items: list[schema.SourceItem],
    size_tier: str,
    signals: list[dict[str, Any]],
) -> str:
    if not items:
        return "no current public jobs evidence found"
    if size_tier in {"large-enterprise", "mega-cap"}:
        return "jobs evidence is too diffuse for the inferred company size"
    if not signals:
        return "jobs evidence did not cluster into a clear signal"
    return "jobs evidence is too thin for standard-report inclusion"
