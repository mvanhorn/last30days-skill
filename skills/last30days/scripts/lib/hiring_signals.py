"""Hiring Signals analysis from normalized jobs SourceItems."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

from . import schema


THEME_KEYWORDS: dict[str, tuple[str, ...]] = {
    "enterprise readiness": (
        "enterprise", "soc 2", "sso", "security", "compliance", "procurement",
        "admin", "governance", "audit",
    ),
    "go-to-market": (
        "sales", "account executive", "customer success", "solutions", "partnership",
        "revenue", "demand generation", "marketing",
    ),
    "ai and machine learning": (
        "machine learning", "ml", "ai", "llm", "model", "research scientist",
        "applied scientist", "data scientist",
    ),
    "infrastructure and reliability": (
        "infrastructure", "platform", "devops", "sre", "reliability", "cloud",
        "distributed systems", "backend",
    ),
    "product expansion": (
        "product manager", "product designer", "growth", "activation", "mobile",
        "frontend", "design",
    ),
    "data and analytics": (
        "data", "analytics", "business intelligence", "warehouse", "etl",
        "insights",
    ),
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
    if re.search(r"\b(apple|uber|google|microsoft|amazon|meta|netflix)\b", topic.lower()):
        return "mega-cap"
    if count >= 200 or re.search(r"\b(fortune 500|thousands of employees)\b", text):
        return "large-enterprise"
    if count >= 35 or re.search(r"\b(series [cd]|public company)\b", text):
        return "growth"
    if count <= 12 or re.search(r"\b(founding|seed|series a|early[- ]stage|startup)\b", text):
        return "startup"
    return "mid-market"


def _theme_items(items: list[schema.SourceItem]) -> dict[str, list[schema.SourceItem]]:
    themed: dict[str, list[schema.SourceItem]] = defaultdict(list)
    for item in items:
        text = f"{item.title} {item.body}".lower()
        matched = False
        for theme, keywords in THEME_KEYWORDS.items():
            if any(keyword in text for keyword in keywords):
                themed[theme].append(item)
                matched = True
        if not matched:
            dept = str(item.metadata.get("department") or item.container or "").strip().lower()
            fallback = dept or "general hiring"
            themed[fallback].append(item)
    return dict(themed)


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
    evidence = [
        {
            "title": item.title,
            "url": item.url,
            "department": item.metadata.get("department") or item.container or "",
            "published_at": item.published_at,
        }
        for item in items[:5]
    ]
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
    location_counts = Counter(loc for _item, loc in item_locations if loc)
    dominant = max(location_counts.values()) if location_counts else 0

    scored: list[tuple[int, dict[str, Any]]] = []
    for item, location in item_locations:
        flags = _title_flags(item.title or "")
        if location and location_counts.get(location, 0) == 1 and dominant >= 3:
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


def _title_flags(title: str) -> list[str]:
    """Structural strategic flags derivable from a title alone (no geo)."""
    lowered = title.lower()
    flags: list[str] = []
    if "founding" in lowered or re.search(r"\bfirst\b", lowered):
        flags.append("founding")
    if any(term in lowered for term in LEADERSHIP_TERMS):
        flags.append("leadership")
    if _specialization(title):
        flags.append("specialized")
    return flags


def _is_strategic_title(title: str) -> bool:
    return bool(_title_flags(title))


def _specialization(title: str) -> str:
    """Return a specialized sub-domain qualifier from a title, or ''.

    "Research Scientist, Human Simulation" -> "Human Simulation".
    "Engineer (Forward Deployed)" -> "Forward Deployed".
    "Engineer, Senior" -> "" (generic level word, not a capability).
    """
    tail = ""
    paren = re.search(r"\(([^)]+)\)", title)
    if paren:
        tail = paren.group(1).strip()
    elif "," in title:
        tail = title.rsplit(",", 1)[1].strip()
    if not tail or len(tail) < 4:
        return ""
    if tail.lower() in _GENERIC_QUALIFIERS:
        return ""
    return tail


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
